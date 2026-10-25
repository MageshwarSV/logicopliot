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
import re
import shutil
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


def _run_soffice(args: list[str], profile_dir: Path, timeout: int = 90) -> None:
    # Each call gets its own LibreOffice user profile (-env:UserInstallation) - without this,
    # two Smart Upload requests converting a file at the same moment race for the same default
    # profile lock and one of them fails outright ("Fatal Error: file could not be locked").
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                "soffice", "--headless", "--norestore", "--nologo", "--nodefault",
                "--nofirststartwizard",
                f"-env:UserInstallation=file://{profile_dir}",
                *args,
            ],
            capture_output=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise OfficeConversionFailed(
            "LibreOffice (soffice) is not installed on this server - Word/Excel upload is "
            "unavailable until it is."
        )
    except subprocess.TimeoutExpired:
        raise OfficeConversionFailed("Converting this file to PDF took too long and was stopped.")
    if result.returncode != 0:
        raise OfficeConversionFailed(
            f"LibreOffice could not convert this file to PDF: "
            f"{result.stderr.decode(errors='replace').strip()[:500] or 'unknown error'}"
        )


def convert_to_pdf(src: Path, out_dir: Path, work_dir: Path) -> Path:
    """Convert a single Office file (Word or a single-sheet Excel copy) to PDF. Returns the
    produced PDF's path, under `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = work_dir / f"profile_{uuid.uuid4().hex}"
    try:
        _run_soffice(["--convert-to", "pdf", "--outdir", str(out_dir), str(src)], profile_dir)
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
    kind of data loss than the one this feature exists to prevent."""
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
