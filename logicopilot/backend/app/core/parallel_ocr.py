"""OCR the pages of one file several at a time, results back in page order (Phase 2c).

Mail intake and Smart Upload both OCR'd page after page: 191 pages took ~17 minutes
(JOB-12B137, 2026-11-19), almost all of it waiting on Document AI one call at a time. The calls
are independent, so `workers` of them now run side by side.

What does not change:
  - Results come back in page order, the same dicts ocr_page_image returns.
  - A page whose OCR still fails gives {"text": "", "tokens": []}, exactly the fallback both
    callers used before (they classify that page from its image instead).
  - Pages already OCR'd are served from docai's content cache (_ocr_by_content), so a re-read
    after a restart pays for nothing twice - that was already true and still is.

What is added:
  - A process-wide ceiling (parallel_ocr_max_total) on Document AI calls in flight, so several
    mails read at once cannot multiply into a burst Document AI throttles.
  - When running in parallel, a failed page is retried twice with a short wait before falling
    back to empty text - more calls at once makes a transient refusal more likely, and an
    empty page silently weakens classification.

workers <= 1 is the old loop, call for call: one page at a time, no retry.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

EMPTY = {"text": "", "tokens": []}
RETRY_WAITS = (2.0, 5.0)

_gate_lock = threading.Lock()
_gate: threading.BoundedSemaphore | None = None
_gate_size = 0


def _global_gate() -> threading.BoundedSemaphore:
    """One semaphore for the whole process, sized from settings (rebuilt if the size changes)."""
    global _gate, _gate_size
    from app.core.config import get_settings

    size = max(1, int(get_settings().parallel_ocr_max_total or 1))
    with _gate_lock:
        if _gate is None or _gate_size != size:
            _gate, _gate_size = threading.BoundedSemaphore(size), size
        return _gate


def workers_for(in_large_lane: bool = False) -> int:
    """Pages at once for one file: the setting, or - on the large-job lane, once parallel OCR is
    switched on at all - Platform Settings' "parallel calls"."""
    from app.core.config import get_settings

    base = max(1, int(get_settings().parallel_ocr_workers or 1))
    if base <= 1 or not in_large_lane:
        return base
    try:
        from app.db.session import SessionLocal
        from app.models.system_setting import SystemSetting

        db = SessionLocal()
        try:
            row = db.query(SystemSetting.large_job_parallel_calls).first()
        finally:
            db.close()
        return max(1, int(row[0])) if row and row[0] else base
    except Exception:  # noqa: BLE001 - a settings read must never stop OCR
        logger.warning("could not read large_job_parallel_calls - using %d", base, exc_info=True)
        return base


def _ocr_one(path: Path, label: str, page: int, retry: bool,
             ocr: Callable[[Path], dict]) -> dict:
    attempts = (0.0,) + (RETRY_WAITS if retry else ())
    for n, wait in enumerate(attempts):
        if wait:
            time.sleep(wait)
        try:
            if retry:
                with _global_gate():
                    return ocr(path)
            return ocr(path)
        except Exception:  # noqa: BLE001 - same fallback as before: classify from the image
            last = n == len(attempts) - 1
            logger.warning("OCR %s for %s p%s%s", "unavailable" if last else "failed, retrying",
                           label, page, "" if last else f" (attempt {n + 1})")
    return dict(EMPTY)


def ocr_pages(paths: list[Path], workers: int = 1, label: str = "",
              on_page_done: Callable[[int], None] | None = None,
              ocr: Callable[[Path], dict] | None = None) -> list[dict]:
    """OCR every page image in `paths`; results in the same order. `on_page_done(n_done)` is
    called once per finished page, never from two threads at once (it can safely update a
    progress bar). `ocr` is the page reader - docai.ocr_page_image unless a test passes one."""
    if ocr is None:
        from app.core.docai import ocr_page_image as ocr
    workers = max(1, int(workers or 1))
    results: list[dict] = [dict(EMPTY)] * len(paths)
    done_lock = threading.Lock()
    done = 0

    def _finish(i: int, res: dict) -> None:
        nonlocal done
        with done_lock:
            results[i] = res
            done += 1
            if on_page_done is not None:
                try:
                    on_page_done(done)
                except Exception:  # noqa: BLE001 - a progress bar must never stop OCR
                    logger.debug("progress callback failed", exc_info=True)

    if workers <= 1 or len(paths) <= 1:
        for i, p in enumerate(paths):
            _finish(i, _ocr_one(p, label, i + 1, retry=False, ocr=ocr))
        return results

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(workers, len(paths)), thread_name_prefix="ocr") as pool:
        futures = {pool.submit(_ocr_one, p, label, i + 1, True, ocr): i for i, p in enumerate(paths)}
        # As each finishes - the progress bar moves with the work, not behind the slowest page.
        for fut in as_completed(futures):
            _finish(futures[fut], fut.result())
    logger.info("parallel OCR: %s - %d page(s), %d at once, %.1fs",
                label or "file", len(paths), min(workers, len(paths)), time.monotonic() - started)
    return results
