# Reference projects and design choices

The following repositories were inspected as design references. No code or assets from these repositories were copied into this project.

- https://github.com/polairelabs/PenguinShip — Java shipping-label API with EasyPost and Stripe integration, under Apache-2.0. Its README describes buying labels and membership management; it is not the source of the email order-tracker service at penguinship.com.
- https://github.com/jkeen/tracking_number — Ruby tracking-number identification/validation library, under MIT. Its README explicitly says it does not fetch tracking statuses. OstrichDispatch uses its own conservative format hints; it does not claim the same carrier coverage or checksum validation.
- https://github.com/shlee322/delivery-tracker — shipping-tracking service with GraphQL and carrier scrapers. Its deployment introduces another service, carrier-specific scraping maintenance, and separate licensing considerations. It is not bundled. The first release uses optional EasyPost and email updates.

The app uses Django's authentication, CSRF protection, template escaping, ORM and migrations; Python's IMAP and MIME libraries; cryptography/Fernet for stored credentials; and standards-based Web Push. This minimizes the number of services for a private 1–10-person installation.

The product name and interface are original. The UI uses system fonts and a locally defined package icon. No external fonts, analytics scripts or remote product images are loaded.

The additional product reference https://ordertracker.app/ emphasizes email imports, delivery status, and resale accounting. OstrichDispatch includes linked order-to-inventory entry and net profit after fees/shipping. It does not copy that service's design/assets or claim feature parity: live StockX/TCGplayer valuations, label-camera scanning, address-alias matching, invoices and its retailer-specific scraper coverage are not part of this first release.
