from app.models import ChatMessage
from app.prompting import build_prompt


def test_last_user_prompt_includes_instructions() -> None:
    messages = [
        ChatMessage(role="system", content="Be concise."),
        ChatMessage(role="user", content="First"),
        ChatMessage(role="assistant", content="Answer"),
        ChatMessage(role="user", content="Second"),
    ]
    assert build_prompt(messages) == "[Instructions]\nBe concise.\n\n[User]\nSecond"


def test_full_prompt_serializes_history() -> None:
    messages = [
        ChatMessage(role="user", content="Hello"),
        ChatMessage(role="assistant", content="Hi"),
    ]
    assert build_prompt(messages, "full") == "[User]\nHello\n\n[Assistant]\nHi"


def test_full_prompt_omits_internal_assistant_media_handoff_but_keeps_user_text() -> None:
    messages = [
        ChatMessage(role="user", content="Make a dog"),
        ChatMessage(role="assistant", content='{"skipped_mainline":true}{"skipped_mainline":true}'),
        ChatMessage(role="user", content="Make a cat"),
    ]
    assert build_prompt(messages, "full") == "[User]\\nMake a dog\\n\\n[User]\\nMake a cat"
    # Deliberate user strings must not be silently rewritten.
    messages = [ChatMessage(role="user", content='{"skipped_mainline":true}')]
    assert build_prompt(messages, "full") == '[User]\\n{"skipped_mainline":true}'
