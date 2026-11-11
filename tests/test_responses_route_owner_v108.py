from __future__ import annotations


def test_production_entry_has_one_responses_route_owner() -> None:
    # Import the real composed production app, not a standalone FastAPI fixture.
    from app.entry import app

    post_routes = [
        route for route in app.routes
        if getattr(route, "path", None) == "/v1/responses"
        and "POST" in (getattr(route, "methods", None) or set())
    ]
    get_routes = [
        route for route in app.routes
        if getattr(route, "path", None) == "/v1/responses/{response_id}"
        and "GET" in (getattr(route, "methods", None) or set())
    ]

    assert len(post_routes) == 1
    assert post_routes[0].endpoint.__module__ == "app.responses_v108_patch"
    assert post_routes[0].endpoint.__name__ == "create_response"
    assert len(get_routes) == 1
    assert get_routes[0].endpoint.__module__ == "app.responses_v108_patch"
    assert get_routes[0].endpoint.__name__ == "retrieve_response"
