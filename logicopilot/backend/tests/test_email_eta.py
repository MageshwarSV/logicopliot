"""Reading the ETA off a pre-alert email.

The strings below are the real shapes, taken from the 42 pre-alert emails this product has
kept: `ETA: 06-11-2026` and `ETA: 24-11-2026` in the body, and one sender who puts it in the
SUBJECT run together with the HBL and MBL numbers by slashes.

The trap this has to survive is ETD. A pre-alert prints both, two lines apart, and taking
the wrong one puts a date two weeks early on the job - early enough to look plausible and
wrong enough to matter.
"""
from datetime import date

import pytest

from app.core.email_eta import extract_eta, eta_from_patterns

TODAY = date(2026, 11, 1)


def _eta(text, **kw):
    return eta_from_patterns(text, today=TODAY, **kw)


# ---- the formats these senders actually use -------------------------------------------

@pytest.mark.parametrize("line,expected", [
    ("ETA: 19-11-2026", "2026-11-19"),
    ("ETA: 06-11-2026", "2026-11-06"),
    ("ETA: 24-11-2026", "2026-11-24"),
    ("ETA: 06-Nov-2026", "2026-11-06"),
    ("ETA: 19/11/2026", "2026-11-19"),
    ("ETA : 19.11.2026", "2026-11-19"),
    ("ETA 19-11-2026", "2026-11-19"),
    ("eta: 19-nov-2026", "2026-11-19"),
    ("Estimated Time of Arrival: 19 Nov 2026", "2026-11-19"),
    ("E.T.A: 19-11-2026", "2026-11-19"),
    ("Arrival Date: 19-11-2026", "2026-11-19"),
    ("ETA: 2026-11-19", "2026-11-19"),
])
def test_the_formats_real_senders_use(line, expected):
    assert _eta(line) == expected


def test_day_comes_first():
    """06-11-2026 is 6 November, never 11 June. These are Indian customs documents and a
    month-first reading is wrong by months without ever looking wrong."""
    assert _eta("ETA: 06-11-2026") == "2026-11-06"
    assert _eta("ETA: 12-01-2027") == "2027-01-12"


# ---- the ETD trap ----------------------------------------------------------------------

def test_the_etd_is_never_taken_as_the_eta():
    """The exact shape of a real pre-alert."""
    body = (
        "VESSEL: SITC PORT KLANG / 2618S\n"
        "ETD: 05-11-2026\n"
        "ETA: 19-11-2026\n"
        "MBL NO: SITGSHCEFN00413\n"
        "MBL DATE: 05-11-2026\n"
    )
    assert _eta(body) == "2026-11-19"


def test_an_etd_with_no_eta_anywhere_gives_nothing():
    """Not the departure date, and not a guess either."""
    assert _eta("VESSEL: X / 2618S\nETD: 05-11-2026\nMBL DATE: 05-11-2026\n") is None


def test_etd_on_the_same_line_after_the_eta_is_not_picked_up():
    assert _eta("ETA: 19-11-2026  ETD: 05-11-2026") == "2026-11-19"


def test_the_eta_label_does_not_reach_across_a_line_break():
    """A bare "ETA:" with its date on the next line, and an ETD below - taking "the next
    date after the word ETA" without stopping at the newline grabs the ETD."""
    assert _eta("ETA:\nETD: 05-11-2026") is None


def test_a_word_ending_in_eta_is_not_a_label():
    assert _eta("BETA: 19-11-2026") is None
    assert _eta("ZETA 19-11-2026") is None


# ---- where it appears -------------------------------------------------------------------

def test_the_subject_line_shape_one_real_sender_uses():
    subject = "FW: 72G0085735/AUTOMOTIVE COMPONENTS TECHNOLOGY IN/HB# 6840355813/MB# OOLU8883030720/ETA: 06-Nov-2026"
    assert extract_eta(subject, "", use_model=False, today=TODAY) == "2026-11-06"


def test_the_body_is_preferred_over_the_subject():
    got = extract_eta("Pre-alert ETA: 01-12-2026", "ETA: 19-11-2026",
                      use_model=False, today=TODAY)
    assert got == "2026-11-19", "the body is where the pre-alert's own detail lives"


def test_the_html_part_is_read_when_there_is_no_plain_body():
    got = extract_eta("Pre-alert", "", html="VESSEL: X\nETA: 19-11-2026\n",
                      use_model=False, today=TODAY)
    assert got == "2026-11-19"


# ---- not finding one is a correct answer ------------------------------------------------

def test_most_pre_alerts_have_no_eta_and_that_is_fine():
    """27 of the 42 real emails have none. The job shows "ETA: Not set" and an operator
    fills it in - far better than inventing one."""
    assert extract_eta("Fwd: FLEX - HBL NO: 6911662149",
                       "Please find attached the documents.",
                       use_model=False, today=TODAY) is None


def test_the_date_the_mail_was_sent_is_not_the_eta():
    body = ("============ Forwarded message ============\n"
            "From: operation@4slogistics.com\n"
            "Date: Fri, 13 Nov 2026 11:39:09 +0530\n"
            "Subject: FLEX - HBL NO: 6911662149\n")
    assert _eta(body) is None


def test_empty_input_is_safe():
    assert extract_eta("", "", use_model=False) is None
    assert extract_eta(None, None, use_model=False) is None


# ---- a date that cannot be a sailing is refused -----------------------------------------

def test_a_date_years_away_is_refused():
    """A misread two-digit year, or a reference number that happened to parse as a date."""
    assert _eta("ETA: 19-11-2031") is None
    assert _eta("ETA: 19-11-2019") is None


def test_a_recent_past_eta_is_still_accepted():
    """A pre-alert can arrive after the vessel landed, or be re-sent. Only the far-fetched
    is refused, not the merely late."""
    assert _eta("ETA: 20-10-2026") == "2026-10-20"


def test_a_label_with_nothing_date_shaped_beside_it():
    assert _eta("ETA: TBA") is None
    assert _eta("ETA: to be advised") is None


def test_the_first_eta_wins_when_a_forward_quotes_several():
    """A forwarded chain repeats the same pre-alert. The topmost (newest) is the one to
    take; they are the same date in practice, but the rule has to be definite."""
    body = "ETA: 19-11-2026\n\n--- forwarded ---\nETA: 19-11-2026\n"
    assert _eta(body) == "2026-11-19"
