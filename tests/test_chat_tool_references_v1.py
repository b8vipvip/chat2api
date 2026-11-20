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
