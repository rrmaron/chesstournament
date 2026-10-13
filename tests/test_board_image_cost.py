"""After reading a board from an image, the coach sees how much Claude API usage
it cost: token counts and an estimated USD figure priced by the model that
actually served the request.
"""
from types import SimpleNamespace

from tests.conftest import login
from tests.test_coach_position_editor import PNG


def _usage(**kw):
    base = dict(input_tokens=0, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return SimpleNamespace(**{**base, **kw})


def test_usage_cost_prices_by_served_model(app_module):
    cost = app_module._claude_usage_cost("claude-opus-5-5-20260401", _usage(input_tokens=1000, output_tokens=100))
    assert cost["cost_usd"] == 0.006  # 1000 * $4/M + 100 * $20/M
    assert cost["input_tokens"] == 1000 and cost["output_tokens"] == 100
    assert cost["summary"] == "1,000 input + 100 output tokens on claude-opus-5-5-20260401 ≈ $0.0060"

    # "claude-opus-5" must not swallow "claude-opus-5-5" (different prices).
    assert app_module._claude_usage_cost("claude-opus-5", _usage(input_tokens=1000))["cost_usd"] == 0.005

    cached = app_module._claude_usage_cost("claude-sonnet-5-5", _usage(cache_read_input_tokens=10000, output_tokens=0))
    assert cached["cost_usd"] == 0.001 and cached["cache_read_tokens"] == 10000

    unknown = app_module._claude_usage_cost("claude-future-9", _usage(input_tokens=5))
    assert unknown["cost_usd"] is None and "$" not in unknown["summary"]
    assert app_module._claude_usage_cost("", None)["summary"].endswith("on unknown model")


def test_fen_from_image_returns_placement_and_usage(app_module, monkeypatch):
    response = SimpleNamespace(
        model="claude-opus-5-5", stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="Here it is: rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR")],
        usage=_usage(input_tokens=1500, output_tokens=40),
    )
    fake_client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response)))
    monkeypatch.setattr(app_module, "_anthropic_client", lambda: fake_client)

    placement, usage = app_module._fen_from_image(b"img", "image/jpeg")

    assert placement == "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR"
    assert usage["cost_usd"] == 0.0068 and usage["model"] == "claude-opus-5-5"

    response.stop_reason = "refusal"
    assert app_module._fen_from_image(b"img", "image/jpeg") == (None, usage)


def test_route_returns_usage_with_fen_and_editor_shows_it(client, app_module, monkeypatch):
    app_module.create_user("coachc", "correct-horse", role="coach", status="active")
    login(client, "coachc", "correct-horse")
    monkeypatch.setattr(app_module, "ANTHROPIC_API_KEY", "sk-ant-test")
    usage = app_module._claude_usage_cost("claude-opus-5-5", _usage(input_tokens=2000, output_tokens=50))
    monkeypatch.setattr(app_module, "_fen_from_image",
                        lambda data, mt: ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR", usage))

    resp = client.post("/api/coaching/board-from-image", files={"image": ("b.png", PNG, "image/png")})

    assert resp.status_code == 200
    body = resp.json()
    assert body["usage"]["cost_usd"] == 0.009
    assert body["usage"]["summary"] == "2,000 input + 50 output tokens on claude-opus-5-5 ≈ $0.0090"

    js = client.get("/static/coach_board.js").text
    assert "API usage: " in js and "res.data.usage.summary" in js

    monkeypatch.setattr(app_module, "_fen_from_image", lambda data, mt: (None, usage))
    unreadable = client.post("/api/coaching/board-from-image", files={"image": ("b.png", PNG, "image/png")})
    assert unreadable.status_code == 422 and "$0.0090" in unreadable.json()["detail"]
