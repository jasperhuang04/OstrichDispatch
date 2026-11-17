import copy
import json
from datetime import timedelta
from urllib.parse import quote
import requests
from dateutil.parser import isoparse
from django.conf import settings
from django.utils import timezone
from pywebpush import webpush, WebPushException
from .models import Profile, Delivery, PushSubscription
from .security import unseal, discord_url, push_endpoint
from .services import set_shipment_status
from .parser import safe_date

CARRIER_LINKS = {"UPS": "https://www.ups.com/track?tracknum=", "USPS": "https://tools.usps.com/go/TrackConfirmAction?tLabels=", "FedEx": "https://www.fedex.com/fedextrack/?trknbr=", "DHL": "https://www.dhl.com/us-en/home/tracking.html?tracking-id=", "OnTrac": "https://www.ontrac.com/tracking/?number=", "Amazon": "https://track.amazon.com/tracking/"}

def tracking_url(shipment):
    prefix = CARRIER_LINKS.get(shipment.carrier)
    return prefix + quote(shipment.tracking_number, safe="") if prefix else ""

def refresh_tracking(shipment):
    if not settings.EASYPOST_API_KEY:
        return
    base = "https://api.easypost.com/v2/trackers"
    try:
        if shipment.provider_id:
            response = requests.get(f"{base}/{quote(shipment.provider_id, safe='')}", auth=(settings.EASYPOST_API_KEY, ""), timeout=30)
        else:
            body = {"tracking_code": shipment.tracking_number}
            if shipment.carrier and shipment.carrier != "Amazon":
                body["carrier"] = shipment.carrier
            response = requests.post(base, json={"tracker": body}, auth=(settings.EASYPOST_API_KEY, ""), timeout=30)
        response.raise_for_status()
        data = response.json()
        shipment.provider_id = data["id"]
        shipment.carrier = data.get("carrier", shipment.carrier)
        shipment.eta = safe_date(data.get("est_delivery_date"))
        shipment.error = ""
        shipment.save(update_fields=["provider_id", "carrier", "eta", "error"])
        mapping = {"pre_transit": "shipped", "in_transit": "in_transit", "out_for_delivery": "out_for_delivery", "available_for_pickup": "out_for_delivery", "delivered": "delivered", "return_to_sender": "returned", "failure": "exception", "cancelled": "cancelled", "error": "exception"}
        for event in sorted(data.get("tracking_details", []), key=lambda e: e.get("datetime", "")):
            at = isoparse(event["datetime"])
            if timezone.is_naive(at):
                at = timezone.make_aware(at)
            location = event.get("tracking_location") or {}
            set_shipment_status(shipment, mapping.get(event.get("status"), "in_transit"), at, event.get("message", "Carrier update"), ", ".join(str(location[k]) for k in ("city", "state", "country") if location.get(k)), source="carrier")
        current = mapping.get(data.get("status"))
        if current:
            at = isoparse(data.get("updated_at", timezone.now().isoformat()))
            set_shipment_status(shipment, current, at, "Carrier status", source="carrier")
        if shipment.eta:
            shipment.order.eta = shipment.eta
            shipment.order.save(update_fields=["eta", "updated_at"])
    except (requests.RequestException, ValueError, KeyError, TypeError):
        shipment.error = "Carrier lookup failed. Check the number, carrier and EasyPost account."
    shipment.last_check = timezone.now()
    shipment.save(update_fields=["last_check", "error"])

def send_pending():
    for delivery in Delivery.objects.filter(sent_at__isnull=True, attempts__lt=5, next_attempt__lte=timezone.now()).select_related("notification__user", "subscription")[:100]:
        delivery.attempts += 1
        n = delivery.notification
        profile, _ = Profile.objects.get_or_create(user=n.user)
        try:
            if not profile.notify:
                delivery.sent_at = timezone.now()
            elif delivery.channel == "discord":
                if profile.discord_secret:
                    url = discord_url(unseal(profile.discord_secret)["url"])
                    r = requests.post(url, json={"content": f"**{n.title}**\n{n.body}", "allowed_mentions": {"parse": []}}, timeout=20, allow_redirects=False)
                    if not 200 <= r.status_code < 300:
                        raise ValueError("Discord failed")
                delivery.sent_at = timezone.now()
            elif delivery.channel == "push" and delivery.subscription:
                if not settings.VAPID_PRIVATE_KEY:
                    raise ValueError("Push is not configured")
                subscription = unseal(delivery.subscription.secret)
                push_endpoint(subscription["endpoint"])
                webpush(subscription_info=subscription, data=json.dumps({"title": n.title, "body": n.body, "url": f"/orders/{n.order_id}/"}), vapid_private_key=settings.VAPID_PRIVATE_KEY, vapid_claims={"sub": settings.VAPID_SUBJECT}, timeout=15)
                delivery.sent_at = timezone.now()
            delivery.error = ""
        except WebPushException as exc:
            if exc.response is not None and exc.response.status_code in (404, 410):
                delivery.subscription.delete()
                continue
            delivery.error = "Push delivery failed; will retry."
        except (requests.RequestException, ValueError, KeyError):
            delivery.error = "Notification delivery failed; check integration settings."
        delivery.next_attempt = timezone.now() + timedelta(minutes=2 ** delivery.attempts)
        delivery.save()
