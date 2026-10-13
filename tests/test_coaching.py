"""Coaching feature: coach role, students, sessions with meeting links,
homework with board demonstrations and server-checked puzzles, grading,
and a public course catalog paid through Stripe Connect.
"""
from types import SimpleNamespace

from tests.conftest import make_admin, login


def make_user(app_module, username, password="correct-horse", role="viewer"):
    return app_module.create_user(username, password, role=role, status="active")


def _create_homework(client, **fields):
    data = {"title": "Rook endings", "kind": "study", "instructions": "Study this", "fen": "", "pgn": "",
            "solution": "", "due_date": ""}
    data.update(fields)
    resp = client.post("/coaching/homework", data=data, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return int(resp.headers["location"].rsplit("/", 1)[1])


def test_viewer_cannot_use_coach_tools(client, app_module):
    make_user(app_module, "vic")
    login(client, "vic", "correct-horse")

    resp = client.post("/coaching/students", data={"username": "someone"})

    assert resp.status_code == 403


def test_coach_adds_student_and_both_dashboards_reflect_it(client, app_module):
    make_user(app_module, "coachk", role="coach")
    make_user(app_module, "stu")
    login(client, "coachk", "correct-horse")

    resp = client.post("/coaching/students", data={"username": "stu"}, follow_redirects=False)
    assert resp.status_code == 303
    coach_page = client.get("/coaching")
    assert "stu" in coach_page.text
    assert "Assign homework" in coach_page.text

    client.post("/logout")
    login(client, "stu", "correct-horse")
    student_page = client.get("/coaching")
    assert "My coaches" in student_page.text
    assert "coachk" in student_page.text


def test_session_link_shows_join_button_only_to_its_students(client, app_module):
    make_user(app_module, "coachk", role="coach")
    make_user(app_module, "stu")
    make_user(app_module, "outsider")
    login(client, "coachk", "correct-horse")
    client.post("/coaching/students", data={"username": "stu"})
    client.post("/coaching/profile", data={"default_link": "https://zoom.us/j/standing", "bio": "IM, 20 years coaching"})

    resp = client.post("/coaching/sessions", data={
        "title": "Endgame class", "starts_at": "2026-11-01T18:00", "link": "https://meet.google.com/abc-defg-hij",
    }, follow_redirects=False)
    assert resp.status_code == 303

    client.post("/logout")
    login(client, "stu", "correct-horse")
    page = client.get("/coaching")
    assert "https://meet.google.com/abc-defg-hij" in page.text
    assert "https://zoom.us/j/standing" in page.text
    assert "Endgame class" in page.text

    client.post("/logout")
    login(client, "outsider", "correct-horse")
    page = client.get("/coaching")
    assert "meet.google.com" not in page.text
    assert "Endgame class" not in page.text


def test_homework_visibility_submission_and_grading(client, app_module):
    make_user(app_module, "coachk", role="coach")
    make_user(app_module, "stu")
    make_user(app_module, "outsider")
    login(client, "coachk", "correct-horse")
    client.post("/coaching/students", data={"username": "stu"})
    hid = _create_homework(client, title="Lucena position",
                           fen="1K1k4/1P6/8/8/8/8/r7/2R5 w - - 0 1",
                           pgn="1. Rc4 Ra1 2. Kc7 Rc1 3. Kb6 Rb1+ 4. Kc6 Rc1+ 5. Kb5 Rb1+ 6. Rb4")

    client.post("/logout")
    login(client, "outsider", "correct-horse")
    assert client.get(f"/coaching/homework/{hid}").status_code == 403

    client.post("/logout")
    login(client, "stu", "correct-horse")
    page = client.get(f"/coaching/homework/{hid}")
    assert page.status_code == 200
    assert "Lucena position" in page.text
    assert "CoachBoard.viewer" in page.text
    assert "1K1k4/1P6/8/8/8/8/r7/2R5 w - - 0 1" in page.text

    resp = client.post(f"/coaching/homework/{hid}/submit", data={
        "note": "I think the bridge works", "pgn": "1. Rc4 Ra1 2. Kc7",
    }, follow_redirects=False)
    assert resp.status_code == 303

    client.post("/logout")
    login(client, "coachk", "correct-horse")
    page = client.get(f"/coaching/homework/{hid}")
    assert "I think the bridge works" in page.text
    assert "Submitted" in page.text
    assignment = app_module.get_assignment(hid, app_module.get_user_by_username("stu")["id"])
    resp = client.post(f"/coaching/homework/{hid}/grade", data={
        "assignment_id": assignment["id"], "grade": "A", "feedback": "Perfect technique",
    }, follow_redirects=False)
    assert resp.status_code == 303

    client.post("/logout")
    login(client, "stu", "correct-horse")
    page = client.get(f"/coaching/homework/{hid}")
    assert "Graded: A" in page.text
    assert "Perfect technique" in page.text
    assert "Graded: A" in client.get("/coaching").text


def test_puzzle_moves_are_checked_server_side_and_solution_stays_hidden(client, app_module):
    make_user(app_module, "coachk", role="coach")
    make_user(app_module, "stu")
    login(client, "coachk", "correct-horse")
    client.post("/coaching/students", data={"username": "stu"})
    hid = _create_homework(client, title="Mate in 2", kind="puzzle",
                           fen="r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 0 1",
                           solution="1. Qxf7+ Ke7 2. Qxe5#")
    assert app_module.get_homework(hid)["solution"] == "Qxf7+ Ke7 Qxe5#"

    client.post("/logout")
    login(client, "stu", "correct-horse")
    page = client.get(f"/coaching/homework/{hid}")
    assert "CoachBoard.puzzle" in page.text
    assert "Qxf7" not in page.text

    wrong = client.post("/api/coaching/puzzle-move", json={"hid": hid, "ply": 0, "san": "Bxf7+"}).json()
    assert wrong == {"correct": False, "reply": None, "solved": False}

    first = client.post("/api/coaching/puzzle-move", json={"hid": hid, "ply": 0, "san": "Qxf7+"}).json()
    assert first == {"correct": True, "reply": "Ke7", "solved": False}

    last = client.post("/api/coaching/puzzle-move", json={"hid": hid, "ply": 2, "san": "Qxe5"}).json()
    assert last == {"correct": True, "reply": None, "solved": True}
    assert "Solved" in client.get("/coaching").text


def test_puzzle_check_refuses_unassigned_users(client, app_module):
    make_user(app_module, "coachk", role="coach")
    make_user(app_module, "outsider")
    login(client, "coachk", "correct-horse")
    hid = _create_homework(client, title="Mate in 1", kind="puzzle",
                           fen="6k1/5ppp/8/8/8/8/5PPP/4R1K1 w - - 0 1", solution="Re8#")

    client.post("/logout")
    login(client, "outsider", "correct-horse")
    resp = client.post("/api/coaching/puzzle-move", json={"hid": hid, "ply": 0, "san": "Re8#"})

    assert resp.status_code == 403


def test_free_course_is_public_and_enrolling_makes_you_a_student(client, app_module):
    make_user(app_module, "coachk", role="coach")
    make_user(app_module, "stu")
    login(client, "coachk", "correct-horse")
    client.post("/coaching/courses", data={"title": "Openings for club players", "description": "Six sessions", "price": "0"})
    client.post("/logout")

    catalog = client.get("/courses")
    assert catalog.status_code == 200
    assert "Openings for club players" in catalog.text
    assert "Free" in catalog.text

    login(client, "stu", "correct-horse")
    course = app_module.list_courses()[0]
    resp = client.post(f"/courses/{course['id']}/enroll", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/enroll/success")
    assert "enrolled" in client.get(f"/courses/{course['id']}/enroll/success").text.lower()

    coach_id = app_module.get_user_by_username("coachk")["id"]
    stu_id = app_module.get_user_by_username("stu")["id"]
    assert app_module.is_coach_student(coach_id, stu_id)


def test_paid_course_blocked_until_coach_connects_stripe(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "STRIPE_SECRET_KEY", "sk_test_x")
    make_user(app_module, "coachk", role="coach")
    make_user(app_module, "stu")
    login(client, "coachk", "correct-horse")
    client.post("/coaching/courses", data={"title": "Tactics bootcamp", "price": "25"})
    client.post("/logout")

    login(client, "stu", "correct-horse")
    cid = app_module.list_courses()[0]["id"]
    assert "Payments not set up yet" in client.get(f"/courses/{cid}").text
    resp = client.post(f"/courses/{cid}/enroll", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"].endswith("?error=payments")
    coach_id = app_module.get_user_by_username("coachk")["id"]
    stu_id = app_module.get_user_by_username("stu")["id"]
    assert not app_module.is_coach_student(coach_id, stu_id)


def test_paid_course_checkout_routes_money_to_coach_with_platform_fee(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(app_module, "STRIPE_WEBHOOK_SECRET", "whsec_x")
    app_module.set_setting("platform_fee_percent", "15")
    make_user(app_module, "coachk", role="coach")
    make_user(app_module, "stu")
    coach_id = app_module.get_user_by_username("coachk")["id"]
    stu_id = app_module.get_user_by_username("stu")["id"]
    app_module.set_coach_stripe_account(coach_id, "acct_coach123", True)
    login(client, "coachk", "correct-horse")
    client.post("/coaching/courses", data={"title": "Tactics bootcamp", "price": "20.00"})
    client.post("/logout")

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(url="https://checkout.stripe.test/session_abc")

    monkeypatch.setattr(app_module.stripe.checkout.Session, "create", fake_create)

    login(client, "stu", "correct-horse")
    cid = app_module.list_courses()[0]["id"]
    resp = client.post(f"/courses/{cid}/enroll", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "https://checkout.stripe.test/session_abc"
    assert captured["line_items"][0]["price_data"]["unit_amount"] == 2000
    assert captured["payment_intent_data"]["application_fee_amount"] == 300
    assert captured["payment_intent_data"]["transfer_data"]["destination"] == "acct_coach123"
    eid = int(captured["metadata"]["enrollment_id"])
    assert app_module.get_enrollment(eid)["status"] == "pending"
    assert not app_module.is_coach_student(coach_id, stu_id)

    class _Obj:
        def to_dict(self):
            return {"payment_status": "paid", "id": "cs_test_1", "metadata": {"enrollment_id": str(eid)}}

    monkeypatch.setattr(app_module.stripe.Webhook, "construct_event",
                        lambda payload, sig, secret: {"type": "checkout.session.completed", "data": {"object": _Obj()}})
    resp = client.post("/stripe/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=x"})

    assert resp.status_code == 200
    enrollment = app_module.get_enrollment(eid)
    assert enrollment["status"] == "paid"
    assert enrollment["stripe_session_id"] == "cs_test_1"
    assert app_module.is_coach_student(coach_id, stu_id)
    assert app_module.list_coach_courses(coach_id)[0]["revenue_cents"] == 2000


def test_stripe_connect_onboarding_and_return(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "STRIPE_SECRET_KEY", "sk_test_x")
    make_user(app_module, "coachk", role="coach")
    coach_id = app_module.get_user_by_username("coachk")["id"]
    monkeypatch.setattr(app_module.stripe.Account, "create", lambda **kw: SimpleNamespace(id="acct_new1"))
    monkeypatch.setattr(app_module.stripe.AccountLink, "create",
                        lambda **kw: SimpleNamespace(url="https://connect.stripe.test/onboard"))
    login(client, "coachk", "correct-horse")

    resp = client.post("/coaching/stripe/connect", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "https://connect.stripe.test/onboard"
    profile = app_module.get_coach_profile(coach_id)
    assert profile["stripe_account_id"] == "acct_new1"
    assert not profile["stripe_charges_enabled"]

    monkeypatch.setattr(app_module.stripe.Account, "retrieve", lambda acct: SimpleNamespace(charges_enabled=True))
    resp = client.get("/coaching/stripe/return", follow_redirects=False)

    assert resp.status_code == 303
    assert app_module.get_coach_profile(coach_id)["stripe_charges_enabled"] == 1
    assert "payouts enabled" in client.get("/coaching").text


def test_admin_can_assign_coach_role_and_set_platform_fee(client, app_module):
    make_admin(app_module, "root", "correct-horse")
    login(client, "root", "correct-horse")

    page = client.get("/users")
    assert 'value="coach"' in page.text
    assert 'name="platform_fee_percent"' in page.text

    resp = client.post("/admin/settings/platform-fee", data={"platform_fee_percent": "20"}, follow_redirects=False)
    assert resp.status_code == 303
    assert app_module.get_setting("platform_fee_percent") == "20"

    uid = make_user(app_module, "newcoach")
    client.post(f"/users/{uid}/edit", data={"username": "newcoach", "role": "coach", "status": "active"})
    assert app_module.get_user_by_username("newcoach")["role"] == "coach"
