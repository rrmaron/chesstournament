"""Uploaded board images are shrunk to an RGB JPEG no larger than 1568 px on
the longest side before being sent to Claude, so a full-resolution screenshot
(which can exceed the API's 10 MB base64 limit) never fails on size.
"""
import io

from PIL import Image

from tests.conftest import login


def _png(width, height, mode="RGBA"):
    img = Image.effect_noise((width, height), 64).convert(mode)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_normalize_downscales_and_reencodes_as_jpeg(app_module):
    big = _png(3200, 2000)
    assert len(big) > 2_000_000

    data, media_type = app_module._normalize_board_image(big, "image/png")

    assert media_type == "image/jpeg"
    out = Image.open(io.BytesIO(data))
    assert out.format == "JPEG" and out.mode == "RGB"
    assert out.size == (1568, 980)
    assert len(data) < 1_500_000


def test_normalize_keeps_small_images_at_native_size(app_module):
    data, media_type = app_module._normalize_board_image(_png(400, 400, "RGB"), "image/png")
    assert media_type == "image/jpeg" and Image.open(io.BytesIO(data)).size == (400, 400)


def test_route_sends_normalized_image_and_rejects_undecodable_files(client, app_module, monkeypatch):
    app_module.create_user("coachi", "correct-horse", role="coach", status="active")
    login(client, "coachi", "correct-horse")
    monkeypatch.setattr(app_module, "ANTHROPIC_API_KEY", "sk-ant-test")
    seen = {}

    def fake_reader(data, media_type):
        seen.update(size=len(data), media_type=media_type, dims=Image.open(io.BytesIO(data)).size)
        return "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR", None

    monkeypatch.setattr(app_module, "_fen_from_image", fake_reader)

    resp = client.post("/api/coaching/board-from-image", files={"image": ("shot.png", _png(2500, 2500), "image/png")})
    assert resp.status_code == 200
    assert seen["media_type"] == "image/jpeg" and seen["dims"] == (1568, 1568) and seen["size"] < 1_500_000

    bad = client.post("/api/coaching/board-from-image", files={"image": ("x.png", b"\x89PNG not really", "image/png")})
    assert bad.status_code == 422
    assert "image" in bad.json()["detail"].lower()
