"""Reading the Bill of Lading off a pre-alert subject.

Every string below is a real subject this product has processed. The one that matters most
is the HB#/MB# pair: the subject carries BOTH bills, and taking the master one would call
two genuinely separate house shipments the same job.
"""
import pytest

from app.core.email_bl import extract_bl_number, normalise_bl


@pytest.mark.parametrize("subject,expected", [
    ("Fwd: FLEX - HBL NO: 6911662149", "6911662149"),
    ("Fwd: HBL no: SHAS00427635", "SHAS00427635"),
    ("Fwd: HBL: 897140033682", "897140033682"),
    ("Fwd: SEG - HBL NO: 610410064", "610410064"),
    ("Fwd: RE: SEA - Pre-alert : BL - 022G545786 /// MOOWR", "022G545786"),
    ("Fwd: Pre-alert : BL - OOLU2174547880 /// MOOWR", "OOLU2174547880"),
    ("Fwd: AFT LYO - CBL - 5970000523 - PALLAVA TEXTILES", "5970000523"),
    ("Fwd: PRE ALERT // YUZHAN // BL : YLJ26326520", "YLJ26326520"),
    ("Fwd: NEW SHIPMENT OF SEKISUI // BL NO # 161040040353 // INV NO # TSCINA26/09-0006",
     "161040040353"),
    ("FW: OCEAN PRE ALERT DOCUMENT/BL NO : 1078027914// SHIPPER :NOLATO", "1078027914"),
    ("FW: HBL# 6650026178 // Reg : 07-Sep-2026", "6650026178"),
])
def test_the_subjects_real_senders_send(subject, expected):
    assert extract_bl_number(subject) == expected


def test_the_house_bill_wins_over_the_master():
    """The subject carries both. HB# is the house bill - the one the job itself shows as its
    BL No - and MB# is the carrier's, which several different house shipments can share.
    Keying duplicates off the master would merge two separate jobs into one."""
    subject = ("Fwd: FW: 72G0085735/AUTOMOTIVE COMPONENTS TECHNOLOGY IN/"
               "HB# 6840355813/MB# OOLU8883030720/ETA: 06-Nov-2026")
    assert extract_bl_number(subject) == "6840355813"


def test_a_separator_is_not_part_of_the_number():
    """"HB# 6840355813/MB#" - the slash ends the number. Allowing it through swallowed the
    next label and gave "6840355813MB"."""
    assert extract_bl_number("HB# 6840355813/MB# OOLU888") == "6840355813"


@pytest.mark.parametrize("subject", [
    "Fwd: MB# OOLU8883030720",
    "PRE ALERT MBL NO: ABCD1234567",
    "FW: MASTER BL : OOLU2174547880",
    "FW: Pre-Alert | MAWB - 4250228119 | Jognic's",
])
def test_a_master_bill_alone_is_not_taken(subject):
    assert extract_bl_number(subject) is None


@pytest.mark.parametrize("subject", [
    "FW: JUNIPER",
    "FW: TEREX",
    "PRE ALERT - KUEHNE NAGEL",
    "Fwd: FW: DOC",
    "Application for AI Engineer Position",
    "Smarter OCR and reliable predictions at scale",
    "(no subject)",
    "",
    None,
])
def test_mail_with_no_bl_in_the_subject(subject):
    """Most mail. These go through the ordinary path and never take part in the check."""
    assert extract_bl_number(subject) is None


def test_a_word_is_never_a_bl_number():
    assert extract_bl_number("FW: BL NO : DOCUMENT ATTACHED") is None
    assert extract_bl_number("Pre-alert : BL - SHIPPER") is None


def test_too_short_to_identify_a_shipment():
    """A four-character scrap would collide across customers constantly."""
    assert extract_bl_number("BL NO: 123") is None


def test_normalising_makes_one_spelling():
    """Punctuation and case are how someone typed it, not part of the number."""
    assert normalise_bl("SHAS-00427635") == "SHAS00427635"
    assert normalise_bl("shas 00427635") == "SHAS00427635"
    assert normalise_bl(" 1077706333 ") == "1077706333"
    assert normalise_bl(None) == ""


def test_the_same_bill_typed_two_ways_matches():
    a = extract_bl_number("Fwd: HBL no: SHAS-00427635")
    b = extract_bl_number("Fwd: HBL NO: shas00427635")
    assert a == b == "SHAS00427635"
