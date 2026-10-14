"""The learned half of a kind="lookup" custom field, alongside the customer's uploaded material
master (app/core/material_master.py). The master answers most lines; a line it has nothing for
is typed in once by an operator, and that answer is kept here so the SAME identifying values
never have to be typed twice - on this job or any later one, because a part's classification
does not change from shipment to shipment.

Matching is exact on normalised text by default - material_master.py uses the same rule for
the same reason: a near-miss on a material code or CTH is a wrong customs declaration, not a
near-enough guess. A field can opt into fuzzy matching instead (fuzzy_match=True, is_target_value
fields only - see _fuzzy_similar) for cases like a freight forwarder's name, where "KUEHNE +
NAGEL PVT. LTD." and "KUEHNE+NAGEL" are obviously the same company and an exact-only match would
just ask the operator to re-type an answer it already has under a different spelling.
"""
import difflib
import logging

from sqlalchemy.orm import Session

from app.models.custom_field_reference import CustomFieldReferenceValue

logger = logging.getLogger(__name__)

MATCH_SLOTS = 4

# Two machine-read company names carry more incidental noise (OCR, spacing/punctuation around
# "&"/"+", a legal suffix present on one and not the other) than the page-filter's two copies
# of the same PDF page, so this is a touch looser than that feature's 0.85 - the substring
# check in _fuzzy_similar already catches the single most common case (abbreviation vs full
# legal name) exactly, so this threshold only has to cover genuine spelling/OCR noise.
FUZZY_SIMILARITY_THRESHOLD = 0.82


# A handful of corporate-suffix words that show up spelled out on one document and
# abbreviated on the next - the same real company, but "PRIVATE LIMITED" and "PVT LTD" share
# no substring and sit nowhere near 0.82 on a character-level ratio once the rest of the name
# is short (found live: "KUEHNE AND NAGEL PRIVATE LIMITED" vs the reference table's "KUEHNE +
# NAGEL PVT LTD" would not have matched, and a manual correction would have gone on to add a
# second, near-duplicate row for the same freight forwarder). Word-level, applied before the
# alnum squash below, so "AND" only ever folds when it stands alone as a word (never a name
# that happens to contain "AND" as a substring).
_SUFFIX_SYNONYMS = {
    "AND": "", "PRIVATE": "PVT", "LIMITED": "LTD", "COMPANY": "CO", "CORPORATION": "CORP",
}


def _fuzzy_key(text: str) -> str:
    """Letters and digits only - so 'Kuehne + Nagel Pvt. Ltd.' and 'KUEHNE+NAGEL' compare
    equal regardless of spacing or punctuation around a company's own name, and so a spelled-out
    corporate suffix and its abbreviation (see _SUFFIX_SYNONYMS) compare equal too."""
    words = "".join(ch if ch.isalnum() else " " for ch in str(text or "").upper()).split()
    words = [_SUFFIX_SYNONYMS.get(w, w) for w in words]
    return "".join(words)


def _fuzzy_similar(a: str, b: str) -> bool:
    """Same real-world thing, allowing for OCR noise and a missing/present legal suffix -
    ONLY ever called for a field that opted in via fuzzy_match=True. A material code or CTH
    lookup never calls this; a near-miss there is a wrong customs declaration, not a
    near-enough guess (see material_master.py's own comment on the same point)."""
    ka, kb = _fuzzy_key(a), _fuzzy_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if ka in kb or kb in ka:
        # One is the other's abbreviation, or one carries a legal suffix ("PVT LTD", "CO LTD")
        # the other doesn't - "KUEHNENAGEL" inside "KUEHNENAGELPVTLTD" is exactly that, not a
        # coincidence worth scoring more cautiously.
        return True
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= FUZZY_SIMILARITY_THRESHOLD


def normalize_key(text: str) -> str:
    """One spelling for comparison: upper case, single spaces, no surrounding punctuation.

    The same normalisation material_master._norm uses, kept as its own copy rather than an
    import of a name that starts with an underscore there - two modules relying on each other's
    "private" helper is how one changes shape under the other without anyone noticing.
    """
    return " ".join(str(text or "").split()).strip(" -–—:,;()").upper()


def _padded_slots(match_values) -> list[str]:
    values = [normalize_key(v) for v in (match_values or [])][:MATCH_SLOTS]
    values += [""] * (MATCH_SLOTS - len(values))
    return values


def _owner_filter(custom_field_id: str | None, mark_id: str | None):
    # Exactly one of the two is ever set (see the model's check constraint) - filtering on
    # both columns at once, rather than picking whichever id was passed, is what keeps a
    # mark-owned row and a custom-field-owned row from ever being confused for each other.
    return (
        CustomFieldReferenceValue.custom_field_id == custom_field_id,
        CustomFieldReferenceValue.mark_id == mark_id,
    )


def lookup_reference(
    db: Session, custom_field_id: str | None = None, match_values=None, *, mark_id: str | None = None,
    fuzzy: bool = False,
) -> str | None:
    """The value learned for this field + these identifying values, or None if nothing has
    been learned for them yet. For a kind="lookup" custom field, called after the material
    master has already been tried and found nothing - the dump data always wins when it has an
    answer. For an is_target_value field (custom_field_id OR mark_id, never both), this IS the
    only source - there is no separate sheet behind it.

    fuzzy=True (only ever set for a field with fuzzy_match=True - see _fuzzy_similar) matches
    on the same real-world thing despite formatting noise, at the cost of leaving the database
    unable to do the comparison itself: every row this owner has gets fetched and compared in
    Python. Fine at this scale (one field's own reference table, typically a few dozen-to-
    hundred known names, e.g. a freight forwarder's) and never the default - a kind="lookup"
    material/CTH call never passes it. A field keyed on TWO OR MORE values that needs fuzzy
    matching at real scale (a ~72,000-row world port table, say) uses
    lookup_reference_ai_assisted instead, which this function knows nothing about - see
    _resolve_target_value's own two-step call for how the two fit together (exact first here,
    then that, only if this finds nothing).
    """
    slots = _padded_slots(match_values)
    if not any(slots):
        # Nothing to key on at all - every field's untyped lines would collide on this one row,
        # each overwriting whatever a DIFFERENT blank line had just been given.
        return None
    if not fuzzy:
        row = (
            db.query(CustomFieldReferenceValue)
            .filter(
                *_owner_filter(custom_field_id, mark_id),
                CustomFieldReferenceValue.match_value_1 == slots[0],
                CustomFieldReferenceValue.match_value_2 == slots[1],
                CustomFieldReferenceValue.match_value_3 == slots[2],
                CustomFieldReferenceValue.match_value_4 == slots[3],
            )
            .first()
        )
        return row.resolved_value if row is not None else None
    candidates = (
        db.query(CustomFieldReferenceValue).filter(*_owner_filter(custom_field_id, mark_id)).all()
    )
    # An EXACT match always wins over a fuzzy one, even here - fuzzy is a fallback for when
    # nothing matches exactly, never a reason to prefer a near-miss over a row that already
    # matches perfectly (two genuinely different real-world things can still satisfy
    # _fuzzy_similar's own abbreviation/substring rule, e.g. two company names that happen to
    # share a word). Checking every candidate for an exact match FIRST, before any fuzzy
    # comparison runs at all, is what keeps a real exact match from ever losing to a
    # coincidental fuzzy one further down the same candidate list.
    for row in candidates:
        stored = [row.match_value_1, row.match_value_2, row.match_value_3, row.match_value_4]
        if all((not q) or (not s) or q == s for q, s in zip(slots, stored)) and any(
            q and s for q, s in zip(slots, stored)
        ):
            return row.resolved_value
    for row in candidates:
        stored = [row.match_value_1, row.match_value_2, row.match_value_3, row.match_value_4]
        # Every slot either side actually uses must agree (fuzzily); a slot blank on both sides
        # is not itself a match signal, or every row with an unused slot 2/3/4 would tie.
        if any(q and s and not _fuzzy_similar(q, s) for q, s in zip(slots, stored)):
            continue
        if any(q and s for q, s in zip(slots, stored)):
            return row.resolved_value
    return None


# How many of the closest-ranked candidates ever get shown to the model - a bound on the
# token cost regardless of how many rows one exact-prefix group holds (a big country can have
# hundreds of ports).
AI_ASSISTED_CANDIDATE_LIMIT = 15


def lookup_reference_ai_assisted(
    db: Session, custom_field_id: str, match_values, *, field_description: str = "a value",
) -> str | None:
    """Multi-key resolution for a field whose exact lookup (lookup_reference, fuzzy=False)
    already came back empty - called ONLY as that second step, never on its own (see
    _resolve_target_value). Every key except the LAST is matched exactly in SQL first (e.g. a
    shipment port's own country), narrowing a huge reference table (a ~72,000-row world port
    list, say) down to the rows that could possibly apply - still potentially thousands for a
    major trading country, far too many to ever show the model directly.

    Two AI steps, in order, for two different kinds of mismatch:

    1. NORMALIZE first (normalize_place_name_with_ai) - asks the model for the current,
       standard name of the last key as printed (e.g. "Peking" -> "Beijing", "Saigon" -> "Ho
       Chi Minh City"), using nothing but its own world knowledge, no candidate list at all.
       This is what catches a GENUINE rename: a truly renamed place shares almost no
       characters with its current name, so step 2's string-similarity ranking would never
       even put the right row in front of the model to pick from. The normalized name is
       tried as an exact match first (free), then feeds step 2 if that still misses.
    2. RANK then PICK (resolve_fuzzy_candidate_with_ai) - ranks the (possibly re-normalized)
       candidates by plain string similarity and shows only the closest
       AI_ASSISTED_CANDIDATE_LIMIT to the model, which picks the one that's really the same
       place - catching OCR noise or a minor spelling variant step 1 was never meant to fix.

    Returns None if there is nothing to key on, no candidates at all, or neither step finds a
    genuine match.
    """
    slots = _padded_slots(match_values)
    real_keys = len([v for v in (match_values or []) if (v or "").strip()])
    if real_keys < 2:
        # Nothing to pre-filter on - a single-key fuzzy field uses lookup_reference's own
        # fuzzy=True path instead, which scans its own (typically small) table directly.
        return None
    exact_columns = [
        CustomFieldReferenceValue.match_value_1, CustomFieldReferenceValue.match_value_2,
        CustomFieldReferenceValue.match_value_3, CustomFieldReferenceValue.match_value_4,
    ]

    def _prefiltered(exact: bool) -> list[CustomFieldReferenceValue]:
        query = db.query(CustomFieldReferenceValue).filter(
            CustomFieldReferenceValue.custom_field_id == custom_field_id,
        )
        for i in range(real_keys - 1):
            col = exact_columns[i]
            query = query.filter(col == slots[i] if exact else col.ilike(f"%{slots[i]}%"))
        return query.all()

    candidates = _prefiltered(exact=True)
    if not candidates:
        # The pre-filter key (e.g. a shipment's own country) is sometimes a common/informal
        # name the sheet itself never uses verbatim - "Korea" where the world port table's
        # own spelling is "SOUTH KOREA" - so an exact match finds nothing before the LAST
        # key's own fuzzy matching (below) ever gets a chance to run at all. Falling back to a
        # substring match only ever WIDENS an empty result - an exact match is always also a
        # substring match, so a lookup that already worked is completely unaffected - and the
        # last key still has to agree (ranked, then AI-confirmed) before anything is returned,
        # so a wider country net can never by itself produce a wrong port; at worst it fails
        # to narrow the field down as much as an exact country match would have.
        candidates = _prefiltered(exact=False)
    if not candidates:
        return None
    last_idx = real_keys - 1
    last_query = slots[last_idx]

    def _stored_last(row: CustomFieldReferenceValue) -> str:
        return [row.match_value_1, row.match_value_2, row.match_value_3, row.match_value_4][last_idx] or ""

    from app.core.llm import normalize_place_name_with_ai, resolve_fuzzy_candidate_with_ai

    normalized = normalize_place_name_with_ai(match_values[last_idx] or "",
                                              field_description=field_description)
    if normalized:
        normalized_key = normalize_key(normalized)
        for row in candidates:
            if _stored_last(row) == normalized_key:
                return row.resolved_value
        # Not an exact hit even normalized - still worth ranking against, since the
        # normalized form reads far closer to the sheet's own spelling than the original did.
        last_query = normalized_key

    ranked = sorted(
        candidates,
        key=lambda row: difflib.SequenceMatcher(
            None, _fuzzy_key(last_query), _fuzzy_key(_stored_last(row)),
        ).ratio(),
        reverse=True,
    )[:AI_ASSISTED_CANDIDATE_LIMIT]
    if not ranked:
        return None
    pairs = [(_stored_last(row), row.resolved_value) for row in ranked]
    return resolve_fuzzy_candidate_with_ai(
        last_query, pairs, field_description=field_description,
    ) or None


def remember_reference(
    db: Session, custom_field_id: str | None = None, match_values=None, resolved_value: str = "",
    *, mark_id: str | None = None, fuzzy: bool = False,
) -> None:
    """Save what was just typed/corrected for these identifying values, so it is not asked for
    again. Does not commit - the caller decides the transaction boundary, the same as every
    other write-then-let-the-caller-commit helper in this codebase.

    Silently does nothing for a blank answer or values with nothing to key on: a row like that
    would either remember nothing worth remembering, or swallow every other blank line for this
    field the moment one of them is filled in.

    fuzzy=True (only ever set for a field with fuzzy_match=True, mirroring lookup_reference's
    own `fuzzy` param): checked BEFORE the exact-slot lookup below, not just after it. An
    operator only ever reaches this path when lookup_reference (itself fuzzy-aware) already
    found nothing - but that only means no EXISTING row was close enough on ITS reading of the
    text; the freshly typed correction can still be a near-miss on a row that lookup rejected,
    or on one added since. Without this, every spelling variant that keeps arriving ("KUEHNE +
    NAGEL PVT LTD", "KUEHNE AND NAGEL PRIVATE LIMITED", ...) adds its OWN row instead of
    updating the one the fuzzy reader already treats as the same company - exactly the
    duplicate-row growth this field's fuzzy_match=True was meant to prevent.
    """
    value = (resolved_value or "").strip()
    if not value:
        return
    slots = _padded_slots(match_values)
    if not any(slots):
        return
    row = None
    if fuzzy:
        for cand in db.query(CustomFieldReferenceValue).filter(*_owner_filter(custom_field_id, mark_id)).all():
            stored = [cand.match_value_1, cand.match_value_2, cand.match_value_3, cand.match_value_4]
            if any(q and s and not _fuzzy_similar(q, s) for q, s in zip(slots, stored)):
                continue
            if any(q and s for q, s in zip(slots, stored)):
                row = cand
                break
    if row is None:
        row = (
            db.query(CustomFieldReferenceValue)
            .filter(
                *_owner_filter(custom_field_id, mark_id),
                CustomFieldReferenceValue.match_value_1 == slots[0],
                CustomFieldReferenceValue.match_value_2 == slots[1],
                CustomFieldReferenceValue.match_value_3 == slots[2],
                CustomFieldReferenceValue.match_value_4 == slots[3],
            )
            .first()
        )
    if row is not None:
        if row.resolved_value != value:
            logger.info("reference cache: updating %s (was %r, now %r)", slots, row.resolved_value, value)
            row.resolved_value = value
        return
    owner = f"custom field {custom_field_id}" if custom_field_id else f"mark {mark_id}"
    logger.info("reference cache: learned %s -> %r for %s", slots, value, owner)
    db.add(CustomFieldReferenceValue(
        custom_field_id=custom_field_id, mark_id=mark_id,
        match_value_1=slots[0], match_value_2=slots[1],
        match_value_3=slots[2], match_value_4=slots[3],
        resolved_value=value,
    ))


def stage_upload_row(
    db: Session, custom_field_id: str | None, match_values, resolved_value: str, *, mark_id: str | None = None,
) -> dict | None:
    """One row of a bulk template upload: added if the key is new, left untouched and reported
    if the key already has a DIFFERENT value on file, silently counted as unchanged if it
    already has the SAME one. None for a row with nothing worth recording.

    Unlike remember_reference, a conflict here is never auto-resolved - a bulk upload is a
    second, independent source of data arriving on top of whatever the table already knows
    (possibly typed in by a person on a real job), and picking one over the other silently
    would make that choice for someone who never got asked. Whether to keep the old value or
    apply the new one is a separate, explicit decision - see update_reference_value.

    Returns {"status": "added"|"unchanged"|"conflict", "row": CustomFieldReferenceValue,
    "match_values": [...], "uploaded_value": str} for a row that named a key, else None.
    """
    value = (resolved_value or "").strip()
    slots = _padded_slots(match_values)
    if not value or not any(slots):
        return None
    row = (
        db.query(CustomFieldReferenceValue)
        .filter(
            *_owner_filter(custom_field_id, mark_id),
            CustomFieldReferenceValue.match_value_1 == slots[0],
            CustomFieldReferenceValue.match_value_2 == slots[1],
            CustomFieldReferenceValue.match_value_3 == slots[2],
            CustomFieldReferenceValue.match_value_4 == slots[3],
        )
        .first()
    )
    if row is None:
        row = CustomFieldReferenceValue(
            custom_field_id=custom_field_id, mark_id=mark_id,
            match_value_1=slots[0], match_value_2=slots[1],
            match_value_3=slots[2], match_value_4=slots[3],
            resolved_value=value,
        )
        db.add(row)
        return {"status": "added", "row": row, "match_values": slots, "uploaded_value": value}
    if row.resolved_value == value:
        return {"status": "unchanged", "row": row, "match_values": slots, "uploaded_value": value}
    return {"status": "conflict", "row": row, "match_values": slots, "uploaded_value": value}


def update_reference_value(db: Session, row: CustomFieldReferenceValue, resolved_value: str) -> None:
    """The explicit "update the existing data" choice for a row an upload found already
    present with a different value - or any other direct edit. Does not commit."""
    row.resolved_value = (resolved_value or "").strip()
