import base64
import calendar
import csv
import hashlib
import io
import json
import secrets
import time
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode
import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction, IntegrityError
from django.db.models import Q, Sum, Count, F
from django.db.models.functions import TruncMonth
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET
from .models import Inbox, Order, Shipment, Inventory, Sale, Notification, Profile, Invite, PushSubscription, LoginAttempt, Lease, Delivery, digest, STATUSES
from .forms import RegisterForm, InboxForm, OrderForm, InventoryForm, SaleForm
from .security import seal, unseal, discord_url, push_endpoint
from .parser import infer_carrier
from .mail import oauth_config
from .integrations import tracking_url
from .services import ingest

def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    error = ""
    if request.method == "POST":
        # Hash both dimensions separately so rotating usernames cannot avoid IP throttling.
        keys = [digest("ip:" + request.META.get("REMOTE_ADDR", "")), digest("user:" + request.POST.get("username", "").lower())]
        rows = []
        for key in keys:
            row, _ = LoginAttempt.objects.get_or_create(key=key, defaults={"expires_at": timezone.now()+timedelta(minutes=15)})
            if row.expires_at <= timezone.now():
                LoginAttempt.objects.filter(pk=key).update(count=0, expires_at=timezone.now()+timedelta(minutes=15))
                row.refresh_from_db()
            rows.append(row)
        if any(r.count >= 10 for r in rows):
            error = "Too many attempts. Try again in 15 minutes."
        else:
            user = authenticate(request, username=request.POST.get("username", "").strip().lower(), password=request.POST.get("password", ""))
            if user:
                LoginAttempt.objects.filter(key__in=keys).delete()
                login(request, user)
                return redirect("dashboard")
            LoginAttempt.objects.filter(key__in=keys).update(count=F("count")+1)
            error = "The email or password did not match."
    return render(request, "login.html", {"error": error})

@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")

def register(request, token):
    invitation = get_object_or_404(Invite, digest=digest(token), used=False, expires_at__gt=timezone.now())
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            # A shared row serializes invite redemptions on PostgreSQL.
            Lease.objects.get_or_create(name="signup")
            Lease.objects.select_for_update().get(name="signup")
            invitation = Invite.objects.select_for_update().get(pk=invitation.pk)
            if invitation.used or invitation.expires_at <= timezone.now() or User.objects.count() >= settings.MAX_USERS:
                form.add_error(None, "This invitation is unavailable or all ten accounts are in use.")
            elif User.objects.filter(username=invitation.email.lower()).exists():
                form.add_error(None, "This email already has an account.")
            else:
                user = form.save(commit=False)
                user.username = user.email = invitation.email.lower()
                user.save()
                invitation.used = True
                invitation.save(update_fields=["used"])
                login(request, user)
                return redirect("dashboard")
    return render(request, "form.html", {"form": form, "title": "Your private delivery desk", "subtitle": f"Create your account for {invitation.email}", "button": "Create account"})

def filtered_orders(request):
    orders = Order.objects.filter(user=request.user, archived=request.GET.get("archived") == "1").prefetch_related("shipments").select_related("inbox")
    if request.GET.get("active") == "1":
        orders = orders.exclude(status__in=["delivered", "cancelled", "returned"])
    if request.GET.get("q"):
        q = request.GET["q"][:200]
        orders = orders.filter(Q(title__icontains=q) | Q(number__icontains=q) | Q(merchant__icontains=q) | Q(shipments__tracking_number__icontains=q)).distinct()
    for key in ("status", "merchant", "address"):
        if request.GET.get(key):
            orders = orders.filter(**{key: request.GET[key]})
    if request.GET.get("inbox", "").isdigit():
        orders = orders.filter(inbox_id=int(request.GET["inbox"]))
    if request.GET.get("review") == "1":
        orders = orders.filter(needs_review=True)
    for param, lookup in [("from", "ordered_at__date__gte"), ("to", "ordered_at__date__lte")]:
        try:
            if request.GET.get(param):
                orders = orders.filter(**{lookup: date.fromisoformat(request.GET[param])})
        except ValueError:
            pass
    return orders

@login_required
def dashboard(request):
    from django.core.paginator import Paginator
    all_orders = Order.objects.filter(user=request.user, archived=False)
    orders = filtered_orders(request)
    today = timezone.localdate()
    stats = {"active": all_orders.exclude(status__in=["delivered", "cancelled", "returned"]).count(), "today": all_orders.filter(eta=today).exclude(status__in=["delivered", "cancelled", "returned"]).count(), "delivered": all_orders.filter(status="delivered").count(), "review": all_orders.filter(needs_review=True).count()}
    return render(request, "dashboard.html", {"page": Paginator(orders, 24).get_page(request.GET.get("page")), "stats": stats, "inboxes": Inbox.objects.filter(user=request.user), "statuses": STATUSES, "today": today, "filters": request.GET.urlencode()})

@login_required
def order_form(request, pk=None):
    order = get_object_or_404(Order, pk=pk, user=request.user) if pk else Order(user=request.user, identity=digest(secrets.token_hex(24)))
    form = OrderForm(request.POST or None, instance=order)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            order = form.save(commit=False)
            order.status_at = timezone.now()
            order.save()
            n = form.cleaned_data["tracking_number"]
            if n:
                Shipment.objects.get_or_create(order=order, tracking_number=n, defaults={"carrier": form.cleaned_data["carrier"] or infer_carrier(n), "eta": order.eta})
        messages.success(request, "Order saved.")
        return redirect("order_detail", pk=order.pk)
    return render(request, "form.html", {"form": form, "title": "Edit order" if pk else "Add an order", "subtitle": "Keep the details that matter, all in one place.", "button": "Save order"})

@login_required
def order_detail(request, pk):
    order = get_object_or_404(Order.objects.prefetch_related("shipments__events"), user=request.user, pk=pk)
    shipments = list(order.shipments.all())
    for shipment in shipments:
        shipment.link = tracking_url(shipment)
    return render(request, "detail.html", {"order": order, "shipments": shipments, "live_tracking": bool(settings.EASYPOST_API_KEY)})

@login_required
@require_POST
def order_archive(request, pk):
    order = get_object_or_404(Order, user=request.user, pk=pk)
    order.archived = not order.archived
    order.save(update_fields=["archived"])
    return redirect("dashboard")

@login_required
def inboxes(request):
    form = InboxForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        inbox = form.save(commit=False)
        inbox.user, inbox.secret = request.user, seal({"password": form.cleaned_data["password"]})
        try:
            inbox.save()
            messages.success(request, "Inbox added. The worker will check it shortly.")
            return redirect("inboxes")
        except IntegrityError:
            form.add_error("email", "That inbox is already connected.")
    return render(request, "inboxes.html", {"form": form, "inboxes": Inbox.objects.filter(user=request.user), "gmail_ready": bool(settings.GOOGLE_CLIENT_ID), "outlook_ready": bool(settings.MICROSOFT_CLIENT_ID)})

@login_required
@require_POST
def inbox_action(request, pk, action):
    inbox = get_object_or_404(Inbox, user=request.user, pk=pk)
    if action == "sync":
        inbox.next_sync = timezone.now()
        inbox.save(update_fields=["next_sync"])
        messages.success(request, "Sync queued. The background worker will pick it up.")
    elif action == "toggle":
        inbox.enabled = not inbox.enabled
        inbox.save(update_fields=["enabled"])
    elif action == "disconnect":
        inbox.delete()
        messages.success(request, "Inbox credentials removed. Existing orders are kept. You can also revoke access in your email provider's settings.")
    return redirect("inboxes")

@login_required
@require_POST
def import_email(request):
    inbox = get_object_or_404(Inbox, user=request.user, pk=request.POST.get("inbox"))
    upload = request.FILES.get("email")
    if not upload or upload.size > 2*1024*1024:
        messages.error(request, "Choose an .eml message smaller than 2 MB.")
        return redirect("inboxes")
    raw = upload.read()
    order = ingest(inbox, raw, "upload:" + hashlib.sha256(raw).hexdigest())
    messages.success(request, "Email imported." if order else "Already imported or no order details were detected.")
    return redirect("order_detail", pk=order.pk) if order else redirect("inboxes")

@login_required
def oauth_start(request, provider):
    if request.method != "POST":
        return HttpResponse(status=405)
    if provider not in ("gmail", "outlook"):
        return HttpResponseBadRequest()
    cfg = oauth_config(provider)
    if not cfg["id"] or not cfg["secret"]:
        messages.error(request, "The administrator needs to configure this provider first.")
        return redirect("inboxes")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    request.session["oauth"] = {"state": state, "verifier": verifier, "provider": provider, "created": time.time(), "user": request.user.pk}
    query = {"client_id": cfg["id"], "redirect_uri": settings.APP_URL + f"/oauth/{provider}/callback/", "response_type": "code", "scope": cfg["scope"], "state": state, "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(), "code_challenge_method": "S256", "prompt": "consent"}
    if provider == "gmail":
        query["access_type"] = "offline"
    return redirect(cfg["authorize"] + "?" + urlencode(query))

@login_required
def oauth_callback(request, provider):
    saved = request.session.pop("oauth", {})
    if not saved or saved.get("provider") != provider or saved.get("user") != request.user.pk or time.time()-saved.get("created", 0) > 600 or not secrets.compare_digest(saved.get("state", ""), request.GET.get("state", "")):
        return HttpResponseBadRequest("Connection expired. Start again from Connected inboxes.")
    if not request.GET.get("code"):
        messages.error(request, "Email connection was not authorized.")
        return redirect("inboxes")
    try:
        cfg = oauth_config(provider)
        r = requests.post(cfg["token"], data={"client_id": cfg["id"], "client_secret": cfg["secret"], "code": request.GET["code"], "code_verifier": saved["verifier"], "grant_type": "authorization_code", "redirect_uri": settings.APP_URL + f"/oauth/{provider}/callback/"}, timeout=30)
        r.raise_for_status()
        token = r.json()
        profile_url = "https://gmail.googleapis.com/gmail/v1/users/me/profile" if provider == "gmail" else "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName"
        r = requests.get(profile_url, headers={"Authorization": "Bearer " + token["access_token"]}, timeout=30)
        r.raise_for_status()
        profile = r.json()
        email = profile.get("emailAddress") if provider == "gmail" else profile.get("mail") or profile.get("userPrincipalName")
        if not email:
            raise ValueError("No mailbox address")
        existing = Inbox.objects.filter(user=request.user, provider=provider, email=email.lower()).first()
        refresh = token.get("refresh_token") or (unseal(existing.secret).get("refresh_token") if existing else None)
        if not refresh:
            raise ValueError("Offline access was not granted")
        Inbox.objects.update_or_create(user=request.user, provider=provider, email=email.lower(), defaults={"secret": seal({"access_token": token["access_token"], "refresh_token": refresh, "expires_at": time.time()+int(token.get("expires_in", 3600))}), "enabled": True, "error": "", "next_sync": timezone.now()})
        messages.success(request, "Inbox connected.")
    except (requests.RequestException, ValueError, KeyError):
        messages.error(request, "Could not connect. Check provider credentials, approved scopes, and callback URL.")
    return redirect("inboxes")

@login_required
def analytics(request):
    orders = filtered_orders(request)
    eligible = orders.exclude(status__in=["cancelled", "returned"])
    totals = eligible.values("currency").annotate(total=Sum("total"), count=Count("id"))
    merchants = eligible.values("merchant", "currency").annotate(total=Sum("total"), count=Count("id")).order_by("-total")
    monthly = eligible.annotate(month=TruncMonth("ordered_at")).values("month", "currency").annotate(total=Sum("total")).order_by("month", "currency")
    charts = []
    by_currency = defaultdict(list)
    for row in monthly:
        by_currency[row["currency"]].append(row)
    for currency, rows in by_currency.items():
        rows = rows[-12:]
        peak = max((r["total"] or Decimal(0) for r in rows), default=Decimal(1)) or Decimal(1)
        width = 600 / max(6, len(rows))
        bars = [{"x": round(40+i*width, 1), "label_x": round(40+i*width+width*.35, 1), "width": round(width*.7, 1), "y": round(180-float((r["total"] or 0)/peak)*140, 1), "height": round(float((r["total"] or 0)/peak)*140, 1), "label": r["month"].strftime("%b"), "value": str(r["total"] or 0)} for i, r in enumerate(rows)]
        charts.append({"currency": currency, "bars": bars})
    count = orders.count()
    cancelled = orders.filter(status="cancelled").count()
    products = defaultdict(int)
    for order in eligible:
        for item in order.items or [{"name": order.title, "quantity": 1}]:
            products[item.get("name", order.title)] += int(item.get("quantity", 1))
    return render(request, "analytics.html", {"totals": totals, "merchants": merchants, "monthly": monthly, "charts": charts, "stick_rate": round((count-cancelled)/count*100, 1) if count else None, "missing": eligible.filter(total__isnull=True).count(), "products": sorted(products.items(), key=lambda x: -x[1])[:10]})

@login_required
def calendar_view(request):
    today = timezone.localdate()
    try:
        month = date.fromisoformat(request.GET.get("month", today.strftime("%Y-%m")) + "-01")
    except ValueError:
        month = today.replace(day=1)
    dates = list(calendar.Calendar().itermonthdates(month.year, month.month))
    orders = Order.objects.filter(user=request.user, archived=False, eta__range=(dates[0], dates[-1])).exclude(status__in=["cancelled", "returned"])
    if request.GET.get("address"):
        orders = orders.filter(address=request.GET["address"])
    by_date = defaultdict(list)
    for order in orders:
        by_date[order.eta].append(order)
    cells = [{"date": d, "orders": by_date[d], "muted": d.month != month.month, "today": d == today} for d in dates]
    return render(request, "calendar.html", {"cells": cells, "month": month, "previous": (month-timedelta(days=1)).strftime("%Y-%m"), "next": (month+timedelta(days=32)).strftime("%Y-%m"), "addresses": Order.objects.filter(user=request.user).exclude(address="").values_list("address", flat=True).distinct(), "unscheduled": Order.objects.filter(user=request.user, eta__isnull=True, archived=False).exclude(status__in=["delivered", "cancelled", "returned"]).count()})

@login_required
def inventory_view(request):
    items = Inventory.objects.filter(user=request.user).prefetch_related("sales").order_by("name")
    if request.GET.get("q"):
        items = items.filter(name__icontains=request.GET["q"][:200])
    rows = list(items)
    filt = request.GET.get("filter")
    if filt == "in_hand": rows = [i for i in rows if i.in_hand > 0]
    if filt == "sold": rows = [i for i in rows if i.sold > 0]
    if filt == "profit": rows = [i for i in rows if i.profit > 0]
    if filt == "loss": rows = [i for i in rows if i.profit < 0]
    totals = defaultdict(lambda: {"cost": Decimal(0), "revenue": Decimal(0), "profit": Decimal(0), "sold_cost": Decimal(0)})
    for i in rows:
        t = totals[i.currency]
        t["cost"] += i.unit_cost*i.in_hand
        t["profit"] += i.profit
        t["sold_cost"] += i.unit_cost*i.sold
        t["revenue"] += sum((s.revenue for s in i.sales.all()), Decimal(0))
    for t in totals.values():
        t["roi"] = round(t["profit"]/t["sold_cost"]*100, 1) if t["sold_cost"] else None
    sorters = {"qty": lambda i:i.in_hand, "profit": lambda i:i.profit, "cost": lambda i:i.unit_cost}
    if request.GET.get("sort") in sorters:
        rows.sort(key=sorters[request.GET["sort"]], reverse=True)
    return render(request, "inventory.html", {"items": rows, "totals": dict(totals), "sales": Sale.objects.filter(inventory__user=request.user).select_related("inventory").order_by("-sold_at", "-pk")[:100]})

@login_required
def inventory_form(request, pk=None):
    item = get_object_or_404(Inventory, pk=pk, user=request.user) if pk else Inventory(user=request.user)
    if not pk and request.method == "GET" and request.GET.get("order", "").isdigit():
        order = get_object_or_404(Order, pk=request.GET["order"], user=request.user)
        item.order, item.name, item.currency = order, order.title, order.currency
        if len(order.items) <= 1:
            item.quantity = max(1, int(order.items[0].get("quantity", 1))) if order.items else 1
            item.unit_cost = (order.total / item.quantity).quantize(Decimal(".01")) if order.total is not None else Decimal(0)
    form = InventoryForm(request.POST or None, instance=item, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("inventory")
    return render(request, "form.html", {"form": form, "title": "Edit inventory" if pk else "Add inventory", "subtitle": "Track your cost, units on hand, and resale profit.", "button": "Save item"})

@login_required
def sale_form(request, pk):
    item = get_object_or_404(Inventory, pk=pk, user=request.user)
    form = SaleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            locked = Inventory.objects.select_for_update().get(pk=item.pk, user=request.user)
            if form.cleaned_data["quantity"] > locked.in_hand:
                form.add_error("quantity", "You don't have that many units on hand.")
            else:
                sale = form.save(commit=False)
                sale.inventory = locked
                sale.save()
                messages.success(request, "Sale recorded.")
                return redirect("inventory")
    return render(request, "form.html", {"form": form, "title": "Record a sale", "subtitle": f"{item.name} · {item.in_hand} on hand · {item.currency}", "button": "Save sale"})

@login_required
@require_POST
def sale_delete(request, pk):
    sale = get_object_or_404(Sale, pk=pk, inventory__user=request.user)
    sale.delete()
    messages.success(request, "Sale removed; units returned to inventory.")
    return redirect("inventory")

def csv_cell(value):
    v = "" if value is None else str(value)
    return "'"+v if v.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) or v.startswith(("\t", "\r", "\n")) else v

@login_required
def export_csv(request):
    kind = request.GET.get("kind", "orders")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="parcelnest-{kind if kind in ("orders", "inventory", "sales") else "orders"}.csv"'
    writer = csv.writer(response)
    if kind == "inventory":
        writer.writerow(["Name", "Purchased", "Sold", "In hand", "Unit cost", "Currency", "Profit"])
        rows = ([i.name, i.quantity, i.sold, i.in_hand, i.unit_cost, i.currency, i.profit] for i in Inventory.objects.filter(user=request.user).prefetch_related("sales"))
    elif kind == "sales":
        writer.writerow(["Item", "Date", "Quantity", "Revenue", "Fees", "Shipping", "Currency"])
        rows = ([s.inventory.name, s.sold_at, s.quantity, s.revenue, s.fees, s.shipping, s.inventory.currency] for s in Sale.objects.filter(inventory__user=request.user).select_related("inventory"))
    else:
        writer.writerow(["Store", "Order", "Title", "Total", "Currency", "Status", "Order date", "ETA", "Inbox", "Address", "Tracking numbers"])
        rows = ([o.merchant, o.number, o.title, o.total, o.currency, o.status, o.ordered_at.isoformat(), o.eta, o.inbox.email if o.inbox else "", o.address, "; ".join(s.tracking_number for s in o.shipments.all())] for o in filtered_orders(request))
    for row in rows:
        writer.writerow([csv_cell(v) for v in row])
    return response

@login_required
def notifications(request):
    if request.method == "POST":
        Notification.objects.filter(user=request.user, read=False).update(read=True)
        return redirect("notifications")
    return render(request, "notifications.html", {"notifications": Notification.objects.filter(user=request.user).select_related("order").order_by("-created_at")[:100]})

@login_required
def settings_view(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    invite_url = ""
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "preferences":
            try:
                webhook = request.POST.get("webhook", "").strip()
                if webhook:
                    profile.discord_secret = seal({"url": discord_url(webhook)})
                if request.POST.get("clear_webhook"):
                    profile.discord_secret = ""
                profile.notify = request.POST.get("notify") == "on"
                profile.save()
                messages.success(request, "Notification settings saved.")
            except ValueError:
                messages.error(request, "Use a valid Discord webhook URL.")
        elif action == "invite" and request.user.is_staff:
            from django.core.validators import validate_email
            from django.core.exceptions import ValidationError
            try:
                email = request.POST.get("email", "").lower().strip()
                validate_email(email)
                if len(email) > 150 or User.objects.count() >= settings.MAX_USERS:
                    raise ValidationError("Account limit or invalid email.")
                token = secrets.token_urlsafe(32)
                Invite.objects.create(email=email, digest=digest(token), expires_at=timezone.now()+timedelta(days=7))
                invite_url = settings.APP_URL+f"/join/{token}/"
            except ValidationError:
                messages.error(request, "Enter a valid email. At most ten user accounts are supported.")
        elif action == "delete":
            if request.user.check_password(request.POST.get("password", "")) and request.POST.get("confirm") == "DELETE":
                user = request.user
                logout(request)
                user.delete()
                return redirect("login")
            messages.error(request, "Enter your current password and type DELETE to confirm.")
    return render(request, "settings.html", {"profile": profile, "invite_url": invite_url, "push_key": settings.VAPID_PUBLIC_KEY, "tracking_ready": bool(settings.EASYPOST_API_KEY), "gmail_ready": bool(settings.GOOGLE_CLIENT_ID), "outlook_ready": bool(settings.MICROSOFT_CLIENT_ID), "worker": Lease.objects.filter(name="worker").first(), "failed_notifications": Delivery.objects.filter(notification__user=request.user, sent_at__isnull=True, attempts__gte=5).count()})

@login_required
@require_POST
def push_subscribe(request):
    try:
        data = json.loads(request.body)
        endpoint = push_endpoint(data["endpoint"])
        if not isinstance(data.get("keys"), dict) or not data["keys"].get("auth") or not data["keys"].get("p256dh"):
            raise ValueError()
        # Reassign this device when a different account subscribes; no shared alerts.
        PushSubscription.objects.update_or_create(endpoint=endpoint, defaults={"user": request.user, "secret": seal(data)})
        return JsonResponse({"ok": True})
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"error": "Invalid push subscription"}, status=400)

@login_required
@require_POST
def push_unsubscribe(request):
    PushSubscription.objects.filter(user=request.user).delete()
    return JsonResponse({"ok": True})

def service_worker(request):
    return HttpResponse((settings.BASE_DIR/"static"/"sw.js").read_text(), content_type="application/javascript")

def health(request):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})

def privacy(request):
    return render(request, "privacy.html")
