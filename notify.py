import os
import base64
import logging
import httpx

RESEND_API_KEY   = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM      = os.environ.get("RESEND_FROM", "noreply@mychessrating.com")
TWILIO_SID       = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN     = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM      = os.environ.get("TWILIO_FROM_NUMBER", "")


async def send_verification_email(to: str, code: str):
    if not RESEND_API_KEY:
        logging.warning(f"[DEV] Email OTP for {to}: {code}  (set RESEND_API_KEY to send real emails)")
        return
    html = f"""
    <div style="font-family:sans-serif;max-width:400px">
      <h2>♟️ MyChessRating</h2>
      <p>Your verification code is:</p>
      <p style="font-size:2rem;letter-spacing:0.4em;font-weight:bold">{code}</p>
      <p style="color:#888">Expires in 10 minutes. If you didn't request this, ignore it.</p>
    </div>
    """
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": RESEND_FROM, "to": [to],
                  "subject": "Your MyChessRating verification code", "html": html},
        )
    if r.status_code not in (200, 201):
        logging.error(f"Resend error {r.status_code}: {r.text}")
        raise RuntimeError("Email could not be sent. Please try again.")


async def send_student_invite_email(to: str, coach_name: str, username: str, link: str):
    import html as _html
    if not RESEND_API_KEY:
        logging.warning(f"[DEV] Student invite for {to}: {link}  (set RESEND_API_KEY to send real emails)")
        return
    html = f"""
    <div style="font-family:sans-serif;max-width:440px">
      <h2>♟️ MyChessRating</h2>
      <p>Your coach <strong>{_html.escape(coach_name)}</strong> created a MyChessRating account for you.</p>
      <p>Your username is <strong>{_html.escape(username)}</strong>. Click below to choose a password and sign in —
         you'll find your lessons, homework and session links on the Coaching page.</p>
      <p style="margin:24px 0">
        <a href="{link}"
           style="background:#003087;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:bold">
          Set my password &amp; sign in
        </a>
      </p>
      <p style="color:#888;font-size:0.9em">This link expires in 72 hours.</p>
      <p style="color:#aaa;font-size:0.85em;word-break:break-all">{link}</p>
    </div>
    """
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": RESEND_FROM, "to": [to],
                  "subject": f"{coach_name} invited you to MyChessRating", "html": html},
        )
    if r.status_code not in (200, 201):
        logging.error(f"Resend error {r.status_code}: {r.text}")
        raise RuntimeError("Email could not be sent.")


async def send_password_reset_email(to: str, reset_url: str):
    if not RESEND_API_KEY:
        logging.warning(f"[DEV] Password reset link for {to}: {reset_url}  (set RESEND_API_KEY to send real emails)")
        return
    html = f"""
    <div style="font-family:sans-serif;max-width:440px">
      <h2>♟️ MyChessRating</h2>
      <p>We received a request to reset your password.</p>
      <p style="margin:24px 0">
        <a href="{reset_url}"
           style="background:#003087;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:bold">
          Reset my password
        </a>
      </p>
      <p style="color:#888;font-size:0.9em">This link expires in 1 hour. If you didn't request this, ignore it.</p>
      <p style="color:#aaa;font-size:0.85em;word-break:break-all">{reset_url}</p>
    </div>
    """
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": RESEND_FROM, "to": [to],
                  "subject": "Reset your MyChessRating password", "html": html},
        )
    if r.status_code not in (200, 201):
        logging.error(f"Resend error {r.status_code}: {r.text}")
        raise RuntimeError("Email could not be sent. Please try again.")


async def send_verification_sms(to: str, code: str):
    if not TWILIO_SID:
        logging.warning(f"[DEV] SMS OTP for {to}: {code}  (set TWILIO_* vars to send real SMS)")
        return
    auth = base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            headers={"Authorization": f"Basic {auth}"},
            data={"From": TWILIO_FROM, "To": to,
                  "Body": f"MyChessRating code: {code}  (valid 10 min)"},
        )
    if r.status_code not in (200, 201):
        logging.error(f"Twilio error {r.status_code}: {r.text}")
        raise RuntimeError("SMS could not be sent. Please try again.")


async def send_forward_email(to: str, original_from: str, subject: str, text: str, html: str, attachments: list):
    """Forward a message received at a user's tournament address to their real inbox.
    attachments: [{filename, content_type, data(bytes)}]."""
    import html as _html
    if not RESEND_API_KEY:
        logging.warning(f"[DEV] Would forward '{subject}' from {original_from} to {to}")
        return
    note = f"Forwarded from {original_from} by MyChessRating"
    payload = {
        "from": RESEND_FROM, "to": [to], "subject": f"Fwd: {subject or '(no subject)'}",
        "text": f"{note}\n\n{text or ''}",
    }
    if original_from:
        payload["reply_to"] = original_from
    if html:
        payload["html"] = f'<p style="color:#888;font:12px sans-serif">{_html.escape(note)}</p>' + html
    atts = [{"filename": a["filename"], "content": base64.b64encode(a["data"]).decode("ascii")}
            for a in (attachments or []) if len(a.get("data") or b"") <= 2 * 1024 * 1024][:10]
    if atts:
        payload["attachments"] = atts
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post("https://api.resend.com/emails",
                              headers={"Authorization": f"Bearer {RESEND_API_KEY}"}, json=payload)
    if r.status_code not in (200, 201):
        logging.error(f"Resend forward error {r.status_code}: {r.text}")
        raise RuntimeError("Forward could not be sent")
