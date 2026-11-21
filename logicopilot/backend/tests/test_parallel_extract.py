"""Phase 2d: parallel extraction by prefetch. The loop's answers must be exactly what the same
request returns; anything not prefetched (or whose prefetch failed) is a real call, as before;
off is the original functions untouched."""
import threading
import time
from pathlib import Path

import pytest

from app.core import parallel_extract as px
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _cap(monkeypatch):
    monkeypatch.setattr(get_settings(), "parallel_extract_max_total", 12)


def _counting(answer_for):
    calls = []

    def fn(*args, **kwargs):
        calls.append((args, kwargs))
        return answer_for(*args, **kwargs)

    return fn, calls


def test_off_is_the_original_function_and_plans_nothing():
    fn, calls = _counting(lambda text, fields, **k: {"f": text})
    pf = px.Prefetch(1)
    assert pf.wrap("fields", fn) is fn
    pf.plan("fields", "INV", [{"label": "f"}], tenant_id="t")
    pf.run()
    assert calls == [] and pf.planned == []


def test_loop_gets_the_prefetched_answer_without_a_second_call():
    fn, calls = _counting(lambda text, fields, **k: {"invoice_no": text[-3:]})
    pf = px.Prefetch(6)
    wrapped = pf.wrap("fields", fn)
    for n in range(10):
        pf.plan("fields", f"INVOICE {n:03d}", [{"label": "invoice_no"}], tenant_id="t")
    pf.run()
    assert len(calls) == 10
    got = [wrapped(f"INVOICE {n:03d}", [{"label": "invoice_no"}], tenant_id="t") for n in range(10)]
    assert got == [{"invoice_no": f"{n:03d}"} for n in range(10)]
    assert len(calls) == 10 and pf.hits == 10 and pf.misses == 0


def test_a_request_that_differs_from_the_plan_is_a_real_call():
    fn, calls = _counting(lambda text, fields, **k: {"v": text})
    pf = px.Prefetch(4)
    wrapped = pf.wrap("fields", fn)
    pf.plan("fields", "A", [{"label": "v"}], tenant_id="t")
    pf.run()
    assert wrapped("A", [{"label": "v"}], tenant_id="OTHER") == {"v": "A"}   # different kwargs
    assert len(calls) == 2 and pf.misses == 1


def test_a_failed_prefetch_leaves_the_call_to_the_loop_with_its_own_error():
    state = {"n": 0}

    def flaky(text, fields, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("429 during prefetch")
        return {"v": "ok"}

    pf = px.Prefetch(4)
    wrapped = pf.wrap("fields", flaky)
    pf.plan("fields", "A", [], tenant_id="t")
    pf.run()                       # failed: nothing kept
    assert wrapped("A", [], tenant_id="t") == {"v": "ok"} and state["n"] == 2


def test_the_loop_editing_rows_in_place_does_not_change_the_kept_answer():
    pf = px.Prefetch(2)
    wrapped = pf.wrap("rows", lambda text, specs, hint, **k: [{"container_number": "msku 123"}])
    pf.plan("rows", "T", [], None, tenant_id="t")
    pf.run()
    first = wrapped("T", [], None, tenant_id="t")
    first[0]["container_number"] = "MSKU123"   # the loop normalises in place
    assert wrapped("T", [], None, tenant_id="t") == [{"container_number": "msku 123"}]


def test_paths_in_the_request_match_by_their_text():
    fn, calls = _counting(lambda paths, fields, **k: {"pages": len(paths)})
    pf = px.Prefetch(2)
    wrapped = pf.wrap("fields_img", fn)
    pf.plan("fields_img", [Path("/x/page_1.png"), Path("/x/page_2.png")], [], model=None, tenant_id="t")
    pf.run()
    assert wrapped([Path("/x/page_1.png"), Path("/x/page_2.png")], [], model=None, tenant_id="t") == {"pages": 2}
    assert len(calls) == 1


def test_duplicate_planned_requests_are_made_once():
    fn, calls = _counting(lambda text, fields, **k: {})
    pf = px.Prefetch(4)
    pf.wrap("fields", fn)
    for _ in range(3):
        pf.plan("fields", "same", [], tenant_id="t")
    pf.run()
    assert len(calls) == 1


def test_process_wide_ceiling_on_calls_in_flight(monkeypatch):
    monkeypatch.setattr(get_settings(), "parallel_extract_max_total", 2)
    lock, now, peak = threading.Lock(), [0], [0]

    def slow(text, fields, **k):
        with lock:
            now[0] += 1
            peak[0] = max(peak[0], now[0])
        time.sleep(0.01)
        with lock:
            now[0] -= 1
        return {}

    pf = px.Prefetch(6)
    pf.wrap("fields", slow)
    for n in range(10):
        pf.plan("fields", str(n), [], tenant_id="t")
    pf.run()
    assert peak[0] <= 2


def test_page_text_prefetch_reads_each_page_once_and_tolerates_a_failure():
    seen, lock = [], threading.Lock()

    def get_page_ocr(doc_dir, page):
        with lock:
            seen.append((str(doc_dir), page))
        if page == 2:
            raise RuntimeError("Document AI down")
        return {"text": "x"}

    pages = [(Path("/jobs/d1"), p) for p in (1, 2, 3)] + [(Path("/jobs/d2"), 1)]
    px.prefetch_page_ocr(pages, workers=4, get_page_ocr=get_page_ocr)
    assert sorted(seen) == sorted((str(d), p) for d, p in pages)
    seen.clear()
    px.prefetch_page_ocr(pages, workers=1, get_page_ocr=get_page_ocr)   # off: nothing
    assert seen == []


def test_workers_setting(monkeypatch):
    monkeypatch.setattr(get_settings(), "parallel_extract_workers", 1)
    assert px.workers_for(False) == 1 and px.workers_for(True) == 1
    monkeypatch.setattr(get_settings(), "parallel_extract_workers", 6)
    assert px.workers_for(False) == 6
