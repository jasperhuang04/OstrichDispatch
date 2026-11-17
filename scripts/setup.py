"""Generate local secrets once; never overwrite an existing environment file."""
import base64
import os
from pathlib import Path
import secrets
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

root = Path(__file__).resolve().parent.parent
target = root / ".env"
if target.exists():
    raise SystemExit(".env already exists; left unchanged.")
key = ec.generate_private_key(ec.SECP256R1())
private = key.private_numbers().private_value.to_bytes(32, "big")
public = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
encode = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
password = secrets.token_hex(24)
content = f"""# Local development defaults. Read docs/DEPLOYMENT.md before hosting.
DEBUG=true
SECRET_KEY={secrets.token_urlsafe(64)}
ENCRYPTION_KEY={Fernet.generate_key().decode()}
APP_URL=http://localhost:8000
ALLOWED_HOSTS=localhost,127.0.0.1,testserver
TIME_ZONE=America/Los_Angeles
POSTGRES_PASSWORD={password}
SYNC_INTERVAL=300
TRACK_INTERVAL=3600
INITIAL_SYNC_DAYS=30
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
MICROSOFT_CLIENT_ID=
MICROSOFT_CLIENT_SECRET=
EASYPOST_API_KEY=
VAPID_PRIVATE_KEY={encode(private)}
VAPID_PUBLIC_KEY={encode(public)}
VAPID_SUBJECT=mailto:admin@example.com
CUSTOM_IMAP_HOSTS=
"""
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as file:
    file.write(content)
print("Created .env with fresh secrets. Next: python manage.py migrate")
