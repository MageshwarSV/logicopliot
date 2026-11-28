"""Any image a customer sends becomes a PDF before classification (2026-11-28).

Classification and extraction read PDF, PNG and JPG. Everything else an email or upload can
carry as a picture used to be dropped without a trace - found live: Jose's MASIMO mail
(28 Nov) came with four .tif scans (courier documents) and was logged "nothing readable was
attached". TIFF is what scanners and courier systems produce, often several pages in one
file, so each frame becomes a page.

    to_pdf(name, data) -> (name.pdf, pdf bytes)        raises ImageConvertFailed
    convert_files(files) -> files with every convertible image turned into a PDF
"""
from __future__ import annotations

import io
import logging
import mimetypes
from pathlib import Path

logger = logging.getLogger(__name__)

# Read natively already - left as they are.
NATIVE_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}
# Opened with Pillow and turned into a PDF.
CONVERT_EXTS = {".tif", ".tiff", ".bmp", ".gif", ".webp", ".jfif", ".jpe", ".pjpeg", ".pjp", ".ico", ".dib",
                ".pcx", ".tga", ".ppm", ".pgm", ".pbm"}
MAX_PAGES = 200


class ImageConvertFailed(Exception):
    pass


def is_convertible(name: str) -> bool:
    return Path(name or "").suffix.lower() in CONVERT_EXTS


def ext_for_mime(content_type: str | None) -> str | None:
    """An extension for an image part that came without a usable file name."""
    ct = (content_type or "").lower().split(";")[0].strip()
    if not ct.startswith("image/"):
        return None
    special = {"image/tiff": ".tif", "image/x-tiff": ".tif", "image/jpg": ".jpg", "image/pjpeg": ".jpg",
               "image/x-ms-bmp": ".bmp", "image/bmp": ".bmp", "image/webp": ".webp"}
    ext = special.get(ct) or mimetypes.guess_extension(ct) or ""
    return ext if ext in NATIVE_EXTS | CONVERT_EXTS else None


def to_pdf(name: str, data: bytes) -> tuple[str, bytes]:
    """PyMuPDF first (already on every server: TIFF incl. multi-page, BMP, GIF, PNM, JPX ...);
    Pillow, if installed, for what MuPDF cannot open."""
    try:
        return _to_pdf_mupdf(name, data)
    except Exception as first:  # noqa: BLE001
        try:
            return _to_pdf_pillow(name, data)
        except ImportError:
            raise ImageConvertFailed(f"{name}: this image could not be opened ({first})") from first
        except Exception as exc:  # noqa: BLE001
            raise ImageConvertFailed(f"{name}: this image could not be opened ({exc})") from exc


def _to_pdf_mupdf(name: str, data: bytes) -> tuple[str, bytes]:
    import fitz  # PyMuPDF

    ext = Path(name).suffix.lower().lstrip(".") or "png"
    ftype = {"tif": "tiff", "jfif": "jpeg", "jpe": "jpeg", "pjpeg": "jpeg", "pjp": "jpeg", "dib": "bmp"}.get(ext, ext)
    with fitz.open(stream=data, filetype=ftype) as img:
        if img.page_count == 0:
            raise ImageConvertFailed(f"{name}: the image has no pages")
        pdf_bytes = img.convert_to_pdf(0, min(img.page_count, MAX_PAGES) - 1)
    with fitz.open("pdf", pdf_bytes) as check:
        if check.page_count == 0:
            raise ImageConvertFailed(f"{name}: the image has no pages")
    return f"{Path(name).stem or 'image'}.pdf", pdf_bytes


def _to_pdf_pillow(name: str, data: bytes) -> tuple[str, bytes]:
    from PIL import Image, ImageSequence

    img = Image.open(io.BytesIO(data))
    pages = []
    for n, frame in enumerate(ImageSequence.Iterator(img)):
        if n >= MAX_PAGES:
            break
        f = frame.copy()
        if f.mode in ("RGBA", "LA", "P"):
            f = f.convert("RGBA")
            bg = Image.new("RGB", f.size, "white")
            bg.paste(f, mask=f.split()[-1])
            f = bg
        elif f.mode != "RGB":
            f = f.convert("RGB")
        pages.append(f)
    if not pages:
        raise ImageConvertFailed(f"{name}: the image has no pages")
    out = io.BytesIO()
    dpi = img.info.get("dpi")
    res = float(dpi[0]) if isinstance(dpi, tuple) and dpi and dpi[0] else 150.0
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=res)
    return f"{Path(name).stem or 'image'}.pdf", out.getvalue()


def convert_files(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Each convertible image replaced by its PDF; anything else untouched. An image that
    cannot be opened is dropped with a log line (it could never have been read anyway)."""
    out: list[tuple[str, bytes]] = []
    for name, data in files:
        if not is_convertible(name):
            out.append((name, data))
            continue
        try:
            out.append(to_pdf(name, data))
            logger.info("image %s turned into a PDF for reading", name)
        except ImageConvertFailed as exc:
            logger.warning("%s", exc)
    return out
