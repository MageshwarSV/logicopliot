"""Proper GST tax invoice: amount in words, financial year, CGST+SGST vs IGST, the running
number per financial year, and the printable invoice."""
from datetime import date, datetime, timedelta, timezone

from app.core.billing import ensure_invoices_finalized
from app.core.tax_invoice import amount_in_words, financial_year, printable, split_tax
from app.models.invoice import Invoice
from app.models.invoice_settings import InvoiceSettings
from app.models.tenant_billing_config import TenantBillingConfig
from tests.conftest import make_tenant


def test_amount_in_words_matches_the_sample_invoice():
    assert amount_in_words(7508.94) == "Indian Rupee Seven Thousand Five Hundred Eight and Ninety-Four Paise Only"
    assert amount_in_words(5202.49) == "Indian Rupee Five Thousand Two Hundred Two and Forty-Nine Paise Only"
    assert amount_in_words(1250000) == "Indian Rupee Twelve Lakh Fifty Thousand Only"
    assert amount_in_words(23456789.5) == "Indian Rupee Two Crore Thirty-Four Lakh Fifty-Six Thousand Seven Hundred Eighty-Nine and Fifty Paise Only"


def test_financial_year_runs_april_to_march():
    assert financial_year(date(2026, 11, 19)) == "2026-27"
    assert financial_year(date(2027, 3, 31)) == "2026-27"
    assert financial_year(date(2027, 4, 1)) == "2027-28"


def test_same_state_is_cgst_sgst_other_state_is_igst():
    assert split_tax(1145.44, "33", "33") == ("cgst_sgst", 572.72, 572.72, 0.0)
    assert split_tax(1145.44, "33", "29") == ("igst", 0.0, 0.0, 1145.44)
    assert split_tax(10.01, "33", None)[0] == "cgst_sgst"          # customer state unknown
    _, c, s, _ = split_tax(10.01, "33", "33")
    assert round(c + s, 2) == 10.01                                 # never loses a paisa


def _tenant_with_billing(db_session, name, gstin):
    t = make_tenant(db_session, name=name)
    t.billing_legal_name = f"{name} PRIVATE LIMITED"
    t.billing_gstin = gstin
    t.billing_address = "Chennai"
    t.billing_state_code = gstin[:2]
    cfg = TenantBillingConfig(tenant_id=t.id)
    db_session.add(cfg)
    db_session.commit()
    cfg.created_at = datetime.now(timezone.utc) - timedelta(days=75)   # ~2 finished months
    db_session.commit()
    return t


def test_invoices_are_numbered_per_financial_year_with_details_frozen(db_session):
    db_session.add(InvoiceSettings(id="default", seller_name="AXIORA LABS AI (OPC) PRIVATE LIMITED",
                                   gstin="33ABGCA3833P1ZF", state_code="33", number_prefix="INV",
                                   sac_platform="998315", sac_usage="998313", due_days=0))
    db_session.commit()
    kss = _tenant_with_billing(db_session, "KSS ROADWAYS", "33AAFCK1245B1Z7")
    blr = _tenant_with_billing(db_session, "BLR CARGO", "29AAAAA1111A1Z1")
    ensure_invoices_finalized(db_session, kss.id)
    ensure_invoices_finalized(db_session, blr.id)
    invs = db_session.query(Invoice).order_by(Invoice.fy_sequence).all()
    fy = financial_year(datetime.now(timezone.utc).date())
    assert [i.invoice_number for i in invs] == [f"INV/{fy}/{n:04d}" for n in range(1, len(invs) + 1)]
    assert len(invs) >= 4
    k = next(i for i in invs if i.tenant_id == kss.id)
    b = next(i for i in invs if i.tenant_id == blr.id)
    assert k.tax_mode == "cgst_sgst" and b.tax_mode == "igst"
    assert k.buyer["gstin"] == "33AAFCK1245B1Z7" and k.seller["gstin"] == "33ABGCA3833P1ZF"

    out = printable(k, kss, db_session.get(InvoiceSettings, "default"))
    assert out["lines"][0]["sac"] == "998315" and out["place_of_supply"] == "Tamil Nadu (33)"
    assert out["amount_in_words"].startswith("Indian Rupee") and out["balance_due"] == out["total"]
