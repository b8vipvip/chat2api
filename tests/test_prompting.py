from app.models import ChatCompletionRequest, ChatMessage
from app.prompting import build_prompt


def test_last_user_prompt_includes_instructions() -> None:
    messages = [
        ChatMessage(role="system", content="Be concise."),
        ChatMessage(role="user", content="First"),
        ChatMessage(role="assistant", content="Answer"),
        ChatMessage(role="user", content="Second"),
    ]
    assert build_prompt(messages, "last_user") == "[Instructions]\nBe concise.\n\n[User]\nSecond"


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
    assert build_prompt(messages, "full") == "[User]\nMake a dog\n\n[User]\nMake a cat"
    # Deliberate user strings must not be silently rewritten.
    messages = [ChatMessage(role="user", content='{"skipped_mainline":true}')]
    assert build_prompt(messages, "full") == '[User]\n{"skipped_mainline":true}'


def test_openai_chat_completions_default_preserves_the_complete_messages_history() -> None:
    messages = [
        ChatMessage(role="system", content="Be concise."),
        ChatMessage(role="user", content="First question"),
        ChatMessage(role="assistant", content="First answer"),
        ChatMessage(role="user", content="Follow-up"),
    ]

    request = ChatCompletionRequest(model="gpt-5.6-sol", messages=messages)

    assert request.prompt_mode == "full"
    assert build_prompt(request.messages, request.prompt_mode) == (
        "[System]\\nBe concise.\\n\\n"
        "[User]\\nFirst question\\n\\n"
        "[Assistant]\\nFirst answer\\n\\n"
        "[User]\\nFollow-up"
    )


def test_last_user_prompt_remains_available_as_an_explicit_chat2api_extension() -> None:
    messages = [
        ChatMessage(role="user", content="Earlier turn"),
        ChatMessage(role="assistant", content="Earlier answer"),
        ChatMessage(role="user", content="Latest turn"),
    ]

    assert build_prompt(messages, "last_user") == "Latest turn"
