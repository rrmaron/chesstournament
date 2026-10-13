"""Coaches can set up puzzles/positions by dragging pieces and by pasting or
uploading a screenshot/photo of a board, instead of hand-typing FEN.
"""
from tests.conftest import login

def _tiny_png() -> bytes:
    import io as _io
    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (8, 8), (200, 200, 200)).save(buf, format="PNG")
    return buf.getvalue()


PNG = _tiny_png()  # a real, decodable image: uploads are normalized before reaching Claude


def make_coach(app_module, username="coachk", password="correct-horse"):
    return app_module.create_user(username, password, role="coach", status="active")


def test_coach_dashboard_has_board_editor_and_image_drop(client, app_module):
    make_coach(app_module)
    login(client, "coachk", "correct-horse")

    page = client.get("/coaching")

    assert 'id="hw-editor"' in page.text
    assert 'id="hw-image-drop"' in page.text
    assert 'data-action="record"' in page.text
    assert "CoachBoard.editor(" in page.text


def test_board_from_image_requires_coach(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "ANTHROPIC_API_KEY", "sk-ant-test")
    app_module.create_user("stu", "correct-horse", role="viewer", status="active")
    login(client, "stu", "correct-horse")

    resp = client.post("/api/coaching/board-from-image", files={"image": ("b.png", PNG, "image/png")})

    assert resp.status_code == 403


def test_board_from_image_reports_when_not_configured(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "ANTHROPIC_API_KEY", "")
    make_coach(app_module)
    login(client, "coachk", "correct-horse")

    resp = client.post("/api/coaching/board-from-image", files={"image": ("b.png", PNG, "image/png")})

    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"]


def test_board_from_image_returns_fen_from_recognized_placement(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "ANTHROPIC_API_KEY", "sk-ant-test")
    seen = {}

    def fake_reader(data, media_type):
        seen["data"], seen["media_type"] = data, media_type
        return "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR"

    monkeypatch.setattr(app_module, "_fen_from_image", fake_reader)
    make_coach(app_module)
    login(client, "coachk", "correct-horse")

    resp = client.post("/api/coaching/board-from-image", files={"image": ("shot.jpg", PNG, "image/jpeg")})

    assert resp.status_code == 200
    assert resp.json() == {"fen": "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w - - 0 1"}
    assert seen["media_type"] == "image/jpeg"
    assert seen["data"][:3] == b"\xff\xd8\xff"  # normalized to JPEG before reaching Claude


def test_board_from_image_rejects_unreadable_or_bad_results(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "ANTHROPIC_API_KEY", "sk-ant-test")
    make_coach(app_module)
    login(client, "coachk", "correct-horse")

    monkeypatch.setattr(app_module, "_fen_from_image", lambda data, mt: None)
    resp = client.post("/api/coaching/board-from-image", files={"image": ("b.png", PNG, "image/png")})
    assert resp.status_code == 422

    monkeypatch.setattr(app_module, "_fen_from_image", lambda data, mt: "8/8/8/8/8/8/8/8")
    resp = client.post("/api/coaching/board-from-image", files={"image": ("b.png", PNG, "image/png")})
    assert resp.status_code == 422

    resp = client.post("/api/coaching/board-from-image", files={"image": ("b.txt", b"hello", "text/plain")})
    assert resp.status_code == 415

    def boom(data, mt):
        raise RuntimeError("api down")
    monkeypatch.setattr(app_module, "_fen_from_image", boom)
    resp = client.post("/api/coaching/board-from-image", files={"image": ("b.png", PNG, "image/png")})
    assert resp.status_code == 502


def test_valid_fen_placement(app_module):
    ok = app_module._valid_fen_placement
    assert ok("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")
    assert ok("6k1/5ppp/8/8/8/8/5PPP/4R1K1")
    assert not ok("8/8/8/8/8/8/8/8")                       # no kings
    assert not ok("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP")    # 7 ranks
    assert not ok("rnbqkbnr/ppppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")  # 9 squares on a rank
    assert not ok("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNK")   # two white kings
    assert not ok("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQXBNR")   # bad char


def test_anthropic_client_sends_workspace_header_only_when_configured(app_module, monkeypatch):
    """Org-level keys that aren't scoped to a workspace are rejected by the API
    unless every request carries anthropic-workspace-id; the app passes it from
    the ANTHROPIC_WORKSPACE_ID secret."""
    monkeypatch.setattr(app_module, "ANTHROPIC_API_KEY", "sk-ant-test")

    monkeypatch.setattr(app_module, "ANTHROPIC_WORKSPACE_ID", "")
    assert "anthropic-workspace-id" not in app_module._anthropic_client().default_headers

    monkeypatch.setattr(app_module, "ANTHROPIC_WORKSPACE_ID", "wrkspc_01ABC")
    client = app_module._anthropic_client()
    assert client.default_headers["anthropic-workspace-id"] == "wrkspc_01ABC"
    assert client.api_key == "sk-ant-test"
