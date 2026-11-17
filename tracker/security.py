import json
import re
import socket
import ipaddress
from urllib.parse import urlparse
from cryptography.fernet import Fernet
from django.conf import settings

IMAP_HOSTS = {"icloud": "imap.mail.me.com", "yahoo": "imap.mail.yahoo.com"}

def seal(value):
    return Fernet(settings.ENCRYPTION_KEY.encode()).encrypt(json.dumps(value).encode()).decode()

def unseal(value):
    return json.loads(Fernet(settings.ENCRYPTION_KEY.encode()).decrypt(value.encode()))

def public_host(host):
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise ValueError("Invalid host name.")
    answers = socket.getaddrinfo(host, 993, type=socket.SOCK_STREAM)
    if not answers or any(not ipaddress.ip_address(a[4][0]).is_global for a in answers):
        raise ValueError("Mail servers must have public IP addresses.")
    return host

def imap_host(provider, host=""):
    resolved = IMAP_HOSTS.get(provider, host.lower().strip())
    if provider not in IMAP_HOSTS and resolved not in settings.CUSTOM_IMAP_HOSTS:
        raise ValueError("Ask the administrator to add this server to CUSTOM_IMAP_HOSTS.")
    return public_host(resolved)

def discord_url(url):
    p = urlparse(url)
    if p.scheme != "https" or p.hostname != "discord.com" or p.port not in (None, 443) or p.username or p.password or not re.fullmatch(r"/api/webhooks/\d+/[\w-]+", p.path) or p.query or p.fragment:
        raise ValueError("Use an https://discord.com/api/webhooks/... URL.")
    return url

def push_endpoint(url):
    p = urlparse(url)
    allowed = {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}
    if p.scheme != "https" or p.hostname not in allowed or p.port not in (None, 443) or p.username or p.password:
        raise ValueError("This browser's push service is not supported.")
    return url
