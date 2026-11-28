"""Any image becomes a PDF before classification - TIFF (multi-page), BMP, GIF, WebP ..."""
import io
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import fitz
from PIL import Image

from app.core.email_puller import _extract_attachments
from app.core.image_convert import convert_files, ext_for_mime, to_pdf


def _tiff(pages=2) -> bytes:
    frames = [Image.new("RGB", (400, 300), c) for c in ("white", "lightgrey", "yellow")[:pages]]
    buf = io.BytesIO()
    frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:])
    return buf.getvalue()


def _img(fmt: str, mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (200, 100), "white").save(buf, fmt)
    return buf.getvalue()


def test_multi_page_tiff_becomes_a_pdf_with_every_page():
    name, pdf = to_pdf("547242112765.tif", _tiff(3))
    assert name == "547242112765.pdf"
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        assert doc.page_count == 3


def test_other_formats_convert_and_native_ones_are_left_alone():
    files = [("a.bmp", _img("BMP")), ("b.gif", _img("GIF", "P")), ("c.webp", _img("WEBP")),
             ("d.png", b"PNGDATA"), ("e.pdf", b"%PDF-1.4"), ("broken.tif", b"not an image")]
    out = dict(convert_files(files))
    assert set(out) == {"a.pdf", "b.pdf", "c.pdf", "d.png", "e.pdf"}      # broken one dropped
    assert out["d.png"] == b"PNGDATA" and out["e.pdf"] == b"%PDF-1.4"


def test_ext_for_mime():
    assert ext_for_mime("image/tiff") == ".tif" and ext_for_mime("image/png") == ".png"
    assert ext_for_mime("application/pdf") is None


def test_email_with_tiff_attachments_like_joses_masimo_mail():
    """Outlook: logo images shown in the body (cid) + real .tif scans that ALSO carry a
    Content-ID but are marked attachment and not shown in the body."""
    msg = MIMEMultipart("related")
    msg.attach(MIMEText('<p>Please find</p><img src="cid:logo1">', "html"))
    logo = MIMEImage(_img("PNG"), "png")
    logo.add_header("Content-ID", "<logo1>")
    logo.add_header("Content-Disposition", "inline", filename="image001.png")
    msg.attach(logo)
    for n in ("547242112765", "485731400878"):
        part = MIMEImage(_tiff(1), "tiff")
        part.add_header("Content-ID", f"<{n}>")
        part.add_header("Content-Disposition", "attachment", filename=f"{n}.tif")
        msg.attach(part)
    noname = MIMEApplication(_img("BMP"), "octet-stream")
    noname.replace_header("Content-Type", "image/bmp")
    noname.add_header("Content-Disposition", "attachment")
    msg.attach(noname)
    names = [n for n, _ in _extract_attachments(msg)]
    assert "547242112765.tif" in names and "485731400878.tif" in names
    assert "attachment.bmp" in names                  # an image without a file name still counts
    assert "image001.png" not in names                # the logo in the body is still skipped
