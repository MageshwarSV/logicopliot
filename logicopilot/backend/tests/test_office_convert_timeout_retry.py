"""convert_to_pdf's retry-on-timeout: a LibreOffice conversion that times out (transient CPU/
memory contention from several concurrent Smart Upload conversions, not a bad file) gets
exactly ONE retry with a fresh profile dir before giving up - found live on UAT, a 3-page
Word document timed out at the old 90s ceiling with nothing obviously wrong with the file
itself. A non-timeout failure (a genuinely bad file, soffice missing) must NOT be retried."""
import subprocess
from unittest.mock import patch

import pytest

from app.core.office_convert import OfficeConversionFailed, convert_to_pdf


def _pdf_out(out_dir, src):
    out = out_dir / f"{src.stem}.pdf"
    out.write_bytes(b"%PDF-1.4 fake")
    return out


def test_a_single_timeout_is_retried_and_can_still_succeed(tmp_path):
    src = tmp_path / "doc.docx"
    src.write_bytes(b"not a real docx")
    out_dir = tmp_path / "out"
    work_dir = tmp_path / "work"

    calls = {"n": 0}

    def fake_run(args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(cmd="soffice", timeout=180)
        _pdf_out(out_dir, src)
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    with patch("app.core.office_convert.subprocess.run", side_effect=fake_run):
        result = convert_to_pdf(src, out_dir, work_dir)

    assert calls["n"] == 2  # exactly one retry, not an unbounded loop
    assert result.exists()


def test_two_consecutive_timeouts_raise_the_same_message_as_before(tmp_path):
    src = tmp_path / "doc.docx"
    src.write_bytes(b"not a real docx")
    out_dir = tmp_path / "out"
    work_dir = tmp_path / "work"

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="soffice", timeout=180)

    with patch("app.core.office_convert.subprocess.run", side_effect=fake_run):
        with pytest.raises(OfficeConversionFailed, match="took too long"):
            convert_to_pdf(src, out_dir, work_dir)


def test_a_non_timeout_failure_is_never_retried(tmp_path):
    src = tmp_path / "doc.docx"
    src.write_bytes(b"not a real docx")
    out_dir = tmp_path / "out"
    work_dir = tmp_path / "work"

    calls = {"n": 0}

    def fake_run(args, **kwargs):
        calls["n"] += 1
        return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"corrupt file")

    with patch("app.core.office_convert.subprocess.run", side_effect=fake_run):
        with pytest.raises(OfficeConversionFailed, match="corrupt file"):
            convert_to_pdf(src, out_dir, work_dir)

    assert calls["n"] == 1  # a genuine conversion error fails fast, no wasted retry
