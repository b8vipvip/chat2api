from .models import ChatMessage


def build_prompt(messages: list[ChatMessage], mode: str = "last_user") -> str:
    if mode == "full":
        labels = {
            "system": "System",
            "developer": "Developer",
            "user": "User",
            "assistant": "Assistant",
            "tool": "Tool",
        }
        blocks = [f"[{labels[item.role]}]\n{item.text()}" for item in messages if item.text()]
        return "\n\n".join(blocks).strip()

    instructions = [item.text() for item in messages if item.role in {"system", "developer"} and item.text()]
    user_text = next((item.text() for item in reversed(messages) if item.role == "user" and item.text()), "")
    if instructions:
        return "[Instructions]\n" + "\n\n".join(instructions) + "\n\n[User]\n" + user_text
    return user_text
