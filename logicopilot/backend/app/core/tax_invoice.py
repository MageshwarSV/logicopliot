"""A proper GST tax invoice for a Cargora bill: Axiora's own details (InvoiceSettings), the
customer's billing details (Tenant.billing_*), invoice date / terms / due date, HSN/SAC per line,
CGST + SGST for a customer in Axiora's own state or IGST for any other, and the total in words.

Numbering: a running number per financial year (April-March), e.g. INV/2026-27/0001. Invoices
raised before this existed keep their old number (INV-202610-...); they have no snapshot, so they
print with the current details.
"""
from __future__ import annotations

from datetime import date

# GST state codes (first two digits of a GSTIN).
GST_STATES: dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
    "97": "Other Territory",
}


def financial_year(d: date) -> str:
    """April-March: 19 Nov 2026 -> "2026-27", 10 Feb 2027 -> "2026-27"."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def state_code_of(state_code: str | None, gstin: str | None) -> str | None:
    """The state code given, or the first two digits of the GSTIN."""
    code = (state_code or "").strip()
    if code:
        return code.zfill(2)[:2]
    g = (gstin or "").strip()
    return g[:2] if len(g) >= 2 and g[:2].isdigit() else None


def split_tax(gst_amount: float, seller_code: str | None, buyer_code: str | None) -> tuple[str, float, float, float]:
    """(mode, cgst, sgst, igst). Same state (or the customer's state unknown) -> CGST + SGST,
    half each; a different state -> IGST, the whole amount."""
    if buyer_code and seller_code and buyer_code != seller_code:
        return "igst", 0.0, 0.0, round(gst_amount, 2)
    half = round(gst_amount / 2, 2)
    return "cgst_sgst", half, round(gst_amount - half, 2), 0.0


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven",
         "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_100(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")


def _below_1000(n: int) -> str:
    h, r = divmod(n, 100)
    parts = []
    if h:
        parts.append(f"{_ONES[h]} Hundred")
    if r:
        parts.append(_below_100(r))
    return " ".join(parts)


def _indian_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    if crore:
        parts.append(f"{_indian_words(crore)} Crore")
    if lakh:
        parts.append(f"{_below_100(lakh)} Lakh")
    if thousand:
        parts.append(f"{_below_100(thousand)} Thousand")
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)


def amount_in_words(amount: float) -> str:
    """7508.94 -> "Indian Rupee Seven Thousand Five Hundred Eight and Ninety-Four Paise Only"."""
    rupees = int(round(amount * 100)) // 100
    paise = int(round(amount * 100)) % 100
    words = f"Indian Rupee {_indian_words(rupees)}"
    if paise:
        words += f" and {_below_100(paise)} Paise"
    return words + " Only"


def seller_snapshot(settings) -> dict:
    code = state_code_of(getattr(settings, "state_code", None), getattr(settings, "gstin", None))
    return {
        "name": settings.seller_name or "", "address": settings.address or "",
        "state_name": settings.state_name or GST_STATES.get(code or "", ""), "state_code": code or "",
        "country": settings.country or "India", "gstin": settings.gstin or "",
        "email": settings.email or "", "phone": settings.phone or "",
    }


def buyer_snapshot(tenant) -> dict:
    code = state_code_of(getattr(tenant, "billing_state_code", None), getattr(tenant, "billing_gstin", None))
    return {
        "name": (tenant.billing_legal_name or tenant.name or ""), "gstin": tenant.billing_gstin or "",
        "address": tenant.billing_address or "", "state_code": code or "",
        "state_name": tenant.billing_state_name or GST_STATES.get(code or "", ""),
        "email": tenant.billing_email or "", "ship_to": tenant.billing_ship_to or "",
    }


def _month_label(period_start: str) -> str:
    d = date.fromisoformat(period_start)
    return d.strftime("%B %Y")


def printable(invoice, tenant, settings) -> dict:
    """Everything the tax invoice page shows, in one dict - from the invoice's own snapshot when
    it has one, else from the current details (an invoice raised before this existed)."""
    seller = invoice.seller or seller_snapshot(settings)
    buyer = invoice.buyer or buyer_snapshot(tenant)
    mode = invoice.tax_mode or split_tax(invoice.gst_amount, seller.get("state_code"), buyer.get("state_code"))[0]
    half = (invoice.gst_percent or 0) / 2
    month = _month_label(invoice.period_start)
    lines = []

    def add(desc: str, sac: str, qty: float, rate: float, amount: float):
        if not amount and qty == 0:
            return
        tax = round(amount * (invoice.gst_percent or 0) / 100, 2)
        c = round(tax / 2, 2)
        lines.append({
            "description": desc, "sac": sac, "qty": qty, "rate": rate, "taxable": round(amount, 2),
            "cgst_percent": half if mode == "cgst_sgst" else 0, "cgst": c if mode == "cgst_sgst" else 0,
            "sgst_percent": half if mode == "cgst_sgst" else 0, "sgst": round(tax - c, 2) if mode == "cgst_sgst" else 0,
            "igst_percent": invoice.gst_percent if mode == "igst" else 0, "igst": tax if mode == "igst" else 0,
            "amount": round(amount, 2),
        })

    fee_desc = f"Cargora platform fee – {month}"
    if invoice.platform_fee_billed_days != invoice.platform_fee_month_days:
        fee_desc += f" ({invoice.platform_fee_billed_days} of {invoice.platform_fee_month_days} days)"
    add(fee_desc, settings.sac_platform or "998315", 1, invoice.platform_fee, invoice.platform_fee)
    add(f"Document processing – Tier 1 (per page) – {month}", settings.sac_usage or "998313",
        invoice.tier1_pages, invoice.tier1_rate, invoice.tier1_amount)
    if invoice.tier2_pages:
        add(f"Document processing – Tier 2 (per page) – {month}", settings.sac_usage or "998313",
            invoice.tier2_pages, invoice.tier2_rate, invoice.tier2_amount)
    if invoice.not_required_pages:
        add(f"Pages not required (per page) – {month}", settings.sac_usage or "998313",
            invoice.not_required_pages, invoice.not_required_rate, invoice.not_required_amount)

    if invoice.tax_mode:
        cgst, sgst, igst = invoice.cgst_amount or 0, invoice.sgst_amount or 0, invoice.igst_amount or 0
    else:
        _, cgst, sgst, igst = split_tax(invoice.gst_amount, seller.get("state_code"), buyer.get("state_code"))
    raised = invoice.invoice_date or (invoice.created_at.date().isoformat() if invoice.created_at else invoice.period_end)
    return {
        "invoice_number": invoice.invoice_number,
        "invoice_date": raised,
        "due_date": invoice.due_date or raised,
        "terms": (settings.terms or "Due on Receipt"),
        "period": month,
        "place_of_supply": (f"{buyer.get('state_name')} ({buyer.get('state_code')})" if buyer.get("state_code")
                            else f"{seller.get('state_name')} ({seller.get('state_code')})"),
        "seller": seller,
        "buyer": buyer,
        "tax_mode": mode,
        "lines": lines,
        "subtotal": round(invoice.subtotal, 2),
        "gst_percent": invoice.gst_percent,
        "cgst": cgst, "sgst": sgst, "igst": igst,
        "total": round(invoice.total_amount, 2),
        "balance_due": 0.0 if invoice.status == "paid" else round(invoice.total_amount, 2),
        "amount_in_words": amount_in_words(invoice.total_amount),
        "notes": settings.notes or "Thanks for your business.",
        "bank_details": settings.bank_details or "",
        "status": invoice.status,
        "paid_at": invoice.paid_at,
    }
