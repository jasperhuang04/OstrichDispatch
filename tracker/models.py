import hashlib
import secrets
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone

STATUSES = [(s, s.replace("_", " ").title()) for s in ["ordered", "shipped", "in_transit", "out_for_delivery", "delivered", "exception", "cancelled", "returned"]]

class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    discord_secret = models.TextField(blank=True)
    notify = models.BooleanField(default=True)

class Inbox(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    provider = models.CharField(max_length=16, choices=[("icloud", "iCloud"), ("gmail", "Gmail"), ("outlook", "Outlook"), ("yahoo", "Yahoo"), ("imap", "Other IMAP")])
    email = models.EmailField()
    username = models.CharField(max_length=254, blank=True)
    host = models.CharField(max_length=253, blank=True)
    folder = models.CharField(max_length=100, default="INBOX")
    secret = models.TextField()
    enabled = models.BooleanField(default=True)
    cursor = models.JSONField(default=dict)
    last_sync = models.DateTimeField(null=True, blank=True)
    next_sync = models.DateTimeField(default=timezone.now)
    error = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "email", "provider"], name="unique_user_inbox")]
    def __str__(self):
        return self.email

class Order(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    inbox = models.ForeignKey(Inbox, null=True, blank=True, on_delete=models.SET_NULL)
    identity = models.CharField(max_length=64)
    merchant = models.CharField(max_length=160)
    number = models.CharField(max_length=180, blank=True)
    title = models.CharField(max_length=240)
    items = models.JSONField(default=list)
    total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    address = models.TextField(blank=True)
    status = models.CharField(max_length=30, choices=STATUSES, default="ordered")
    status_at = models.DateTimeField(default=timezone.now)
    ordered_at = models.DateTimeField(default=timezone.now)
    eta = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    needs_review = models.BooleanField(default=False)
    archived = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self):
        return f"{self.merchant} · {self.title}"
    class Meta:
        ordering = ["-ordered_at"]
        constraints = [models.UniqueConstraint(fields=["user", "identity"], name="unique_order_identity")]

class Shipment(models.Model):
    order = models.ForeignKey(Order, related_name="shipments", on_delete=models.CASCADE)
    tracking_number = models.CharField(max_length=80)
    carrier = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=30, choices=STATUSES, default="shipped")
    status_at = models.DateTimeField(default=timezone.now)
    eta = models.DateField(null=True, blank=True)
    provider_id = models.CharField(max_length=100, blank=True)
    last_check = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=200, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["order", "tracking_number"], name="unique_order_tracking")]

class TrackingEvent(models.Model):
    shipment = models.ForeignKey(Shipment, related_name="events", on_delete=models.CASCADE)
    key = models.CharField(max_length=64)
    status = models.CharField(max_length=30)
    message = models.CharField(max_length=300)
    location = models.CharField(max_length=200, blank=True)
    at = models.DateTimeField()
    class Meta:
        ordering = ["-at"]
        constraints = [models.UniqueConstraint(fields=["shipment", "key"], name="unique_tracking_event")]

class ProcessedMessage(models.Model):
    inbox = models.ForeignKey(Inbox, on_delete=models.CASCADE)
    key = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["inbox", "key"], name="unique_processed_message")]

class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    order = models.ForeignKey(Order, on_delete=models.CASCADE)
    title = models.CharField(max_length=180)
    body = models.CharField(max_length=300)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

class PushSubscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    endpoint = models.TextField(unique=True)
    secret = models.TextField()

class Delivery(models.Model):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE)
    channel = models.CharField(max_length=20)
    subscription = models.ForeignKey(PushSubscription, null=True, on_delete=models.CASCADE)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=200, blank=True)

class Inventory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=240)
    quantity = models.PositiveIntegerField(default=1)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="USD")
    notes = models.TextField(blank=True)
    @property
    def sold(self):
        return sum(s.quantity for s in self.sales.all())
    @property
    def in_hand(self):
        return self.quantity - self.sold
    @property
    def profit(self):
        return sum((s.revenue - s.fees - s.shipping - self.unit_cost * s.quantity for s in self.sales.all()), Decimal("0"))

class Sale(models.Model):
    inventory = models.ForeignKey(Inventory, related_name="sales", on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    revenue = models.DecimalField(max_digits=12, decimal_places=2)
    fees = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shipping = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sold_at = models.DateField(default=timezone.localdate)
    notes = models.CharField(max_length=240, blank=True)

class Invite(models.Model):
    email = models.EmailField()
    digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)

class Lease(models.Model):
    name = models.CharField(max_length=100, primary_key=True)
    expires_at = models.DateTimeField(default=timezone.now)

class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()
