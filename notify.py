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
