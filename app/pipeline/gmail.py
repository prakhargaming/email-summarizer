import base64
import re
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

from app.core.config import settings

TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_URL = "https://gmail.googleapis.com/gmail/v1/users/me"

_client = httpx.Client(timeout=15)


class InvalidGrant(Exception):
    pass


# Body lives in memory only for the run; never persist or log it.
@dataclass(frozen=True)
class Email:
    message_id: str
    thread_id: str
    sender: str
    subject: str
    date: str
    body: str


def refresh_access_token(refresh_token: str) -> str:
    r = _client.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
    })
    if r.status_code == 400 and r.json().get("error") == "invalid_grant":
        raise InvalidGrant()
    r.raise_for_status()
    return r.json()["access_token"]


def fetch_emails(access_token: str, since_epoch: int, cap: int = 50) -> tuple[list[Email], bool]:
    auth = {"Authorization": f"Bearer {access_token}"}
    ids: list[str] = []
    page = None
    while len(ids) <= cap:
        params = {"q": f"in:inbox after:{since_epoch} -in:spam -in:trash", "maxResults": min(100, cap + 1 - len(ids))}
        if page:
            params["pageToken"] = page
        r = _client.get(f"{GMAIL_URL}/messages", headers=auth, params=params)
        r.raise_for_status()
        data = r.json()
        ids += [m["id"] for m in data.get("messages", [])]
        page = data.get("nextPageToken")
        if not page:
            break
    out = []
    # shortcut: one request per message; use Gmail batch requests if runs get slow.
    for mid in ids[:cap]:
        r = _client.get(f"{GMAIL_URL}/messages/{mid}", headers=auth, params={"format": "full"})
        r.raise_for_status()
        msg = r.json()
        payload = msg.get("payload", {})
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        out.append(Email(
            message_id=msg["id"],
            thread_id=msg["threadId"],
            sender=headers.get("from", ""),
            subject=headers.get("subject", ""),
            date=headers.get("date", ""),
            body=extract_text(payload),
        ))
    return out, len(ids) > cap


def extract_text(payload: dict, limit: int = 4000) -> str:
    parts = {"text/plain": None, "text/html": None}
    stack = [payload]
    while stack:
        p = stack.pop(0)
        stack += p.get("parts", [])
        mime, data = p.get("mimeType"), p.get("body", {}).get("data")
        if mime in parts and data and not p.get("filename") and parts[mime] is None:
            parts[mime] = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    if parts["text/plain"] is not None:
        text = parts["text/plain"]
    elif parts["text/html"] is not None:
        text = _html_to_text(parts["text/html"])
    else:
        text = ""
    return _strip_quotes(text)[:limit]


_ON_WROTE = re.compile(r"^\s*On .+ wrote:\s*$")


def _strip_quotes(text: str) -> str:
    kept = []
    for line in text.splitlines():
        if _ON_WROTE.match(line):
            break
        if not line.lstrip().startswith(">"):
            kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


_SKIP_TAGS = ("script", "style", "head", "blockquote")


class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__()
        self.chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in self._BLOCK:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.chunks.append(data)


def _html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    lines = (" ".join(line.split()) for line in "".join(p.chunks).splitlines())
    return "\n".join(lines)
