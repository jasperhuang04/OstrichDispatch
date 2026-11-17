# First-release verification — November 17, 2026

## Passed locally

- 40 Django tests. Coverage includes privacy between accounts, CSRF, encrypted credential storage, invitation restrictions and the ten-account cap, more than five inboxes, CSV formula protection, template escaping, account deletion, inventory cost/profit and overselling, order-to-inventory entry, malformed OAuth state, duplicate messages, multiple shipments and older status events, IMAP read-only/PEEK behavior, Gmail pagination, carrier response parsing, notification retries and outbound destination validation.
- Django system checks and migration consistency checks.
- Static asset collection and JavaScript syntax checks.
- A Chromium browser smoke test: login, all main screens, spending chart, theme switch, a 390px mobile viewport with no horizontal page overflow, manual order creation, and archiving.
- Visual inspection of synthetic-data dashboard screenshots at desktop and mobile sizes, plus the spending insights screen. Mobile filters were adjusted to keep the dropdown text readable.
- Production security check with DEBUG disabled. The only warning was intentional: HSTS preload is off. Preloading a domain has long-lived consequences and is not a default requirement.

## Not yet verified with live services

- Gmail/Outlook OAuth app registrations and actual account consent/refresh behavior.
- Authentication and sync against your iCloud/Yahoo/IMAP mailboxes.
- Extraction accuracy against your stores' real email layouts. Tests use synthetic examples and structured order markup, not a corpus of current retailer emails.
- EasyPost production tracking, carrier availability, billing and carrier-specific events.
- Delivery of real push notifications to iPhone/Android or messages to Discord.
- The PostgreSQL/Docker/Caddy deployment on an actual host. Docker was unavailable in the build workspace; the app and tests ran against local SQLite.

Provider calls in the automated suite use mocks; a passing suite is not a claim that credentials or live integrations have been configured. Begin deployment with one inbox and a few known orders, confirm the results, then add the remaining inboxes and invite friends.

## Improving retailer parsing

Useful samples: an order confirmation, shipping notice, delivered notice and cancellation/refund message from each retailer you use often. Include a split shipment or multi-product order if available. Redacted `.eml` files preserve the MIME/HTML layout better than screenshots. Keep field labels, product names, quantity formatting, sender domain, and the shapes of order/tracking identifiers. Replace personal names, addresses, email addresses, payment details, live tracking IDs and order-management links with plausible dummy values.

Samples will be turned into parser fixtures only after private details are removed. Raw real inbox messages should never be committed to the repository. The `.gitignore` excludes `.eml`, mailbox archives, environment files and database dumps.

To reproduce optional browser checks, install Playwright in a development environment and its Chromium browser, seed the local demo account, then run `scripts/smoke_browser.py` with the chosen `DEMO_PASSWORD`. `START_SMOKE_SERVER=true` starts and stops a local test server automatically. This script uses synthetic data and produces screenshots only in ignored `test-results/`.
