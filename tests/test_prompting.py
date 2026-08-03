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
