"""The Bill of Lading number a pre-alert email announces, read off its subject line.

Used to spot the same shipment arriving twice: the same BL from the same sender inside 30
days is a re-send, not a second job, and creating one costs an OCR pass and an AI run on
documents the customer already has a job for.

The subject is the right place to read it. It is written by a person who knows which
shipment this is, it survives forwarding, and - unlike the attachments - reading it costs
nothing, which is what lets the duplicate check run BEFORE a single page is OCR'd.

The real shapes, taken from this product's own 2,383 processed emails:

    FW: OCEAN PRE ALERT DOCUMENT/BL NO : 1078027914// SHIPPER :NOLATO ...
    Fwd: FLEX - HBL NO: 6911662149
    Fwd: HBL no: SHAS00427635
    Fwd: HBL: 897140033682
    Fwd: RE: SEA - Pre-alert : BL - 022G545786 /// MOOWR
    Fwd: FW: 72G0085735/AUTOMOTIVE COMPONENTS .../HB# 6840355813/MB# OOLU8883030720/ETA: ...
    Fwd: AFT LYO - CBL - 5970000523 - PALLAVA TEXTILES

THE MASTER BL IS NOT THE ONE. `MB#`, `MBL`, `MAWB` and "master" name the carrier's own bill,
which several different house shipments can share - keying duplicates off it would call two
genuinely separate jobs the same one. The subject above carries both: `HB# 6840355813` is
the house bill, and it is the one the job itself shows as its BL No.
"""
import re

# The house bill, by every name these senders give it. Longest first so "HBL NO" wins over
# a bare "BL" inside it.
_LABEL = (
    r"(?:HBL\s*(?:NO|NUMBER)?|HOUSE\s*B/?L(?:\s*NO)?|HB\s*#|CBL|B/?L\s*(?:NO|NUMBER)|B/?L)"
)
# A master-bill label right before the number means this is not the one we want. "MASTER"
# stands on its own here because in "MASTER BL : X" the match itself starts at "BL", so by
# the time we look back only the word "MASTER" is left in front of it.
_MASTER = re.compile(
    r"(?:MB\s*#|MBL|MAWB|MASTER)\s*(?:B/?L)?\s*(?:NO|NUMBER)?\s*[:\-]?\s*$",
    re.IGNORECASE,
)

# No "/" inside the number itself. In every real subject it is a SEPARATOR - "HB# 6840355813/
# MB# OOLU8883030720" is two different bills - and allowing it swallowed the next label,
# giving "6840355813MB" as the house bill.
_BL = re.compile(
    rf"\b{_LABEL}\s*[:\-#]?\s*([A-Z0-9][A-Z0-9-]{{4,24}})",
    re.IGNORECASE,
)

# Words that are never a BL number, however the pattern lines up.
_NOT_A_NUMBER = {"NUMBER", "DOCUMENT", "SHIPPER", "DETAILS", "ATTACHED", "PREALERT",
                 "ALERT", "MOOWR", "NOTICE", "COPY", "DRAFT", "FINAL"}


def normalise_bl(value: str | None) -> str:
    """One spelling for comparison. Punctuation and case are how someone typed it, not part
    of the number - "SHAS-00427635" and "shas00427635" are the same bill."""
    return re.sub(r"[^A-Z0-9]+", "", (value or "").upper())


def extract_bl_number(subject: str | None) -> str | None:
    """The house BL this subject announces, or None.

    None is a perfectly ordinary answer - plenty of mail carries no BL at all, and those
    simply go through the normal path with no duplicate check.
    """
    text = " ".join((subject or "").split())
    if not text:
        return None
    for m in _BL.finditer(text):
        # A master-bill label immediately before this match disqualifies it.
        if _MASTER.search(text[max(0, m.start() - 24): m.start()]):
            continue
        raw = m.group(1).strip(" -/:#")
        if raw.upper() in _NOT_A_NUMBER:
            continue
        key = normalise_bl(raw)
        # Long enough to identify a shipment. A 4-character "scrap" would collide constantly.
        if len(key) < 5:
            continue
        # All letters is a word, not a bill number.
        if not any(c.isdigit() for c in key):
            continue
        return key
    return None
