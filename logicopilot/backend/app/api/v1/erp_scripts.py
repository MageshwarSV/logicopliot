import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.browser import VIEWPORT, manager, play_steps
from app.core.llm import ai_choose_action, suggest_field_mapping
from app.core.deps import get_db, require_role
from app.models.erp_script import ErpScript
from app.models.tenant import Tenant
from app.models.user import SUPER_ADMIN
from app.schemas.erp_script import ErpScriptCreate, ErpScriptOut, ErpScriptUpdate

logger = logging.getLogger(__name__)


def _park_snapshot(script: ErpScript) -> dict:
    """The plain-dict view of a script that a parked worker needs. No ORM object crosses into
    the browser thread — it would be attached to a session that thread does not own."""
    from pathlib import Path

    from app.core.config import get_settings

    return {
        "id": script.id,
        "name": script.name,
        "url": script.url,
        "steps": script.steps or [],
        "checkpoint_index": script.checkpoint_index,
        "login": ({"username": script.login_username or "", "password": script.login_password or ""}
                  if script.has_login else None),
        "session_file": Path(get_settings().uploads_dir) / "erp_sessions" / f"{script.id}.json",
    }


def _sync_parked(script: ErpScript) -> dict | None:
    """Make the parked browser match the saved script: park it when the Super Admin has set a
    checkpoint AND ticked stay open, stop it otherwise. Never raises — failing to park must
    not fail the save, because the script itself is still perfectly usable without parking."""
    try:
        from app.core.parked import MANAGER

        if script.stay_open and script.checkpoint_index is not None:
            return MANAGER.park(_park_snapshot(script))
        MANAGER.unpark(script.id)
        return None
    except Exception:  # noqa: BLE001
        logger.exception("could not sync the parked session for script %s", script.id)
        return None


router = APIRouter(prefix="/erp-scripts", tags=["erp scripts"])


@router.post("", response_model=ErpScriptOut, status_code=status.HTTP_201_CREATED)
def create_erp_script(
    payload: ErpScriptCreate,
    tenant_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> ErpScriptOut:
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    script = ErpScript(
        tenant_id=tenant_id,
        name=payload.name,
        url=payload.url,
        has_login=payload.has_login,
        login_username=payload.login_username,
        login_password=payload.login_password,
        template_ids=payload.template_ids,
        steps=[],
        status="draft",
    )
    db.add(script)
    db.commit()
    db.refresh(script)
    return ErpScriptOut.model_validate(script)


@router.get("", response_model=list[ErpScriptOut])
def list_erp_scripts(
    tenant_id: str | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> list[ErpScriptOut]:
    query = db.query(ErpScript)
    if tenant_id:
        query = query.filter(ErpScript.tenant_id == tenant_id)
    return [ErpScriptOut.model_validate(s) for s in query.order_by(ErpScript.created_at.desc()).all()]


@router.get("/{script_id}", response_model=ErpScriptOut)
def get_erp_script(
    script_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> ErpScriptOut:
    script = db.get(ErpScript, script_id)
    if script is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ERP script not found")
    return ErpScriptOut.model_validate(script)


@router.patch("/{script_id}", response_model=ErpScriptOut)
def update_erp_script(
    script_id: str,
    payload: ErpScriptUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> ErpScriptOut:
    script = db.get(ErpScript, script_id)
    if script is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ERP script not found")
    updates = payload.model_dump(exclude_unset=True)
    if "steps" in updates and updates["steps"] is not None:
        updates["steps"] = [s if isinstance(s, dict) else s.model_dump() for s in payload.steps]
    # stay_open is NOT NULL in the database, but the request model allows null so the field can
    # be omitted. An explicit null means "off", not "write NULL and fail the commit".
    if updates.get("stay_open") is None and "stay_open" in updates:
        updates["stay_open"] = False
    for field, value in updates.items():
        setattr(script, field, value)
    db.commit()
    db.refresh(script)
    # Setting the checkpoint + stay open takes effect now, not at the next restart. Also stops
    # a parked browser whose steps or URL just changed under it.
    if {"checkpoint_index", "stay_open", "steps", "url", "login_username", "login_password"} & set(updates):
        _sync_parked(script)
    return ErpScriptOut.model_validate(script)


@router.get("/{script_id}/parked")
def parked_status(
    script_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Is a browser currently parked at this script's checkpoint, and what is it doing?

    phase: disabled | refused | starting | parking | ready | busy | backoff | stopped | none
    """
    from app.core.parked import MANAGER

    script = db.get(ErpScript, script_id)
    if script is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ERP script not found")
    mine = [s for s in MANAGER.status() if s["script_id"] == script_id]
    if mine:
        return mine[0]
    return {
        "script_id": script_id,
        "phase": "none",
        "detail": ("no checkpoint set — this script opens a browser, runs every step and closes"
                   if script.checkpoint_index is None else
                   "checkpoint set but 'stay open' is off" if not script.stay_open else
                   "not parked yet"),
    }


@router.post("/{script_id}/parked/restart")
def parked_restart(
    script_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Rebuild the parked session — used after changing the login or when the ERP has changed
    under it. Stops the current browser and parks a fresh one."""
    from app.core.parked import MANAGER

    script = db.get(ErpScript, script_id)
    if script is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ERP script not found")
    MANAGER.unpark(script_id)
    snap = _sync_parked(script)
    return snap or {"script_id": script_id, "phase": "none",
                    "detail": "nothing to park (no checkpoint, or stay open is off)"}


@router.delete("/{script_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_erp_script(
    script_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> None:
    script = db.get(ErpScript, script_id)
    if script is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ERP script not found")
    # Stop the parked browser first: nothing else will ever release it once the row is gone.
    try:
        from app.core.parked import MANAGER

        MANAGER.unpark(script_id)
    except Exception:  # noqa: BLE001
        logger.exception("could not unpark the deleted script %s", script_id)
    db.delete(script)
    db.commit()


# --------------------------------------------------------------------------- #
# Live browser recorder — server-side Chromium streamed as screenshots
# --------------------------------------------------------------------------- #
class Point(BaseModel):
    x: float
    y: float


class TabIndex(BaseModel):
    index: int


class TypeAt(BaseModel):
    x: float
    y: float
    value: str = ""
    field_label: str | None = None


class SelectAt(BaseModel):
    x: float
    y: float
    value: str
    selector: str | None = None
    frames: list[str] | None = None


class InspectAt(BaseModel):
    x: float
    y: float
    fields: list[str] = []


class AutocompleteAt(BaseModel):
    x: float
    y: float
    value: str = ""


class AutocompletePick(BaseModel):
    value: str
    selector: str
    frames: list[str] | None = None


class ClickSelector(BaseModel):
    selector: str
    frames: list[str] | None = None


class ScrollBy(BaseModel):
    # Pixels to move: positive scrolls down, negative up. Bounded so a stray value cannot
    # jump the page somewhere the Super Admin did not intend and lose their place.
    dy: int = 600

    def clamped(self) -> int:
        return max(-20000, min(20000, self.dy))


class AiAction(BaseModel):
    goal: str


class PlayRequest(BaseModel):
    values: dict[str, str] = {}


def _get_script(db: Session, script_id: str) -> ErpScript:
    script = db.get(ErpScript, script_id)
    if script is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ERP script not found")
    return script


def _session_or_404(sid: str):
    s = manager.get(sid)
    if s is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recorder session not found or expired")
    return s


@router.post("/{script_id}/recorder/start")
def recorder_start(script_id: str, db: Session = Depends(get_db), _=Depends(require_role(SUPER_ADMIN))):
    script = _get_script(db, script_id)
    login = {"username": script.login_username or "", "password": script.login_password or ""} if script.has_login else None
    try:
        sid = manager.start(script.url, login)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not open the site: {exc}")
    s = manager.get(sid)
    return {"session_id": sid, "viewport": s.active_viewport(), "screenshot": s.screenshot_b64(),
            "dialogs": s.dialogs()}


@router.get("/{script_id}/recorder/{sid}/screenshot")
def recorder_screenshot(script_id: str, sid: str, _=Depends(require_role(SUPER_ADMIN))):
    """The live page, plus any alert the ERP raised since the last poll.

    `viewport` rides along on every poll on purpose. The UI turns a click on the screenshot
    into page coordinates by scaling through it, so if the two ever disagree every click lands
    somewhere other than where it was aimed. Sending it each time makes that self-correcting
    instead of depending on the UI catching each transition that could change it - a tab that
    is a smaller popup window, a window the ERP resizes itself.
    """
    s = _session_or_404(sid)
    return {"screenshot": s.screenshot_b64(), "dialogs": s.dialogs(),
            "viewport": s.active_viewport()}


@router.get("/{script_id}/recorder/{sid}/tabs")
def recorder_tabs(script_id: str, sid: str, _=Depends(require_role(SUPER_ADMIN))):
    """Every tab open in the recorder's browser. An ERP that opens the next screen in a new tab
    on submit was previously invisible — the recorder only ever watched the first one."""
    return {"tabs": _session_or_404(sid).list_tabs()}


@router.post("/{script_id}/recorder/{sid}/switch-tab")
def recorder_switch_tab(script_id: str, sid: str, body: TabIndex, _=Depends(require_role(SUPER_ADMIN))):
    """Drive a different tab from now on, and hand back a screenshot of it."""
    s = _session_or_404(sid)
    info = s.switch_tab(body.index)
    # The new tab may be a popup window of a different size, and the UI maps clicks on the
    # screenshot through this — using the session's original size would skew every click.
    return {
        "tab": info,
        "tabs": s.list_tabs(),
        "screenshot": s.screenshot_b64(),
        "viewport": s.active_viewport(),
        "dialogs": s.dialogs(),
    }


@router.post("/{script_id}/recorder/{sid}/click")
def recorder_click(script_id: str, sid: str, p: Point, _=Depends(require_role(SUPER_ADMIN))):
    s = _session_or_404(sid)
    info = s.click_at(p.x, p.y)
    return {"element": info, "screenshot": s.screenshot_b64(), "dialogs": s.dialogs()}


@router.post("/{script_id}/recorder/{sid}/inspect")
def recorder_inspect(script_id: str, sid: str, body: InspectAt, _=Depends(require_role(SUPER_ADMIN))):
    """Touch an element: focus it, capture its descriptor, and (for inputs) ask the AI
    which template field belongs here so the popup can pre-select it."""
    s = _session_or_404(sid)
    info = s.inspect_at(body.x, body.y)
    suggestion = None
    if info and (info.get("is_input") or info.get("is_select")) and body.fields:
        suggestion = suggest_field_mapping(info.get("label") or info.get("text") or "", info.get("options"), body.fields)
    return {"element": info, "suggestion": suggestion, "screenshot": s.screenshot_b64(),
            "dialogs": s.dialogs()}


# A cleaned sample per mark, worked out once and kept for the life of the process. Recording is
# interactive, so the first drag of a field pays for the transform and every later one is free.
_SAMPLE_CACHE: dict[str, str] = {}
# One sample workbook per template group, kept for the life of the process: a Super Admin presses
# Next many times while recording, and rebuilding it each press would be pointless work.
_SAMPLE_BOOKS: dict[str, str] = {}


def _sample_for_label(db: Session, script: ErpScript, label: str) -> tuple[str, str]:
    """A REAL value to type while recording. Returns (value, where it came from).

    Dragging a field used to type the LABEL into the ERP box - "Container No" as the container
    number. The ERP then rejects it, dependent lookups never fire, and the next steps cannot be
    recorded at all, because the form is sitting in a state it would never be in on a real run.

    A raw marked sample is not good enough either: `Gross Wt` is marked as
    "Gross 654.320 654.320 Weight kgs" and the tenant's own format rule says to keep only the
    weight. Typing the raw text would be worse than the label. So the rule is applied first -
    the same rule, through the same resolver the ERP step itself uses at run time.

    The saved step still carries field_label, so a real run uses the job's own value. This only
    changes what is typed WHILE RECORDING.
    """
    from app.models.custom_field import CustomField
    from app.models.field_mark import FieldMark
    from app.models.template_document import TemplateDocument

    groups = script.template_ids or []
    if not groups or not label:
        return "", "no template linked"

    # 1. a hardcoded custom field is already the final value - nothing to transform
    cf = (
        db.query(CustomField)
        .filter(CustomField.group_id.in_(groups), CustomField.label_name == label)
        .first()
    )
    if cf is not None:
        if cf.kind == "hardcoded" and (cf.hardcoded_value or "").strip():
            return cf.hardcoded_value.strip(), "hardcoded custom field"
        # A target-value field (Quotation Value, say) is still an AI rule, but its own
        # reference table already holds real, previously-learned answers - "RFQ/0003/23-24"
        # for one forwarder, "RFQ/0003/24-25" for another. Typing the field's own LABEL
        # ("Quotation Value") instead, as the fallback below used to, is not just a poor
        # sample: on this ERP it is not a valid quotation number at all, so the box never
        # validates and every step after it fails to record. Any one learned value is a much
        # closer stand-in for what a real job will actually put there than the label is.
        if cf.is_target_value:
            from app.models.custom_field_reference import CustomFieldReferenceValue

            # Newest learned first - id is a running row number, so the highest one is
            # whichever answer was added or corrected most recently, and the most recent
            # answer is the best stand-in for "what a real job looks like right now" (a
            # quotation number from last year reads as a stale, slightly wrong sample next
            # to this year's real ones).
            row = (
                db.query(CustomFieldReferenceValue)
                .filter(CustomFieldReferenceValue.custom_field_id == cf.id,
                        CustomFieldReferenceValue.resolved_value.isnot(None))
                .order_by(CustomFieldReferenceValue.id.desc())
                .first()
            )
            if row and (row.resolved_value or "").strip():
                return row.resolved_value.strip(), "a previously-learned reference value"
        # An AI rule with nothing learned yet has no sample: it is decided per job from that
        # job's own documents.
        return "", f"{label!r} is an AI rule - it has no sample value until a job runs"

    doc_ids = [
        d.id for d in db.query(TemplateDocument).filter(TemplateDocument.group_id.in_(groups)).all()
    ]
    if not doc_ids:
        return "", "the template has no documents"

    marks = (
        db.query(FieldMark)
        .filter(FieldMark.document_id.in_(doc_ids), FieldMark.label_name == label)
        .all()
    )
    # The same label is marked on several documents for cross-checking. Prefer one that has an
    # example at all, then the shortest - the tightest crop is the cleanest sample.
    with_example = [m for m in marks if (m.example_value or "").strip()]
    if not with_example:
        return "", f"{label!r} has no example value on any marked document"
    mark = min(with_example, key=lambda m: len(m.example_value or ""))
    raw = (mark.example_value or "").strip()

    rule = (mark.tenant_format_prompt or "").strip()
    if not rule:
        return raw, "marked example"

    if mark.id in _SAMPLE_CACHE:
        return _SAMPLE_CACHE[mark.id], "marked example, format rule applied (cached)"
    try:
        from app.core.llm import resolve_ai_value

        cleaned = (resolve_ai_value(rule, {label: raw}) or "").strip()
    except Exception:  # noqa: BLE001
        cleaned = ""
    # A transform that returns nothing must not blank the sample - the raw text still gets the
    # ERP further than the label does.
    out = cleaned or raw
    _SAMPLE_CACHE[mark.id] = out
    return out, ("marked example, format rule applied" if cleaned else
                 "marked example (the format rule returned nothing)")


@router.post("/{script_id}/recorder/{sid}/type")
def recorder_type(
    script_id: str,
    sid: str,
    body: TypeAt,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
):
    s = _session_or_404(sid)
    val = f"[[{body.field_label}]]" if body.field_label else body.value
    typed, source = body.value, "typed value"
    if body.field_label:
        script = db.get(ErpScript, script_id)
        sample, source = ("", "script not found") if script is None else _sample_for_label(
            db, script, body.field_label)
        # Fall back to the label only when there is genuinely no sample. It is still wrong for
        # the ERP, but it is what the recorder has always done and it keeps the mapping visible.
        typed = sample or body.field_label
        if not sample:
            source = f"no sample ({source}) - typed the field name instead"
    info = s.type_at(body.x, body.y, typed)
    return {"element": info, "value": val, "typed": typed, "sample_source": source,
            "screenshot": s.screenshot_b64(), "dialogs": s.dialogs()}


@router.post("/{script_id}/recorder/{sid}/autocomplete")
def recorder_autocomplete(script_id: str, sid: str, body: AutocompleteAt, _=Depends(require_role(SUPER_ADMIN))):
    """Type into a type-ahead field and return the live suggestion list + a fresh shot."""
    s = _session_or_404(sid)
    suggestions = s.autocomplete_at(body.x, body.y, body.value)
    return {"suggestions": suggestions, "screenshot": s.screenshot_b64(),
            "dialogs": s.dialogs()}


@router.post("/{script_id}/recorder/{sid}/autocomplete-pick")
def recorder_autocomplete_pick(script_id: str, sid: str, body: AutocompletePick, _=Depends(require_role(SUPER_ADMIN))):
    """Commit a chosen type-ahead option in the live browser and return a fresh shot."""
    s = _session_or_404(sid)
    res = s.autocomplete_pick(body.selector, body.frames, body.value)
    # `action` tells the recorder HOW the option was actually committed - a native <select>
    # needs a select step, a type-ahead needs autocomplete. Recording the wrong one means
    # playback fails exactly the way the live attempt would have.
    return {"applied": res.get("applied"), "action": res.get("action") or "autocomplete",
            "screenshot": s.screenshot_b64(), "dialogs": s.dialogs()}


@router.post("/{script_id}/recorder/{sid}/select")
def recorder_select(script_id: str, sid: str, body: SelectAt, _=Depends(require_role(SUPER_ADMIN))):
    s = _session_or_404(sid)
    if body.selector:  # preferred: apply by the known selector from inspect
        info = s.select_value(body.selector, body.frames, body.value)
    else:  # fallback: detect by coordinate
        info = s.select_at(body.x, body.y, body.value)
    return {"element": info, "screenshot": s.screenshot_b64(), "dialogs": s.dialogs()}


@router.post("/{script_id}/recorder/{sid}/events")
def recorder_events(script_id: str, sid: str, _=Depends(require_role(SUPER_ADMIN))):
    """Capture events: every clickable element currently on the live page."""
    s = _session_or_404(sid)
    return {"events": s.list_events()}


@router.post("/{script_id}/recorder/{sid}/click-selector")
def recorder_click_selector(script_id: str, sid: str, body: ClickSelector, _=Depends(require_role(SUPER_ADMIN))):
    """Click a captured element by its selector (records that event into the flow)."""
    s = _session_or_404(sid)
    res = s.click_selector(body.selector, body.frames)
    return {"ok": res.get("ok"), "error": res.get("error"), "screenshot": s.screenshot_b64(),
            "dialogs": s.dialogs()}


@router.post("/{script_id}/recorder/{sid}/double-click")
def recorder_double_click(script_id: str, sid: str, body: ClickSelector, _=Depends(require_role(SUPER_ADMIN))):
    """Double-click a known element live, for the places a single click does nothing."""
    s = _session_or_404(sid)
    res = s.double_click_selector(body.selector, body.frames)
    return {"ok": res.get("ok"), "error": res.get("error"), "screenshot": s.screenshot_b64(),
            "dialogs": s.dialogs()}


@router.post("/{script_id}/recorder/{sid}/double-click-at")
def recorder_double_click_at(script_id: str, sid: str, body: Point, _=Depends(require_role(SUPER_ADMIN))):
    """Double-click a POINT in the live view and report what was under it.

    Used by the recorder's armed double click. It exists alongside /double-click (which takes a
    selector) because the UI has to know what it hit in order to record the step, and the only
    way to ask that before this was /inspect — which clicks to focus first. That made every
    armed double click a single click followed by a double, and on a row that navigates on the
    single one the double landed on the following screen.
    """
    s = _session_or_404(sid)
    el = s.double_click_at(body.x, body.y)
    return {"element": el, "screenshot": s.screenshot_b64(), "dialogs": s.dialogs()}


def _free_sample_values(db: Session, script: ErpScript) -> dict[str, str]:
    """Sample values that cost NOTHING to produce, for an AI rule to reason over.

    An AI rule written during recording has no job behind it, so without this it is handed an
    empty payload and can only guess. Feeding it every field would mean a model call per marked
    field with a format rule - twenty seconds of staring at a spinner the first time. So this
    returns only what is already free: hardcoded custom fields, marked examples that need no
    rule, and any rule result already worked out and cached. It gets better as the cache warms.
    """
    from app.models.custom_field import CustomField
    from app.models.field_mark import FieldMark
    from app.models.template_document import TemplateDocument

    out: dict[str, str] = {}
    groups = script.template_ids or []
    if not groups:
        return out
    for cf in db.query(CustomField).filter(CustomField.group_id.in_(groups)).all():
        if cf.kind == "hardcoded" and (cf.hardcoded_value or "").strip():
            out.setdefault(cf.label_name, cf.hardcoded_value.strip())
    doc_ids = [d.id for d in
               db.query(TemplateDocument).filter(TemplateDocument.group_id.in_(groups)).all()]
    if not doc_ids:
        return out
    for m in db.query(FieldMark).filter(FieldMark.document_id.in_(doc_ids)).all():
        raw = (m.example_value or "").strip()
        if not raw:
            continue
        rule = (m.tenant_format_prompt or "").strip()
        if not rule:
            out.setdefault(m.label_name, raw)          # already clean
        elif m.id in _SAMPLE_CACHE:
            out.setdefault(m.label_name, _SAMPLE_CACHE[m.id])   # worked out earlier
    return out


def _sample_workbook(db: Session, script: ErpScript) -> dict[str, str]:
    """The workbook to attach while RECORDING, keyed the way an upload step resolves it.

    An Excel-entry customer's ERP takes a bulk import, so the upload on its import screen is the
    step that matters - and it could not be recorded, because the real workbook is only built
    during a job run. This builds one from the template's own sample data: the values the
    recorder already types when a field is dragged onto a box, plus the marks' example values
    for the per-line sheets.

    Returns {} when this template does not use Excel entry, or when the workbook cannot be
    built - recording must not break because a sample could not be produced.
    """
    from pathlib import Path

    from app.core.config import get_settings
    from app.models.custom_field import CustomField
    from app.models.field_mark import FieldMark
    from app.models.template_document import TemplateDocument
    from app.models.template_group import TemplateGroup

    groups = script.template_ids or []
    grp = next((g for g in (db.get(TemplateGroup, gid) for gid in groups)
                if g is not None and (g.entry_mode or "fields") == "excel"), None)
    if grp is None:
        return {}
    cached = _SAMPLE_BOOKS.get(grp.id)
    if cached and Path(cached).exists():
        return {"excel_import": cached, Path(cached).name: cached}

    from app.core.excel_entry import build_for_job, sheet_plans

    cfg = grp.excel_config or {}
    # FREE samples only. Asking for each column's properly formatted sample meant a model call
    # per field - 25 of them on this template, one after another, 50 seconds or more before the
    # recorder could take a single step. The ERP only needs a plausible file to accept the
    # import, so a mark's RAW example is good enough here; a real job run still formats
    # everything properly through the field's own rule.
    values = dict(_free_sample_values(db, script))
    for m in db.query(FieldMark).filter(FieldMark.document_id.in_(
            [d.id for d in db.query(TemplateDocument)
             .filter(TemplateDocument.group_id == grp.id).all()] or [""])).all():
        raw = (m.example_value or "").strip()
        if raw and not values.get(m.label_name):
            values[m.label_name] = raw
    # Sample values recorded on the template itself WIN over the two sources above. An AI rule
    # has no sample at all until a job runs, and a mark's raw example is the wrong shape - the
    # port of loading reads "SHANGHAI" where the import demands the 2-letter country code. The
    # ERP validates the workbook on upload and refuses it, so recording stops dead. These come
    # from a real completed job, so every column has a value of the right shape and it costs
    # nothing to produce.
    for label, val in (cfg.get("sample_values") or {}).items():
        if str(val or "").strip():
            values[str(label)] = str(val).strip()

    # Per-line columns need more than one row, or a line-item sheet would import a single row and
    # the ERP would not exercise the part of the screen that matters.
    doc_ids = [d.id for d in
               db.query(TemplateDocument).filter(TemplateDocument.group_id == grp.id).all()]
    rows: dict[str, list[str]] = {}
    if doc_ids:
        for m in db.query(FieldMark).filter(FieldMark.document_id.in_(doc_ids)).all():
            if not m.is_multi_value:
                continue
            val = (values.get(m.label_name) or (m.example_value or "")).strip()
            if val:
                rows.setdefault(m.label_name, [val, val])
    for cf in db.query(CustomField).filter(CustomField.group_id == grp.id).all():
        if cf.per_row:
            val = (values.get(cf.label_name) or (cf.hardcoded_value or "")).strip()
            rows.setdefault(cf.label_name, [val, val])

    out_dir = Path(get_settings().uploads_dir) / "erp_recording" / grp.id
    tpl_dir = Path(get_settings().uploads_dir) / "excel_templates" / grp.id
    tpl = (next((p for p in tpl_dir.glob("template.*")), None)
           if cfg.get("source") == "template" else None)
    try:
        book = build_for_job(cfg, values, rows, out_dir, tpl)
    except Exception as exc:  # noqa: BLE001
        # Say so in the log and let the recording continue; the upload step will report that it
        # had no file, which is clearer than a broken recorder screen.
        logger.warning("no sample workbook for %s while recording: %s", grp.name, exc)
        return {}
    _SAMPLE_BOOKS[grp.id] = str(book)
    logger.info("built %s from template samples, for recording", book.name)
    return {"excel_import": str(book), book.name: str(book)}


class ApplyStep(BaseModel):
    # The step the recorder has just built. Sent back so the server can resolve its value the
    # way a real run would - an AI rule evaluated, a mapped field turned into its sample - and
    # then actually perform it on the live screen.
    step: dict


@router.post("/{script_id}/recorder/{sid}/apply-step")
def recorder_apply_step(
    script_id: str,
    sid: str,
    body: ApplyStep,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
):
    """Resolve a just-built step and perform it on the screen the recorder is standing on.

    Used for the two popup modes that previously recorded a step and entered nothing: an AI
    rule, and a data field mapped onto a dropdown. Leaving the box empty stops the ERP
    validating and makes every later step impossible to record.
    """
    s = _session_or_404(sid)
    step = dict(body.step or {})
    script = db.get(ErpScript, script_id)
    values: dict[str, str] = {}
    if script is not None:
        label = step.get("field_label")
        if label:
            sample, _why = _sample_for_label(db, script, label)
            if sample:
                values[label] = sample
        if step.get("prompt"):
            # A rule may name any field, so give it everything that is free to supply.
            values.update(_free_sample_values(db, script))
    # Only an upload step needs the workbook. Building it for every fill and click was pure
    # waiting - and it happened again after each backend restart, because the cache is in memory.
    needs_book = (step.get("action") or "").strip().lower() == "upload"
    uploads = _sample_workbook(db, script) if (script is not None and needs_book) else {}
    res = s.apply_step(step, values, uploads=uploads)
    return {**res, "element": None, "screenshot": s.screenshot_b64(), "dialogs": s.dialogs()}


@router.post("/{script_id}/recorder/{sid}/scroll")
def recorder_scroll(script_id: str, sid: str, body: ScrollBy, _=Depends(require_role(SUPER_ADMIN))):
    """Scroll the live page up or down and return the fresh screenshot.

    The recorder shows one viewport at a time, so on a long ERP entry form everything below
    the fold could not be seen, clicked or mapped. `moved` reports how far the page really
    travelled (a request for 600px near the end of a form may only move 120), and
    `at_top` / `at_bottom` let the UI say so rather than leaving the button looking broken.
    """
    s = _session_or_404(sid)
    res = s.scroll_by(body.clamped())
    return {**res, "screenshot": s.screenshot_b64(), "dialogs": s.dialogs()}


@router.post("/{script_id}/recorder/{sid}/ai-action")
def recorder_ai_action(script_id: str, sid: str, body: AiAction, _=Depends(require_role(SUPER_ADMIN))):
    """Ask AI which on-page element to click for a goal — returns the pick + all events,
    so the Super Admin can confirm before recording it as an AI step."""
    s = _session_or_404(sid)
    events = s.list_events()
    suggestion = ai_choose_action(body.goal, events)
    return {"events": events, "suggestion": suggestion}


class ReplaySteps(BaseModel):
    steps: list[dict] = []
    values: dict = {}
    # auto  — run the whole draft straight through (the original behaviour, so an older
    #         frontend that sends no mode keeps working)
    # next  — apply just the next recorded step
    # prev  — step one back (re-runs from the start with one step fewer)
    # reset — start over: cursor to zero and back to the first page
    # seek  — set the cursor to `index` without touching the browser, for when a step was
    #         inserted mid-draft and performed by hand
    mode: str = "auto"
    index: int | None = None
    # Diagnosing a draft: push past a broken step instead of stopping, so one pass shows
    # every problem. Replay only — a real job run always stops, because a missing element
    # means the flow has left the rails and carrying on would file an empty entry.
    keep_going: bool = False


@router.post("/{script_id}/recorder/{sid}/replay")
def recorder_replay(
    script_id: str,
    sid: str,
    body: ReplaySteps,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
):
    """Walk the already-recorded steps in the live recorder browser.

    Stopping the recorder closes the browser, so reopening lands on the login page again.
    This puts the screen back where the recording left off, ready to carry on.

    `mode` chooses how: `auto` runs the whole draft, `next`/`prev` walk it one step at a
    time so the Super Admin can watch the exact point a step misbehaves.

    A replay has no job behind it, so every mapped field used to resolve to NOTHING and get
    filled blank - and the ERP then refuses to move on ("Container Number can not be blank"),
    which made a draft impossible to walk past its first required field. The same sample values
    the recorder types when a field is dragged are filled in here, so the form validates and the
    walk reaches the end. Anything the caller supplied wins, and a real job run never comes
    through here - it has the job's own values.
    """
    s = _session_or_404(sid)
    values = dict(body.values or {})
    script = db.get(ErpScript, script_id)
    mode = (body.mode or "auto").strip().lower()
    all_steps = list(body.steps or [])
    # Work out a sample only for the steps this press will actually perform. A sample costs a
    # model call when the mark carries a format rule and nothing is cached yet, so resolving
    # the whole draft to walk one step meant a long wait before the browser even moved - and
    # "seek", which only moves the cursor, paid for the entire draft to do nothing.
    if mode in ("seek", "reset"):
        needed = []
    elif mode == "next":
        # getattr, not s.replay_index: the route must not assume every session object exposes
        # a cursor. Without a cursor, treat it as the start rather than crashing the replay.
        at = int(getattr(s, "replay_index", 0) or 0)
        needed = all_steps[at:at + 1]
    elif mode in ("prev", "back"):
        at = int(getattr(s, "replay_index", 0) or 0)
        needed = all_steps[:max(0, at - 1)]
    else:
        needed = all_steps
    if script is not None:
        for st in needed:
            label = (st.get("field_label") if isinstance(st, dict) else getattr(st, "field_label", None))
            if not label or values.get(label):
                continue
            sample, _why = _sample_for_label(db, script, label)
            if sample:
                values[label] = sample
    body.values = values
    # Same for a replay: only when the steps about to run include an upload.
    will_upload = any((st.get("action") if isinstance(st, dict) else getattr(st, "action", ""))
                      == "upload" for st in (needed or []))
    uploads = _sample_workbook(db, script) if (script is not None and will_upload) else {}
    if mode == "next":
        res = s.replay_next(body.steps, body.values, keep_going=body.keep_going, uploads=uploads)
    elif mode in ("prev", "back"):
        res = s.replay_back(body.steps, body.values, uploads=uploads)
    elif mode == "reset":
        res = s.replay_reset(body.steps)
    elif mode == "seek":
        res = s.replay_seek(body.steps, body.index if body.index is not None else 0)
    else:
        res = s.replay(body.steps, body.values, keep_going=body.keep_going, uploads=uploads)
    return {**res, "screenshot": s.screenshot_b64(), "dialogs": s.dialogs()}


@router.post("/{script_id}/recorder/{sid}/stop")
def recorder_stop(script_id: str, sid: str, _=Depends(require_role(SUPER_ADMIN))):
    manager.stop(sid)
    return {"stopped": True}


@router.post("/{script_id}/play")
def play_script(script_id: str, req: PlayRequest, db: Session = Depends(get_db), _=Depends(require_role(SUPER_ADMIN))):
    script = _get_script(db, script_id)
    login = {"username": script.login_username or "", "password": script.login_password or ""} if script.has_login else None
    return play_steps(script.url, login, script.steps or [], req.values)
