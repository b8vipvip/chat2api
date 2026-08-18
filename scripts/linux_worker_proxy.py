#!/usr/bin/env python3
"""Parse supported proxy share links into a minimal Xray client configuration.

The module is intentionally dependency-free so the bootstrap Worker venv only
needs the WebSocket client. Parsed credentials remain in memory on the Worker
and are never returned to the control plane; callers should use the sanitized
summary returned by ``build_xray_config`` for status/UI purposes.
"""
from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


SUPPORTED_PROXY_SCHEMES = frozenset({"vless", "vmess", "trojan", "ss"})
MAX_SHARE_LINK_LENGTH = 16384


class ProxyConfigError(ValueError):
    pass


def _b64decode_text(value: str) -> str:
    compact = "".join(str(value or "").strip().split())
    if not compact:
        raise ProxyConfigError("empty base64 payload")
    compact += "=" * (-len(compact) % 4)
    try:
        return base64.urlsafe_b64decode(compact.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise ProxyConfigError("invalid base64 payload") from exc


def _require_port(value: Any) -> int:
    try:
        port = int(value)
    except Exception as exc:
        raise ProxyConfigError("invalid server port") from exc
    if not 1 <= port <= 65535:
        raise ProxyConfigError("server port is out of range")
    return port


def _first(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key) or []
    return str(values[0]) if values else default


def _stream_settings(
    *,
    network: str,
    security: str,
    query: dict[str, list[str]],
    address: str,
) -> dict[str, Any]:
    network = (network or "tcp").lower()
    if network not in {"tcp", "ws", "grpc", "http", "h2"}:
        raise ProxyConfigError(f"unsupported transport: {network}")

    normalized_network = "http" if network in {"http", "h2"} else network
    stream: dict[str, Any] = {"network": normalized_network}
    security = (security or "none").lower()
    if security not in {"none", "tls", "reality"}:
        raise ProxyConfigError(f"unsupported transport security: {security}")
    if security != "none":
        stream["security"] = security

    sni = _first(query, "sni") or _first(query, "serverName") or address
    fingerprint = _first(query, "fp") or _first(query, "fingerprint")
    alpn = _first(query, "alpn")

    if security == "tls":
        tls: dict[str, Any] = {"serverName": sni}
        if fingerprint:
            tls["fingerprint"] = fingerprint
        if alpn:
            tls["alpn"] = [part.strip() for part in alpn.split(",") if part.strip()]
        if _first(query, "allowInsecure").lower() in {"1", "true", "yes"}:
            tls["allowInsecure"] = True
        stream["tlsSettings"] = tls
    elif security == "reality":
        public_key = _first(query, "pbk") or _first(query, "publicKey")
        if not public_key:
            raise ProxyConfigError("VLESS Reality link is missing public key")
        reality: dict[str, Any] = {
            "serverName": sni,
            "publicKey": public_key,
        }
        if fingerprint:
            reality["fingerprint"] = fingerprint
        short_id = _first(query, "sid") or _first(query, "shortId")
        spider_x = _first(query, "spx") or _first(query, "spiderX")
        if short_id:
            reality["shortId"] = short_id
        if spider_x:
            reality["spiderX"] = spider_x
        stream["realitySettings"] = reality

    if normalized_network == "ws":
        ws: dict[str, Any] = {}
        path = _first(query, "path")
        host = _first(query, "host")
        if path:
            ws["path"] = path
        if host:
            ws["headers"] = {"Host": host}
        stream["wsSettings"] = ws
    elif normalized_network == "grpc":
        service_name = _first(query, "serviceName") or _first(query, "path")
        grpc: dict[str, Any] = {}
        if service_name:
            grpc["serviceName"] = service_name.lstrip("/")
        if _first(query, "mode").lower() in {"multi", "multi-mode", "gun"}:
            grpc["multiMode"] = True
        stream["grpcSettings"] = grpc
    elif normalized_network == "http":
        http_settings: dict[str, Any] = {}
        path = _first(query, "path")
        host = _first(query, "host")
        if path:
            http_settings["path"] = path
        if host:
            http_settings["host"] = [host]
        stream["httpSettings"] = http_settings
    elif normalized_network == "tcp":
        header_type = _first(query, "headerType") or _first(query, "header")
        if header_type and header_type != "none":
            stream["tcpSettings"] = {"header": {"type": header_type}}

    return stream


def _parse_vless(uri: str) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed = urlsplit(uri)
    user_id = unquote(parsed.username or "")
    address = parsed.hostname or ""
    if not user_id or not address or parsed.port is None:
        raise ProxyConfigError("VLESS link requires UUID, server and port")
    port = _require_port(parsed.port)
    query = parse_qs(parsed.query, keep_blank_values=True)
    network = _first(query, "type", "tcp")
    security = _first(query, "security", "none")
    user: dict[str, Any] = {
        "id": user_id,
        "encryption": _first(query, "encryption", "none") or "none",
    }
    flow = _first(query, "flow")
    if flow:
        user["flow"] = flow
    outbound = {
        "protocol": "vless",
        "tag": "proxy",
        "settings": {"vnext": [{"address": address, "port": port, "users": [user]}]},
        "streamSettings": _stream_settings(
            network=network,
            security=security,
            query=query,
            address=address,
        ),
    }
    return outbound, {
        "protocol": "vless",
        "server": address,
        "port": port,
        "transport": network.lower(),
        "security": security.lower(),
    }


def _parse_vmess(uri: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = uri.split("://", 1)[1].split("#", 1)[0].strip()
    try:
        item = json.loads(_b64decode_text(payload))
    except json.JSONDecodeError as exc:
        raise ProxyConfigError("invalid VMess JSON payload") from exc
    if not isinstance(item, dict):
        raise ProxyConfigError("invalid VMess payload")
    address = str(item.get("add") or item.get("address") or "").strip()
    user_id = str(item.get("id") or "").strip()
    if not address or not user_id:
        raise ProxyConfigError("VMess link requires server and UUID")
    port = _require_port(item.get("port"))
    network = str(item.get("net") or "tcp").lower()
    tls_value = str(item.get("tls") or "").lower()
    security = "tls" if tls_value in {"tls", "1", "true"} else "none"
    query: dict[str, list[str]] = {}
    for source, target in (
        ("host", "host"),
        ("path", "path"),
        ("sni", "sni"),
        ("fp", "fp"),
        ("alpn", "alpn"),
        ("type", "headerType"),
    ):
        value = item.get(source)
        if value not in {None, ""}:
            query[target] = [str(value)]
    user: dict[str, Any] = {
        "id": user_id,
        "alterId": int(item.get("aid") or 0),
        "security": str(item.get("scy") or "auto"),
    }
    outbound = {
        "protocol": "vmess",
        "tag": "proxy",
        "settings": {"vnext": [{"address": address, "port": port, "users": [user]}]},
        "streamSettings": _stream_settings(
            network=network,
            security=security,
            query=query,
            address=address,
        ),
    }
    return outbound, {
        "protocol": "vmess",
        "server": address,
        "port": port,
        "transport": network,
        "security": security,
    }


def _parse_trojan(uri: str) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed = urlsplit(uri)
    password = unquote(parsed.username or "")
    address = parsed.hostname or ""
    if not password or not address or parsed.port is None:
        raise ProxyConfigError("Trojan link requires password, server and port")
    port = _require_port(parsed.port)
    query = parse_qs(parsed.query, keep_blank_values=True)
    network = _first(query, "type", "tcp")
    security = _first(query, "security", "tls") or "tls"
    outbound = {
        "protocol": "trojan",
        "tag": "proxy",
        "settings": {"servers": [{"address": address, "port": port, "password": password}]},
        "streamSettings": _stream_settings(
            network=network,
            security=security,
            query=query,
            address=address,
        ),
    }
    return outbound, {
        "protocol": "trojan",
        "server": address,
        "port": port,
        "transport": network.lower(),
        "security": security.lower(),
    }


def _decode_ss_userinfo(value: str) -> tuple[str, str]:
    raw = unquote(value)
    if ":" not in raw:
        raw = _b64decode_text(raw)
    if ":" not in raw:
        raise ProxyConfigError("Shadowsocks link is missing method/password")
    method, password = raw.split(":", 1)
    if not method or not password:
        raise ProxyConfigError("Shadowsocks link is missing method/password")
    return method, password


def _parse_ss(uri: str) -> tuple[dict[str, Any], dict[str, Any]]:
    body = uri.split("://", 1)[1].split("#", 1)[0]
    if "@" not in body:
        decoded = _b64decode_text(body)
        if "@" not in decoded:
            raise ProxyConfigError("invalid Shadowsocks link")
        credentials, endpoint = decoded.rsplit("@", 1)
        parsed = urlsplit("ss://" + endpoint)
        method, password = _decode_ss_userinfo(credentials)
    else:
        credentials, endpoint = body.rsplit("@", 1)
        parsed = urlsplit("ss://" + endpoint)
        method, password = _decode_ss_userinfo(credentials)
    address = parsed.hostname or ""
    if not address or parsed.port is None:
        raise ProxyConfigError("Shadowsocks link requires server and port")
    port = _require_port(parsed.port)
    outbound = {
        "protocol": "shadowsocks",
        "tag": "proxy",
        "settings": {
            "servers": [
                {
                    "address": address,
                    "port": port,
                    "method": method,
                    "password": password,
                }
            ]
        },
    }
    return outbound, {
        "protocol": "ss",
        "server": address,
        "port": port,
        "transport": "tcp+udp",
        "security": method,
    }


def build_xray_config(share_link: str) -> tuple[dict[str, Any], dict[str, Any]]:
    uri = str(share_link or "").strip()
    if not uri or len(uri) > MAX_SHARE_LINK_LENGTH:
        raise ProxyConfigError("proxy share link is empty or too long")
    if "\n" in uri or "\r" in uri:
        raise ProxyConfigError("proxy share link must be a single line")
    scheme = uri.split(":", 1)[0].lower()
    if scheme not in SUPPORTED_PROXY_SCHEMES:
        raise ProxyConfigError("unsupported proxy share-link scheme")

    if scheme == "vless":
        outbound, summary = _parse_vless(uri)
    elif scheme == "vmess":
        outbound, summary = _parse_vmess(uri)
    elif scheme == "trojan":
        outbound, summary = _parse_trojan(uri)
    else:
        outbound, summary = _parse_ss(uri)

    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": 10808,
                "protocol": "socks",
                "settings": {"udp": True},
            }
        ],
        "outbounds": [outbound, {"protocol": "freedom", "tag": "direct"}],
    }
    return config, summary
