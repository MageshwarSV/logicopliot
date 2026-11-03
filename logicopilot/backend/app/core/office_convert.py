"""Word/Excel -> PDF, for Smart Upload documents that arrive as Office files instead of a
scan. LibreOffice headless does the actual conversion (already installed on both UAT and
production for this exact purpose) - there is no reliable pure-Python renderer for either
format that reproduces real-world layout closely enough to OCR/classify against.

Word: the whole document becomes one PDF, submitted for classification exactly like any
scanned PDF would be.

Excel: each WORKSHEET becomes its OWN separate PDF and is submitted for classification on its
own - a workbook with an invoice on one sheet and a packing list on another must not be
treated as a single, unclassifiable document. Done by building a temporary single-sheet copy
of the workbook per sheet (openpyxl) and converting each of those separately, since
LibreOffice's own --convert-to has no per-sheet output mode.
"""
import os
import re
import shutil
import signal
import subprocess
import uuid
from pathlib import Path

WORD_EXTS = {".doc", ".docx"}
EXCEL_EXTS = {".xls", ".xlsx"}
OFFICE_EXTS = WORD_EXTS | EXCEL_EXTS


class OfficeConversionFailed(Exception):
    """LibreOffice did not produce a PDF - a Super Admin should be told why, not just that
    the file was silently dropped."""


def _safe_stem(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip())
    return base or "sheet"


def _run_soffice(args: list[str], profile_dir: Path, timeout: int = 180) -> None:
    # Each call gets its own LibreOffice user profile (-env:UserInstallation) - without this,
    # two Smart Upload requests converting a file at the same moment race for the same default
    # profile lock and one of them fails outright ("Fatal Error: file could not be locked").
    profile_dir.mkdir(parents=True, exist_ok=True)
    # .resolve() - a relative profile_dir produced "file://uploads/_classify/..." here, which
    # is not a valid file:// URI (everything up to the next "/" reads as a HOST, not a path
    # segment). Found live (2026-11-03, JOB-8616BC) as the real difference between a soffice
    # call that hung reliably inside this app and the exact same command run by hand with an
    # absolute profile path, which finished in 2 seconds. Always resolving to an absolute path
    # here means the URI is well-formed regardless of what the caller happened to pass in.
    cmd = [
        "soffice", "--headless", "--norestore", "--nologo", "--nodefault",
        "--nofirststartwizard",
        f"-env:UserInstallation=file://{profile_dir.resolve()}",
        *args,
    ]
    try:
        # start_new_session puts soffice in its OWN process group - without this, a timeout
        # only ever kills the one process Python directly launched. LibreOffice forks its own
        # child during startup, and that child does not die with its parent: found live
        # (2026-11-03, JOB-8616BC) as a soffice.bin that sat doing NOTHING - 0 seconds of CPU
        # time - for 5+ minutes, well past this function's own 180s timeout, because killing
        # the parent alone left the actual worker running untouched. Killing the whole group on
        # timeout (os.killpg) reaches every process LibreOffice spawned, not just the first one.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
    except FileNotFoundError:
        raise OfficeConversionFailed(
            "LibreOffice (soffice) is not installed on this server - Word/Excel upload is "
            "unavailable until it is."
        )
    try:
        _out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()  # reap, now that every process in the group is dead
        raise OfficeConversionFailed("Converting this file to PDF took too long and was stopped.")
    if proc.returncode != 0:
        raise OfficeConversionFailed(
            f"LibreOffice could not convert this file to PDF: "
            f"{err.decode(errors='replace').strip()[:500] or 'unknown error'}"
        )


def convert_to_pdf(src: Path, out_dir: Path, work_dir: Path) -> Path:
    """Convert a single Office file (Word or a single-sheet Excel copy) to PDF. Returns the
    produced PDF's path, under `out_dir`.

    A timeout gets ONE retry with a brand-new LibreOffice profile - found live, a Word file
    that should convert in seconds instead hit the 90s(-> now 180s) ceiling, almost certainly
    several Smart Upload requests' own soffice processes competing for the same box's CPU at
    that moment rather than anything wrong with the file itself; a second attempt a few
    seconds later, once that contention has cleared, is cheap and often succeeds outright.
    Never retried for any OTHER failure (a genuinely corrupt/unsupported file, soffice
    missing) - those will not fix themselves on a second try."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        profile_dir = work_dir / f"profile_{uuid.uuid4().hex}"
        try:
            _run_soffice(["--convert-to", "pdf", "--outdir", str(out_dir), str(src)], profile_dir)
            break
        except OfficeConversionFailed as exc:
            if attempt == 1 or "took too long" not in str(exc):
                raise
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)
    out = out_dir / f"{src.stem}.pdf"
    if not out.exists():
        raise OfficeConversionFailed(f"LibreOffice did not produce a PDF for {src.name!r}.")
    return out


def convert_word_to_pdf(src: Path, out_dir: Path, work_dir: Path) -> Path:
    return convert_to_pdf(src, out_dir, work_dir)


def convert_excel_sheets_to_pdfs(src: Path, out_dir: Path, work_dir: Path) -> list[tuple[str, Path]]:
    """One PDF per worksheet, in the workbook's own sheet order. A sheet LibreOffice renders
    as genuinely blank (no print area, no cells with content) still gets its own PDF - an
    empty page is a real, if unlikely, document and silently dropping it would be a different
    kind of data loss than the one this feature exists to prevent.

    The per-sheet split below needs openpyxl to delete every OTHER sheet and re-save - and
    openpyxl only reads the modern .xlsx format, not the older binary .xls one, even though
    .xls is one of this module's own advertised EXCEL_EXTS. The obvious fix - convert .xls to
    .xlsx with soffice first, then split as normal - was tried and found live (JOB-8616BC,
    2026-11-03) to reliably hang: soffice --convert-to xlsx took 180s+ (sometimes indefinitely)
    on a genuine, ordinary customer .xls, while a DIRECT --convert-to pdf on that exact same
    file finished in 2 seconds flat. Format-to-format conversion (xls -> xlsx) and format-to-
    PDF export go through different internal paths in LibreOffice, and only the former is
    unreliable here - so for .xls specifically, this skips the per-sheet split entirely and
    converts the WHOLE workbook to one combined PDF via the fast, proven path, the same way a
    Word document already becomes one PDF. A multi-sheet .xls loses the "each sheet its own
    document" separation .xlsx gets - an accepted, documented trade-off: a working combined
    document beats a hung request every time, and splitting a PDF after the fact by page would
    guess at sheet boundaries no more reliably than this does."""
    if src.suffix.lower() == ".xls":
        pdf = convert_to_pdf(src, out_dir, work_dir)
        # "(combined)" rather than a real sheet name - every caller builds its own result
        # filename as "<original file> - <sheet name>.pdf" (see e.g. jobs.py's own use of this
        # function), so this reads sensibly there without needing each of them to special-case
        # an .xls's single whole-workbook result.
        renamed = out_dir / f"{_safe_stem(src.stem)} - (combined).pdf"
        pdf.replace(renamed)
        return [("(combined)", renamed)]

    from openpyxl import load_workbook

    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    sheet_names = list(load_workbook(src, read_only=True).sheetnames)
    results: list[tuple[str, Path]] = []
    for sheet_name in sheet_names:
        single = load_workbook(src)
        for other in list(single.sheetnames):
            if other != sheet_name:
                del single[other]
        sheet_xlsx = work_dir / f"sheet_{uuid.uuid4().hex}_{_safe_stem(sheet_name)}.xlsx"
        single.save(sheet_xlsx)
        pdf = convert_to_pdf(sheet_xlsx, out_dir, work_dir)
        # convert_to_pdf names the PDF after sheet_xlsx's own stem (unique per call already) -
        # rename it to something that reads back as "<original file> - <sheet name>.pdf" for
        # the classifier's own per-file logging/results, same as a real uploaded file name.
        renamed = out_dir / f"{_safe_stem(src.stem)} - {_safe_stem(sheet_name)}.pdf"
        pdf.replace(renamed)
        results.append((sheet_name, renamed))
    return results
