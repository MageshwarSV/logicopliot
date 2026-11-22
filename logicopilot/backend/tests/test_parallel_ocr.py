"""Phase 2c: pages of one file OCR'd several at a time - same results, same order, same
fallback for a failed page; a retry when parallel; a process-wide ceiling on calls in flight;
and workers=1 is exactly the old page-by-page loop."""
import random
import threading
import time
from pathlib import Path

import pytest

from app.core import parallel_ocr as po
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(po, "RETRY_WAITS", (0.0, 0.0))
    monkeypatch.setattr(get_settings(), "parallel_ocr_max_total", 12)


def _paths(n):
    return [Path(f"/tmp/page_{i}.png") for i in range(1, n + 1)]


def _page_no(p: Path) -> int:
    return int(p.stem.split("_")[1])


def test_results_come_back_in_page_order_whatever_finishes_first():
    def ocr(p):
        time.sleep(random.uniform(0, 0.02))
        return {"text": f"page {_page_no(p)}", "tokens": [_page_no(p)]}

    out = po.ocr_pages(_paths(20), workers=6, ocr=ocr)
    assert [r["text"] for r in out] == [f"page {i}" for i in range(1, 21)]


def test_one_worker_is_the_old_loop_in_order_with_no_retry():
    calls = []

    def ocr(p):
        calls.append(_page_no(p))
        if _page_no(p) == 2:
            raise RuntimeError("Document AI down")
        return {"text": "ok", "tokens": []}

    out = po.ocr_pages(_paths(3), workers=1, ocr=ocr)
    assert calls == [1, 2, 3]                       # page by page, page 2 tried once
    assert out[1] == {"text": "", "tokens": []}     # the old fallback: classify from the image
    assert out[0]["text"] == out[2]["text"] == "ok"


def test_parallel_retries_a_page_that_fails_once():
    tries = {}

    def ocr(p):
        tries[p] = tries.get(p, 0) + 1
        if _page_no(p) == 3 and tries[p] == 1:
            raise RuntimeError("429 quota")
        return {"text": f"p{_page_no(p)}", "tokens": []}

    out = po.ocr_pages(_paths(5), workers=4, ocr=ocr)
    assert out[2]["text"] == "p3" and tries[Path("/tmp/page_3.png")] == 2


def test_parallel_gives_up_on_a_page_after_three_tries():
    def ocr(p):
        if _page_no(p) == 1:
            raise RuntimeError("bad image")
        return {"text": "ok", "tokens": []}

    out = po.ocr_pages(_paths(3), workers=3, ocr=ocr)
    assert out[0] == {"text": "", "tokens": []} and out[1]["text"] == "ok"


def test_progress_counts_every_page_once_never_two_at_a_time():
    seen, inside = [], [0]

    def progress(n):
        inside[0] += 1
        assert inside[0] == 1   # never called from two threads at once
        seen.append(n)
        time.sleep(0.001)
        inside[0] -= 1

    po.ocr_pages(_paths(12), workers=6, on_page_done=progress,
                 ocr=lambda p: (time.sleep(0.005), {"text": "", "tokens": []})[1])
    assert seen == list(range(1, 13))


def test_process_wide_ceiling_on_calls_in_flight(monkeypatch):
    monkeypatch.setattr(get_settings(), "parallel_ocr_max_total", 2)
    lock, now, peak = threading.Lock(), [0], [0]

    def ocr(p):
        with lock:
            now[0] += 1
            peak[0] = max(peak[0], now[0])
        time.sleep(0.01)
        with lock:
            now[0] -= 1
        return {"text": "", "tokens": []}

    po.ocr_pages(_paths(10), workers=6, ocr=ocr)
    assert peak[0] <= 2


def test_workers_setting(monkeypatch):
    monkeypatch.setattr(get_settings(), "parallel_ocr_workers", 1)
    assert po.workers_for(False) == 1 and po.workers_for(True) == 1   # off: lane stays one-by-one too
    monkeypatch.setattr(get_settings(), "parallel_ocr_workers", 6)
    assert po.workers_for(False) == 6


def test_the_real_image_shrink_is_safe_from_many_threads(tmp_path):
    """PyMuPDF is not thread-safe; docai._docai_safe_bytes runs it under a lock, so parallel OCR
    can call it from several threads at once without crashing the process."""
    import fitz

    from app.core import docai

    png = tmp_path / "page_1.png"
    doc = fitz.open()
    page = doc.new_page(width=3000, height=4000)   # big enough to be downscaled
    page.insert_text((100, 100), "INVOICE 12345")
    page.get_pixmap(dpi=72).save(png)
    doc.close()
    out, errors = [], []

    def go():
        try:
            out.append(docai._docai_safe_bytes(png))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(out) == 12 and len({o[1] for o in out}) == 1
