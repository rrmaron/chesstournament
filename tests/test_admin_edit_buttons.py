"""Regression test: clicking "Edit" on a featured tournament or federation
did nothing in the browser.

Root cause: both admin_tournaments.html and admin_federations.html had
onclick="openEdit({{ t | tojson }})" -- a double-quoted HTML attribute
containing raw JSON from Jinja's tojson filter. tojson's htmlsafe_json_dumps
escapes '<', '>', '&', and "'" (so the output is safe inside a *single*-
quoted attribute) but never escapes '"', since JSON object/string syntax is
full of literal double quotes by necessity. Embedded in a double-quoted
attribute, the browser's HTML parser terminated the attribute at the very
first '"' inside the JSON (right after "openEdit("), truncating the handler
into garbage -- so the click silently did nothing.

Fix: use single-quoted attributes (onclick='openEdit({{ t | tojson }})'),
which is exactly what tojson's escaping is designed for.
"""
import json
import re

from bs4 import BeautifulSoup

from tests.conftest import make_admin, login


def _onclick_openEdit_calls_correctly(html: str, button_text: str, scope=None) -> dict:
    """Parse real HTML (not the template source) and return the dict that
    openEdit() would actually receive, failing loudly if the attribute is
    malformed the way the original bug made it. `scope`, if given, is a
    BeautifulSoup tag to search within (e.g. one federation's card)."""
    soup = scope if scope is not None else BeautifulSoup(html, "html.parser")
    btn = next(b for b in soup.find_all("button") if b.get_text(strip=True) == button_text)
    onclick = btn.get("onclick")
    assert onclick is not None, f"no onclick attribute on the {button_text!r} button"
    m = re.match(r"^openEdit\((.*)\)$", onclick)
    assert m, f"onclick attribute was truncated/malformed: {onclick!r}"
    return json.loads(m.group(1))


def test_tournament_edit_button_onclick_survives_quotes_and_special_chars(client, app_module):
    make_admin(app_module, "admin1", "adminpass123")
    login(client, "admin1", "adminpass123")
    app_module.add_featured_tournament(
        name='Nationals "2026" & Beyond',
        subtitle="Kids' Edition",
        description=None, info_url="https://example.com/?a=1&b=2",
        pairings_url=None, source="manual", source_url=None,
        display_order=0, country_code="",
    )

    resp = client.get("/admin/tournaments")
    assert resp.status_code == 200
    data = _onclick_openEdit_calls_correctly(resp.text, "Edit")
    assert data["name"] == 'Nationals "2026" & Beyond'
    assert data["subtitle"] == "Kids' Edition"
    assert data["info_url"] == "https://example.com/?a=1&b=2"


def test_federation_edit_button_onclick_survives_quotes_and_special_chars(client, app_module):
    make_admin(app_module, "admin2", "adminpass123")
    login(client, "admin2", "adminpass123")
    app_module.create_federation(
        name="O'Brien's Federation", abbreviation=None,
        country_code="US", country_name="USA",
        rating_system=None, website_url=None, tournaments_url=None,
    )

    resp = client.get("/admin/federations")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.text, "html.parser")
    name_span = soup.find("span", class_="fw-semibold", string="O'Brien's Federation")
    assert name_span is not None, "newly created federation not found on the page"
    card = name_span.find_parent("div", class_="card")
    data = _onclick_openEdit_calls_correctly(resp.text, "Edit", scope=card)
    assert data["name"] == "O'Brien's Federation"
