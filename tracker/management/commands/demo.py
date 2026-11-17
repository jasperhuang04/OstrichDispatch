import os
from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from tracker.models import Order, Shipment, Inventory, Sale, digest

class Command(BaseCommand):
    help = "Add synthetic demo data in DEBUG mode. Requires DEMO_PASSWORD."
    def handle(self, *args, **options):
        if not settings.DEBUG or not os.getenv("DEMO_PASSWORD"):
            raise CommandError("Demo is local-only. Set DEBUG=true and DEMO_PASSWORD explicitly.")
        user, created = User.objects.get_or_create(username="demo@example.com", defaults={"email": "demo@example.com", "first_name": "Jasper", "is_staff": True})
        if created:
            user.set_password(os.environ["DEMO_PASSWORD"])
            user.save()
        examples = [("Pokémon Center", "Pokémon TCG · Elite Trainer Box", "59.99", "in_transit", 2), ("Target", "One Piece · Starter Deck ST-31", "25.98", "out_for_delivery", 0), ("Amazon", "ESP32 development board · 3 pack", "21.49", "delivered", -1), ("Best Buy", "Logitech MX Anywhere 3S", "79.99", "shipped", 4), ("Nike", "Everyday cushioned crew socks", "24.00", "delivered", -3), ("Costco", "Kirkland Signature · Storage boxes", "34.99", "ordered", 7)]
        for i, (store, title, cost, status, offset) in enumerate(examples):
            o, _ = Order.objects.get_or_create(user=user, identity=digest(f"demo-{i}"), defaults={"merchant": store, "title": title, "number": f"DEMO-{1000+i}", "items": [{"name": title, "quantity": 1}], "total": Decimal(cost), "status": status, "eta": timezone.localdate()+timedelta(days=offset), "ordered_at": timezone.now()-timedelta(days=i+1), "address": "Sample destination · San Diego, CA", "notes": "Synthetic demo data; this is not a real order."})
            if status != "ordered":
                Shipment.objects.get_or_create(order=o, tracking_number=f"DEMO{i:010}", defaults={"carrier": "UPS", "status": status, "eta": o.eta})
        item, _ = Inventory.objects.get_or_create(user=user, name="One Piece ST-31 · Demo", defaults={"quantity": 6, "unit_cost": Decimal("13.00")})
        if not item.sales.exists():
            Sale.objects.create(inventory=item, quantity=2, revenue=Decimal("46.00"), fees=Decimal("2.00"))
        self.stdout.write("Synthetic demo orders added. Sign in as demo@example.com.")
