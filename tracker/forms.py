import re
from decimal import Decimal
from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Inbox, Order, Inventory, Sale
from .security import imap_host

class RegisterForm(UserCreationForm):
    first_name = forms.CharField(label="Your name", max_length=80)
    class Meta:
        model = User
        fields = ("first_name", "password1", "password2")

class InboxForm(forms.ModelForm):
    password = forms.CharField(label="App-specific password", widget=forms.PasswordInput(render_value=False))
    class Meta:
        model = Inbox
        fields = ["provider", "email", "username", "host", "folder", "password"]
        help_texts = {"username": "Optional IMAP login name; otherwise your email is used.", "host": "Leave blank for iCloud or Yahoo.", "folder": "Only this folder is scanned. Default: INBOX."}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["provider"].choices = [("icloud", "iCloud"), ("yahoo", "Yahoo"), ("imap", "Other IMAP")]
        self.fields["email"].help_text = "Use your primary mailbox address. Aliases share the same connection."
    def clean(self):
        data = super().clean()
        if data.get("provider"):
            try:
                data["host"] = imap_host(data["provider"], data.get("host", ""))
            except (ValueError, OSError) as exc:
                self.add_error("host", "Server is not allowed or could not be resolved. Check the server setting.")
        for key in ("folder", "username"):
            if re.search(r"[\r\n\x00]", data.get(key, "")):
                self.add_error(key, "Control characters are not allowed.")
        data["email"] = data.get("email", "").lower()
        return data

class CurrencyMixin:
    def clean_currency(self):
        value = self.cleaned_data["currency"].upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise forms.ValidationError("Use a three-letter currency code, such as USD.")
        return value

class OrderForm(CurrencyMixin, forms.ModelForm):
    tracking_number = forms.CharField(max_length=80, required=False, help_text="Add another package to this order.")
    carrier = forms.ChoiceField(required=False, choices=[("", "Detect / unknown"), ("UPS", "UPS"), ("USPS", "USPS"), ("FedEx", "FedEx"), ("DHL", "DHL"), ("Amazon", "Amazon"), ("OnTrac", "OnTrac")])
    class Meta:
        model = Order
        fields = ["title", "merchant", "number", "total", "currency", "status", "eta", "address", "notes", "needs_review", "tracking_number", "carrier"]
        widgets = {"eta": forms.DateInput(attrs={"type": "date"}), "address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 3})}
    def clean_total(self):
        v = self.cleaned_data["total"]
        if v is not None and v < 0:
            raise forms.ValidationError("Total must be zero or greater.")
        return v
    def clean_tracking_number(self):
        v = re.sub(r"\s", "", self.cleaned_data["tracking_number"]).upper()
        if v and not re.fullmatch(r"[A-Z0-9-]{6,80}", v):
            raise forms.ValidationError("Use a tracking number containing letters, digits or hyphens.")
        return v

class InventoryForm(CurrencyMixin, forms.ModelForm):
    class Meta:
        model = Inventory
        fields = ["name", "quantity", "unit_cost", "currency", "order", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}
    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["order"].queryset = Order.objects.filter(user=user)
        self.fields["quantity"].min_value = 1
        self.fields["unit_cost"].min_value = 0
    def clean(self):
        data = super().clean()
        if data.get("quantity", 0) < 1:
            self.add_error("quantity", "Quantity must be at least one.")
        if self.instance.pk and data.get("quantity", 0) < self.instance.sold:
            self.add_error("quantity", "Quantity cannot be less than units already sold.")
        if data.get("unit_cost", Decimal(0)) < 0:
            self.add_error("unit_cost", "Cost cannot be negative.")
        if self.instance.pk and self.instance.sales.exists() and data.get("currency") != self.instance.currency:
            self.add_error("currency", "Cannot change currency after sales have been recorded.")
        return data

class SaleForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = ["quantity", "revenue", "fees", "shipping", "sold_at", "notes"]
        labels = {"revenue": "Total sale revenue", "shipping": "Shipping you paid"}
        widgets = {"sold_at": forms.DateInput(attrs={"type": "date"})}
    def clean(self):
        data = super().clean()
        for key in ("quantity", "revenue", "fees", "shipping"):
            value = data.get(key)
            if value is not None and value < (1 if key == "quantity" else 0):
                self.add_error(key, "Value is too small.")
        return data
