import time
from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone
from tracker.models import Inbox, Shipment, Lease
from tracker.mail import sync_inbox
from tracker.integrations import refresh_tracking, send_pending

class Command(BaseCommand):
    help = "Run one background worker; use --once for a scheduler."
    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
    def handle(self, *args, **options):
        while True:
            close_old_connections()
            Lease.objects.get_or_create(name="worker")
            claimed = Lease.objects.filter(name="worker", expires_at__lte=timezone.now()).update(expires_at=timezone.now()+timedelta(hours=2))
            if claimed:
                try:
                    for inbox in Inbox.objects.filter(enabled=True, next_sync__lte=timezone.now()).order_by("next_sync"):
                        sync_inbox(inbox)
                    if settings.EASYPOST_API_KEY:
                        for shipment in Shipment.objects.exclude(status__in=["delivered", "cancelled", "returned"]).filter(Q(last_check__isnull=True) | Q(last_check__lte=timezone.now()-timedelta(seconds=settings.TRACK_INTERVAL)))[:100]:
                            refresh_tracking(shipment)
                    send_pending()
                except Exception as exc:
                    # Do not log exception messages or email bodies.
                    self.stderr.write(f"Worker cycle failed ({type(exc).__name__}); retrying next cycle.")
                finally:
                    Lease.objects.filter(name="worker").update(expires_at=timezone.now())
            if options["once"]:
                return
            time.sleep(15)
