"""Open a mail's (or a Smart Upload's) attachments into the files the classifier reads - including
everything inside a zip, at any folder depth, and inside zips that are themselves inside a zip.

Shared by the email puller and Smart Upload, so both read the same files the same way.

Built for a real customer mail (312-93004472 - SINA2626873 (DTA).zip, Nov 2026): one zip with 13
sub-HAWB folders, six of them holding the invoices and packing lists in a zip of their own, one
of those password-protected. The old code only opened the outer zip, so 74 of its 113 PDFs were
silently never read, and a password-protected entry would have crashed the whole mail.

What it deliberately does NOT change - mails that worked before must come out exactly the same:
  * a file keeps its bare file name (folder path only goes into the notes), in the zip's order;
  * an ordinary (non-zip) attachment is passed through untouched - no limit applies to it;
  * the limits below only apply to what comes OUT of a zip (zip-bomb protection).

Nothing here writes to disk, so a zip entry named "../../x" can never land anywhere.
"""
from __future__ import annotations

import io
import logging
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

MAX_DEPTH = 5                      # zip inside zip inside zip ... levels
MAX_FILES = 300                    # files taken out of zips, per mail / upload
MAX_TOTAL_BYTES = 300 * 1024 * 1024
MAX_ENTRY_BYTES = 50 * 1024 * 1024
MAX_RATIO = 100                    # unpacked / packed size; above this (and over 1 MB) = suspicious
_RATIO_MIN_BYTES = 1024 * 1024
MAX_NOTES = 200                    # entries kept in the notes, so one odd zip cannot bloat a row

# System files a zip made on a Mac or Windows carries - never documents, never worth a note.
_JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
# Not documents and never expected to be: e-mail signature images, calendar invites, contact
# cards, signatures, the mail's own HTML copy. Not read and not listed as "not read" either, so
# the list on the job only shows files someone might actually have needed.
_QUIET_EXTS = {".gif", ".bmp", ".ico", ".ics", ".vcf", ".p7s", ".htm", ".html", ".css", ".js"}


@dataclass
class UnpackResult:
    files: list[tuple[str, bytes]] = field(default_factory=list)
    # [{"path": "outer.zip/folder/x.rar", "reason": "file type not read"}]
    skipped: list[dict] = field(default_factory=list)
    # [{"path": "outer.zip/folder/locked.zip", "files": ["a.pdf", ...]}]
    locked: list[dict] = field(default_factory=list)

    def notes(self) -> dict | None:
        """What goes on the job (Job.attachment_notes), or None when everything was read."""
        if not self.skipped and not self.locked:
            return None
        return {"skipped": self.skipped[:MAX_NOTES], "locked": self.locked[:MAX_NOTES]}

    def summary(self) -> str:
        """One short phrase for the email log, e.g. "1 password-protected zip, 2 files not read"."""
        parts = []
        if self.locked:
            n = len(self.locked)
            parts.append(f"{n} password-protected zip{'s' if n != 1 else ''}")
        if self.skipped:
            n = len(self.skipped)
            parts.append(f"{n} file{'s' if n != 1 else ''} not read")
        return ", ".join(parts)


def _is_junk(entry_name: str) -> bool:
    p = PurePosixPath(entry_name)
    if "__MACOSX" in p.parts:
        return True
    return p.name.startswith("._") or p.name.lower() in _JUNK_NAMES


def unpack_attachments(items: list[tuple[str, bytes]], accept_exts: set[str]) -> UnpackResult:
    """`items` = (file name, bytes) as attached. `accept_exts` = the extensions the caller reads
    (lower case, with the dot). Returns the readable files plus what was skipped or locked."""
    out = UnpackResult()
    budget = {"files": 0, "bytes": 0}
    accept = {e.lower() for e in accept_exts}
    for name, blob in items:
        if (name or "").lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(blob)) as z:
                    _walk(z, name, 1, accept, out, budget)
            except zipfile.BadZipFile:
                out.skipped.append({"path": name, "reason": "damaged zip - could not be opened"})
            continue
        if PurePosixPath(name or "").suffix.lower() in accept:
            out.files.append((name, blob))           # exactly as before - no limits here
        elif name and PurePosixPath(name).suffix.lower() not in _QUIET_EXTS:
            out.skipped.append({"path": name, "reason": "file type not read"})
    return out


def _walk(z: zipfile.ZipFile, label: str, depth: int, accept: set[str],
          out: UnpackResult, budget: dict) -> None:
    locked_names: list[str] = []
    for info in z.infolist():
        entry = info.filename
        if entry.endswith("/") or _is_junk(entry):
            continue
        path = f"{label}/{entry}"
        bare = PurePosixPath(entry).name
        ext = PurePosixPath(entry).suffix.lower()
        if info.flag_bits & 0x1:                      # encrypted - needs a password
            locked_names.append(bare)
            continue
        if ext == ".zip":
            if depth >= MAX_DEPTH:
                out.skipped.append({"path": path, "reason": f"zip nested more than {MAX_DEPTH} levels deep"})
                continue
            data = _read(z, info, path, out, budget, count_file=False)
            if data is None:
                continue
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as inner:
                    _walk(inner, path, depth + 1, accept, out, budget)
            except zipfile.BadZipFile:
                out.skipped.append({"path": path, "reason": "damaged zip - could not be opened"})
            continue
        if ext not in accept:
            if ext not in _QUIET_EXTS:
                out.skipped.append({"path": path, "reason": "file type not read"})
            continue
        if budget["files"] >= MAX_FILES:
            out.skipped.append({"path": path, "reason": f"more than {MAX_FILES} files in this mail"})
            continue
        data = _read(z, info, path, out, budget, count_file=True)
        if data is not None:
            out.files.append((bare, data))
    if locked_names:
        out.locked.append({"path": label, "files": locked_names})


def _read(z: zipfile.ZipFile, info: zipfile.ZipInfo, path: str, out: UnpackResult,
          budget: dict, *, count_file: bool) -> bytes | None:
    """One entry's bytes, after the size checks - or None (with a note) if it must be skipped.
    Sizes are checked from the zip's own header BEFORE anything is unpacked."""
    size = info.file_size
    if size > MAX_ENTRY_BYTES:
        out.skipped.append({"path": path, "reason": f"larger than {MAX_ENTRY_BYTES // (1024 * 1024)} MB"})
        return None
    if size > _RATIO_MIN_BYTES and info.compress_size and size / info.compress_size > MAX_RATIO:
        out.skipped.append({"path": path, "reason": "suspicious compression - not opened"})
        return None
    if budget["bytes"] + size > MAX_TOTAL_BYTES:
        out.skipped.append({"path": path, "reason": f"over {MAX_TOTAL_BYTES // (1024 * 1024)} MB unpacked for this mail"})
        return None
    try:
        data = z.read(info)
    except (RuntimeError, zipfile.BadZipFile, zlib.error, NotImplementedError, OSError) as exc:
        logger.warning("attachment unpack: could not read %s: %s", path[:200], exc)
        out.skipped.append({"path": path, "reason": "could not be read"})
        return None
    budget["bytes"] += len(data)
    if count_file:
        budget["files"] += 1
    return data
