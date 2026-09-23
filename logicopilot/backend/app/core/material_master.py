"""A customer's material master: their part code -> the CTH (HS code) it is declared under.

Some importers ship the same few hundred parts over and over, and the customs classification of
each one is settled long before any particular shipment. Nokia is one: the invoice names a
material like "839599A" and the CTH that goes with it is 85177990, every time. That is not
something to work out from the document - the document does not say it - and not something to
ask an operator to type on every line either. It is a lookup against a list the customer
supplies.

The list is an ordinary spreadsheet exported from their own system, so the columns are whatever
they happen to be called:

    the MATERIAL column      - any header containing "material" (not "desc") or "part"
    the DESCRIPTION column   - any header containing "desc"
    the CODE column          - any header containing "comm", "imp", "code", "cth" or "hsn"

which is how the same file is read in customflow, deliberately: it is the same export, and a
customer should not have to reformat it to use it here.

BOTH the code and the description are indexed against that row's CTH. An invoice line does not
always carry the part number - sometimes the description is all there is - and the customer's
own list has that column anyway, so there is no reason to only look at one of them.

The code is cleaned to DIGITS ONLY, first 8 - an export may carry "8517 79 90" or "85177990.0"
and the ERP wants 85177990.
"""

import logging
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Where a group's uploaded master lives. One per template group.
MASTER_DIR = "material_master"

# Bump when the SHAPE of the persisted copy changes. The stamp only notices a changed FILE, so
# without this a code change that stores something different keeps reading the old layout back
# and nobody can tell why. v1 stored the raw header row, empty cells and all.
_CACHE_VERSION = 2

_MATERIAL_HINTS = ("material", "part", "item code", "itemcode")
_CODE_HINTS = ("comm", "imp", "code", "cth", "hsn", "hs code", "tariff")


def master_path(group_id: str) -> Path:
    """The uploaded master for this template group, whatever extension it came with."""
    base = Path(get_settings().uploads_dir) / MASTER_DIR / group_id
    if base.parent.exists():
        for candidate in sorted(base.parent.glob(f"{group_id}.*")):
            # Sidecars sit beside the sheet: .name holds the admin's original filename, .json
            # the parsed copy, .building the in-flight marker. None of them is the workbook -
            # and ".building" sorting alphabetically before ".xlsx" is exactly how this used to
            # pick the marker file itself and hand it to openpyxl.
            if candidate.suffix.lower() not in (".name", ".json", ".building"):
                return candidate
    return base.with_suffix(".xlsx")


def sheet_headers(group_id: str) -> list[str]:
    """The reference sheet's own column headings, in order.

    The wizard offers these so an admin picks the columns by the names their export actually
    uses, rather than us guessing and being wrong on the one customer whose export calls it
    something else.

    Cached like the mapping is, and for a sharper reason: reading one row still means openpyxl
    parsing the sheet, which on a real 9.8 MB master measured 32 SECONDS. The wizard asks for
    these when the screen opens, so uncached it would simply hang.
    """
    path = master_path(group_id)
    if not path.exists():
        _HEADERS.pop(group_id, None)
        return []
    stamp = _stamp(path)
    hit = _HEADERS.get(group_id)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    # The parsed copy carries the headings, whatever columns it was parsed for - the headings
    # are a property of the file, not of the question being asked of it.
    try:
        import json

        blob = json.loads(_disk_cache(group_id).read_text(encoding="utf-8"))
        cached = blob.get("headers")
        if isinstance(cached, list) and cached and list(blob.get("stamp") or [])[:3] == list(stamp):
            # Filtered here too, not only when written. A copy made by an older version holds
            # the raw row, and re-reading it must not put nine options in a dropdown when the
            # sheet has four named columns.
            named = [str(h).strip() for h in cached if h is not None and str(h).strip()]
            if named:
                _HEADERS[group_id] = (stamp, named)
                return named
    except Exception:  # noqa: BLE001
        pass
    wb = None
    try:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        row = next(ws.iter_rows(values_only=True), ())
        out = [str(h).strip() for h in row if h is not None and str(h).strip()]
        _HEADERS[group_id] = (stamp, out)
        return out
    except Exception:  # noqa: BLE001
        logger.exception("could not read the headings of the reference sheet for %s", group_id)
        return []
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:  # noqa: BLE001
                pass


def _disk_cache(group_id: str) -> Path:
    """Where the parsed sheet is kept between restarts."""
    return master_path(group_id).with_suffix(".json")


def _read_disk_cache(group_id: str, stamp: tuple) -> dict[str, str] | None:
    """The parsed sheet from disk, if it was parsed from THIS file with THESE columns.

    Restarting the process used to mean the next person to open the screen waited ~50 seconds
    while a 9.8 MB workbook was parsed twice - and pm2 restarts on every deploy. A JSON copy
    beside the sheet turns that into a 50 ms read. The stamp includes the file's size and
    modification time and the chosen columns, so a replaced sheet is never served from it.
    """
    path = _disk_cache(group_id)
    if not path.exists():
        return None
    try:
        import json

        blob = json.loads(path.read_text(encoding="utf-8"))
        if list(blob.get("stamp") or []) != [list(x) if isinstance(x, tuple) else x
                                             for x in stamp]:
            return None
        got = blob.get("map")
        return got if isinstance(got, dict) else None
    except Exception:  # noqa: BLE001
        logger.warning("the cached copy of the reference sheet for %s is unusable", group_id)
        return None


def _write_disk_cache(group_id: str, stamp: tuple, mapping: dict[str, str],
                      headers: list[str]) -> None:
    try:
        import json

        _disk_cache(group_id).write_text(
            json.dumps({"stamp": list(stamp), "map": mapping, "headers": headers}),
            encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("could not keep a parsed copy of the reference sheet for %s", group_id)


def remember_name(group_id: str, original: str) -> None:
    """Keep the name the admin's file actually had.

    The sheet is stored as <group_id>.xlsx so there can only ever be one per customer - which
    is right, but it means the screen was showing an admin a UUID instead of the file they
    picked. "JUNE DUMP FILE.xlsx" tells them which export is attached and roughly when it is
    from; "29334bf9-bef0-4bad-82d2-ae4a012ad943.xlsx" tells them nothing.
    """
    try:
        side = master_path(group_id).with_suffix(".name")
        side.parent.mkdir(parents=True, exist_ok=True)
        side.write_text((original or "").strip()[:200], encoding="utf-8")
    except OSError:
        logger.warning("could not record the reference sheet's name for %s", group_id)


def original_name(group_id: str) -> str:
    """The name the file was uploaded under, or "" if it was never recorded."""
    try:
        side = master_path(group_id).with_suffix(".name")
        return side.read_text(encoding="utf-8").strip() if side.exists() else ""
    except OSError:
        return ""


def clean_code(raw: str) -> str:
    """Digits only, first 8. "8517 79 90", "85177990.0" and "85177990" all mean the same CTH."""
    return "".join(ch for ch in str(raw or "") if ch.isdigit())[:8]


def _stem(key: str) -> str:
    """A part code without its variant suffix: "094624A.101" -> "094624A".

    Only a suffix that looks like one is removed - a short run of letters and digits after a
    single dot. A description that happens to contain a full stop keeps its own text, and a
    code with no dot is returned unchanged. Returns "" when there is nothing to strip, so the
    caller can tell "no variant here" from "the stem is the whole thing".
    """
    if "." not in key:
        return ""
    head, _, tail = key.partition(".")
    head = head.strip()
    tail = tail.strip()
    if not head or not tail or len(tail) > 4 or not tail.isalnum():
        return ""
    return head


def _pick(headers: list[str], hints: tuple, exclude: tuple = ()) -> int | None:
    for i, h in enumerate(headers):
        low = str(h or "").strip().lower()
        if not low or any(x in low for x in exclude):
            continue
        if any(x in low for x in hints):
            return i
    return None


def _norm(text: str) -> str:
    """One spelling for comparison: upper case, single spaces, no surrounding punctuation.

    An invoice prints "AREC PA PLATE  A" where the master says "AREC PA PLATE A", and OCR adds
    its own spacing. Normalising both sides matches those without any fuzzy guessing, which
    matters here - a wrong CTH is a wrong customs declaration, so a near-miss must not match.
    """
    return " ".join(str(text or "").split()).strip(" -–—:,;()").upper()


# The parsed master, kept between jobs. A real one is 22,000 rows and takes 13-17 SECONDS to
# read, which every job was paying - and an email pull of four jobs paid four times over. The
# file changes only when an admin uploads a new one, so it is keyed on the file's identity
# (path, size, modification time): re-upload and the next call reads the new file, touch
# nothing and it is free.
_CACHE: dict[str, tuple[tuple, dict[str, str]]] = {}
# The headings, cached separately: the wizard wants only these, and paying a 32-second parse to
# read one row is not something a screen can do while someone waits for it.
_HEADERS: dict[str, tuple[tuple, list[str]]] = {}


def _stamp(path: Path) -> tuple:
    try:
        st = path.stat()
        return (str(path), st.st_size, int(st.st_mtime_ns))
    except OSError:
        return (str(path), 0, 0)


def clear_cache(group_id: str | None = None) -> None:
    """Forget the parsed master and its headings - for one group, or all of them."""
    if group_id is None:
        _CACHE.clear()
        _HEADERS.clear()
        return
    _CACHE.pop(group_id, None)
    _HEADERS.pop(group_id, None)
    # And the copy on disk: leaving it would let a replaced sheet be served from the old parse
    # after the next restart, which is the one failure a cache must never have.
    try:
        _disk_cache(group_id).unlink(missing_ok=True)
    except OSError:
        logger.warning("could not remove the parsed copy for %s", group_id)
    # A stale marker (left behind by a parse that crashed the whole process before its finally
    # ran) would otherwise convince every future upload that a parse is already under way,
    # forever - the one thing worse than a slow parse is one that can never be retried.
    try:
        _building_marker(group_id).unlink(missing_ok=True)
    except OSError:
        pass


def cached_mapping(group_id: str, match_columns: list | None = None,
                   return_column: str | None = None) -> dict[str, str] | None:
    """The mapping if it is already sitting in memory or on disk - never parses the workbook
    live. None means a real parse is needed, which the caller must not do inline on a
    request-serving worker (see start_background_parse)."""
    path = master_path(group_id)
    if not path.exists():
        _CACHE.pop(group_id, None)      # the master was deleted; do not serve a stale one
        return None
    want = (tuple(sorted(str(c) for c in (match_columns or []))), str(return_column or ""))
    stamp = _stamp(path) + want + (_CACHE_VERSION,)
    hit = _CACHE.get(group_id)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    from_disk = _read_disk_cache(group_id, stamp)
    if from_disk is not None:
        _CACHE[group_id] = (stamp, from_disk)
        logger.info("material master for %s: %s keys from the parsed copy on disk",
                    group_id, len(from_disk))
        return from_disk
    return None


def _building_marker(group_id: str) -> Path:
    """A plain sentinel file, not part of the cache - disk state so EVERY worker process can
    see a parse is already under way, not just the one that started it."""
    return master_path(group_id).with_suffix(".building")


def is_building(group_id: str) -> bool:
    return _building_marker(group_id).exists()


def start_background_parse(group_id: str) -> None:
    """Parse a 9.8 MB / 22,000-row master off the request thread. Measured at 25-32 seconds -
    long enough that running it inline in a request handler was observed to get that worker
    killed outright (its process supervisor treats a worker that goes quiet that long as hung),
    taking down live traffic on every attempt. A background thread decouples the parse from any
    particular request/response, so a slow or even failing parse can no longer touch a worker
    that is also serving other jobs. The .building marker (see is_building) is what lets ANY
    worker's GET notice a parse is already in flight, since this thread only exists in the one
    process that started it.
    """
    import threading

    marker = _building_marker(group_id)
    if marker.exists():
        return  # already being parsed - by this worker or another one
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")

    def _run() -> None:
        try:
            load_master(group_id)
        except Exception:  # noqa: BLE001
            logger.exception("background parse of the reference sheet for %s failed", group_id)
        finally:
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass

    threading.Thread(target=_run, daemon=True, name=f"material-master-parse-{group_id}").start()


def load_master(group_id: str, match_columns: list | None = None,
                return_column: str | None = None) -> dict[str, str]:
    """Everything that identifies a part -> its CTH. {} if the customer has no master.

    Both the MATERIAL code and the MATERIAL DESCRIPTION are indexed, pointing at the same
    row's code, because an invoice line does not always carry the part number - sometimes all
    there is to go on is the description, and the customer's own list has that column too.

    Never raises: a customer without a master, or with an unreadable one, simply falls back to
    reading the code off the documents. Failing a whole extraction over a missing lookup file
    would be worse than a blank CTH an operator can fill in.

    Parses live if nothing is cached yet - safe to call from extraction (already running off
    its own background thread, see jobs.py) or from start_background_parse's own thread, but
    NEVER directly from a request handler for an uploaded-but-not-yet-parsed file; use
    _cached_only + start_background_parse there instead (see material_master_status).
    """
    path = master_path(group_id)
    if not path.exists():
        _CACHE.pop(group_id, None)      # the master was deleted; do not serve a stale one
        return {}
    cached = cached_mapping(group_id, match_columns, return_column)
    if cached is not None:
        return cached
    # The chosen columns are part of the cache key: the same sheet answering a different
    # question is a different mapping, and serving one for the other would be silent nonsense.
    want = (tuple(sorted(str(c) for c in (match_columns or []))), str(return_column or ""))
    stamp = _stamp(path) + want + (_CACHE_VERSION,)
    wb = None
    try:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = ws.iter_rows(values_only=True)
        headers = [str(h or "") for h in next(rows, ())]
        # NOTE: wb.close() in the finally below is not optional. A read_only workbook holds the
        # file open, and this one is 10 MB: leaving it open means the admin cannot replace the
        # master (Windows refuses outright, and a handle leaks per group everywhere else).
        lower = [h.strip().lower() for h in headers]

        def _named(name: str) -> int | None:
            n = str(name or "").strip().lower()
            return lower.index(n) if n and n in lower else None

        # Named columns win. Falling back to the heading hints keeps a sheet working when
        # nobody has chosen anything yet, which is every existing template.
        chosen = [i for i in (_named(c) for c in (match_columns or [])) if i is not None]
        code_i = _named(return_column) if return_column else None
        if code_i is None:
            code_i = _pick(headers, _CODE_HINTS, exclude=("desc",))
        if chosen:
            mat_i, desc_i = chosen[0], (chosen[1] if len(chosen) > 1 else None)
        else:
            mat_i = _pick(headers, _MATERIAL_HINTS, exclude=("desc",))
            desc_i = _pick(headers, ("desc",))
        if code_i is None or (mat_i is None and desc_i is None):
            logger.warning(
                "material master for %s: could not find a code column and something to key it "
                "on in %s", group_id, headers[:12])
            return {}
        # Collected first, indexed second. A description is only safe to match on if the whole
        # list agrees what it is: in a real 22,000-row master, 569 descriptions (4.3%) point at
        # more than one CTH - "PRINTED WIRING BOARD" is 85177910 or 85340000, "PACKAGING
        # MODULE" is one of five. Matching those on the description would pick whichever row
        # came first and file 4% of lines under a classification chosen by sort order. Part
        # numbers have no such problem: all 19,611 of them are unique.
        materials: dict[str, str] = {}
        desc_codes: dict[str, set] = {}
        for row in rows:
            if code_i >= len(row):
                continue
            code = clean_code(row[code_i])
            if not code:
                continue
            if mat_i is not None and mat_i < len(row):
                key = _norm(row[mat_i])
                if key:
                    materials.setdefault(key, code)
            if desc_i is not None and desc_i < len(row):
                key = _norm(row[desc_i])
                if key:
                    desc_codes.setdefault(key, set()).add(code)

        out: dict[str, str] = dict(materials)
        ambiguous = 0
        for key, codes in desc_codes.items():
            if len(codes) > 1:
                ambiguous += 1          # the list disagrees with itself - do not guess
                continue
            out.setdefault(key, next(iter(codes)))
        logger.info(
            "material master for %s: %s keys from %s (%s by %r, %s by %r, %s description(s) "
            "left out because the list gives them more than one code) -> %r",
            group_id, len(out), path.name, len(materials),
            headers[mat_i] if mat_i is not None else "-",
            len(out) - len(materials), headers[desc_i] if desc_i is not None else "-",
            ambiguous, headers[code_i])
        # The FILTERED headings, not the raw row. `headers` keeps every cell of row 1 so a
        # column can be found by its index, and a sheet with 9 columns of which 4 are named
        # would otherwise offer the wizard five blank options to choose from.
        named = [h.strip() for h in headers if h and h.strip()]
        _CACHE[group_id] = (stamp, out)
        _HEADERS[group_id] = (_stamp(path), named)
        _write_disk_cache(group_id, stamp, out, named)
        return out
    except Exception:  # noqa: BLE001
        logger.exception("could not read the material master for %s", group_id)
        return {}
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:  # noqa: BLE001
                pass


def material_from(code: str, description: str) -> str:
    """The material code for one invoice line.

    The extracted code if there is one. Otherwise the leading token of the description, which is
    how these invoices are written - "839599A - AREC PA PLATE A" - but only when it contains a
    digit, so a line that simply starts with a word is not mistaken for a part number.
    """
    got = str(code or "").strip()
    if got:
        return got
    head = str(description or "").strip().split()
    for token in head[:2]:
        cleaned = token.strip("-–—:,;()").strip()
        if len(cleaned) >= 3 and any(ch.isdigit() for ch in cleaned):
            return cleaned
    return ""


def lookup_cth(material: str, master: dict[str, str], description: str = "") -> str:
    """The CTH for this line, or "".

    Tries the part number first and the description second - the part number is the more
    precise thing to match on, and a description is only worth using when there is no code.
    Matching is exact once both sides are normalised; nothing fuzzy, because a part that is
    merely similar is a different part with a different classification.
    """
    if not master:
        return ""
    for candidate in (material, description):
        key = _norm(candidate)
        if not key:
            continue
        if key in master:
            return master[key]
        squashed = key.replace(" ", "")
        for k, v in master.items():
            if k.replace(" ", "") == squashed:
                return v
        # VARIANT SUFFIXES. A material master lists a part once per variant - 094624A.101,
        # 094624A.102, 094624A.P01 - while the invoice prints the part itself, 094624A. Neither
        # side is wrong and they are the same part, but nothing above matches them, so every
        # such line came back blank and the operator was asked to type a CTH the dump already
        # held. Compare on the stem, the piece before the first dot.
        #
        # Only where the variants AGREE. Where a part's variants carry different codes it is
        # genuinely ambiguous, and a wrong CTH is a wrong customs declaration - so that returns
        # "" and the operator is asked, which is what should have happened all along. Variants
        # with no code at all say nothing either way and are ignored: on this customer's master
        # the .P01 rows are empty while the .101 row carries the code.
        # The invoice usually carries the bare part and the master the variants, so the stem is
        # taken from BOTH sides: whichever way round the suffix happens to be, they meet.
        stem = _stem(key) or key
        found = {v for k, v in master.items() if v and (_stem(k) or k) == stem}
        if len(found) == 1:
            return found.pop()
    return ""
