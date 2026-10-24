"""Featured tournaments can carry a live broadcast link, shown as a button on
the featured card and editable from the admin page."""
from tests.conftest import make_admin, login


def test_admin_sets_broadcast_link_and_card_shows_it(client, app_module):
    make_admin(app_module, "root", "correct-horse")
    login(client, "root", "correct-horse")

    resp = client.post("/admin/tournaments", data={
        "name": "Dallas Fall Open", "info_url": "https://dallaschess.example/fall-open",
        "pairings_url": "https://chess-results.com/tnr1.aspx", "broadcast_url": "https://lichess.org/broadcast/dallas/abc",
        "source": "manual", "display_order": "0",
    }, follow_redirects=False)
    assert resp.status_code == 303

    ft = app_module.list_featured_tournaments()[0]
    assert ft["broadcast_url"] == "https://lichess.org/broadcast/dallas/abc"

    admin_page = client.get("/admin/tournaments").text
    assert 'name="broadcast_url"' in admin_page and "https://lichess.org/broadcast/dallas/abc" in admin_page

    card_page = client.get("/uscf-calculator").text
    assert "Live broadcast" in card_page and 'href="https://lichess.org/broadcast/dallas/abc"' in card_page

    client.post(f"/admin/tournaments/{ft['id']}/edit", data={
        "name": "Dallas Fall Open", "broadcast_url": "https://lichess.org/broadcast/dallas/xyz", "display_order": "0",
    })
    assert app_module.list_featured_tournaments()[0]["broadcast_url"] == "https://lichess.org/broadcast/dallas/xyz"

    client.post(f"/admin/tournaments/{ft['id']}/edit", data={"name": "Dallas Fall Open", "display_order": "0"})
    assert app_module.list_featured_tournaments()[0]["broadcast_url"] in (None, "")
    assert "Live broadcast" not in client.get("/uscf-calculator").text
