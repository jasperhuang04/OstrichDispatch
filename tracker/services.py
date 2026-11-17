import hashlib
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .models import Order, Shipment, TrackingEvent, Notification, Delivery, Profile, PushSubscription, ProcessedMessage, digest
from .parser import parse_email

RANK = {"ordered": 0, "shipped": 1, "in_transit": 2, "out_for_delivery": 3, "delivered": 4}

def notify(order, title, body):
    profile, _ = Profile.objects.get_or_create(user=order.user)
    n = Notification.objects.create(user=order.user, order=order, title=title[:180], body=body[:300])
    if profile.notify:
        if profile.discord_secret:
            Delivery.objects.create(notification=n, channel="discord")
        for sub in PushSubscription.objects.filter(user=order.user):
            Delivery.objects.create(notification=n, channel="push", subscription=sub)

def rollup(order, at):
    order.refresh_from_db()
    states = list(order.shipments.values_list("status", flat=True))
    if not states or order.status in ("cancelled", "returned"):
        return
    if all(s == "delivered" for s in states):
        status = "delivered"
    elif all(s == "returned" for s in states):
        status = "returned"
    elif all(s == "cancelled" for s in states):
        status = "cancelled"
    elif "exception" in states:
        status = "exception"
    elif "out_for_delivery" in states:
        status = "out_for_delivery"
    elif any(s in ("in_transit", "delivered") for s in states):
        status = "in_transit"
    else:
        status = "shipped"
    if order.status != status:
        order.status, order.status_at = status, at
        order.save(update_fields=["status", "status_at", "updated_at"])
        notify(order, f"{order.merchant}: {status.replace('_', ' ')}", order.title)

def set_shipment_status(shipment, status, at, message, location="", source="email"):
    TrackingEvent.objects.get_or_create(shipment=shipment, key=digest(f"{source}|{at.isoformat()}|{status}|{message}"), defaults={"status": status, "at": at, "message": message[:300], "location": location[:200]})
    if at >= shipment.status_at and not (shipment.status == "delivered" and status in RANK and status != "delivered"):
        shipment.status, shipment.status_at = status, at
        shipment.save(update_fields=["status", "status_at"])
    rollup(shipment.order, at)

@transaction.atomic
def ingest(inbox, raw, external_id, received_at=None):
    key = digest(external_id)
    if ProcessedMessage.objects.filter(inbox=inbox, key=key).exists():
        return None
    parsed = parse_email(raw, received_at)
    if parsed is None:
        ProcessedMessage.objects.create(inbox=inbox, key=key)
        return None
    reference = parsed.number or (parsed.tracking[0][0] if parsed.tracking else external_id)
    identity = digest(f"{inbox.pk}|{parsed.merchant.lower()}|{reference}")
    order = Order.objects.filter(user=inbox.user, identity=identity).first()
    if not order and parsed.tracking:
        # A carrier email may omit the retailer's order ID.
        candidates = Order.objects.filter(user=inbox.user, inbox=inbox, shipments__tracking_number__in=[n for n, _ in parsed.tracking], ordered_at__gte=parsed.at-timedelta(days=180)).distinct()
        if candidates.count() == 1:
            order = candidates.first()
    created = order is None
    if created:
        order = Order.objects.create(user=inbox.user, inbox=inbox, identity=identity, merchant=parsed.merchant, number=parsed.number, title=parsed.title, items=parsed.items, total=parsed.total, currency=parsed.currency, address=parsed.address, status=parsed.status, status_at=parsed.at, ordered_at=parsed.at, eta=parsed.eta, needs_review=parsed.review)
        notify(order, f"New {order.merchant} order", order.title)
    else:
        if parsed.items and not order.items:
            order.items, order.title = parsed.items, parsed.title
        if parsed.total is not None and order.total is None:
            order.total, order.currency = parsed.total, parsed.currency
        if parsed.address and not order.address:
            order.address = parsed.address
        if parsed.eta and parsed.at >= order.status_at:
            order.eta = parsed.eta
        if not order.number and parsed.number:
            order.number = parsed.number
        if parsed.status == "ordered":
            order.ordered_at = min(order.ordered_at, parsed.at)
        if parsed.status in ("cancelled", "returned") and parsed.at >= order.status_at:
            order.status, order.status_at = parsed.status, parsed.at
            notify(order, f"{order.merchant}: {parsed.status}", order.title)
        elif not parsed.tracking and not order.shipments.exists() and parsed.at >= order.status_at and RANK.get(parsed.status, -1) > RANK.get(order.status, -1):
            order.status, order.status_at = parsed.status, parsed.at
            notify(order, f"{order.merchant}: {parsed.status.replace('_', ' ')}", order.title)
        order.needs_review = not bool(order.number and order.items and order.total is not None)
        order.save()
    for number, carrier in parsed.tracking:
        shipment, new = Shipment.objects.get_or_create(order=order, tracking_number=number[:80], defaults={"carrier": carrier[:60], "status_at": parsed.at, "eta": parsed.eta})
        status = parsed.status if parsed.status != "ordered" else "shipped"
        set_shipment_status(shipment, status, parsed.at, "Detected in an email")
        if parsed.eta and parsed.at >= shipment.status_at:
            shipment.eta = parsed.eta
        if not shipment.carrier:
            shipment.carrier = carrier[:60]
        shipment.save()
    ProcessedMessage.objects.create(inbox=inbox, key=key)
    return order
