"""The admin top menu has many links; on desktop widths it must wrap onto
extra lines instead of overflowing the right edge (hiding Logout etc.)."""
from tests.conftest import login


def test_admin_nav_wraps_instead_of_overflowing(client, app_module):
    app_module.create_user("boss", "correct-horse", role="admin", status="active")
    login(client, "boss", "correct-horse")
    html = client.get("/profile").text
    assert 'class="collapse navbar-collapse main-nav"' in html
    assert "flex-lg-wrap" in html and "justify-content-lg-end" in html
    assert ".main-nav { min-width: 0; }" in html
    for label in ("PGN DB", "Federations", "Logout"):
        assert label in html
