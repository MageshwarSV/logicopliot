"""The ERP saying the entry has no products must FAIL the run, whatever the script says.

Production 2026-11-25. MAA/AI/35112, 35113 and 35118 each filed an ERP entry holding nothing,
and each one reported `ok`. The ERP had said so in as many words, and the run pressed OK:

    ALERT: the ERP raised a alert - 'No products in this job' - accepted (as alert step 88 says)

Two separate doors let an empty entry through, and only one was closed on 2026-11-25:

  1. "Invalid Data Found" - a modal the ERP writes INTO the page when it refuses the Excel
     import. Closed by _import_rejection (see test_erp_import_rejection_fails_loudly.py).
  2. "No products in this job" - a BROWSER dialog. A browser dialog has to be answered or the
     page hangs, so it was answered; worse, an "if this alert comes, press OK" step had been
     recorded for it, so it was answered deliberately and the run carried on to its success
     check - which is only "did a job number appear".

A recorded alert step decides which BUTTON to press. It cannot decide that a run succeeded.
That is the whole of this fix: the dialog is still answered, and the run then fails.

The narrowness is the safety. Every other dialog these scripts meet - credit limits, duplicate
invoice confirms, session prompts, "job saved successfully" - is answered exactly as before,
which is what the parametrised test below pins down.
"""
import pytest

from app.core.browser import _empty_entry_alert, _empty_entry_block

# Verbatim from the production logs of the three jobs.
NO_PRODUCTS = "No products in this job"
NO_PRODUCTS_FOR_INVOICE = "There are no products for following invoice(s):- CI26110005"


def test_the_alert_that_filed_three_empty_entries_is_caught():
    assert _empty_entry_alert(NO_PRODUCTS) == NO_PRODUCTS


def test_the_per_invoice_wording_is_caught_too():
    """The same fact in the ERP's other phrasing - seen on MAA/SI/04017's own script."""
    got = _empty_entry_alert(NO_PRODUCTS_FOR_INVOICE)
    assert got is not None
    assert "CI26110005" in got, "the operator needs to know WHICH invoice came through empty"


def test_it_reads_through_whitespace_and_case():
    assert _empty_entry_alert("  NO   PRODUCTS\nIN THIS JOB  ") is not None


@pytest.mark.parametrize("dialog", [
    "Credit Limit for NOKIA SOLUTIONS AND NETWORKS INDIA PRIVATE LIMITED has exhausted/exceeded",
    "Update the Newly selected Importer & its branch into Customer Entity ?",
    "You have logged in from another web portal,\nDo you want to kill the other session?",
    "Are you sure you want to close the form ?",
    "Job saved successfully",
    "Selected quotation [RFQ/0004/24-25] is not valid.",
    "ShowSpecialInstruction : PageMethods.ShowSpecialInstruction : [object Object]",
    "Products added successfully",
    "",
    None,
])
def test_every_other_dialog_is_answered_exactly_as_before(dialog):
    """These are all dialogs the scripts handle correctly today. Failing a run on any of them
    would turn a working entry into a stuck job - a worse bug than the one being fixed."""
    assert _empty_entry_alert(dialog) is None


def test_a_job_that_HAS_products_is_not_matched():
    """The obvious false positive: a message that merely mentions products."""
    assert _empty_entry_alert("3 products imported successfully") is None
    assert _empty_entry_alert("Do you want to add more products?") is None


def test_the_operator_is_told_the_entry_exists_and_is_empty():
    """The entry is already created by the time the ERP says this - the job number was
    generated steps earlier. So the message has to say both things: nothing went in, AND
    there is now an empty entry in the ERP to deal with."""
    reason = _empty_entry_block(NO_PRODUCTS)["reason"]
    assert NO_PRODUCTS in reason, "the ERP's own words, not a paraphrase"
    assert "empty" in reason.lower()
    assert "Retry" in reason, "they need to know what to do next"
    assert "delete" in reason.lower(), "and that the empty ERP entry has to go"


def test_it_fails_the_job_rather_than_marking_it_duplicate():
    """persist_run_outcome maps outcome 'duplicate'/'blocked'/'unknown' to status
    'duplicated' and everything else to 'failed'. An empty entry is a failure: it must show
    the red Failed badge and keep its Retry button."""
    assert _empty_entry_block(NO_PRODUCTS)["outcome"] == "validation"
