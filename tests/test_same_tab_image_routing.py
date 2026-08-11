from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_gpt_image_v3_stays_in_current_chat_and_does_not_navigate_to_images_gallery() -> None:
    routing = (EXTENSION / "image_routing_v3.js").read_text(encoding="utf-8")
    assert 'route: "chatgpt-current-chat"' in routing
    assert "image_same_tab: true" in routing
    assert "images_page: false" in routing
    assert 'image_tab_strategy: "reuse-current-chat"' in routing
    assert 'type: "chat2api.image.request.v3"' in routing
    assert 'type: "chat2api.attach.prepare.v4"' in routing
    assert "requestId" in routing
    assert 'https://chatgpt.com/images/' not in routing
    assert "IMAGES_URL" not in routing
    assert "restore(" not in routing


def test_same_tab_image_route_keeps_strict_v3_result_controller() -> None:
    controller = (EXTENSION / "content_image_v3.js").read_text(encoding="utf-8")
    assert "submitAndConfirm(active, prompt)" in controller
    assert "promptEchoedOutsideComposer" in controller
    assert "baseline.get(img) !== src" in controller
    assert "Generated image appeared but could not be captured as image bytes" in controller
    assert 'type: "image.completed"' in controller
