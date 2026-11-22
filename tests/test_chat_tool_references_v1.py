import asyncio

from app.broker import RequestBroker
from app.chat_tool_references_v1 import (
    apply_inline_citation_markers, extract_tool_references, normalize_tool_references,
)


def test_extracts_bounded_public_citations_from_supported_metadata_roots() -> None:
    metadata = {
        "content_references": [
            {
                "title": "Python library reference",
                "snippet": "Official language documentation",
                "ref_id": "turn0search0",
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
                "citation_id": "turn0search1",
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
            "id": "turn0search0",
            "snippet": "Official language documentation",
        },
        {
            "url": "https://example.org/reference",
            "title": "Second source",
            "id": "turn0search1",
            "snippet": "A public source.",
        },
    ]


def test_normalizes_persisted_references_with_deduplication_and_bounds() -> None:
    references = normalize_tool_references([
        {"id": "turn0search0", "url": "https://example.org/a", "title": "  A\nSource  ", "snippet": "x" * 900},
        {"id": "turn0search0", "url": "https://example.org/duplicate", "title": "Duplicate"},
        {"url": "https://chatgpt.com/c/123456789012", "title": "Session"},
        {"url": "https://192.168.1.10/private", "title": "Private"},
        {"url": "javascript:alert(1)", "title": "Active"},
    ])

    assert len(references) == 1
    assert references[0]["title"] == "A Source"
    assert references[0]["id"] == "turn0search0"
    assert len(references[0]["snippet"]) == 420
    invalid_id = normalize_tool_references([{
        "id": "turn0search0<script>",
        "url": "https://example.org/source",
        "title": "Source",
    }])
    assert invalid_id == [{
        "url": "https://example.org/source",
        "title": "Source",
        "snippet": "",
    }]


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


def test_broker_extracts_forwarded_citation_roots_for_web_chat_mode() -> None:
    async def scenario() -> None:
        broker = RequestBroker()
        state = await broker.create("req_tool_refs_123", "worker-client")
        state.mode1_web_chat = True

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
                    "citation_id": "turn0search0",
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
                "id": "turn0search0",
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


def test_broker_does_not_forward_web_chat_citations_to_mode2_sdk_events() -> None:
    async def scenario() -> None:
        broker = RequestBroker()
        state = await broker.create("req_tool_refs_mode2", "worker-client")
        assert state.mode1_web_chat is False
        accepted = await broker.publish("req_tool_refs_mode2", {
            "type": "chat.tool.result",
            "tool_name": "browser.search",
            "call_id": "call-mode2",
            "status": "completed",
            "output": "Found sources",
            "metadata": {
                "search_results": [{
                    "title": "Search result",
                    "url": "https://search.example.org/a",
                    "citation_id": "turn0search0",
                }],
            },
        })
        assert accepted is True
        event = await state.queue.get()
        assert "references" not in event

    asyncio.run(scenario())



def test_extracts_responses_url_citation_annotations_and_nested_web_search_sources() -> None:
    metadata = {
        "annotations": [
            {
                "type": "url_citation",
                "start_index": 12,
                "end_index": 24,
                "url_citation": {
                    "url": "https://docs.python.org/3/library/",
                    "title": "Python library reference",
                    "ref_id": "turn0search42",
                },
            },
        ],
        "web_search_results": [
            {
                "source_title": "Python packaging guide",
                "link_url": "https://packaging.python.org/en/latest/",
                "source_ref": "turn0search43",
            },
        ],
        "unrelated": {
            "url": "https://private.example.net/not-a-source",
            "title": "Must not be forwarded",
        },
    }

    assert extract_tool_references(metadata) == [
        {
            "url": "https://docs.python.org/3/library/",
            "title": "Python library reference",
            "snippet": "",
            "id": "turn0search42",
            "start_index": 12,
            "end_index": 24,
        },
        {
            "url": "https://packaging.python.org/en/latest/",
            "title": "Python packaging guide",
            "snippet": "",
            "id": "turn0search43",
        },
    ]



def test_final_assistant_message_annotations_attach_to_canonical_terminal_for_web_chat() -> None:
    async def scenario() -> None:
        broker = RequestBroker()
        state = await broker.create("req_final_refs_123", "worker-client")
        state.mode1_web_chat = True

        accepted = await broker.publish("req_final_refs_123", {
            "type": "chat.completed",
            "text": "The API is documented here.",
            "metadata": {
                "annotations": [{
                    "type": "url_citation",
                    "start_index": 0,
                    "end_index": 26,
                    "url_citation": {
                        "url": "https://docs.example.org/api",
                        "title": "API reference",
                        "ref_id": "turn0search0",
                    },
                }],
            },
        })

        assert accepted is True
        event = await state.queue.get()
        assert event["type"] == "chat.completed"
        assert event["text"] == "The API is documented here."
        assert event["references"] == [{
            "url": "https://docs.example.org/api",
            "title": "API reference",
            "snippet": "",
            "id": "turn0search0",
            "start_index": 0,
            "end_index": 26,
        }]
        assert state.references == event["references"]
        assert state.terminal_event_type == "chat.completed"

    asyncio.run(scenario())


def test_final_assistant_message_annotations_are_not_exposed_to_mode2() -> None:
    async def scenario() -> None:
        broker = RequestBroker()
        state = await broker.create("req_final_refs_mode2", "worker-client")
        accepted = await broker.publish("req_final_refs_mode2", {
            "type": "chat.completed",
            "text": "Answer",
            "metadata": {
                "annotations": [{
                    "type": "url_citation",
                    "url_citation": {"url": "https://docs.example.org/api"},
                }],
            },
        })
        assert accepted is True
        event = await state.queue.get()
        assert event["type"] == "chat.completed"
        assert "references" not in event
        assert state.references == []

    asyncio.run(scenario())



def test_inline_citation_markers_use_utf16_offsets_and_preserve_multiple_mentions() -> None:
    text = "A😀BC and D"
    references = [
        {
            "id": "turn0search0",
            "url": "https://docs.example.org/api",
            "title": "API",
            "start_index": 1,
            "end_index": 3,
        },
        {
            "id": "turn0search0",
            "url": "https://docs.example.org/api",
            "title": "API",
            "start_index": 10,
            "end_index": 11,
        },
        {
            # This begins inside the emoji's UTF-16 surrogate pair and must
            # not produce a marker at an invalid Unicode boundary.
            "id": "turn0search1",
            "url": "https://docs.example.org/invalid",
            "title": "Invalid",
            "start_index": 2,
            "end_index": 3,
        },
    ]

    marker = "\uE200cite\uE202turn0search0\uE201"
    assert apply_inline_citation_markers(text, references) == (
        "A😀" + marker + "BC and D" + marker
    )


def test_live_reference_event_keeps_the_durable_sse_cursor() -> None:
    async def scenario() -> None:
        broker = RequestBroker()
        state = await broker.create("req_live_refs_cursor", "worker-client")
        state.mode1_web_chat = True
        accepted = await broker.publish("req_live_refs_cursor", {
            "type": "chat.references",
            "event_id": 17,
            "message_id": "assistant-final",
            "metadata": {
                "annotations": [{
                    "type": "url_citation",
                    "url_citation": {
                        "url": "https://docs.example.org/api",
                        "title": "API reference",
                        "ref_id": "turn0search0",
                    },
                }],
            },
        })
        assert accepted is True
        event = await state.queue.get()
        assert event["type"] == "chat.references"
        assert event["event_id"] == 17
        assert event["references"][0]["id"] == "turn0search0"

    asyncio.run(scenario())
