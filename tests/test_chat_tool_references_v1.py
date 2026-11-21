import asyncio

from app.broker import RequestBroker
from app.chat_tool_references_v1 import extract_tool_references, normalize_tool_references


def test_extracts_bounded_public_citations_from_supported_metadata_roots() -> None:
    metadata = {
        "content_references": [
            {
                "title": "Python library reference",
                "snippet": "Official language documentation",
                "safe_urls": ["https://docs.python.org/3/library/"],
            },
            {"title": "Credential URL", "url": "https://user:secret@example.org/private"},
            {"title": "Loopback URL", "url": "https://127.0.0.1/private"},
            {"title": "Local URL", "url": "https://worker.local/private"},
            {"title": "Active URL", "url": "javascript:alert(1)"},
        ],
        "search_result_groups": [{
            "entries": [{
                "title": "Second source",
                "url": "https://example.org/reference",
                "description": "A public source.",
            }],
        }],
        # Arbitrary metadata is deliberately not a citation source.
        "unrelated": {"url": "https://ignored.example.net/private"},
    }

    references = extract_tool_references(metadata)
    assert references == [
        {
            "url": "https://docs.python.org/3/library/",
            "title": "Python library reference",
            "snippet": "Official language documentation",
        },
        {
            "url": "https://example.org/reference",
            "title": "Second source",
            "snippet": "A public source.",
        },
    ]


def test_normalizes_persisted_references_with_deduplication_and_bounds() -> None:
    references = normalize_tool_references([
        {"url": "https://example.org/a", "title": "  A\nSource  ", "snippet": "x" * 900},
        {"url": "https://example.org/a", "title": "Duplicate"},
        {"url": "https://chatgpt.com/c/123456789012", "title": "Session"},
        {"url": "https://192.168.1.10/private", "title": "Private"},
        {"url": "javascript:alert(1)", "title": "Active"},
    ])

    assert len(references) == 1
    assert references[0]["title"] == "A Source"
    assert len(references[0]["snippet"]) == 420


def test_extracts_search_results_references_and_sources_roots() -> None:
    metadata = {
        "search_results": [{
            "title": "Search result",
            "url": "https://search.example.org/a",
        }],
        "references": [{
            "name": "Reference",
            "href": "https://docs.example.org/reference",
        }],
        "sources": [{
            "source_name": "Source",
            "safe_url": "https://source.example.org/item",
        }],
    }

    assert extract_tool_references(metadata) == [
        {
            "url": "https://search.example.org/a",
            "title": "Search result",
            "snippet": "",
        },
        {
            "url": "https://docs.example.org/reference",
            "title": "Reference",
            "snippet": "",
        },
        {
            "url": "https://source.example.org/item",
            "title": "Source",
            "snippet": "",
        },
    ]


def test_mode1_broker_extracts_forwarded_citation_roots_from_tool_metadata() -> None:
    async def scenario() -> None:
        broker = RequestBroker()
        state = await broker.create("req_tool_refs_123", "worker-client")
        state.mode1_web_chat = True
        state.mode1_owner_key_id = "key-owner"

        accepted = await broker.publish("req_tool_refs_123", {
            "type": "chat.tool.result",
            "tool_name": "browser.search",
            "call_id": "call-123",
            "result_id": "result-123",
            "status": "completed",
            "output": "Found sources",
            "metadata": {
                "search_results": [{
                    "title": "Search result",
                    "url": "https://search.example.org/a",
                }],
                "references": [{
                    "name": "Reference",
                    "href": "https://docs.example.org/reference",
                }],
                "sources": [{
                    "source_name": "Source",
                    "safe_url": "https://source.example.org/item",
                }],
            },
        })

        assert accepted is True
        event = await state.queue.get()
        assert event["references"] == [
            {
                "url": "https://search.example.org/a",
                "title": "Search result",
                "snippet": "",
            },
            {
                "url": "https://docs.example.org/reference",
                "title": "Reference",
                "snippet": "",
            },
            {
                "url": "https://source.example.org/item",
                "title": "Source",
                "snippet": "",
            },
        ]

    asyncio.run(scenario())
