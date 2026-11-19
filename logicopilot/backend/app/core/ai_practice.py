"""Practice AI: free testing of AI-heavy flows (large-job lane, classification, extraction).

Three modes, chosen on Super Admin -> Platform Settings (SystemSetting.practice_ai_mode):

  off     - normal. Every call goes to OpenAI. (The default, and the ONLY behaviour anywhere
            the server's .env lacks PRACTICE_AI_ALLOWED=1 - i.e. production.)
  record  - normal calls, and each answer is also saved to disk, keyed by the exact request.
  replay  - NO call to OpenAI. A recorded answer is returned after roughly the time the real
            call took; a request never recorded gets an empty answer ("{}") after a fixed wait,
            which every caller already treats as "the AI found nothing". Zero cost.

Hooked into the one place every chat call goes through (llm.create_chat_completion_with_retry),
so no caller changes. The mode is read from the database at most every 15 seconds - the backend
runs several worker processes and a mode switched in one must reach the others.

Recordings live under uploads/ai_recordings/. Replaying the same mail gives the same AI answers,
so the old and new code paths can be compared on identical input.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from app.core.config import get_settings

logger = logging.getLogger(__name__)

MODES = ("off", "record", "replay")
_CACHE_SECONDS = 15.0
_MAX_REPLAY_WAIT = 60.0

_lock = threading.Lock()
_cached: tuple[float, str] = (0.0, "off")


def allowed() -> bool:
    """Practice AI only exists on a server whose .env says so - never production."""
    return os.environ.get("PRACTICE_AI_ALLOWED", "").strip() == "1"


def _miss_wait() -> float:
    try:
        return max(0.0, float(os.environ.get("PRACTICE_AI_MISS_SECONDS", "30")))
    except ValueError:
        return 30.0


def current_mode() -> str:
    """off | record | replay - always "off" where practice AI is not allowed."""
    global _cached
    if not allowed():
        return "off"
    now = time.monotonic()
    with _lock:
        at, mode = _cached
        if now - at < _CACHE_SECONDS:
            return mode
    mode = "off"
    try:
        from app.db.session import SessionLocal
        from app.models.system_setting import SystemSetting

        db = SessionLocal()
        try:
            row = db.query(SystemSetting.practice_ai_mode).first()
            mode = (row[0] if row else "off") or "off"
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - a settings read must never break a real call
        logger.exception("could not read practice_ai_mode - treating it as off")
    if mode not in MODES:
        mode = "off"
    with _lock:
        _cached = (now, mode)
    return mode


def forget_cached_mode() -> None:
    """Called right after the mode is saved, so this process sees it at once."""
    global _cached
    with _lock:
        _cached = (0.0, "off")


def request_key(kwargs: dict) -> str:
    """The same request (model, messages - images included - and every other parameter) always
    gives the same key. `seed` is left out: it is added inside the retry loop."""
    body = {k: v for k, v in kwargs.items() if k not in ("seed", "timeout")}
    raw = json.dumps(body, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _path(key: str) -> Path:
    return Path(get_settings().uploads_dir) / "ai_recordings" / key[:2] / f"{key}.json"


def _response(content: str, finish_reason: str = "stop", model: str = "") -> SimpleNamespace:
    """Shaped like the OpenAI SDK's ChatCompletion as far as this codebase reads it."""
    message = SimpleNamespace(role="assistant", content=content, tool_calls=None, refusal=None)
    choice = SimpleNamespace(index=0, message=message, finish_reason=finish_reason)
    return SimpleNamespace(id="practice", model=model, choices=[choice], usage=None,
                           system_fingerprint="practice")


def save(kwargs: dict, resp, seconds: float) -> None:
    """record mode: keep the answer for later replays. Never raises."""
    try:
        choice = resp.choices[0]
        data = {"model": str(kwargs.get("model") or ""), "seconds": round(seconds, 2),
                "content": choice.message.content or "",
                "finish_reason": getattr(choice, "finish_reason", None) or "stop"}
        path = _path(request_key(kwargs))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.exception("practice AI: could not save a recording")


def replay(kwargs: dict) -> SimpleNamespace:
    """replay mode: the recorded answer, or an empty one - never a call to OpenAI."""
    model = str(kwargs.get("model") or "")
    path = _path(request_key(kwargs))
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = None
    if data is None:
        time.sleep(_miss_wait())
        return _response("{}", model=model)
    time.sleep(min(float(data.get("seconds") or 0), _MAX_REPLAY_WAIT))
    return _response(data.get("content") or "", data.get("finish_reason") or "stop", model)
