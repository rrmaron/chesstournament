"""Users can view and change their location (country / federation / city) from
My Profile, not only at signup. The home page scopes featured tournaments by it.
"""
from tests.conftest import login


def _user(app_module, name="loc1"):
    return app_module.create_user(name, "correct-horse", role="viewer", status="active")


def test_profile_shows_location_card_and_saves_changes(client, app_module):
    uid = _user(app_module)
    login(client, "loc1", "correct-horse")

    page = client.get("/profile")
    assert page.status_code == 200
    assert "Location" in page.text and "No location set." in page.text
    assert 'action="/profile/location"' in page.text

    uscf = [f for f in app_module.list_federations() if f["abbreviation"] == "USCF"][0]
    resp = client.post("/profile/location", data={
        "country_code": "us", "federation_id": str(uscf["id"]), "city": "Dallas",
    }, follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/profile?saved=1"

    loc = app_module.get_user_location(uid)
    assert loc == {"country_code": "US", "city": "Dallas", "federation_id": uscf["id"]}
    page = client.get("/profile")
    assert "Dallas, " in page.text and uscf["name"] in page.text
    assert 'value="US" selected' in page.text


def test_profile_location_infers_single_federation_and_rejects_mismatch(client, app_module):
    uid = _user(app_module, "loc2")
    login(client, "loc2", "correct-horse")
    uscf = [f for f in app_module.list_federations() if f["abbreviation"] == "USCF"][0]
    ca_feds = app_module.get_federations_for_country("CA")
    assert len(ca_feds) == 1

    # No federation given for a country with exactly one -> inferred.
    client.post("/profile/location", data={"country_code": "CA", "city": ""})
    assert app_module.get_user_location(uid)["federation_id"] == ca_feds[0]["id"]

    # A federation from another country is dropped, and the country's own one is used.
    client.post("/profile/location", data={"country_code": "CA", "federation_id": str(uscf["id"])})
    assert app_module.get_user_location(uid)["federation_id"] == ca_feds[0]["id"]

    # Clearing works.
    client.post("/profile/location", data={"country_code": "", "federation_id": "", "city": ""})
    assert app_module.get_user_location(uid) == {"country_code": "", "city": "", "federation_id": None}


def test_profile_location_requires_login(client):
    resp = client.post("/profile/location", data={"country_code": "US"}, follow_redirects=False)
    assert resp.status_code in (303, 401)
