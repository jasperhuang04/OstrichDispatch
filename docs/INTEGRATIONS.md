# Integration setup

No credentials are bundled. Configure providers yourself, then connect each inbox from the application. A connection button being available means configuration exists, not that a live connection has been verified.

## iCloud and Yahoo

1. Enable two-factor authentication in your Apple Account, then generate a dedicated app-specific password at https://account.apple.com/.
2. In OstrichDispatch → Connected inboxes, choose iCloud, enter the primary mailbox address and app-specific password, and keep folder `INBOX` unless you use a separate order folder.
3. Start the worker and inspect the inbox's last-sync/error status. If authentication fails with the full address, enter the portion before `@icloud.com` as the optional IMAP username.
4. To disconnect, remove the connection in OstrichDispatch and revoke the app-specific password at Apple.

iCloud uses `imap.mail.me.com:993` with TLS certificate verification. Yahoo uses `imap.mail.yahoo.com:993` and its app-password process. Other IMAP hosts must be listed by the server operator in `CUSTOM_IMAP_HOSTS`; only TLS port 993 and public DNS destinations are supported. No SMTP is used.

The client selects the folder read-only and retrieves `BODY.PEEK[]`, preserving read/unread flags. This is application behavior, not a provider-enforced read-only scope: an IMAP app password can grant broader mail access if stolen. Credentials are Fernet-encrypted with `ENCRYPTION_KEY` on the server. Keep the key separately from database backups.

Each connection scans one folder, initially looking back `INITIAL_SYNC_DAYS` days. It then uses UIDs and UIDVALIDITY, never `UNSEEN`. Messages over 2 MB are skipped, including messages made large by attachments. IMAP aliases share a mailbox; connect the primary account once. Filtered-out mail in other folders requires the folder setting to match where your orders arrive. To change it, disconnect and reconnect; existing orders are preserved, so inspect for duplicate orders after reimport.

Official Apple instructions: https://support.apple.com/en-us/102525 and https://support.apple.com/en-us/102654.

## Gmail

1. Create a Google Cloud project and enable the Gmail API.
2. Configure the OAuth consent screen for your personal group. Add `https://www.googleapis.com/auth/gmail.readonly` and list each Google account as a test user while in Testing mode.
3. Create a Web application OAuth client. Add the exact redirect URI `http://localhost:8000/oauth/gmail/callback/` for local development, or `https://YOUR_DOMAIN/oauth/gmail/callback/` for deployment. `APP_URL` must match the origin exactly.
4. Put the client ID and secret in `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`; restart the web service and worker.
5. Use Connect Gmail. Offline access enables background refresh tokens. Consent is requested for each account you connect.

Gmail read-only is a restricted scope. Google has a personal-use verification exception for a small group known personally to the developer, but unverified apps have warnings and a user cap. **Testing-mode refresh tokens for these scopes expire after seven days.** For ongoing use, review Google's publishing/verification guidance and configure the appropriate publishing status; changing status is not a guarantee of approval or unrestricted use. Account/admin policies can still block access. Tokens may also expire after password changes, revocation, or inactivity. Reconnect when the app reports failure.

Sources: https://developers.google.com/workspace/gmail/api/auth/scopes, https://developers.google.com/identity/protocols/oauth2/, and https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification.

Gmail sync searches a bounded time window, paginates in resumable batches, and overlaps the previous window to catch delayed messages. It does not mark messages as read. Spam and Trash are not requested. The initial history lookback is configurable.

## Outlook / Hotmail

1. Register an application in Microsoft Entra. For both work/school and personal Outlook accounts, select the audience that includes organizational directories and personal Microsoft accounts.
2. Add a Web redirect URI ending in `/oauth/outlook/callback/`, matching `APP_URL` exactly.
3. Configure delegated `Mail.Read`, `User.Read`, and `offline_access` permissions. `Mail.ReadBasic` is insufficient for parsing message bodies.
4. Create a client secret. Set `MICROSOFT_CLIENT_ID` and `MICROSOFT_CLIENT_SECRET`; restart the app and worker.
5. Select Connect Outlook. Organizational accounts may require administrator consent or policy changes by the organization's administrator.

The application uses delegated per-user access, not application-wide access to every mailbox. Refresh-token rotation is saved after refresh. Microsoft Graph is polled with date bounds and validated pagination links; no webhooks are required.

Sources: https://learn.microsoft.com/en-us/graph/permissions-reference and https://learn.microsoft.com/en-us/graph/api/user-list-messages.

## Carrier tracking

Email extraction and carrier links work without a tracking API subscription. To add background carrier updates, obtain an EasyPost key and set `EASYPOST_API_KEY`. Use a test key first with EasyPost's documented test tracking codes; a production key and provider billing setup are needed for real packages. Nothing in this application purchases shipping labels.

The worker creates a Tracker for each new tracking number, then retrieves the stored Tracker ID hourly. Tracking details populate a timeline. UPS, USPS and FedEx can be resolved through the provider where your account supports them. Amazon logistics and unsupported carriers may remain email-only; tracking links are provided when known. The tracking-number format alone never proves a shipment exists. Ambiguous numeric formats are left with unknown carrier for provider detection or manual correction.

Live carrier APIs are not guaranteed free, and current costs/quotas must be checked with your provider. Polling is not instantaneous push from carriers. Set `TRACK_INTERVAL` in seconds, with a 15-minute minimum. Source: https://docs.easypost.com/docs/trackers.

## Push and Discord

`scripts/setup.py` generates VAPID keys. Set `VAPID_SUBJECT` to a real `mailto:` contact. Persist the same VAPID keys across deploys; changing them requires device resubscription. Use HTTPS when online. Open Settings and enable push on each browser/device.

On iPhone/iPad, add the app to the Home Screen first and launch it there. Web Push requires supported OS/browser versions (iOS/iPadOS 16.4+ for Home Screen apps). Android Chrome and desktop Chrome/Firefox are supported by the current endpoint allowlist; Safari uses Apple's push service. Additional push-service hosts require an operator code change, rather than accepting arbitrary server-side URLs.

To use Discord, create a channel webhook in a server you control and enter its URL in Settings. Notifications include order titles and status but no address or email body. A durable outbox retries failures up to five times. Settings shows exhausted failures. Delivery is at-least-once: a crash immediately after a successful send can result in a duplicate notification. Disable all push devices from Settings before handing a device to another person.

Apple source: https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers.
