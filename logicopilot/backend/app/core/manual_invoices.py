"""Manual (one-off) invoices: totals, numbering shared with usage invoices, issue / pay /
cancel, and the same printable shape the tax-invoice page already reads."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.tax_invoice import amount_in_words, buyer_snapshot, financial_year, seller_snapshot, split_tax


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_lines(raw: list[dict]) -> list[dict]:
    """Each line's amount = qty x rate (2 decimals). Blank lines are dropped."""
    out = []
    for ln in raw or []:
        desc = str(ln.get("description") or "").strip()
        try:
            qty = float(ln.get("qty") or 0)
            rate = float(ln.get("rate") or 0)
        except (TypeError, ValueError):
            raise ValueError("Quantity and rate must be numbers")
        if not desc and not qty and not rate:
            continue
        if not desc:
            raise ValueError("Every line needs a description")
        if qty <= 0:
            raise ValueError(f"Quantity must be more than 0 ({desc})")
        if rate < 0:
            raise ValueError(f"Rate cannot be negative ({desc})")
        out.append({"description": desc[:300], "sac": str(ln.get("sac") or "").strip()[:12],
                    "qty": qty, "rate": round(rate, 2), "amount": round(qty * rate, 2)})
    return out


def recompute(inv) -> None:
    inv.subtotal = round(sum(ln["amount"] for ln in inv.lines or []), 2)
    inv.gst_amount = round(inv.subtotal * (inv.gst_percent or 0) / 100, 2)
    inv.total_amount = round(inv.subtotal + inv.gst_amount, 2)


def next_number(db: Session, on: date, prefix: str) -> tuple[str, str, int]:
    """(number, financial year, sequence) - one series across usage and manual invoices."""
    from app.models.invoice import Invoice
    from app.models.manual_invoice import ManualInvoice

    fy = financial_year(on)
    seq = max(
        db.query(func.max(Invoice.fy_sequence)).filter(Invoice.financial_year == fy).scalar() or 0,
        db.query(func.max(ManualInvoice.fy_sequence)).filter(ManualInvoice.financial_year == fy).scalar() or 0,
    ) + 1
    return f"{prefix}/{fy}/{seq:04d}", fy, seq


def issue(db: Session, inv, tenant, settings) -> None:
    if inv.status != "draft":
        raise ValueError("Only a draft can be issued")
    if not inv.lines:
        raise ValueError("Add at least one line before issuing")
    recompute(inv)
    prefix = (settings.number_prefix or "INV").strip() or "INV"
    number, fy, seq = next_number(db, date.fromisoformat(inv.invoice_date), prefix)
    inv.invoice_number, inv.financial_year, inv.fy_sequence = number, fy, seq
    inv.seller = seller_snapshot(settings)
    inv.buyer = buyer_snapshot(tenant)
    inv.tax_mode, inv.cgst_amount, inv.sgst_amount, inv.igst_amount = split_tax(
        inv.gst_amount, inv.seller.get("state_code"), inv.buyer.get("state_code"))
    inv.status = "issued"
    inv.issued_at = _now()


def printable(inv, tenant, settings) -> dict:
    """Same shape as tax_invoice.printable, so the existing tax invoice page prints it. A draft
    prints from the current details, marked DRAFT."""
    seller = inv.seller or seller_snapshot(settings)
    buyer = inv.buyer or buyer_snapshot(tenant)
    mode, cgst, sgst, igst = (
        (inv.tax_mode, inv.cgst_amount or 0, inv.sgst_amount or 0, inv.igst_amount or 0) if inv.tax_mode
        else split_tax(inv.gst_amount, seller.get("state_code"), buyer.get("state_code")))
    half = (inv.gst_percent or 0) / 2
    lines = []
    for ln in inv.lines or []:
        tax = round(ln["amount"] * (inv.gst_percent or 0) / 100, 2)
        c = round(tax / 2, 2)
        lines.append({
            "description": ln["description"], "sac": ln.get("sac") or "", "qty": ln["qty"], "rate": ln["rate"],
            "taxable": ln["amount"],
            "cgst_percent": half if mode == "cgst_sgst" else 0, "cgst": c if mode == "cgst_sgst" else 0,
            "sgst_percent": half if mode == "cgst_sgst" else 0, "sgst": round(tax - c, 2) if mode == "cgst_sgst" else 0,
            "igst_percent": inv.gst_percent if mode == "igst" else 0, "igst": tax if mode == "igst" else 0,
            "amount": ln["amount"],
        })
    return {
        "invoice_number": inv.invoice_number or "DRAFT",
        "invoice_date": inv.invoice_date,
        "due_date": inv.due_date,
        "terms": settings.terms or "Due on Receipt",
        "period": inv.period_label or "",
        "place_of_supply": (f"{buyer.get('state_name')} ({buyer.get('state_code')})" if buyer.get("state_code")
                            else f"{seller.get('state_name')} ({seller.get('state_code')})"),
        "seller": seller, "buyer": buyer, "tax_mode": mode, "lines": lines,
        "subtotal": inv.subtotal, "gst_percent": inv.gst_percent,
        "cgst": cgst, "sgst": sgst, "igst": igst,
        "total": inv.total_amount,
        "balance_due": 0.0 if inv.status in ("paid", "cancelled") else inv.total_amount,
        "amount_in_words": amount_in_words(inv.total_amount),
        "notes": inv.notes or settings.notes or "Thanks for your business.",
        "bank_details": settings.bank_details or "",
        "status": inv.status,
        "paid_at": inv.paid_at,
        "kind": "manual",
    }
