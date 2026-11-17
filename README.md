# OstrichDispatch

A private, phone-friendly order tracker for you and up to nine friends. Connect multiple inboxes, collect purchase and shipping emails, follow packages, and keep track of inventory and resale profit.

This is an independently built application, not the commercial Penguin Ship app. It is a runnable first release with real integration code; connecting live accounts and deploying it are separate setup steps. No email credentials, API keys, customer data, or default production password are included.

## What is included

| Area | Implemented behavior | Setup / limits |
|---|---|---|
| Accounts | Private user accounts, owner-issued invitations, password changes, account deletion | 10 accounts per deployment; each person's data is isolated |
| Inboxes | Gmail OAuth, Outlook OAuth, iCloud and Yahoo IMAP, operator-approved custom IMAP | No application inbox-count cap; providers still have rate/access limits |
| Email sync | Scheduled worker, incremental cursors, pagination, retries, duplicate detection | Initial 30-day lookback; IMAP scans one selected folder; messages over 2 MB are skipped |
| Order extraction | MIME text/HTML, schema.org Order/ParcelDelivery, retailer sender recognition, labelled order totals and products, tracking links/numbers | Local rules, no external AI. Retailer templates vary; incomplete details are flagged for review |
| Orders | Manual add/edit, search, status/inbox/date filters, archive/restore, destination and notes, multiple packages per order | Order details can be corrected manually |
| Tracking | Email status updates, carrier tracking links, optional EasyPost API polling and timeline | Live carrier service needs an API key; carrier support and costs are provider-dependent |
| Notifications | In-app activity, browser push, Discord webhooks, persistent delivery retry queue | Browser permission, VAPID keys and HTTPS for push; Discord webhook optional |
| Calendar | Monthly estimated arrivals, destination filter | Dates are estimates, not guarantees; orders without ETA are counted separately |
| Insights | Monthly spending chart, store/month totals, product quantities, cancellation-based stick rate | Currencies are separate; unknown prices excluded; no exchange-rate conversion or partial refund ledger |
| Inventory | Quantity, unit cost, linked order, on-hand value, sales, fees/shipping, profit and ROI, filters/sorting | Inventory is explicitly added by you; it isn't auto-created for every purchase |
| Exports | Orders, inventory and sales CSV | Spreadsheet formula injection protected |
| Mobile | Responsive installable Progressive Web App with push support | Works on web/iPhone/Android; no native App Store or Play Store package |
| Operations | PostgreSQL Docker Compose, Caddy HTTPS, background worker, local SQLite, migrations, tests and GitHub Actions | Requires a running server; Docker stack must be validated on your host |

The interface has light/dark themes. The default locale is America/Los_Angeles and can be changed in `.env`.

## Run locally

Python 3.12+ is required. On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python scripts/setup.py
python manage.py migrate
python manage.py createowner --email you@example.com --name Jasper
python manage.py runserver
```

On macOS/Linux, create the environment with `python3 -m venv .venv`, activate with `source .venv/bin/activate`, then run the same `python` commands.

Open **http://localhost:8000**. In a second terminal, activate the same virtual environment and run:

```bash
python manage.py worker
```

The worker checks for queued work every 15 seconds. Inbox polling defaults to 5 minutes; optional carrier polling defaults to one hour. **The web server alone does not sync mail.** `python manage.py worker --once` runs one cycle for diagnostics or a scheduler.

Start with iCloud: Settings on your Apple Account → Sign-In and Security → App-Specific Passwords. Add the primary iCloud address and generated password on **Connected inboxes**. The app supplies `imap.mail.me.com`, port 993, TLS. Main Apple passwords should never be entered. Aliases and Hide My Email addresses normally deliver to an existing mailbox and aren't separate IMAP accounts.

For Gmail, Outlook, live tracking, push, and Discord, see [the integration guide](docs/INTEGRATIONS.md).

## Put it online for friends

Follow [DEPLOYMENT.md](docs/DEPLOYMENT.md) for PostgreSQL, HTTPS, backups and deployment validation. Do not expose Django's development server. Use **Settings → Invite your circle** to generate a private, single-use link for a friend. Links expire after seven days. Invitations are not automatically emailed.

Hosting, a domain and live carrier tracking may cost money. The application itself has no subscription or five-inbox limit. Passwords and keys go in local/server environment settings, never in GitHub or a chat.

## Testing

```bash
python manage.py check
python manage.py test
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
```

Tests exercise parser fixtures, email and tracking adapters with mocked provider responses, access isolation, CSRF, invitations, CSV escaping, encryption, partial shipments, inventory accounting, OAuth state, and notification retries. They do not log in to real email accounts or prove that every retailer email layout is supported. See [verification notes](docs/VERIFICATION.md).

## Optional sample data

Run only on a local development database. Set `DEMO_PASSWORD` to a password of your choice, then run `python manage.py demo`. Sign in as `demo@example.com`. Sample orders are explicitly synthetic. The command refuses to run when `DEBUG=false`. Never add real carrier credentials to a demo deployment.

## GitHub project

Source: [jasperhuang04/OstrichDispatch](https://github.com/jasperhuang04/OstrichDispatch). Clone this private repository with your GitHub account before running the setup commands. Keep `.env`, databases, downloaded emails and private keys out of commits; the included ignore rules exclude these files.

The internal Django package is named `parcelnest`; this is only a Python module name. The application and repository are OstrichDispatch. The existing repository's MIT license is preserved.

## Layout

`tracker/parser.py` extracts order data. `mail.py` implements the email adapters. `services.py` merges messages and packages. `integrations.py` connects tracking/notification services. `models.py` stores per-user data. `views.py`, `templates/`, and `static/` provide the interface. `management/commands/worker.py` runs background jobs.

Source references and implementation decisions are in [REFERENCES.md](docs/REFERENCES.md). The project uses Django for authentication, CSRF protection, database migrations and server-rendered pages, keeping one small service plus a worker practical for a private group.
