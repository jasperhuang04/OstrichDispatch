import json
import secrets
from datetime import timedelta
from decimal import Decimal
from email.message import EmailMessage
from unittest.mock import patch, MagicMock
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from .models import Inbox, Order, Shipment, Inventory, Sale, Notification, Invite, PushSubscription, ProcessedMessage, Profile, Delivery, digest
from .parser import parse_email, infer_carrier, tracking_numbers
from .services import ingest, set_shipment_status
from .security import seal, unseal, discord_url, push_endpoint
from .mail import sync_inbox, sync_imap, sync_gmail, graph_next, oauth_config
from .integrations import refresh_tracking, send_pending

PASSWORD = "Local-test-passphrase-972!"

def email(subject="Your order #123-1234567-1234567", body=None, sender="auto-confirm@amazon.com", html=False):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "demo@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Tue, 17 Nov 2026 09:00:00 -0800"
    msg.set_content(body if body is not None else "Order #123-1234567-1234567\nItem: ESP32 development board\nOrder total: $21.49\nEstimated delivery: November 21, 2026", subtype="html" if html else "plain")
    return msg.as_bytes()

@override_settings(SECURE_SSL_REDIRECT=False, STORAGES={"default":{"BACKEND":"django.core.files.storage.FileSystemStorage"}, "staticfiles":{"BACKEND":"django.contrib.staticfiles.storage.StaticFilesStorage"}})
class AppTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("me@example.com", "me@example.com", PASSWORD, first_name="Jasper", is_staff=True)
        self.friend = User.objects.create_user("friend@example.com", "friend@example.com", PASSWORD)
        self.inbox = Inbox.objects.create(user=self.user, provider="icloud", email="me@icloud.com", secret=seal({"password":"test-app-password"}))
        self.order = Order.objects.create(user=self.user, identity="manual-test", merchant="Target", title="Starter deck", total=Decimal("13.00"))
        self.client.force_login(self.user)

    def test_all_pages_render(self):
        for url in ["/", "/inboxes/", "/insights/", "/calendar/", "/inventory/", "/inventory/new/", "/orders/new/", f"/orders/{self.order.pk}/", f"/orders/{self.order.pk}/edit/", "/settings/", "/notifications/", "/password/", "/privacy/", "/sw.js", "/healthz/"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_guest_cannot_read_private_pages(self):
        self.client.logout()
        for url in ["/", "/inboxes/", "/export/", "/inventory/", f"/orders/{self.order.pk}/"]:
            self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.get("/login/").status_code, 200)

    def test_friend_cannot_read_or_modify_my_data(self):
        self.client.force_login(self.friend)
        for url in [f"/orders/{self.order.pk}/", f"/orders/{self.order.pk}/edit/"]:
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(f"/orders/{self.order.pk}/archive/").status_code, 404)
        self.assertEqual(self.client.post(f"/inboxes/{self.inbox.pk}/disconnect/").status_code, 404)
        self.assertNotContains(self.client.get("/export/"), "Starter deck")
        self.assertNotContains(self.client.get("/inboxes/"), "me@icloud.com")

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(f"/orders/{self.order.pk}/archive/").status_code, 403)
        self.assertEqual(client.post("/push/subscribe/", "{}", content_type="application/json").status_code, 403)

    def test_encrypted_credentials_and_no_html_leak(self):
        self.assertNotIn("test-app-password", self.inbox.secret)
        self.assertEqual(unseal(self.inbox.secret)["password"], "test-app-password")
        self.assertNotContains(self.client.get("/inboxes/"), "test-app-password")

    def test_parser_extracts_product_money_eta(self):
        p = parse_email(email())
        self.assertEqual(p.merchant, "Amazon")
        self.assertEqual(p.number, "123-1234567-1234567")
        self.assertEqual(p.total, Decimal("21.49"))
        self.assertEqual(p.title, "ESP32 development board")
        self.assertEqual(str(p.eta), "2026-11-21")
        self.assertFalse(p.review)

    def test_all_retailer_sender_adapters(self):
        for domain, name in [("walmart.com","Walmart"),("target.com","Target"),("bestbuy.com","Best Buy"),("costco.com","Costco"),("nike.com","Nike"),("apple.com","Apple"),("pokemoncenter.com","Pokémon Center"),("shopifyemail.com","Shopify")]:
            self.assertEqual(parse_email(email(sender="orders@"+domain)).merchant, name)

    def test_promotional_mail_does_not_create_order(self):
        self.assertIsNone(parse_email(email("Save on your next order", "Order now to save 20%. Tracking your favorites? Shop our sale!")))

    def test_structured_email_with_multiple_products(self):
        data = {"@type":"Order", "orderNumber":"ABC-2345", "price":"100.00", "priceCurrency":"USD", "acceptedOffer":[{"itemOffered":{"name":"Deck one"},"eligibleQuantity":{"value":2}},{"itemOffered":{"name":"Deck two"}}]}
        p = parse_email(email("Thanks for shopping", '<script type="application/ld+json">'+json.dumps(data)+'</script>', html=True))
        self.assertEqual(len(p.items), 2)
        self.assertEqual(p.items[0]["quantity"], 2)
        self.assertEqual(p.total, Decimal("100.00"))

    def test_jsonld_without_order_number_is_reviewable(self):
        raw = email("Thanks", '<script type="application/ld+json">{"@type":"Order","price":"5"}</script>', html=True)
        order = ingest(self.inbox, raw, "no-number")
        self.assertTrue(order.needs_review)

    def test_delivered_subject_and_numeric_carrier_ambiguity(self):
        p = parse_email(email("Delivered: Order #ABCD1234", "Tracking number: 123456789012\nItem: Deck\nTotal: $13.00"))
        self.assertEqual(p.status, "delivered")
        self.assertEqual(p.tracking[0], ("123456789012", ""))
        self.assertEqual(infer_carrier("1Z879E930346834440"), "UPS")

    def test_duplicate_messages_and_shipping_updates_merge(self):
        initial = ingest(self.inbox, email(), "m1")
        self.assertIsNone(ingest(self.inbox, email(), "m1"))
        raw = email("Your order #123-1234567-1234567 has shipped", "Tracking number: 1Z879E930346834440")
        updated = ingest(self.inbox, raw, "m2")
        self.assertEqual(initial.pk, updated.pk)
        self.assertEqual(updated.shipments.count(), 1)
        updated.refresh_from_db()
        self.assertEqual(updated.status, "shipped")
        self.assertEqual(updated.total, Decimal("21.49"))

    def test_same_order_number_in_different_inboxes_stays_separate(self):
        other = Inbox.objects.create(user=self.friend, provider="icloud", email="friend@icloud.com", secret=seal({"password":"x"}))
        a = ingest(self.inbox, email(), "m1")
        b = ingest(other, email(), "m1")
        self.assertNotEqual(a.pk, b.pk)
        self.assertEqual(b.user_id, self.friend.pk)

    def test_multiple_shipments_and_out_of_order_updates(self):
        now = timezone.now()
        s1 = Shipment.objects.create(order=self.order, tracking_number="A123456789", status="in_transit", status_at=now)
        Shipment.objects.create(order=self.order, tracking_number="B123456789", status="shipped", status_at=now)
        set_shipment_status(s1, "delivered", now+timedelta(minutes=1), "Delivered")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "in_transit")
        set_shipment_status(s1, "shipped", now-timedelta(days=1), "Older event")
        s1.refresh_from_db()
        self.assertEqual(s1.status, "delivered")
        s2 = self.order.shipments.exclude(pk=s1.pk).get()
        set_shipment_status(s2, "delivered", now+timedelta(minutes=2), "Delivered")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "delivered")

    def test_imap_readonly_peek_and_uid_cursor(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.select.return_value = ("OK", [b"1"])
        conn.response.return_value = ("UIDVALIDITY", [b"456"])
        def uid(command, *args):
            if command == "search": return "OK", [b"7"]
            if args[-1] == "(RFC822.SIZE)": return "OK", [b"1 (UID 7 RFC822.SIZE 800)"]
            return "OK", [(b"1 (UID 7 BODY[])", email())]
        conn.uid.side_effect = uid
        with patch("tracker.mail.imap_host", return_value="imap.mail.me.com"), patch("tracker.mail.imaplib.IMAP4_SSL", return_value=conn):
            self.assertEqual(sync_imap(self.inbox), 1)
        conn.select.assert_called_once_with('"INBOX"', readonly=True)
        self.assertTrue(any(c.args[-1] == "(BODY.PEEK[])" for c in conn.uid.call_args_list))
        self.inbox.refresh_from_db()
        self.assertEqual(self.inbox.cursor["uid"], 7)

    def test_failed_sync_preserves_cursor_and_redacts_error(self):
        self.inbox.cursor = {"uid": 12, "validity":"456"}
        self.inbox.save()
        with patch("tracker.mail.sync_imap", side_effect=ValueError("sensitive token secret")):
            sync_inbox(self.inbox)
        self.assertEqual(self.inbox.cursor["uid"], 12)
        self.assertNotIn("secret", self.inbox.error)
        self.assertGreater(self.inbox.next_sync, timezone.now())

    def test_gmail_pagination_cursor_advances_only_after_page(self):
        self.inbox.provider = "gmail"
        first, second = MagicMock(), MagicMock()
        first.json.return_value = {"messages":[], "nextPageToken":"page2"}
        second.json.return_value = {"messages":[]}
        with patch("tracker.mail.access_token", return_value="fake"), patch("tracker.mail.requests.get", side_effect=[first, second]) as get:
            sync_gmail(self.inbox)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["pageToken"], "page2")
        self.inbox.refresh_from_db()
        self.assertIn("after", self.inbox.cursor)
        self.assertNotIn("page_token", self.inbox.cursor)

    def test_graph_pagination_rejects_offsite_url(self):
        with self.assertRaises(ValueError): graph_next("https://evil.example/steal")
        self.assertIn("Mail.Read", oauth_config("outlook")["scope"])
        self.assertNotIn("Mail.ReadBasic", oauth_config("outlook")["scope"])

    def test_bad_oauth_state_rejected(self):
        session = self.client.session
        session["oauth"] = {"state":"correct", "provider":"gmail", "user":self.user.pk, "created":timezone.now().timestamp()}
        session.save()
        self.assertEqual(self.client.get("/oauth/gmail/callback/?state=wrong&code=bad").status_code, 400)

    def test_oauth_needs_post(self):
        self.assertEqual(self.client.get("/oauth/gmail/start/").status_code, 405)

    @override_settings(EASYPOST_API_KEY="test-key")
    def test_carrier_adapter_creates_tracker_and_events(self):
        shipment = Shipment.objects.create(order=self.order, tracking_number="1Z879E930346834440", carrier="UPS", status_at=timezone.now()-timedelta(days=3))
        response = MagicMock()
        response.json.return_value = {"id":"trk_test", "carrier":"UPS", "status":"delivered", "updated_at":timezone.now().isoformat(), "est_delivery_date":"2026-11-19", "tracking_details":[{"status":"delivered", "datetime":timezone.now().isoformat(), "message":"Delivered", "tracking_location":{"city":"San Diego"}}]}
        with patch("tracker.integrations.requests.post", return_value=response) as post:
            refresh_tracking(shipment)
        self.assertEqual(post.call_args.kwargs["json"]["tracker"]["tracking_code"], shipment.tracking_number)
        shipment.refresh_from_db()
        self.assertEqual(shipment.status, "delivered")
        self.assertEqual(shipment.provider_id, "trk_test")

    def test_inventory_profit_and_overselling(self):
        item = Inventory.objects.create(user=self.user, name="Deck", quantity=2, unit_cost=Decimal("13"))
        r = self.client.post(f"/inventory/{item.pk}/sell/", {"quantity":2, "revenue":"46.00", "fees":"2.00", "shipping":"3.00", "sold_at":"2026-11-17", "notes":""})
        self.assertEqual(r.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.in_hand, 0)
        self.assertEqual(item.profit, Decimal("15.00"))
        r = self.client.post(f"/inventory/{item.pk}/sell/", {"quantity":1, "revenue":"23", "fees":"0", "shipping":"0", "sold_at":"2026-11-17"})
        self.assertContains(r, "many units")
        self.assertEqual(item.sales.count(), 1)

    def test_inventory_cannot_link_friends_order(self):
        self.client.force_login(self.friend)
        r = self.client.post("/inventory/new/", {"name":"Deck", "quantity":1, "unit_cost":"13", "currency":"USD", "order":self.order.pk})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Inventory.objects.filter(user=self.friend).exists())

    def test_csv_formula_injection_is_neutralized(self):
        self.order.title = '=HYPERLINK("https://example.com")'
        self.order.save()
        self.assertIn("'=HYPERLINK", self.client.get("/export/").content.decode())

    def test_template_escapes_email_title(self):
        self.order.title = '<script>alert("x")</script>'
        self.order.save()
        r = self.client.get(f"/orders/{self.order.pk}/")
        self.assertContains(r, "&lt;script&gt;")
        self.assertNotContains(r, '<script>alert("x")</script>')

    def test_disconnect_keeps_orders_but_removes_credentials(self):
        self.order.inbox = self.inbox
        self.order.save()
        self.client.post(f"/inboxes/{self.inbox.pk}/disconnect/")
        self.order.refresh_from_db()
        self.assertIsNone(self.order.inbox)
        self.assertFalse(Inbox.objects.filter(pk=self.inbox.pk).exists())

    def test_invitation_is_single_use(self):
        token = secrets.token_urlsafe(32)
        invite = Invite.objects.create(email="new@example.com", digest=digest(token), expires_at=timezone.now()+timedelta(days=1))
        self.client.logout()
        r = self.client.post(f"/join/{token}/", {"first_name":"New", "password1":PASSWORD, "password2":PASSWORD})
        self.assertEqual(r.status_code, 302)
        invite.refresh_from_db()
        self.assertTrue(invite.used)
        self.assertEqual(self.client.get(f"/join/{token}/").status_code, 404)

    def test_member_cannot_issue_invites(self):
        self.client.force_login(self.friend)
        self.client.post("/settings/", {"action":"invite", "email":"third@example.com"})
        self.assertEqual(Invite.objects.count(), 0)

    def test_account_cap(self):
        for i in range(8): User.objects.create(username=f"user-{i}")
        token = secrets.token_urlsafe(32)
        Invite.objects.create(email="eleven@example.com", digest=digest(token), expires_at=timezone.now()+timedelta(days=1))
        self.client.logout()
        self.client.post(f"/join/{token}/", {"first_name":"Eleven", "password1":PASSWORD, "password2":PASSWORD})
        self.assertEqual(User.objects.count(), 10)

    def test_delete_requires_password_and_cascades(self):
        self.client.post("/settings/", {"action":"delete", "confirm":"DELETE", "password":"wrong"})
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.client.post("/settings/", {"action":"delete", "confirm":"DELETE", "password":PASSWORD})
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Order.objects.filter(pk=self.order.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.friend.pk).exists())

    def test_notify_and_ssrf_validation(self):
        for url in ["http://127.0.0.1/", "https://discord.com.evil.example/api/webhooks/123/abc", "https://discord.com/api/webhooks/123/abc?redirect=bad"]:
            with self.assertRaises(ValueError): discord_url(url)
        with self.assertRaises(ValueError): push_endpoint("https://127.0.0.1/push")
        r = self.client.post("/push/subscribe/", json.dumps({"endpoint":"https://example.com/private", "keys":{"auth":"a","p256dh":"b"}}), content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_notification_retry_is_persistent(self):
        Profile.objects.create(user=self.user, notify=True, discord_secret=seal({"url":"https://discord.com/api/webhooks/123/abc"}))
        notification = Notification.objects.create(user=self.user, order=self.order, title="Delivered", body="Deck")
        delivery = Delivery.objects.create(notification=notification, channel="discord")
        with patch("tracker.integrations.requests.post", side_effect=ValueError("fail")):
            send_pending()
        delivery.refresh_from_db()
        self.assertEqual(delivery.attempts, 1)
        self.assertIsNone(delivery.sent_at)
        self.assertGreater(delivery.next_attempt, timezone.now())

    def test_login_rate_limiting(self):
        self.client.logout()
        for _ in range(10): self.client.post("/login/", {"username":"me@example.com", "password":"wrong"})
        r = self.client.post("/login/", {"username":"me@example.com", "password":PASSWORD})
        self.assertContains(r, "Too many attempts")

    def test_private_response_headers(self):
        r = self.client.get("/")
        self.assertIn("no-store", r["Cache-Control"])
        self.assertIn("script-src 'self'", r["Content-Security-Policy"])

    def test_currency_is_not_combined_in_insights(self):
        Order.objects.create(user=self.user, identity="eur", merchant="Shop", title="Item", total=Decimal("10"), currency="EUR")
        r = self.client.get("/insights/")
        self.assertContains(r, "EUR")
        self.assertContains(r, "USD")
        totals = {t["currency"]: t["total"] for t in r.context["totals"]}
        self.assertEqual(totals, {"EUR":Decimal("10"), "USD":Decimal("13")})

    def test_more_than_five_inboxes_are_allowed(self):
        with patch("tracker.forms.imap_host", return_value="imap.mail.me.com"):
            for i in range(6):
                response = self.client.post("/inboxes/", {"provider":"icloud", "email":f"mail{i}@icloud.com", "password":"synthetic-app-password", "folder":"INBOX"})
                self.assertEqual(response.status_code, 302)
        self.assertEqual(Inbox.objects.filter(user=self.user).count(), 7)

    def test_active_filter_matches_active_summary(self):
        Order.objects.create(user=self.user, identity="delivered", merchant="Shop", title="Completed", status="delivered")
        self.assertNotContains(self.client.get("/?active=1"), "Completed")
        self.assertContains(self.client.get("/?active=1"), "Starter deck")

    def test_all_returned_shipments_roll_up(self):
        now = timezone.now()
        shipment = Shipment.objects.create(order=self.order, tracking_number="RETURN12345", status_at=now)
        set_shipment_status(shipment, "returned", now+timedelta(minutes=1), "Returned to sender")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "returned")

    def test_add_inventory_from_order_prefills_cost(self):
        self.order.total = Decimal("26.00")
        self.order.items = [{"name":"Deck", "quantity":2}]
        self.order.save()
        response = self.client.get(f"/inventory/new/?order={self.order.pk}")
        form = response.context["form"]
        self.assertEqual(form.instance.quantity, 2)
        self.assertEqual(form.instance.unit_cost, Decimal("13.00"))
        self.assertEqual(form.instance.order_id, self.order.pk)

    def test_inventory_prefill_cannot_read_another_user(self):
        self.client.force_login(self.friend)
        self.assertEqual(self.client.get(f"/inventory/new/?order={self.order.pk}").status_code, 404)
