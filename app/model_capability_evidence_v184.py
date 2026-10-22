"""Capability evidence projection for the *existing* Worker model-library authority.

No Worker is selected here and no request terminal is changed. Model-picker
validation establishes which model may be selected; extension capabilities
describe the browser transport, not proven per-model image-generation support.
"""
from __future__ import annotations

from typing import Any

CAPABILITY_EVIDENCE_VERSION = 184

_MODEL_CAPABILITIES = {
    "text_input": "text",
    "text_output": "text",
    "image_input": "vision",
    "file_input": "file-understanding",
}

_TRANSPORT_CAPABILITIES = {
    "image_generation": "image-generation",
    "image_edit": "image-reference",
}


def _advertised(metadata: Any) -> set[str]:
    if not isinstance(metadata, dict):
        return set()
    values = metadata.get("capabilities")
    if not isinstance(values, list):
        return set()
    return {str(value).lower().strip() for value in values if isinstance(value, str)}


def project_capabilities(
    registry: Any,
    model_entry: dict[str, Any],
    validated_models_by_worker: dict[str, dict[str, dict[str, Any]]],
    *,
    special_model: bool = False,
) -> dict[str, Any]:
    """Produce evidence for each operation using only eligible catalog Workers.

    Each entry has its own verified/reporting Worker set; a capability from one
    Worker must not be credited to every other Worker advertising the model.
    "transport_advertised" never means that an image-generation task succeeded.
    """
    model_id = str(model_entry.get("id") or "")
    clients = sorted(set(str(value) for value in (model_entry.get("clients") or [])))
    results: dict[str, dict[str, Any]] = {}
    for name, advertised_name in _MODEL_CAPABILITIES.items():
        validated: list[str] = []
        reported: list[str] = []
        for client_id in clients:
            row = validated_models_by_worker.get(client_id, {}).get(model_id)
            if row is None:
                continue
            flags = row.get("capabilities")
            # Browser-side picker verification proves a concrete model can be
            # selected; older reports with no capabilities prove text only.
            model_flags = (
                {str(v).strip().lower() for v in flags if isinstance(v, str)}
                if isinstance(flags, list) else {"text"}
            )
            if advertised_name in model_flags:
                if name.startswith("text_"):
                    validated.append(client_id)
                else:
                    reported.append(client_id)
        results[name] = {
            "state": "picker_validated" if validated else "model_reported" if reported else "unverified",
            "workers": sorted(validated or reported),
            "evidence": "model_picker_validation" if validated else "worker_model_capabilities" if reported else None,
        }

    for name, advertised_name in _TRANSPORT_CAPABILITIES.items():
        clients_with_transport = []
        for client_id in clients:
            client = getattr(registry, "clients", {}).get(client_id)
            flags = _advertised(getattr(client, "metadata", None) if client else None)
            if advertised_name in flags:
                clients_with_transport.append(client_id)
        results[name] = {
            "state": "transport_advertised" if clients_with_transport else "unverified",
            "workers": clients_with_transport,
            "evidence": "extension_runtime_capabilities" if clients_with_transport else None,
            "model_specific_verified": False,
        }

    # The old Images API uses gpt-image as a transport alias, not a verified
    # GPT model choice. Preserve legacy discovery without claiming model proof.
    if special_model:
        for name in _MODEL_CAPABILITIES:
            results[name] = {"state": "not_applicable", "workers": [], "evidence": None}

    is_validated = bool(model_entry.get("validated"))
    compat_workers = sorted(set(model_entry.get("compatibility_clients") or []))
    return {
        "revision": CAPABILITY_EVIDENCE_VERSION,
        "authority": "worker-model-library-v145",
        "model_picker_validated": bool(is_validated and not special_model),
        "model_validation_state": (
            "compatibility_unverified" if compat_workers and not is_validated
            else "picker_validated" if is_validated and not special_model
            else "legacy_transport_alias" if special_model
            else "unverified"
        ),
        "legacy_transport_alias": bool(special_model),
        "compatibility_workers": compat_workers,
        "operations": results,
    }
