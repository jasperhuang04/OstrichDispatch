# Host OstrichDispatch for your private group

Use PostgreSQL for shared deployment. SQLite is provided for local development. Run one worker process. This release includes a Docker Compose definition; it still needs an end-to-end test on the target host with your actual integrations.

## Docker Compose with HTTPS

1. Choose a server and a domain/subdomain. Point its DNS record to the server. Allow inbound 80/443; keep PostgreSQL and the application port private. Your host must permit outbound TLS connections to port 993 for IMAP and HTTPS for provider APIs.
2. Copy the source to the host. Install Docker Engine and the Compose plugin using the vendor's instructions. Create `.env` using `scripts/setup.py` in a Python virtual environment as in the README, or provide equivalent secrets through your host's secret manager.
3. Edit `.env`: set `DEBUG=false`, `APP_URL=https://YOUR_DOMAIN`, `ALLOWED_HOSTS=YOUR_DOMAIN`, `DOMAIN=YOUR_DOMAIN`, `TRUST_PROXY=true`, and a strong `POSTGRES_PASSWORD` without URL-reserved characters (the setup script uses hex). Keep `SECRET_KEY` and `ENCRYPTION_KEY` from setup. Do not regenerate them on restart. Set a real `VAPID_SUBJECT`.
4. Run `docker compose up -d --build`. The `init` service runs migrations and collects static files before the web/worker services start. Caddy obtains and renews HTTPS certificates for the configured domain.
5. Create your account: `docker compose exec web python manage.py createowner --email you@example.com --name Jasper`. The password is prompted securely; no hardcoded owner is installed.
6. Open the site, sign in, connect one inbox, and verify a test order before inviting friends. Inspect `docker compose logs --tail=100 worker` if it does not sync.
7. Run `docker compose exec web python manage.py check --deploy`. Review any warning rather than suppressing it. `SECURE_HSTS_PRELOAD` remains false intentionally; do not request domain preloading unless you understand its long-lived effect.

The `TRUST_PROXY` setting is appropriate only when a trusted reverse proxy controls the forwarding header and users cannot directly reach the web service. Compose binds the host application port to loopback; Caddy reaches it over the internal service network. The database is not published to the host.

## Other hosts, including Render

The app can run on a Python web host as a web service plus one continuously running background worker, both using the same persistent PostgreSQL database and environment secrets. Build with `pip install -r requirements.lock && python manage.py collectstatic --noinput`. Run migrations as a single pre-deploy/release step. Start the web service with `gunicorn parcelnest.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 2` and the worker with `python manage.py worker`.

Configure `DATABASE_URL` for the provider's PostgreSQL service, HTTPS `APP_URL`, `ALLOWED_HOSTS`, and only enable proxy trust when the host documents that it strips/replaces forwarded headers. Check that the worker plan doesn't sleep and supports outbound IMAP port 993. Do not put SQLite in an ephemeral app filesystem: a rebuild can otherwise erase data. GitHub stores source code, not your running app or database. It does not monitor mail by itself.

## Back up and restore

Back up the PostgreSQL database with your host's managed backup facility or `pg_dump` using encrypted storage. Back up the encryption key separately. A database backup without `ENCRYPTION_KEY` cannot decrypt stored mail credentials; a leaked backup plus the key exposes them. Protect backup retention because account deletion does not erase older backups automatically.

For a Compose deployment, a database export can be produced with:

```bash
docker compose exec -T db pg_dump -U parcelnest -d parcelnest -Fc > parcelnest-backup.dump
```

The file contains private user information. Do not commit it to GitHub. To validate recovery, create a separate empty PostgreSQL instance and restore with `pg_restore`, then run migrations using the same encryption key. Start the restored app with outbound integrations disabled until you intentionally resume mail/notification work.

When upgrading: take a backup, review dependency and migration changes, then run `docker compose up -d --build`. The named PostgreSQL volume persists across container rebuilds. Never use `docker compose down -v` on a deployment with data you want to keep.

## Operational limits

- One worker is supported. A database lease prevents routine accidental overlap and expires after two hours if a worker crashes. Long-running very large sync jobs should be split or migrated to a full task queue before increasing load.
- Initial inbox scanning is 30 days, up to 500 messages per inbox per cycle; OAuth pagination continues next cycle. Messages over 2 MB are not parsed. Read and unread messages are both eligible.
- Admins create invitations, but there is no cross-user order browser in the application. The server operator still has access to the database and encryption key.
- Login attempts are throttled by account and connection IP for 15 minutes. Configure upstream rate limiting for an internet-facing deployment as well. Behind a proxy, the connection-IP limit may be shared by users.
- Credentials and notification payloads are encrypted where appropriate, but parsed order data is readable by the database/server operator. Use disk/database encryption and access controls on your host.
- No SMTP or automated password-reset email is included. The owner can reset a password using `python manage.py changepassword EMAIL` on the server after verifying the person requesting it.
- Keep the dependencies patched and retest before upgrades. Runtime versions are captured in `requirements.lock` for repeatable installs.
