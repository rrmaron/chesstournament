"""Regression test: "What will happen to my ratings if I play this player?"
only ever showed USCF Regular and FIDE Standard -- Quick/Blitz, Online
Quick/Blitz, and FIDE Rapid/Blitz never appeared.

Root cause: those fields are only written into the player card's dataset by
the separate "Load Quick & Blitz ratings" panel (fragments/player_quick_blitz.html).
If a user clicked "What will happen to my ratings?" without first loading
that panel, loadRatingImpact() read empty dataset attributes for everything
except USCF Regular/FIDE Standard (which are populated directly by
/api/player-details), so those columns silently never appeared.

Fix: loadRatingImpact() now auto-loads the quick/blitz panel first (if it
hasn't been loaded yet) before reading the card's dataset.
"""
from tests.conftest import make_admin, login


def test_rating_impact_button_autoloads_quick_blitz_panel_first(client, app_module):
    make_admin(app_module, "erin", "correct-horse")
    login(client, "erin", "correct-horse")

    resp = client.get("/player-lookup")

    assert resp.status_code == 200
    assert "async function loadRatingImpact(btn)" in resp.text
    assert 'qbDiv.querySelector("button")' in resp.text
    assert "/api/player-quick-blitz?uscf_id=" in resp.text
