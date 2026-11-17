import base64
import imaplib
import re
import ssl
from datetime import datetime, timedelta, timezone as dt_timezone
from email.message import EmailMessage
from urllib.parse import urlparse, quote
import requests
from django.conf import settings
from django.utils import timezone
from .security import seal, unseal, imap_host
from .services import ingest
from .models import ProcessedMessage, digest

MAX_MESSAGE = 2 * 1024 * 1024

def oauth_config(provider):
    if provider == "gmail":
        return {"id": settings.GOOGLE_CLIENT_ID, "secret": settings.GOOGLE_CLIENT_SECRET, "authorize": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token", "scope": "https://www.googleapis.com/auth/gmail.readonly"}
    if provider == "outlook":
        return {"id": settings.MICROSOFT_CLIENT_ID, "secret": settings.MICROSOFT_CLIENT_SECRET, "authorize": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize", "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token", "scope": "offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read"}
    raise ValueError("Unknown OAuth provider")

def access_token(inbox):
    secret = unseal(inbox.secret)
    if secret.get("expires_at", 0) > timezone.now().timestamp() + 120:
        return secret["access_token"]
    cfg = oauth_config(inbox.provider)
    response = requests.post(cfg["token"], data={"client_id": cfg["id"], "client_secret": cfg["secret"], "grant_type": "refresh_token", "refresh_token": secret["refresh_token"]}, timeout=30)
    response.raise_for_status()
    token = response.json()
    secret.update({"access_token": token["access_token"], "refresh_token": token.get("refresh_token", secret["refresh_token"]), "expires_at": timezone.now().timestamp() + int(token.get("expires_in", 3600))})
    inbox.secret = seal(secret)
    inbox.save(update_fields=["secret"])
    return secret["access_token"]

def save_cursor(inbox, cursor):
    inbox.cursor = cursor
    inbox.save(update_fields=["cursor"])

def sync_imap(inbox):
    host = imap_host(inbox.provider, inbox.host)
    if re.search(r"[\r\n\x00]", inbox.folder):
        raise ValueError("Invalid folder")
    with imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context(), timeout=25) as conn:
        conn.login(inbox.username or inbox.email, unseal(inbox.secret)["password"])
        status, _ = conn.select('"' + inbox.folder.replace('\\', '\\\\').replace('"', '\\"') + '"', readonly=True)
        if status != "OK":
            raise ValueError("Folder is unavailable")
        validity = str((conn.response("UIDVALIDITY")[1] or [b""])[0].decode())
        old = inbox.cursor if inbox.cursor.get("validity") == validity else {}
        last = int(old.get("uid", 0))
        if last:
            status, data = conn.uid("search", None, "UID", f"{last+1}:*")
        else:
            since = (timezone.now()-timedelta(days=settings.INITIAL_SYNC_DAYS)).strftime("%d-%b-%Y")
            status, data = conn.uid("search", None, "SINCE", since)
        if status != "OK":
            raise ValueError("Message search failed")
        uids = sorted(int(v) for v in data[0].split() if int(v) > last)[:500]
        for uid in uids:
            status, metadata = conn.uid("fetch", str(uid), "(RFC822.SIZE)")
            if status != "OK":
                raise ValueError("Message metadata fetch failed")
            info = b" ".join(v for v in metadata if isinstance(v, bytes))
            size = re.search(rb"RFC822.SIZE (\d+)", info)
            if not size:  # Message may have been expunged between search and fetch.
                save_cursor(inbox, {"uid": uid, "validity": validity})
                continue
            if int(size.group(1)) <= MAX_MESSAGE:
                status, message = conn.uid("fetch", str(uid), "(BODY.PEEK[])")
                if status != "OK":
                    raise ValueError("Message fetch failed")
                raw = next((v[1] for v in message if isinstance(v, tuple)), None)
                if raw:
                    ingest(inbox, raw, f"{validity}:{uid}")
            save_cursor(inbox, {"uid": uid, "validity": validity})
        return len(uids)

def sync_gmail(inbox):
    headers = {"Authorization": f"Bearer {access_token(inbox)}"}
    base = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    cursor = dict(inbox.cursor)
    start = int(cursor.get("after", (timezone.now()-timedelta(days=settings.INITIAL_SYNC_DAYS)).timestamp()))
    end = int(cursor.get("until") or timezone.now().timestamp())
    page_token = cursor.get("page_token")
    count = 0
    for _ in range(5):
        params = {"q": f"after:{start-1800} before:{end}", "maxResults": 100}
        if page_token:
            params["pageToken"] = page_token
        response = requests.get(base, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        for item in data.get("messages", []):
            if ProcessedMessage.objects.filter(inbox=inbox, key=digest(item["id"])).exists():
                continue
            # Metadata first prevents downloading large unrelated attachments.
            r = requests.get(f"{base}/{quote(item['id'], safe='')}", params={"format": "metadata"}, headers=headers, timeout=30)
            r.raise_for_status()
            if r.json().get("sizeEstimate", MAX_MESSAGE+1) > MAX_MESSAGE:
                continue
            r = requests.get(f"{base}/{quote(item['id'], safe='')}", params={"format": "raw"}, headers=headers, timeout=30)
            r.raise_for_status()
            encoded = r.json()["raw"]
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            ingest(inbox, raw, item["id"])
            count += 1
        page_token = data.get("nextPageToken")
        cursor = {"after": start, "until": end, "page_token": page_token} if page_token else {"after": end}
        save_cursor(inbox, cursor)
        if not page_token:
            break
    return count

def graph_next(url):
    p = urlparse(url)
    if p.scheme != "https" or p.hostname != "graph.microsoft.com" or p.port not in (None, 443) or p.username or not p.path.startswith("/v1.0/me/messages"):
        raise ValueError("Invalid Microsoft pagination URL")
    return url

def sync_outlook(inbox):
    headers = {"Authorization": f"Bearer {access_token(inbox)}"}
    cursor = dict(inbox.cursor)
    start = cursor.get("after") or (timezone.now()-timedelta(days=settings.INITIAL_SYNC_DAYS)).isoformat()
    end = cursor.get("until") or timezone.now().isoformat()
    url = graph_next(cursor["next"]) if cursor.get("next") else "https://graph.microsoft.com/v1.0/me/messages"
    # Read body content requires Mail.Read; Mail.ReadBasic is not enough.
    params = None if cursor.get("next") else {"$filter": f"receivedDateTime ge {start} and receivedDateTime lt {end}", "$select": "id,subject,from,body,receivedDateTime", "$top": 100, "$orderby": "receivedDateTime asc"}
    count = 0
    for _ in range(5):
        response = requests.get(url, params=params, headers=headers, timeout=30, allow_redirects=False)
        response.raise_for_status()
        data = response.json()
        for item in data.get("value", []):
            if ProcessedMessage.objects.filter(inbox=inbox, key=digest(item["id"])).exists():
                continue
            msg = EmailMessage()
            msg["Subject"] = item.get("subject", "").replace("\r", " ").replace("\n", " ")
            msg["From"] = item.get("from", {}).get("emailAddress", {}).get("address", "unknown@example.com")
            msg.set_content(item.get("body", {}).get("content", "")[:MAX_MESSAGE], subtype="html" if item.get("body", {}).get("contentType", "").lower() == "html" else "plain")
            at = datetime.fromisoformat(item["receivedDateTime"].replace("Z", "+00:00"))
            ingest(inbox, msg.as_bytes(), item["id"], at)
            count += 1
        next_url = data.get("@odata.nextLink")
        if next_url:
            url, params = graph_next(next_url), None
            save_cursor(inbox, {"after": start, "until": end, "next": url})
        else:
            save_cursor(inbox, {"after": (datetime.fromisoformat(end)-timedelta(minutes=30)).isoformat()})
            break
    return count

def sync_inbox(inbox):
    try:
        count = {"gmail": sync_gmail, "outlook": sync_outlook}.get(inbox.provider, sync_imap)(inbox)
        inbox.last_sync, inbox.error = timezone.now(), ""
    except (requests.RequestException, imaplib.IMAP4.error, OSError, ValueError, KeyError, TypeError):
        # Never include provider payloads: they may contain credentials or email text.
        inbox.error = "Sync failed. Check credentials, folder, and network access; reconnect OAuth accounts if needed."
        count = 0
    inbox.next_sync = timezone.now() + timedelta(seconds=settings.SYNC_INTERVAL * (3 if inbox.error else 1))
    inbox.save(update_fields=["last_sync", "error", "next_sync"])
    return count
