"""Read a user's mailbox over IMAP for the My Tournaments feature.

Design constraints:
- Only messages that look chess-related are ever sent to Claude; everything
  else is recorded as metadata only (uid, sender, subject, date).
- Bodies are truncated and never stored.
- Passwords (app passwords) are encrypted at rest with a key derived from the
  app's SECRET_KEY.
"""
import base64
import email
import hashlib
import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

# Well-known IMAP hosts, keyed by the address's domain.
PROVIDERS = {
    "gmail.com": ("imap.gmail.com", 993),
    "googlemail.com": ("imap.gmail.com", 993),
    "yahoo.com": ("imap.mail.yahoo.com", 993),
    "ymail.com": ("imap.mail.yahoo.com", 993),
    "rocketmail.com": ("imap.mail.yahoo.com", 993),
    "icloud.com": ("imap.mail.me.com", 993),
    "me.com": ("imap.mail.me.com", 993),
    "mac.com": ("imap.mail.me.com", 993),
    "aol.com": ("imap.aol.com", 993),
    "att.net": ("imap.mail.att.net", 993),
    "comcast.net": ("imap.comcast.net", 993),
    "verizon.net": ("imap.aol.com", 993),
}
# Microsoft turned off password sign-in for IMAP; these need OAuth, which we don't do yet.
NO_PASSWORD_IMAP = {"outlook.com", "hotmail.com", "live.com", "msn.com"}

CHESS_HINTS = re.compile(
    r"\b(chess|tournament|uscf|fide|pgn|chessnoter|chess\s*noter|round\s*\d|pairings?|"
    r"entry\s*(list|fee)|registration|registered|section|swiss|open\s+section|scholastic|"
    r"grand\s*prix|classic|championship|blitz|rapid)\b",
    re.I,
)
PGN_BLOCK = re.compile(r'(\[Event\s+"[^\n]*\n(?:\[[^\n]*\n)*\s*\n?(?:[^\[]|\[%)+?)(?=\n\s*\n\[Event|\Z)', re.S)
MAX_TEXT = 12_000


def guess_imap(address: str) -> tuple:
    """(host, port) for a known provider, else (None, None)."""
    domain = (address or "").rsplit("@", 1)[-1].strip().lower()
    return PROVIDERS.get(domain, (None, None))


def password_imap_supported(address: str) -> bool:
    domain = (address or "").rsplit("@", 1)[-1].strip().lower()
    return domain not in NO_PASSWORD_IMAP


# ---------------------------------------------------------------------------
# Encryption at rest
# ---------------------------------------------------------------------------

def _fernet(secret_key: str) -> Fernet:
    digest = hashlib.sha256(("mail-reader:" + (secret_key or "")).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(secret_key: str, plain: str) -> str:
    return _fernet(secret_key).encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(secret_key: str, token: str) -> str:
    try:
        return _fernet(secret_key).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as e:
        raise ValueError("stored mail password cannot be decrypted (SECRET_KEY changed?)") from e


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------

def _decode(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def extract_pgn_blocks(text: str) -> list:
    """Every PGN game found in free text (a ChessNoteR email pastes the game inline)."""
    if not text or '[Event "' not in text:
        return []
    blocks = []
    for m in PGN_BLOCK.finditer(text):
        block = m.group(1).strip()
        if '[White "' in block or '[Black "' in block:
            blocks.append(block)
    return blocks


def parse_message(raw: bytes, uid: int = 0) -> dict:
    """Decode an RFC 822 message into what the app needs: headers, a truncated
    plain-text body, and any PGN games (attachments or inline)."""
    msg = email.message_from_bytes(raw)
    from_name, from_addr = parseaddr(_decode(msg.get("From")))
    try:
        received = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
    except Exception:
        received = None
    if received is not None and received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)

    texts, htmls, pgns, attachments = [], [], [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = (part.get_content_type() or "").lower()
        filename = _decode(part.get_filename() or "")
        disposition = (part.get("Content-Disposition") or "").lower()
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, errors="replace")
        except LookupError:
            decoded = payload.decode("utf-8", errors="replace")

        if filename.lower().endswith(".pgn") or ctype in ("application/x-chess-pgn", "application/vnd.chess-pgn"):
            attachments.append(filename or "game.pgn")
            pgns.extend(extract_pgn_blocks(decoded) or ([decoded.strip()] if '[Event "' in decoded else []))
        elif ctype == "text/plain" and "attachment" not in disposition:
            texts.append(decoded)
        elif ctype == "text/html" and "attachment" not in disposition:
            htmls.append(decoded)
        elif filename:
            attachments.append(filename)
            if '[Event "' in decoded[:4000]:
                pgns.extend(extract_pgn_blocks(decoded))

    text = "\n".join(texts).strip() or _html_to_text("\n".join(htmls))
    for block in extract_pgn_blocks(text):
        if block not in pgns:
            pgns.append(block)

    return {
        "uid": uid,
        "message_id": (msg.get("Message-ID") or "").strip(),
        "from_name": from_name,
        "from_addr": (from_addr or "").lower(),
        "subject": _decode(msg.get("Subject")),
        "received_at": received.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if received else "",
        "text": text[:MAX_TEXT],
        "pgns": pgns,
        "attachments": attachments,
    }


def looks_chess_related(msg: dict) -> bool:
    if msg.get("pgns"):
        return True
    hay = " ".join([msg.get("subject", ""), msg.get("from_addr", ""), msg.get("from_name", ""),
                    (msg.get("text") or "")[:3000]])
    return bool(CHESS_HINTS.search(hay))


# ---------------------------------------------------------------------------
# IMAP
# ---------------------------------------------------------------------------

class Mailbox:
    def __init__(self, host: str, port: int, username: str, password: str, folder: str = "INBOX"):
        self.host, self.port, self.username, self.password, self.folder = host, int(port or 993), username, password, folder
        self.conn: Optional[imaplib.IMAP4_SSL] = None

    def __enter__(self):
        self.conn = imaplib.IMAP4_SSL(self.host, self.port, timeout=30)
        self.conn.login(self.username, self.password)
        self.conn.select(self.folder, readonly=True)
        return self

    def __exit__(self, *exc):
        try:
            if self.conn is not None:
                self.conn.logout()
        except Exception:
            pass

    def fetch_new(self, last_uid: int = 0, since_days: int = 90, limit: int = 40) -> list:
        """Messages with UID > last_uid received in the last `since_days`, oldest
        first, at most `limit` (the newest ones when there are more)."""
        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
        criteria = ["SINCE", since]
        if last_uid:
            criteria += ["UID", f"{last_uid + 1}:*"]
        status, data = self.conn.uid("search", None, *criteria)
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")
        uids = [int(u) for u in (data[0] or b"").split() if int(u) > last_uid]
        uids = sorted(uids)[-limit:]
        messages = []
        for uid in uids:
            status, parts = self.conn.uid("fetch", str(uid), "(BODY.PEEK[])")
            if status != "OK" or not parts:
                continue
            raw = next((p[1] for p in parts if isinstance(p, tuple) and len(p) > 1), None)
            if not raw:
                continue
            try:
                messages.append(parse_message(raw, uid))
            except Exception as e:
                logging.warning("mail_reader: could not parse uid %s: %s", uid, e)
        return messages


def test_connection(host: str, port: int, username: str, password: str) -> str:
    """'' when login works, else a short human-readable reason."""
    try:
        with Mailbox(host, port, username, password):
            return ""
    except imaplib.IMAP4.error as e:
        msg = str(e)
        if "AUTHENTICATIONFAILED" in msg.upper() or "Invalid credentials" in msg:
            return "Sign-in failed: check the address and use an app password, not your normal password."
        return f"Mail server refused the sign-in: {msg[:160]}"
    except OSError as e:
        return f"Could not reach {host}: {e}"
