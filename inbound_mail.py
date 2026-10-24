"""Receive-only tournament addresses at our own domain, delivered by Resend.

Resend posts an `email.received` webhook (metadata only); we verify its Svix
signature, look up which user owns the recipient address, then fetch the body
and any PGN attachments through the Resend API and hand the result to the same
pipeline that handles IMAP mail.
"""
import base64
import hashlib
import hmac
import re
import time
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import List, Optional

import httpx

import mail_reader as mr

RESEND_API = "https://api.resend.com"
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024
SIGNATURE_TOLERANCE_SECONDS = 300


def verify_signature(secret: str, msg_id: str, timestamp: str, signature_header: str, body: bytes,
                     now: Optional[float] = None) -> bool:
    """Svix scheme used by Resend: HMAC-SHA256 over "<id>.<timestamp>.<body>" with the
    base64 secret (after the whsec_ prefix); the header holds space-separated "v1,<sig>"."""
    if not (secret and msg_id and timestamp and signature_header):
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    raw_secret = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(raw_secret)
    except Exception:
        return False
    expected = base64.b64encode(hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()).decode()
    for part in signature_header.split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return True
    return False


def sign(secret: str, msg_id: str, timestamp: str, body: bytes) -> str:
    """Produce a header value that verify_signature accepts (used by tests)."""
    raw_secret = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    key = base64.b64decode(raw_secret)
    return "v1," + base64.b64encode(hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()).decode()


def local_part_of(addresses: List[str], domain: str) -> Optional[str]:
    """The local part of the first recipient at our inbound domain."""
    domain = (domain or "").lower().strip()
    if not domain:
        return None
    for addr in addresses or []:
        _, email_addr = parseaddr(addr or "")
        email_addr = (email_addr or addr or "").strip().lower()
        if email_addr.endswith("@" + domain):
            return email_addr.rsplit("@", 1)[0]
    return None


def uid_from_email_id(email_id: str) -> int:
    """Stable positive int for the (account, uid) dedupe key."""
    return int(hashlib.sha1((email_id or "").encode()).hexdigest()[:12], 16)


async def fetch_received_email(api_key: str, email_id: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(f"{RESEND_API}/emails/receiving/{email_id}",
                                headers={"Authorization": f"Bearer {api_key}"})
        resp.raise_for_status()
        return resp.json()


async def fetch_attachments(api_key: str, email_id: str) -> list:
    """[{filename, content_type, data}] for text-like / PGN attachments, downloaded now
    (the download URLs expire after an hour)."""
    out = []
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{RESEND_API}/emails/receiving/{email_id}/attachments",
                                headers={"Authorization": f"Bearer {api_key}"}, params={"limit": 50})
        resp.raise_for_status()
        for att in resp.json().get("data", []):
            name = (att.get("filename") or "").lower()
            ctype = (att.get("content_type") or "").lower()
            if not (name.endswith(".pgn") or name.endswith(".txt") or ctype.startswith("text/")
                    or "pgn" in ctype):
                continue
            if (att.get("size") or 0) > MAX_ATTACHMENT_BYTES or not att.get("download_url"):
                continue
            dl = await client.get(att["download_url"])
            if dl.status_code == 200:
                out.append({"filename": att.get("filename") or "attachment", "content_type": ctype, "data": dl.content})
    return out


def to_message(email: dict, attachments: list, uid: int) -> dict:
    """Shape a Resend received email like mail_reader.parse_message output."""
    from_name, from_addr = parseaddr(email.get("from") or "")
    text = (email.get("text") or "").strip() or mr._html_to_text(email.get("html") or "")
    received = ""
    created = email.get("created_at") or ""
    try:
        received = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    pgns = []
    for att in attachments:
        decoded = att["data"].decode("utf-8", errors="replace")
        blocks = mr.extract_pgn_blocks(decoded) or ([decoded.strip()] if '[Event "' in decoded else [])
        pgns.extend(b for b in blocks if b not in pgns)
    for block in mr.extract_pgn_blocks(text):
        if block not in pgns:
            pgns.append(block)
    return {
        "uid": uid,
        "message_id": (email.get("message_id") or "").strip(),
        "from_name": from_name,
        "from_addr": (from_addr or email.get("from") or "").lower(),
        "subject": email.get("subject") or "",
        "received_at": received,
        "text": text[:mr.MAX_TEXT],
        "pgns": pgns,
        "attachments": [a["filename"] for a in attachments],
        "html": email.get("html") or "",
    }


def make_local_part(username: str, suffix: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (username or "player").lower()).strip("-")[:20] or "player"
    return f"{base}-{suffix}"
