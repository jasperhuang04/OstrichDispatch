"""Local, conservative email extraction. Raw messages are never persisted.

Retailer templates change: fields that cannot be confidently extracted stay
empty, and incomplete orders are flagged for review rather than invented.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import unquote
from bs4 import BeautifulSoup
from dateutil.parser import parse as parse_date
from django.utils import timezone

RETAILERS = {
    "amazon.com": "Amazon", "amazon.ca": "Amazon", "amazon.co.uk": "Amazon",
    "walmart.com": "Walmart", "target.com": "Target", "bestbuy.com": "Best Buy",
    "costco.com": "Costco", "nike.com": "Nike", "apple.com": "Apple",
    "pokemoncenter.com": "Pokémon Center", "samsclub.com": "Sam's Club",
    "shopify.com": "Shopify", "shopifyemail.com": "Shopify",
}

@dataclass
class ParsedOrder:
    merchant: str
    number: str
    title: str
    at: datetime
    status: str = "ordered"
    items: list = field(default_factory=list)
    total: Decimal | None = None
    currency: str = "USD"
    address: str = ""
    eta: object = None
    tracking: list = field(default_factory=list)
    review: bool = False

def safe_date(value):
    try:
        return parse_date(str(value), fuzzy=False).date()
    except (ValueError, TypeError, OverflowError):
        return None

def amount(value):
    try:
        result = Decimal(re.sub(r"[^\d.-]", "", str(value))).quantize(Decimal("0.01"))
        return result if result.is_finite() and 0 <= result < Decimal("10000000000") else None
    except (InvalidOperation, ValueError):
        return None

def infer_carrier(number, hint=""):
    n = number.upper()
    if re.fullmatch(r"1Z[A-Z0-9]{16}", n):
        return "UPS"
    if re.fullmatch(r"TBA\d{12,}", n):
        return "Amazon"
    for pattern, carrier in [(r"\busps\b", "USPS"), (r"\bfedex\b", "FedEx"), (r"\bdhl\b", "DHL"), (r"\bups\b", "UPS"), (r"\bontrac\b", "OnTrac")]:
        if re.search(pattern, hint, re.I):
            return carrier
    if re.fullmatch(r"9[2345]\d{18,24}", n):
        return "USPS"
    # Numeric formats overlap across carriers. Do not guess FedEx from length.
    return ""

def tracking_numbers(text):
    values = []
    for m in re.finditer(r"\b(?:1Z[A-Z0-9]{16}|TBA\d{12,})\b", text, re.I):
        values.append((m.group().upper(), infer_carrier(m.group())))
    labelled = r"(?:tracking\s*(?:number|no\.?|id|#|code)?|tracknum|trackingnumber|tracking_id|tracknumbers)\s*[:#=\s]+([A-Z0-9][A-Z0-9 -]{7,48})"
    for m in re.finditer(labelled, text, re.I):
        n = re.split(r"\s{2,}|\s+(?=[A-Za-z]{3})", m.group(1))[0].replace(" ", "").strip("-").upper()
        if 8 <= len(n) <= 40 and sum(c.isdigit() for c in n) >= 6:
            values.append((n, infer_carrier(n, text[max(0, m.start()-50):m.end()+40])))
    return list(dict.fromkeys(values))

def status_from(text):
    if re.match(r"^delivered\b", text.strip(), re.I):
        return "delivered"
    if re.match(r"^shipped\b", text.strip(), re.I):
        return "shipped"
    patterns = [(r"\b(?:order (?:has been )?cancel[le]+d|cancel[le]+d (?:your )?order)\b", "cancelled"), (r"\b(?:returned to sender|return received|refund issued)\b", "returned"), (r"\b(?:delivery (?:failed|exception)|unable to deliver|delivery delayed)\b", "exception"), (r"\bout for delivery\b", "out_for_delivery"), (r"\b(?:was delivered|has been delivered|package delivered|order delivered|delivered[:! ]|your .+ delivered)\b", "delivered"), (r"\b(?:in transit|on its way)\b", "in_transit"), (r"\b(?:has shipped|have shipped|order shipped|shipment confirmation|shipped[:! ])\b", "shipped")]
    for pattern, status in patterns:
        if re.search(pattern, text, re.I):
            return status
    return "ordered"

def json_objects(value):
    if isinstance(value, list):
        for item in value:
            yield from json_objects(item)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from json_objects(value["@graph"])

def parse_email(raw, received_at=None):
    if len(raw) > 2 * 1024 * 1024:
        return None
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    subject = str(msg.get("Subject", ""))[:500]
    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    domain = sender.rpartition("@")[2]
    merchant = next((v for k, v in RETAILERS.items() if domain == k or domain.endswith("." + k)), domain or "Unknown store")
    try:
        at = received_at or parsedate_to_datetime(str(msg.get("Date", "")))
    except (ValueError, TypeError):
        at = timezone.now()
    if timezone.is_naive(at):
        at = timezone.make_aware(at)
    texts, htmls = [], []
    for part in msg.walk():
        if part.get_content_disposition() == "attachment" or part.is_multipart():
            continue
        if part.get_content_type() in ("text/plain", "text/html"):
            try:
                content = part.get_content()
            except (LookupError, UnicodeError):
                content = part.get_payload(decode=True).decode("utf-8", "replace")
            (htmls if part.get_content_type() == "text/html" else texts).append(content)
    schemas, links = [], []
    for html in htmls:
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                schemas.extend(json_objects(json.loads(script.get_text())))
            except (ValueError, TypeError):
                pass
        links.extend(unquote(str(a.get("href", ""))) for a in soup.find_all("a"))
        for tag in soup(["script", "style", "head"]):
            tag.decompose()
        texts.append(soup.get_text("\n", strip=True))
    body = "\n".join(texts)[:150000]
    content = subject + "\n" + body
    tracking = tracking_numbers(content + "\n" + "\n".join(links))
    # Extract the identifier only when introduced by an order label.
    matches = re.search(r"\border\s*(?:number|no\.?|id|#)?\s*[:#\s]+([A-Z0-9][A-Z0-9-]{3,70})", content, re.I)
    number = matches.group(1) if matches and any(c.isdigit() for c in matches.group(1)) else ""
    if not number and merchant == "Amazon":
        match = re.search(r"\b\d{3}-\d{7}-\d{7}\b", content)
        number = match.group() if match else ""
    order_schema = next((s for s in schemas if "Order" in ([s.get("@type")] if isinstance(s.get("@type"), str) else s.get("@type", []))), {})
    delivery_schema = next((s for s in schemas if s.get("@type") == "ParcelDelivery"), {})
    part_of_order = delivery_schema.get("partOfOrder") or {}
    part_of_order = part_of_order if isinstance(part_of_order, dict) else {}
    number = str(order_schema.get("orderNumber") or part_of_order.get("orderNumber") or number)[:180]
    if delivery_schema.get("trackingNumber"):
        n = str(delivery_schema["trackingNumber"])[:80]
        carrier = delivery_schema.get("carrier", {})
        tracking.append((n, str(carrier.get("name", "")) if isinstance(carrier, dict) else ""))
    if not (number or tracking or order_schema):
        return None
    status = status_from(subject + "\n" + body[:400])
    result = ParsedOrder(merchant, number, subject[:240] or f"{merchant} order", at, status=status, tracking=list(dict.fromkeys(tracking)))
    offers = order_schema.get("acceptedOffer", [])
    if isinstance(offers, dict):
        offers = [offers]
    for offer in offers if isinstance(offers, list) else []:
        product = offer.get("itemOffered", {}) if isinstance(offer, dict) else {}
        if not isinstance(product, dict):
            continue
        if product.get("name"):
            try:
                quantity = max(1, min(10000, int(offer.get("eligibleQuantity", {}).get("value", 1))))
            except (ValueError, TypeError, AttributeError):
                quantity = 1
            result.items.append({"name": str(product["name"])[:240], "quantity": quantity})
    if not result.items:
        for m in re.finditer(r"^(?:item|product)\s*:\s*(.{3,200})$", body, re.I | re.M):
            result.items.append({"name": m.group(1).strip(), "quantity": 1})
    if result.items:
        result.title = result.items[0]["name"] + (f" + {len(result.items)-1} more" if len(result.items) > 1 else "")
    result.total = amount(order_schema.get("price"))
    if result.total is None:
        m = re.search(r"(?<!sub)\b(?:grand total|order total|total paid|total)\s*[:\s]*((?:USD|CAD|GBP|EUR)\s*)?([$£€]?)\s*([\d,]+\.\d{2})", body, re.I)
        if m:
            result.total = amount(m.group(3))
            result.currency = (m.group(1) or {"£": "GBP", "€": "EUR"}.get(m.group(2), "USD")).strip().upper()
    if re.fullmatch(r"[A-Z]{3}", str(order_schema.get("priceCurrency", ""))):
        result.currency = order_schema["priceCurrency"]
    address = delivery_schema.get("deliveryAddress", {})
    if isinstance(address, dict):
        result.address = ", ".join(str(address[k]) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry") if address.get(k) and isinstance(address[k], str))
    if not result.address:
        m = re.search(r"(?:ship(?:ping)? to|delivery address)\s*:\s*([^\n]+(?:\n[^\n]+){0,2})", body, re.I)
        if m:
            result.address = m.group(1).strip()[:500]
    result.eta = safe_date(delivery_schema.get("expectedArrivalUntil"))
    if not result.eta:
        m = re.search(r"(?:estimated delivery|arriving|expected delivery)\s*[:\s]+([^\n]{5,50})", body, re.I)
        if m:
            result.eta = safe_date(m.group(1))
    result.review = not bool(result.number and result.items and result.total is not None)
    return result
