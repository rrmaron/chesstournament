"""Coaches can search for already-registered players and, when a student has
no account yet, create one and send a sign-in link (a 72-hour set-password
link on the existing reset-password page).
"""
from tests.conftest import login


def make_user(app_module, username, role="viewer", email=None):
    uid = app_module.create_user(username, "correct-horse", role=role, status="active")
    if email:
        app_module.update_user_contact(uid, email, None)
    return uid


def test_student_search_finds_registered_players_and_marks_existing(client, app_module):
    make_user(app_module, "coachk", role="coach")
    make_user(app_module, "alice", email="alice@example.com")
    make_user(app_module, "bob")
    login(client, "coachk", "correct-horse")
    client.post("/coaching/students", data={"username": "bob"})

    by_name = client.get("/api/coaching/student-search", params={"q": "ali"})
    assert by_name.status_code == 200
    assert "alice" in by_name.text
    assert 'name="username" value="alice"' in by_name.text

    by_email = client.get("/api/coaching/student-search", params={"q": "alice@ex"})
    assert "alice" in by_email.text

    existing = client.get("/api/coaching/student-search", params={"q": "bob"})
    assert "Already your student" in existing.text

    nothing = client.get("/api/coaching/student-search", params={"q": "zzz@nowhere.com"})
    assert "No registered player matches" in nothing.text
    assert 'value="zzz@nowhere.com"' in nothing.text
    assert "<details class=\"small\" open>" in nothing.text


def test_invite_creates_account_adds_student_and_link_lets_them_sign_in(client, app_module, monkeypatch):
    make_user(app_module, "coachk", role="coach")
    sent = {}

    async def fake_send(to, coach_name, username, link):
        sent.update(to=to, coach_name=coach_name, username=username, link=link)

    monkeypatch.setattr(app_module, "send_student_invite_email", fake_send)
    login(client, "coachk", "correct-horse")

    resp = client.post("/coaching/students/invite",
                       data={"username": "newkid", "email": "Kid@Example.com"}, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/coaching?msg=invited"
    new_user = app_module.get_user_by_username("newkid")
    assert new_user["role"] == "viewer" and new_user["email"] == "kid@example.com"
    coach_id = app_module.get_user_by_username("coachk")["id"]
    assert app_module.is_coach_student(coach_id, new_user["id"])
    assert sent["to"] == "kid@example.com" and sent["coach_name"] == "coachk" and sent["username"] == "newkid"
    assert "/reset-password?token=" in sent["link"]

    dashboard = client.get("/coaching")
    assert "Account <strong>newkid</strong> created" in dashboard.text
    assert sent["link"] in dashboard.text

    token = sent["link"].split("token=")[1]
    client.post("/logout")
    resp = client.post("/reset-password", data={"token": token, "password": "my-new-pass", "confirm": "my-new-pass"})
    assert resp.status_code == 200
    login(client, "newkid", "my-new-pass")
    assert "My Coaching" in client.get("/coaching").text
    assert "coachk" in client.get("/coaching").text


def test_invite_rejects_duplicates_and_non_coaches(client, app_module, monkeypatch):
    make_user(app_module, "coachk", role="coach")
    make_user(app_module, "alice", email="alice@example.com")
    monkeypatch.setattr(app_module, "send_student_invite_email", lambda *a: None)
    login(client, "coachk", "correct-horse")

    dup_name = client.post("/coaching/students/invite", data={"username": "alice", "email": "x@example.com"}, follow_redirects=False)
    assert dup_name.headers["location"] == "/coaching?msg=invite_exists"
    dup_email = client.post("/coaching/students/invite", data={"username": "other", "email": "alice@example.com"}, follow_redirects=False)
    assert dup_email.headers["location"] == "/coaching?msg=invite_exists"
    bad = client.post("/coaching/students/invite", data={"username": "x", "email": "not-an-email"}, follow_redirects=False)
    assert bad.headers["location"] == "/coaching?msg=invite_invalid"

    client.post("/logout")
    make_user(app_module, "viewer1")
    login(client, "viewer1", "correct-horse")
    assert client.get("/api/coaching/student-search", params={"q": "ali"}).status_code == 403
    assert client.post("/coaching/students/invite", data={"username": "a", "email": "a@b.c"}).status_code == 403
