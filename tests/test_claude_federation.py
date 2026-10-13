"""Workload Identity Federation for the Claude API: with no static key, the Fly
machine mints an OIDC JWT from the local Fly API socket and the SDK exchanges it
for a short-lived Anthropic token. A static ANTHROPIC_API_KEY keeps precedence.
"""
import json

import httpx
import pytest

from tests.conftest import login
from tests.test_coach_position_editor import PNG

FEDERATION = {
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_FEDERATION_RULE_ID": "fdrl_01TEST",
    "ANTHROPIC_ORGANIZATION_ID": "00000000-0000-0000-0000-000000000001",
    "ANTHROPIC_SERVICE_ACCOUNT_ID": "svac_01TEST",
    "ANTHROPIC_WORKSPACE_ID": "wrkspc_01TEST",
    "ANTHROPIC_IDENTITY_TOKEN_FILE": "",
}
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJwZXJzb25hbDpteWNoZXNzcmF0aW5nOm0ifQ.c2ln"


def _configure(app_module, monkeypatch, **overrides):
    for key, value in {**FEDERATION, **overrides}.items():
        monkeypatch.setattr(app_module, key, value)


def test_auth_mode_precedence(app_module, monkeypatch):
    _configure(app_module, monkeypatch, ANTHROPIC_FEDERATION_RULE_ID="", ANTHROPIC_ORGANIZATION_ID="")
    assert app_module._claude_auth_mode() == ""

    _configure(app_module, monkeypatch)
    assert app_module._claude_auth_mode() == "federation"

    _configure(app_module, monkeypatch, ANTHROPIC_API_KEY="sk-ant-static")
    assert app_module._claude_auth_mode() == "api_key"


def test_fly_identity_token_mints_a_fresh_jwt_each_call(app_module, monkeypatch):
    calls = []

    def fly_api(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, json.loads(request.content or b"{}")))
        return httpx.Response(200, text=f"{JWT}{len(calls)}\n")

    monkeypatch.setattr(app_module, "_fly_transport", lambda: httpx.MockTransport(fly_api))
    monkeypatch.setattr(app_module, "ANTHROPIC_IDENTITY_TOKEN_AUDIENCE", "")

    first, second = app_module._fly_identity_token(), app_module._fly_identity_token()

    assert first == JWT + "1" and second == JWT + "2"  # never cached: each exchange needs a fresh jti
    assert calls == [("POST", "/v1/tokens/oidc", {})] * 2

    monkeypatch.setattr(app_module, "ANTHROPIC_IDENTITY_TOKEN_AUDIENCE", "https://api.anthropic.com")
    app_module._fly_identity_token()
    assert calls[-1][2] == {"aud": "https://api.anthropic.com"}


def test_fly_identity_token_accepts_json_wrapper_and_rejects_non_jwt(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "_fly_transport",
                        lambda: httpx.MockTransport(lambda r: httpx.Response(200, json={"token": JWT})))
    assert app_module._fly_identity_token() == JWT

    monkeypatch.setattr(app_module, "_fly_transport",
                        lambda: httpx.MockTransport(lambda r: httpx.Response(200, text="not a token")))
    with pytest.raises(RuntimeError):
        app_module._fly_identity_token()

    monkeypatch.setattr(app_module, "_fly_transport",
                        lambda: httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(httpx.HTTPStatusError):
        app_module._fly_identity_token()


def test_federation_credentials_use_fly_socket_or_token_file(app_module, monkeypatch, tmp_path):
    _configure(app_module, monkeypatch)
    creds = app_module._federation_credentials()
    assert creds._federation_rule_id == "fdrl_01TEST"
    assert creds._organization_id == "00000000-0000-0000-0000-000000000001"
    assert creds._service_account_id == "svac_01TEST"
    assert creds._workspace_id == "wrkspc_01TEST"
    assert creds._identity_token_provider is app_module._fly_identity_token

    token_file = tmp_path / "token"
    token_file.write_text(JWT)
    _configure(app_module, monkeypatch, ANTHROPIC_IDENTITY_TOKEN_FILE=str(token_file), ANTHROPIC_SERVICE_ACCOUNT_ID="")
    creds = app_module._federation_credentials()
    assert creds._service_account_id is None
    assert creds._identity_token_provider() == JWT


def test_anthropic_client_is_cached_per_configuration(app_module, monkeypatch):
    _configure(app_module, monkeypatch)
    federated = app_module._anthropic_client()
    assert federated is app_module._anthropic_client()

    _configure(app_module, monkeypatch, ANTHROPIC_API_KEY="sk-ant-static", ANTHROPIC_WORKSPACE_ID="")
    keyed = app_module._anthropic_client()
    assert keyed is not federated and keyed.api_key == "sk-ant-static"
    assert keyed is app_module._anthropic_client()


def test_board_from_image_works_with_federation_and_no_api_key(client, app_module, monkeypatch):
    app_module.create_user("coachf", "correct-horse", role="coach", status="active")
    login(client, "coachf", "correct-horse")

    _configure(app_module, monkeypatch, ANTHROPIC_FEDERATION_RULE_ID="", ANTHROPIC_ORGANIZATION_ID="")
    unconfigured = client.post("/api/coaching/board-from-image", files={"image": ("b.png", PNG, "image/png")})
    assert unconfigured.status_code == 503

    _configure(app_module, monkeypatch)
    monkeypatch.setattr(app_module, "_fen_from_image", lambda data, mt: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")
    resp = client.post("/api/coaching/board-from-image", files={"image": ("b.png", PNG, "image/png")})
    assert resp.status_code == 200
    assert resp.json()["fen"].startswith("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w")


def test_fly_identity_token_logs_claims_for_diagnosis(app_module, monkeypatch, caplog):
    import base64
    claims = {"iss": "https://oidc.fly.io/personal", "sub": "personal:mychessrating:m1",
              "aud": "https://api.anthropic.com", "app_name": "mychessrating", "jti": "secret-ish"}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    token = f"eyJhbGciOiJSUzI1NiJ9.{payload}.c2ln"
    monkeypatch.setattr(app_module, "_fly_transport",
                        lambda: httpx.MockTransport(lambda r: httpx.Response(200, text=token)))

    with caplog.at_level("INFO"):
        assert app_module._fly_identity_token() == token

    line = next(r.message for r in caplog.records if "Fly identity token minted" in r.message)
    assert "personal:mychessrating:m1" in line and "https://api.anthropic.com" in line
    assert "secret-ish" not in line and "c2ln" not in line
    assert app_module._jwt_claims_summary("garbage") == "<undecodable>"
    assert "personal:mychessrating:m1" in app_module._last_fly_token_claims


def test_board_from_image_failure_warning_includes_last_token_claims(client, app_module, monkeypatch, caplog):
    """The app logs only WARNING and above in production, so the claims ride
    along on the exchange-failure warning rather than a separate INFO line."""
    app_module.create_user("coachg", "correct-horse", role="coach", status="active")
    login(client, "coachg", "correct-horse")
    _configure(app_module, monkeypatch)
    monkeypatch.setattr(app_module, "_last_fly_token_claims", '{"sub": "personal:mychessrating:m9"}')

    def boom(data, mt):
        raise RuntimeError("Token exchange failed (HTTP 401)")

    monkeypatch.setattr(app_module, "_fen_from_image", boom)
    with caplog.at_level("WARNING"):
        resp = client.post("/api/coaching/board-from-image", files={"image": ("b.png", PNG, "image/png")})

    assert resp.status_code == 502
    line = next(r.message for r in caplog.records if "board-from-image for user" in r.message)
    assert "HTTP 401" in line and "personal:mychessrating:m9" in line
