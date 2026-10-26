"""Country pickers list the most common chess countries first (USA, India, UK,
Russia, Ukraine, Canada), then every other country alphabetically, once each."""
import re

from tests.conftest import login

POPULAR = ["US", "IN", "GB", "RU", "UA", "CA"]


def _option_codes(html):
    select = html.split('id="countrySelect"', 1)[1].split("</select>", 1)[0]
    return re.findall(r'<option value="([A-Z]+)"', select)


def test_list_countries_puts_popular_first(app_module):
    rows = app_module.list_countries()
    codes = [r["country_code"] for r in rows]
    assert codes[:6] == POPULAR and all(r["popular"] for r in rows[:6])
    rest = [r["country_name"] for r in rows[6:]]
    assert rest == sorted(rest) and not any(r["popular"] for r in rows[6:])
    assert len(codes) == len(set(codes))


def test_register_page_groups_popular_countries(client):
    html = client.get("/register").text
    assert '<optgroup label="Most common">' in html and '<optgroup label="All countries">' in html
    codes = _option_codes(html)
    assert codes[:6] == POPULAR and len(codes) == len(set(codes))


def test_profile_country_picker_groups_and_keeps_selection(client, app_module):
    uid = app_module.create_user("nak", "correct-horse", role="viewer", status="active")
    login(client, "nak", "correct-horse")
    html = client.get("/profile").text
    codes = _option_codes(html)
    assert codes[:6] == POPULAR and len(codes) == len(set(codes))
    assert '<optgroup label="Most common">' in html
