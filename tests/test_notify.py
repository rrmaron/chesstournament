"""Tests for notify.py's email/SMS sending (beads mychesspairings-cd3).

notify.py is self-contained (no DB/env state shared with the rest of the
app's module-reload machinery), so these tests import it directly and
monkeypatch its RESEND_API_KEY/TWILIO_* module attributes plus httpx.AsyncClient,
rather than going through the app_module/client fixtures.
"""
import asyncio

import pytest

import notify


class _FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    def __init__(self, response, calls):
        self._response = response
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None, data=None):
        self._calls.append({"url": url, "headers": headers, "json": json, "data": data})
        return self._response


def _patch_client(monkeypatch, status_code, text=""):
    calls = []
    monkeypatch.setattr(
        notify.httpx, "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(_FakeResponse(status_code, text), calls),
    )
    return calls


# ---------------------------------------------------------------------------
# send_verification_email
# ---------------------------------------------------------------------------

def test_verification_email_dev_fallback_without_api_key(monkeypatch, caplog):
    monkeypatch.setattr(notify, "RESEND_API_KEY", "")
    calls = _patch_client(monkeypatch, 200)
    with caplog.at_level("WARNING"):
        asyncio.run(notify.send_verification_email("a@example.com", "123456"))
    assert calls == [], "dev fallback must not make a network call"
    assert any("[DEV]" in r.message and "123456" in r.message for r in caplog.records)


def test_verification_email_succeeds_on_200(monkeypatch):
    monkeypatch.setattr(notify, "RESEND_API_KEY", "fake-key")
    calls = _patch_client(monkeypatch, 200)
    asyncio.run(notify.send_verification_email("a@example.com", "123456"))
    assert len(calls) == 1
    assert calls[0]["json"]["to"] == ["a@example.com"]
    assert "123456" in calls[0]["json"]["html"]


def test_verification_email_raises_on_non_2xx(monkeypatch):
    monkeypatch.setattr(notify, "RESEND_API_KEY", "fake-key")
    calls = _patch_client(monkeypatch, 422, "invalid recipient")
    with pytest.raises(RuntimeError):
        asyncio.run(notify.send_verification_email("a@example.com", "123456"))
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# send_password_reset_email
# ---------------------------------------------------------------------------

def test_password_reset_email_dev_fallback_without_api_key(monkeypatch, caplog):
    monkeypatch.setattr(notify, "RESEND_API_KEY", "")
    calls = _patch_client(monkeypatch, 200)
    with caplog.at_level("WARNING"):
        asyncio.run(notify.send_password_reset_email("a@example.com", "https://example.com/reset?token=xyz"))
    assert calls == []
    assert any("[DEV]" in r.message and "xyz" in r.message for r in caplog.records)


def test_password_reset_email_succeeds_on_201(monkeypatch):
    monkeypatch.setattr(notify, "RESEND_API_KEY", "fake-key")
    calls = _patch_client(monkeypatch, 201)
    asyncio.run(notify.send_password_reset_email("a@example.com", "https://example.com/reset?token=xyz"))
    assert len(calls) == 1
    assert "https://example.com/reset?token=xyz" in calls[0]["json"]["html"]


def test_password_reset_email_raises_on_non_2xx(monkeypatch):
    monkeypatch.setattr(notify, "RESEND_API_KEY", "fake-key")
    _patch_client(monkeypatch, 500, "server error")
    with pytest.raises(RuntimeError):
        asyncio.run(notify.send_password_reset_email("a@example.com", "https://example.com/reset"))


# ---------------------------------------------------------------------------
# send_verification_sms
# ---------------------------------------------------------------------------

def test_verification_sms_dev_fallback_without_twilio_sid(monkeypatch, caplog):
    monkeypatch.setattr(notify, "TWILIO_SID", "")
    calls = _patch_client(monkeypatch, 200)
    with caplog.at_level("WARNING"):
        asyncio.run(notify.send_verification_sms("+15551234567", "654321"))
    assert calls == []
    assert any("[DEV]" in r.message and "654321" in r.message for r in caplog.records)


def test_verification_sms_succeeds_on_200(monkeypatch):
    monkeypatch.setattr(notify, "TWILIO_SID", "ACfake")
    monkeypatch.setattr(notify, "TWILIO_TOKEN", "tokenfake")
    monkeypatch.setattr(notify, "TWILIO_FROM", "+15559999999")
    calls = _patch_client(monkeypatch, 200)
    asyncio.run(notify.send_verification_sms("+15551234567", "654321"))
    assert len(calls) == 1
    assert calls[0]["data"]["To"] == "+15551234567"
    assert "654321" in calls[0]["data"]["Body"]
    assert calls[0]["headers"]["Authorization"].startswith("Basic ")
    assert "ACfake" in calls[0]["url"]


def test_verification_sms_raises_on_non_2xx(monkeypatch):
    monkeypatch.setattr(notify, "TWILIO_SID", "ACfake")
    monkeypatch.setattr(notify, "TWILIO_TOKEN", "tokenfake")
    monkeypatch.setattr(notify, "TWILIO_FROM", "+15559999999")
    _patch_client(monkeypatch, 400, "bad number")
    with pytest.raises(RuntimeError):
        asyncio.run(notify.send_verification_sms("+15551234567", "654321"))
