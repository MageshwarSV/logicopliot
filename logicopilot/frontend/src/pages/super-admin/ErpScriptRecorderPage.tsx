import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Input, Select } from "../../components/ui/Input";
import { Alert } from "../../components/ui/Alert";
import { Modal } from "../../components/ui/Modal";
import { HelpDot } from "../../components/ui/HelpDot";
import { ACTION_HELP, FEATURE_HELP } from "./erpActionHelp";
import * as erpApi from "../../api/erpScripts";
import * as onboardingApi from "../../api/onboarding";
import type { ElementInfo, FieldSuggestion } from "../../api/erpScripts";
import type { ErpScript, ErpStep } from "../../types/erpScript";

/** An input the user touched — drives the value popup. */
interface PendingInput {
  x: number;
  y: number;
  element: ElementInfo;
  suggestion: FieldSuggestion | null;
}

/** Is this element something you CLICK, or just text on the page?
 *
 *  Clicking a heading or a status message does nothing useful, and you cannot type into it —
 *  so the recorder offers to PICK it instead. Anything genuinely interactive keeps recording a
 *  click as before. */
function isClickable(el: ElementInfo): boolean {
  // The page itself is the authority: role, an onclick handler, or cursor:pointer. A nav
  // icon is an <img> inside an <a> — judging by tag name alone calls that plain text and
  // offers to "pick" it, when the obvious intent is to click the menu item.
  if (typeof el.is_clickable === "boolean") return el.is_clickable;
  if (el.is_button) return true;
  const tag = (el.tag || "").toLowerCase();
  const type = (el.input_type || "").toLowerCase();
  return (
    tag === "button" || tag === "a" || tag === "select" || tag === "textarea" || tag === "input" ||
    type === "submit" || type === "button" || type === "checkbox" || type === "radio"
  );
}

/** Label for a tab button. Every ERP screen sits on the same host, so a hostname tells you
 *  nothing — prefer the page title, fall back to the last path segment, then the tab number.
 *  Titles are only read for the first dozen tabs (see list_tabs), so the fallback matters. */
function tabLabel(t: { index: number; url: string; title: string }): string {
  if (t.title) return t.title;
  try {
    const path = new URL(t.url).pathname.split("/").filter(Boolean);
    if (path.length) return decodeURIComponent(path[path.length - 1]).slice(0, 28);
  } catch {
    /* not a parseable URL (about:blank, data:) — fall through */
  }
  return `tab ${t.index + 1}`;
}

/** Every action the playback engine understands. Order groups them by purpose so the dropdown
 *  reads sensibly: the ones that were always there, then reading values back out of the ERP,
 *  then synchronisation, then the interaction primitives. */
/** Group captured page elements by what they ARE, so the list is scannable. A flat run of
 *  forty rows tells you nothing about which one is the Submit button. */
function groupEvents(
  events: erpApi.PageEvent[],
): [string, { ev: erpApi.PageEvent; i: number }[]][] {
  const buckets = new Map<string, { ev: erpApi.PageEvent; i: number }[]>();
  events.forEach((ev, i) => {
    const tag = (ev.tag || "").toLowerCase();
    const type = (ev.input_type || "").toLowerCase();
    let group = "Other";
    if (tag === "button" || type === "submit" || type === "button") group = "Buttons";
    else if (tag === "a") group = "Links";
    else if (tag === "select") group = "Dropdowns";
    else if (type === "checkbox" || type === "radio") group = "Checkboxes & radios";
    else if (tag === "input" || tag === "textarea") group = "Text inputs";
    else if (tag === "td" || tag === "th" || tag === "tr") group = "Table cells";
    const arr = buckets.get(group) ?? [];
    arr.push({ ev, i });
    buckets.set(group, arr);
  });
  // Buttons first — that is what a success signal usually is.
  const order = ["Buttons", "Links", "Text inputs", "Dropdowns", "Checkboxes & radios", "Table cells", "Other"];
  return order.filter((g) => buckets.has(g)).map((g) => [g, buckets.get(g)!]);
}

/** Plain-words version of the parked browser's phase, for the Super Admin. */
const PARK_LABEL: Record<string, string> = {
  starting: "Opening the browser…",
  parking: "Logging in and opening the entry screen…",
  ready: "Parked and waiting for a job",
  busy: "Running a job now",
  backoff: "ERP unreachable — retrying",
  stopped: "Stopped",
  refused: "Not parked — another script holds the one allowed session",
  disabled: "Parking is switched off on this server",
  none: "Not parked yet",
};

const PARK_TONE: Record<string, string> = {
  ready: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300",
  busy: "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300",
  starting: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300",
  parking: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300",
  backoff: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300",
  refused: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300",
  disabled: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
};

const ACTIONS = [
  "navigate", "click", "fill", "select", "autocomplete", "submit", "wait", "ai_action", "switch_tab",
  "get_text", "assert_text", "download", "screenshot",
  "wait_for", "wait_gone",
  "hover", "send_keys", "check", "radio", "clear", "dialog",
  "pick_date", "upload", "row_action", "double_click", "scroll",
];

/** One press of ▲ / ▼. Just under a screen, so a couple of rows stay visible across the
 *  jump and you keep your place on a long entry form. */
const SCROLL_PX = 600;

const scrollLabel = (dy: number) => `scroll ${dy > 0 ? "down" : "up"} ${Math.abs(dy)}px`;

/** Pause between steps during a watched Auto replay. Long enough that a screen which loads
 *  and is immediately moved on from can still be seen; short enough not to be tedious. */
const AUTO_STEP_PAUSE_MS = 600;

export function ErpScriptRecorderPage() {
  const { scriptId = "" } = useParams();
  const [script, setScript] = useState<ErpScript | null>(null);
  const [steps, setSteps] = useState<ErpStep[]>([]);
  const [fields, setFields] = useState<string[]>([]);
  // Set when a bound template is in Excel entry mode: the job becomes ONE workbook, so the
  // panel offers that file instead of the individual data fields.
  const [excelBook, setExcelBook] = useState<string | null>(null);
  /** Labels ticked "multiple values in this document" — one value per line-item row, so an
   *  ERP form needs to be told how to take them all rather than just where to put one. */
  /** Tabs open in the recorder's browser — an ERP submit often opens the next screen
   *  in a new one, which the recorder previously never saw. */
  const [tabs, setTabs] = useState<erpApi.BrowserTab[]>([]);
  const [multiFields, setMultiFields] = useState<Set<string>>(new Set());
  /** A multi-value field was dropped on an input; ask how its rows should be entered. */
  const [multiDrop, setMultiDrop] = useState<{ label: string; step: ErpStep } | null>(null);
  const [joinWith, setJoinWith] = useState(", ");
  /** While set, recorded actions are collected into the loop instead of the main script:
   *  phase "row" = the block replayed after every value, "end" = run once after the last. */
  const [rowCapture, setRowCapture] = useState<{ index: number; phase: "row" | "end" } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<ErpStep>({ action: "click", selector: "", value: "", description: "" });

  // live recorder state
  const [sid, setSid] = useState<string | null>(null);
  const [shot, setShot] = useState<string | null>(null);
  const [viewport, setViewport] = useState({ width: 1280, height: 800 });
  const [busy, setBusy] = useState(false);
  const [starting, setStarting] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  // value popup that opens when an input is touched
  const [pending, setPending] = useState<PendingInput | null>(null);
  const [popMode, setPopMode] = useState<"field" | "literal" | "ai" | "pick" | "upload" | "manual">("field");
  /** "Manual Entry": a data field that does not exist yet — named right here, on the spot.
   *  Created as an ordinary ask-the-operator field (same as Quotation Value etc.), so it
   *  shows up on Additional Details with an input box before Submit Entry, exactly like
   *  every other field this screen maps. */
  const [manualLabel, setManualLabel] = useState("");
  /** What gets typed into the ERP box live, WHILE RECORDING only — an operator-facing field
   *  has no document to read a sample from, so without one the recorder would type the
   *  field's own name (not a valid value on any real ERP), and the box would never validate. */
  const [manualExample, setManualExample] = useState("");
  // "Pick data": read a value/text/document OUT of the ERP and show it to the operator.
  const [pickLabel, setPickLabel] = useState("");
  const [pickKind, setPickKind] = useState<"value" | "text" | "document">("value");
  const [pickDesc, setPickDesc] = useState("");
  /** Data is often read out of an ERP only to be typed back into a later screen of the SAME
   *  ERP, in which case the operator never needs to see it — hence three choices, not two. */
  const [pickUsage, setPickUsage] = useState<"internal" | "output" | "both">("both");
  /** Which recorded step is open for editing. Editing was impossible before — the list only
   *  offered delete, so one wrong selector meant re-recording everything after it. */
  const [editIndex, setEditIndex] = useState<number | null>(null);
  // Keep the logged-in session reached at the checkpoint, so the next job starts on the entry
  // screen instead of replaying the login. Off => the script behaves exactly as it always has.
  const [stayOpen, setStayOpen] = useState(false);
  // What the parked browser is doing, polled while this script is set to stay open.
  const [parked, setParked] = useState<erpApi.ParkedStatus | null>(null);
  /** Alerts the ERP raised in the live browser. Playwright answers them so the page keeps
   *  moving; showing them is the only way the Super Admin learns the flow has a prompt. */
  const [liveDialogs, setLiveDialogs] = useState<erpApi.LiveDialog[]>([]);
  const [replaying, setReplaying] = useState(false);
  /** Which replay style the Super Admin picked after pressing Replay: null = not chosen yet,
   *  "auto" = run the draft straight through, "manual" = walk it with Next / Prev. */
  const [replayMode, setReplayMode] = useState<null | "auto" | "manual">(null);
  /** How far the replay has got, so Manual can show "step 4 of 11" and what comes next. */
  const [replayAt, setReplayAt] = useState<{ index: number; total: number; next: string; done: string } | null>(null);
  /** While set, anything recorded is INSERTED at this position in the draft instead of being
   *  appended. Turned on when a replay stops part-way because the ERP has changed since the
   *  script was recorded: the missing action is done by hand and saved in the right place. */
  const [insertAt, setInsertAt] = useState<number | null>(null);
  /** "Run to step N": the number typed into the manual-replay box, in Manual mode. Runs from
   *  the top and stops exactly there — for landing on the one step that needs watching
   *  without pressing Next through every ordinary one in front of it. */
  const [runToStep, setRunToStep] = useState("");
  /** Set while a watched Auto replay is running, so Stop can interrupt it between steps. */
  const autoAbort = useRef(false);
  /** "Keep going": don't stop the replay at the first broken step — skip it and carry on, so
   *  one pass shows every problem in the draft instead of one replay per problem. Diagnosing
   *  only; a real job run always stops, or it would file an entry with fields missing. */
  const [keepGoing, setKeepGoing] = useState(false);
  /** What the last touch actually landed on. Shown under the live browser so a coordinate
   *  mismatch is obvious the moment it happens. */
  const [touched, setTouched] = useState<string | null>(null);
  /** ⌖ Double click armed: the next thing touched in the live view gets a double click,
   *  whatever kind of control it is. The tap-twice-quickly gesture below still works, but it
   *  cannot be relied on here — the view is a relayed screenshot, so the first tap has already
   *  been sent and recorded by the time the second one is judged. Arming says it up front. */
  const [dblArmed, setDblArmed] = useState(false);
  /** Armed "Search & Select": the next thing touched opens the value popup, but confirming it
   *  records a click/double-click matched to whichever field's value is on screen — not a
   *  fill. For a search popup's result row, where the recorded position is never trustworthy
   *  (a different value returns a different number of rows) and only the row's own text can
   *  find it reliably, live or on a real run alike. */
  const [searchClickAction, setSearchClickAction] = useState<null | "click" | "double_click">(null);
  // Search & Click / Search & Double-click's OWN value-source state — kept separate from the
  // main touch popup's (popMode/popField/...) since the two can never be open at once but
  // opening one should never show stale state left over from the other.
  const [searchPopMode, setSearchPopMode] = useState<"field" | "literal" | "manual">("field");
  const [searchField, setSearchField] = useState("");
  const [searchLiteral, setSearchLiteral] = useState("");
  const [searchManualLabel, setSearchManualLabel] = useState("");
  const [searchManualExample, setSearchManualExample] = useState("");
  /** The click just recorded, so a second tap in the same spot can be promoted to a DOUBLE
   *  click. Recording the double directly is not possible: the first tap has already fired
   *  by the time the second arrives, so the step is upgraded in place instead. */
  const lastClick = useRef<{ t: number; x: number; y: number; index: number } | null>(null);
  // Which step currently carries the checkpoint (null = none). Drives the toolbar button label.
  const cpIdx = steps.findIndex((s) => s.is_checkpoint);
  const checkpointStep = cpIdx >= 0 ? cpIdx : null;
  const [popField, setPopField] = useState<string>("");
  const [popLiteral, setPopLiteral] = useState<string>("");
  const [popOption, setPopOption] = useState<string>("");
  const [popPrompt, setPopPrompt] = useState<string>("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [isTypeahead, setIsTypeahead] = useState(false); // field-mode: map to a searchable dropdown
  const suggestTimer = useRef<number | null>(null);
  const [playLog, setPlayLog] = useState<string[] | null>(null);
  const [live, setLive] = useState(true);
  // Capture events + AI step
  const [events, setEvents] = useState<erpApi.PageEvent[] | null>(null);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [aiGoal, setAiGoal] = useState("");
  const [aiPick, setAiPick] = useState<{ index: number | null; reason: string } | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const sidRef = useRef<string | null>(null);
  const promptRef = useRef<HTMLTextAreaElement | null>(null);
  // Latest interaction state, read inside the polling loop without re-creating it.
  // Poll the parked-session status. Only while this script is actually set to stay open, so
  // a normal script makes no extra requests at all.
  useEffect(() => {
    if (!stayOpen || checkpointStep === null) {
      setParked(null);
      return;
    }
    let live = true;
    const tick = () =>
      erpApi.getParkedStatus(scriptId)
        .then((s) => { if (live) setParked(s); })
        .catch(() => { if (live) setParked(null); });
    tick();
    const id = window.setInterval(tick, 10000);
    return () => { live = false; window.clearInterval(id); };
  }, [stayOpen, checkpointStep, scriptId]);

  const pollGate = useRef({ busy: false, popupOpen: false });
  pollGate.current = { busy, popupOpen: !!pending };

  useEffect(() => {
    erpApi.getErpScript(scriptId)
      .then((s) => {
        setScript(s);
        setSteps(s.steps ?? []);
        setStayOpen(!!s.stay_open);
        // pull real field labels from every linked template group
        const ids = s.template_ids ?? [];
        return Promise.all(ids.map((id) => onboardingApi.getGroup(id).catch(() => null)));
      })
      .then((groups) => {
        if (!groups) return;
        const labels = new Set<string>();
        const multi = new Set<string>();
        for (const g of groups) {
          if (!g) continue;
          for (const d of g.documents ?? [])
            for (const m of d.marks ?? []) {
              labels.add(m.label_name);
              if (m.is_multi_value) multi.add(m.label_name);
            }
          // CUSTOM fields belong here too. They were left out, so a template could carry sixty
          // of them - Consignment Type, Container No, Cont Type, CustomsHouseCode - and none
          // could be mapped to an ERP input, because you cannot drag a field the panel never
          // offers. The value side always worked: a custom field is written as a JobFieldValue
          // with the same label_name, so `values` already carries it and [[label]] resolves.
          // Only the panel was blind to them. per_row is the custom-field spelling of
          // is_multi_value - one value per product line - so it feeds the same row-loop UI.
          for (const cf of g.custom_fields ?? []) {
            if (!cf.label_name) continue;
            labels.add(cf.label_name);
            if (cf.per_row) multi.add(cf.label_name);
          }
        }
        setFields([...labels]);
        setMultiFields(multi);
        // An Excel-entry template does not type values into boxes - it hands over one file.
        const xl = groups.find((g) => g && (g.entry_mode ?? "fields") === "excel");
        setExcelBook(xl ? ((xl.excel_config?.file_name || "import.xlsx")) : null);
      })
      .catch(() => setError("Failed to load the ERP script."));
  }, [scriptId]);

  // stop the browser session when leaving the page
  useEffect(() => () => {
    if (sidRef.current) erpApi.recorderStop(scriptId, sidRef.current).catch(() => {});
  }, [scriptId]);

  // Live view: while recording (and not mid-action / popup), poll the screenshot so the
  // page updates on its own — navigations and slow loads appear as they happen.
  useEffect(() => {
    if (!sid || !live) return;
    let active = true;
    let inflight = false;
    const id = window.setInterval(async () => {
      if (!active || inflight || pollGate.current.busy || pollGate.current.popupOpen) return;
      inflight = true;
      try {
        const s = await erpApi.recorderScreenshot(scriptId, sid);
        if (active && s.screenshot) setShot(s.screenshot);
        if (active && s.dialogs.length) absorbDialogs(s.dialogs);
        // Track the size on every poll, not just when a tab is switched. Then a mismatch
        // corrects itself within one tick however it arose — a popup window of its own size,
        // a window the ERP resized, a tab that appeared without the UI being told.
        if (active && s.viewport?.width && s.viewport?.height) {
          setViewport((v) =>
            v.width === s.viewport!.width && v.height === s.viewport!.height ? v : s.viewport!,
          );
        }
      } catch {
        /* transient — next tick retries */
      } finally {
        inflight = false;
      }
    }, 1000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, [sid, live, scriptId]);

  // Poll the tab list too, more slowly than the screenshot. A submit that opens the next screen
  // in a new tab must show up on its own — otherwise the recording silently stops following the
  // flow and nobody notices until playback fails.
  useEffect(() => {
    if (!sid) return;
    let active = true;
    const read = async () => {
      if (!active || pollGate.current.busy) return;
      try {
        const t = await erpApi.recorderTabs(scriptId, sid);
        if (active) setTabs(t);
      } catch {
        /* non-fatal — the tab bar is a convenience */
      }
    };
    read();
    const id = window.setInterval(read, 3000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, [sid, scriptId]);

  /** While recording a line-item field, steps belong to that field's repeating row block (or
   *  its closing block) rather than to the main script. */
  /** The spot inside the element that was pressed, to store on the step.
   *
   *  A replay clicks the CENTRE of an element. On a results row the centre is whichever column
   *  sits in the middle — often a link, so a double-click meant to open the row navigated away
   *  to another site instead, however carefully the spot was chosen here. Recording the spot
   *  makes the replay press where the person actually pressed.
   */
  function spotOf(el: erpApi.ElementInfo | null | undefined) {
    if (!el || el.click_fx == null || el.click_fy == null) return {};
    return { click_fx: el.click_fx, click_fy: el.click_fy };
  }

  // A tickbox press is not a button press. "Click this again" toggles, so replaying a recorded
  // tick against a box that is already ticked turns it OFF - and on the import dialog these
  // boxes choose which sheets are read, so an un-tick imports the wrong data without failing.
  // Record the STATE the step should leave it in. The element is read before the press lands,
  // so the state it ends in is the opposite of what it reports now.
  function toggleStep(el: erpApi.ElementInfo | null | undefined) {
    if (!el?.is_toggle || el.checked_now == null) return null;
    const want = !el.checked_now;
    // A radio cannot be un-picked by pressing it, so a radio step is always "pick this".
    return { action: "check", value: String(el.is_toggle === "radio" ? true : want) };
  }

  function pushStep(s: ErpStep) {
    if (rowCapture) {
      const key = rowCapture.phase === "row" ? "row_steps" : "end_steps";
      setSteps((arr) =>
        arr.map((st, idx) => (idx === rowCapture.index ? { ...st, [key]: [...(st[key] ?? []), s] } : st)),
      );
      return;
    }
    // Repairing a draft mid-flight. The ERP has grown a screen that did not exist when this
    // was recorded, so replay stopped there. Whatever is done now goes in AT that point —
    // appending it as the last step would put the fix after the entry was already submitted.
    if (insertAt !== null) {
      const at = Math.max(0, Math.min(insertAt, steps.length));
      const next = [...steps.slice(0, at), s, ...steps.slice(at)];
      setSteps(next);
      setInsertAt(at + 1);            // several new steps go in one after another, in order
      // The action was just performed in the live browser, so the screen is already one step
      // further on than the replay thinks. Move the position to match — re-running it would
      // undo what was just done.
      seekReplay(next, at + 1);
      setStatus(`Saved as step ${at + 1}. Still inserting here — press "done" when the flow is back on track.`);
      return;
    }
    setSteps((arr) => [...arr, s]);
  }

  /** A JS dialog the ERP just raised. Record it as a step AUTOMATICALLY.
   *
   *  It used to need a button press on the banner, and a dialog you did not notice never
   *  became a step. That is not a cosmetic miss: Playwright auto-DISMISSES any dialog nothing
   *  is waiting for, so on replay an unrecorded confirm is silently answered "Cancel" — the
   *  entry quietly does not save. A dialog that fired while recording will fire again on
   *  replay, so it always needs a step. The banner now just reports what was recorded and
   *  lets you switch it to dismiss.
   *
   *  Answered "accept" to match what the live recorder itself did when the dialog appeared.
   */
  function absorbDialogs(list: erpApi.LiveDialog[] | undefined) {
    if (!list || list.length === 0) return;
    setLiveDialogs((d) => [...d, ...list].slice(-4));
    for (const d of list) {
      pushStep({
        action: "dialog",
        value: "accept",
        description: (d.message || d.type || "alert").slice(0, 80),
      });
    }
    setStatus(
      list.length === 1
        ? `The ERP showed an alert — recorded as a "dialog: accept" step so playback answers it too.`
        : `The ERP showed ${list.length} alerts — each recorded as a "dialog: accept" step.`,
    );
  }

  /** Tell the recorder how many steps of the draft are now applied to the live screen. */
  async function seekReplay(list: ErpStep[], index: number) {
    if (!sid) return;
    try {
      const res = await erpApi.recorderReplay(scriptId, sid, list, {}, "seek", index);
      setReplayAt({ index: res.index, total: res.total, next: res.next_label, done: res.done_label });
    } catch {
      /* non-fatal: the counter is a convenience, never block recording on it */
    }
  }

  async function startRecorder() {
    setStarting(true);
    setError(null);
    setStatus("Opening the ERP site in a server browser…");
    try {
      const res = await erpApi.recorderStart(scriptId);
      absorbDialogs(res.dialogs);
      setSid(res.session_id);
      sidRef.current = res.session_id;
      setViewport(res.viewport);
      setShot(res.screenshot);
      setStatus("Live. Click to record clicks; drag a data field onto an input to map it.");
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "Could not open the browser session.");
      setStatus(null);
    } finally {
      setStarting(false);
    }
  }

  async function stopRecorder() {
    if (!sid) return;
    await erpApi.recorderStop(scriptId, sid).catch(() => {});
    setSid(null);
    sidRef.current = null;
    setShot(null);
    setStatus(null);
    setTabs([]);
  }

  /** Put the live browser back where the recording left off. Stopping the recorder closes
   *  the browser, so reopening it lands on the login page with every recorded step still
   *  ahead of you — this walks them so recording can carry on from the right screen. */
  /** Replay the draft back into the live browser.
   *
   *  A recording is often left half-finished and saved as a draft. Reopening the recorder
   *  lands on the login page, so the draft has to be re-run to get back to where it stopped.
   *  AUTO does that in one go. MANUAL walks it a step at a time, which is what you need when
   *  one step misbehaves and you want to see the exact moment it goes wrong.
   *
   *  "prev" cannot undo a browser action — nothing can un-type a value or un-click a button —
   *  so it re-runs from the start with one step fewer. That lands on the same screen and is
   *  why it takes longer than "next".
   */
  async function runReplay(mode: erpApi.ReplayMode) {
    if (!sid || steps.length === 0) return;
    // AUTO walks the draft one step at a time from here rather than in a single request.
    // Running all 21 steps server-side returned only the FINAL screenshot, so every screen
    // the flow passed through — the login, the branch dialog, each menu — flashed by unseen.
    // Stepping it client-side means the live browser repaints after every step, which is the
    // whole point of watching a replay.
    if (mode === "auto") return runAutoWatched();
    setReplaying(true);
    setError(null);
    try {
      const res = await erpApi.recorderReplay(scriptId, sid, steps, {}, mode);
      if (res.screenshot) setShot(res.screenshot);
      absorbDialogs(res.dialogs);
      setReplayAt({ index: res.index, total: res.total, next: res.next_label, done: res.done_label });
      const failed = res.log.find((l) => l.includes("FAILED"));
      if (failed) {
        setError(`Replay stopped at step ${res.index + 1}: ${failed}`);
        return;
      }
      if (mode === "reset") {
        setStatus("Replay position reset — press Next to start from step 1.");
      } else {
        setStatus(
          res.at_end
            ? `Step ${res.index} of ${res.total} done — that is the end of the draft, carry on recording.`
            : `Step ${res.index} of ${res.total} done. Next up: ${res.next_label || "—"}`,
        );
      }
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "Could not replay the recorded steps.");
    } finally {
      setReplaying(false);
    }
  }

  /** "Run to step N": from the top, straight through to a chosen step, then stop and wait
   *  there — one request, not N presses of Next. */
  async function runReplayTo() {
    if (!sid || steps.length === 0) return;
    const n = parseInt(runToStep, 10);
    if (!Number.isFinite(n) || n < 1) {
      setError("Enter which step to run to (1 or more).");
      return;
    }
    setReplaying(true);
    setError(null);
    try {
      const res = await erpApi.recorderReplay(scriptId, sid, steps, {}, "to", Math.min(n, steps.length));
      if (res.screenshot) setShot(res.screenshot);
      absorbDialogs(res.dialogs);
      setReplayAt({ index: res.index, total: res.total, next: res.next_label, done: res.done_label });
      const failed = res.log.find((l) => l.includes("FAILED"));
      if (failed) {
        setError(`Stopped at step ${res.index + 1}: ${failed}`);
      } else {
        setStatus(
          `Ran to step ${res.index} of ${res.total} and stopped there. Next up: ${res.next_label || "—"}`,
        );
      }
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "Could not run to that step.");
    } finally {
      setReplaying(false);
    }
  }

  /** Auto, but visible: reset to the first page, then apply one step at a time, repainting
   *  the live browser after each so every screen the flow moves through can be watched.
   *  Stops on the first failure, and can be interrupted with the Stop button. */
  async function runAutoWatched() {
    if (!sid || steps.length === 0) return;
    autoAbort.current = false;
    setReplaying(true);
    setError(null);
    try {
      const start = await erpApi.recorderReplay(scriptId, sid, steps, {}, "reset");
      if (start.screenshot) setShot(start.screenshot);
      setReplayAt({ index: 0, total: start.total, next: start.next_label, done: "" });
      const broken: number[] = [];

      for (let n = 0; n < steps.length; n++) {
        if (autoAbort.current) {
          setStatus(`Stopped by you at step ${n} of ${steps.length}.`);
          return;
        }
        const res = await erpApi.recorderReplay(scriptId, sid, steps, {}, "next", undefined, keepGoing);
        if (res.screenshot) setShot(res.screenshot);
        absorbDialogs(res.dialogs);
        setReplayAt({ index: res.index, total: res.total, next: res.next_label, done: res.done_label });

        const failed = res.log.find((l) => l.includes("FAILED"));
        if (failed && !keepGoing) {
          setError(`Replay stopped at step ${res.index + 1}: ${failed}`);
          return;
        }
        if (failed) {
          broken.push(res.index);        // keep going — collect it and move on
          setStatus(`Auto: step ${res.index} of ${res.total} FAILED, skipping — ${broken.length} broken so far`);
        } else {
          setStatus(
            `Auto: step ${res.index} of ${res.total} — ${res.done_label || ""}` +
              (res.at_end ? " · end of the draft" : ` · next: ${res.next_label || "—"}`),
          );
        }
        if (res.at_end) {
          if (broken.length) {
            // The whole draft was walked; name every step that needs attention, so one pass
            // replaces one replay per problem.
            setError(
              `Auto reached the end of ${res.total} step(s), but ${broken.length} failed and were skipped: ` +
                `step ${broken.join(", ")}. Fix those, or mark them optional with ?`,
            );
          } else {
            setStatus(`Auto finished: all ${res.total} step(s) replayed — carry on recording.`);
          }
          return;
        }
        // A beat between steps, so a screen that loads and moves on is actually seen.
        await new Promise((r) => setTimeout(r, AUTO_STEP_PAUSE_MS));
      }
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "Could not replay the recorded steps.");
    } finally {
      setReplaying(false);
    }
  }

  async function refreshShot() {
    if (!sid) return;
    try {
      const s = await erpApi.recorderScreenshot(scriptId, sid);
      setShot(s.screenshot);
      absorbDialogs(s.dialogs);
    } catch { /* ignore */ }
  }

  /** Attach the job's workbook to the file box that was touched, and record the step.
   *  While recording, the workbook carries the template's sample values, so the ERP accepts
   *  the import and whatever comes after it can be recorded too. */
  async function recordWorkbookUpload() {
    if (!sid || !pending) return;
    const el = pending.element;
    const step: ErpStep = {
      action: "upload",
      selector: el?.selector ?? "",
      frames: el?.frames ?? undefined,
      value: "excel_import",
      description: el?.text || "import file",
    };
    setBusy(true);
    try {
      const res = await erpApi.recorderApplyStep(scriptId, sid, step);
      absorbDialogs(res.dialogs);
      if (res.screenshot) setShot(res.screenshot);
      if (res.ok) {
        pushStep(step);
        setPending(null);
        setStatus(`Attached ${excelBook}. Each job will attach its own workbook here.`);
      } else {
        setError(res.error || "The ERP would not take the workbook in that box.");
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not attach the workbook.");
    } finally {
      setBusy(false);
    }
  }

  /** "⊙ Just click it" in the value popup: the thing touched is a tab or a button, not data.
   *
   *  Needed because clickability cannot always be detected. A tab header built as a <td> whose
   *  handler was attached with addEventListener has no inline onclick and often no
   *  cursor:pointer, and script cannot enumerate listeners — so it is indistinguishable from a
   *  plain data cell, and the popup opened on it as if the intent were to read a value out.
   *  Rather than guess from the shape of the element, let it be said in one tap. */
  async function recordPendingClick() {
    if (!pending || !sid || busy) return;
    const el = pending.element;
    const target = el.click_selector || el.selector;
    if (!target) {
      setStatus("There is no element here to click.");
      setPending(null);
      return;
    }
    const caption = (el.click_text || el.text || el.label || "").trim();
    setBusy(true);
    setPending(null);
    try {
      pushStep({
        action: "click",
        selector: target,
        frames: el.frames ?? undefined,
        description: caption || undefined,
      });
      const r = await erpApi.recorderClickSelector(scriptId, sid, target, el.frames ?? undefined);
      absorbDialogs(r.dialogs);
      if (r.screenshot) setShot(r.screenshot);
      // The step is kept even when the ERP ignored the click: a tab that needs a double click
      // is then one press of ⌖ Double click away, rather than a step that was silently lost.
      setStatus(
        r.ok
          ? `Recorded a click on ${caption ? `“${caption}”` : target}.`
          : `Recorded the click on ${caption ? `“${caption}”` : target}, but the ERP did not ` +
            `react — if it only opens on a double click, use ⌖ Double click.`,
      );
    } catch (err: any) {
      setStatus(`Recorded the click, but it could not be performed: ${err?.message ?? "unknown"}`);
    } finally {
      setBusy(false);
    }
  }

  /** Translate a browser mouse event on the <img> into page-viewport coordinates.
   *
   *  `viewport` is the authority here rather than the image's natural pixel size, because it is
   *  the server's own reading of window.innerWidth/innerHeight — the exact space Playwright's
   *  mouse works in. It must be kept in step with whatever tab is being shown: when it went
   *  stale after a tab switch, every click was scaled through the wrong size and resolved tens
   *  of pixels away from where it was aimed. */
  function toViewport(e: { clientX: number; clientY: number }): { x: number; y: number } | null {
    const img = imgRef.current;
    if (!img) return null;
    const r = img.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * viewport.width;
    const y = ((e.clientY - r.top) / r.height) * viewport.height;
    return { x: Math.max(0, Math.round(x)), y: Math.max(0, Math.round(y)) };
  }

  /** Does the screenshot on screen actually match the size clicks are being scaled through?
   *  If it does not, every touch is off, so say so in the diagnostic line rather than leaving
   *  it to be guessed from a step that landed on the wrong element. */
  function viewportMismatch(): string {
    const img = imgRef.current;
    if (!img || !img.naturalWidth || !img.naturalHeight) return "";
    const shotRatio = img.naturalWidth / img.naturalHeight;
    const mapRatio = viewport.width / viewport.height;
    if (Math.abs(shotRatio - mapRatio) < 0.01) return "";
    return (
      `  ⚠ the screenshot is ${img.naturalWidth}×${img.naturalHeight} but clicks are being ` +
      `mapped through ${viewport.width}×${viewport.height} — touches will be off`
    );
  }

  async function handleImageClick(e: React.MouseEvent<HTMLImageElement>) {
    if (!sid || busy) return;
    const pt = toViewport(e);
    if (!pt) return;
    setBusy(true);
    try {
      // ⌖ Double click is ARMED: this touch is ONE double click on whatever is under it,
      // whatever kind of control that is — no value popup, no dropdown handling, because
      // arming the button already said what was meant. Handled before the inspect below,
      // since inspect CLICKS to focus: going through it would send a single click first, and
      // on a grid row that opens on a single click the double would land on the screen that
      // just opened. Disarms itself, so the touch after this one is normal again.
      if (dblArmed) {
        setDblArmed(false);
        const r = await erpApi.recorderDoubleClickAt(scriptId, sid, pt.x, pt.y);
        absorbDialogs(r.dialogs);
        if (r.screenshot) setShot(r.screenshot);
        const d = r.element;
        const caption = (d?.click_text || d?.text || d?.label || "").trim();
        setTouched(
          d
            ? `(${Math.round(pt.x)}, ${Math.round(pt.y)}) ×2 → <${d.tag}>` +
              `${caption ? ` “${caption.slice(0, 40)}”` : ""}  ${d.click_selector || d.selector}` +
              viewportMismatch()
            : `(${Math.round(pt.x)}, ${Math.round(pt.y)}) ×2 → nothing found here${viewportMismatch()}`,
        );
        const target = d?.click_selector || d?.selector || null;
        if (!target) {
          setStatus("Double clicked, but nothing was found at that point — so no step was recorded. Try again slightly inside the control.");
          setPending(null);
          return;
        }
        pushStep({
          action: "double_click",
          selector: target,
          frames: d?.frames ?? undefined,
          // The row's own text, so a replay finds THIS job by its reference rather than by the
          // row's position in the table — a search returning a different number of results
          // would otherwise open a different job with nothing to say so.
          description: caption || (d?.click_in_text || "").slice(0, 120) || undefined,
          ...spotOf(d),
        });
        lastClick.current = null;
        const where = d?.click_fx != null
          ? ` at ${Math.round((d.click_fx ?? 0) * 100)}% across, ${Math.round((d.click_fy ?? 0) * 100)}% down`
          : "";
        setStatus(`Recorded a DOUBLE click on ${caption ? `“${caption}”` : target}${where}.`);
        setPending(null);
        return;
      }
      // Touch the element first. If it's an input/dropdown, open the value popup
      // (with the AI's suggested field pre-selected). Otherwise record a click.
      const res = await erpApi.recorderInspect(scriptId, sid, pt.x, pt.y, fields);
      absorbDialogs(res.dialogs);
      if (res.screenshot) setShot(res.screenshot);
      const el = res.element;
      // Say what was found at that point, every time. When a touch lands on the wrong thing
      // — a wrapper div, an overlay — the only symptom otherwise is "it did nothing", which
      // is impossible to report or diagnose.
      setTouched(
        el
          ? `(${Math.round(pt.x)}, ${Math.round(pt.y)}) → <${el.tag}>` +
            `${(el.click_text || el.text || el.label || "").trim() ? ` “${(el.click_text || el.text || el.label || "").trim().slice(0, 40)}”` : ""}` +
            `  ${el.click_selector || el.selector}${viewportMismatch()}`
          : `(${Math.round(pt.x)}, ${Math.round(pt.y)}) → nothing found here${viewportMismatch()}`,
      );
      // An option inside a React/Vue dropdown was clicked. Its id (#react-select-2-option-1)
      // is generated fresh on every mount, so recording it gives a step that breaks tomorrow.
      // Record "choose this text in that control" instead — which is also what a person means.
      if (el && el.widget_role === "option" && (el.option_text || "").trim()) {
        const ctl = el.control_selector || el.selector;
        pushStep({
          action: "autocomplete",
          selector: ctl,
          frames: el.frames ?? undefined,
          value: (el.option_text || "").trim(),
          description: `choose "${(el.option_text || "").trim()}"`,
        });
        setStatus(`Recorded: choose "${(el.option_text || "").trim()}" from the dropdown.`);
        setPending(null);
        return;
      }
      // An IMAGE OR ICON IS ALWAYS A CLICK. Never open a value popup for one: you cannot type
      // into a picture, and an icon in a menu bar is a button in every ERP. If a value ever
      // does need reading off an image, that is asked for deliberately, not guessed here.
      // A second tap within 600ms, within 12px of the first, means a DOUBLE click. Upgrade
      // the step already recorded rather than adding a second click step - two singles are
      // not the same gesture, and a grid that opens on double click ignores them.
      const lc = lastClick.current;
      const isDouble =
        !!lc && Date.now() - lc.t < 600 &&
        Math.abs(lc.x - pt.x) < 12 && Math.abs(lc.y - pt.y) < 12;
      if (isDouble && lc) {
        const target = steps[lc.index]?.selector;
        setSteps((arr) => arr.map((s, j) => (j === lc.index ? { ...s, action: "double_click" } : s)));
        lastClick.current = null;
        if (target) {
          try {
            const r = await erpApi.recorderDoubleClick(scriptId, sid, target, el?.frames ?? undefined);
            absorbDialogs(r.dialogs);
            if (r.screenshot) setShot(r.screenshot);
            setStatus(`Step ${lc.index + 1} changed to a DOUBLE click.`);
          } catch {
            setStatus(`Step ${lc.index + 1} changed to a DOUBLE click.`);
          }
        }
        return;
      }
      const ICONS = ["img", "svg", "path", "use", "i", "picture", "canvas", "image"];
      if (el && ICONS.includes((el.tag || "").toLowerCase())) {
        const target = el.click_selector || el.selector;
        const caption = (el.click_text || el.label || el.text || "").trim();
        pushStep({
          action: "click",
          selector: target,
          frames: el.frames ?? undefined,
          description: caption || undefined,
          ...spotOf(el),
        });
        lastClick.current = { t: Date.now(), x: pt.x, y: pt.y, index: steps.length };
        setStatus(`Recorded a click on ${caption ? `"${caption}"` : "the icon"}. Tap again to make it a double click.`);
        setPending(null);
        return;
      }
      // A React/Vue dropdown (react-select, MUI, Chakra) is a stack of divs, so is_input and
      // is_select are both false for it — and it fell through to "Pick data", as if you wanted
      // to READ the dropdown rather than choose a value in it. A widget control is a value
      // field like any other: treat it as one.
      // A file box is neither a value field nor something to read: the only step it can carry
      // is "attach this job's workbook". Open the popup on that.
      if (el && excelBook && (el.input_type || "").toLowerCase() === "file") {
        setPending({ x: pt.x, y: pt.y, element: el, suggestion: res.suggestion });
        setPopMode("upload");
        return;
      }
      if (el && (el.is_input || el.is_select || el.widget_role === "control")) {
        const suggested = res.suggestion?.field ?? "";
        setPending({ x: pt.x, y: pt.y, element: el, suggestion: res.suggestion });
        // Dropdowns default to picking an option (so it applies + you see it change) —
        // a widget control counts, since typing into it is how its option list is filtered.
        // Text inputs default to field-mapping when a template is linked.
        setPopMode(
          el.is_select || el.widget_role === "control"
            ? "literal"
            : fields.length > 0 ? "field" : "literal",
        );
        // Suggest a label from the element's own caption, so Pick data never starts blank.
        setPickLabel(
          (el.text || el.tag || "")
            .toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 30),
        );
        setPopField(suggested && fields.includes(suggested) ? suggested : "");
        setPopLiteral("");
        setPopPrompt("");
        setManualLabel("");
        setManualExample("");
        setSuggestions([]);
        setIsTypeahead(false);
        setPopOption(res.suggestion?.option ?? (el.options?.[0] ?? ""));
      } else if (el && isClickable(el) && !el.is_input && !el.is_select) {
        // Clickable, but not a value field: record the click on the element that actually
        // owns the behaviour — the <a> or <button> around the icon, never the <img>, whose
        // nth-of-type path breaks the moment the menu changes.
        const target = el.click_selector || el.selector;
        const caption = (el.click_text || el.text || "").trim();
        const tick = toggleStep(el);
        pushStep({
          action: "click",
          selector: target,
          frames: el.frames ?? undefined,
          description: caption || undefined,
          ...spotOf(el),
          ...(tick ?? {}),
        });
        lastClick.current = { t: Date.now(), x: pt.x, y: pt.y, index: steps.length };
        setStatus(`Recorded a click on ${caption ? `"${caption}"` : target}. Tap again to make it a double click.`);
        setPending(null);
        return;
      } else if (el && !isClickable(el)) {
        // Not clickable, so Pick data is the likely intent — but only as the default.
        // Both buttons stay available and the Super Admin can switch to Enter data.
        // Plain text — a heading, a label, a table cell, a status message. You cannot type
        // into it and clicking it does nothing useful, so the only sensible intent is to PICK
        // it: read it out of the ERP. Previously this recorded a meaningless click step and
        // showed no popup at all, which looked like the click had simply been ignored.
        const caption = (el.text || "").trim();
        setPending({ x: pt.x, y: pt.y, element: el, suggestion: res.suggestion });
        setPopMode("pick");
        setPickLabel(
          caption.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 30),
        );
        // A short caption is a value (a reference, an amount); a long one is a message.
        setPickKind(caption.length > 60 ? "text" : "value");
        setPickUsage("both");
        setPickDesc("");
        setPopLiteral("");
      } else if (el && (el.click_selector || el.selector)) {
        // Button / submit / checkbox / radio / link → just record the click (no value popup).
        const target = el.click_selector || el.selector;
        pushStep({
          action: el.is_button ? "submit" : "click",
          selector: target,
          frames: el.frames ?? undefined,
          description: el.click_text || el.text || el.label || el.tag || undefined,
        });
        lastClick.current = { t: Date.now(), x: pt.x, y: pt.y, index: steps.length };
      } else {
        // NOTHING was identified under the touch. Recording a step with an empty selector
        // would look like a click in the list and match nothing at run time - a silent hole
        // in the script. Say so instead, and record nothing.
        setError(
          "Could not identify anything at that spot, so no step was recorded. " +
          "Try again slightly inside the control — or use Capture events and press Record on it.",
        );
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Action failed. The session may have expired — restart the recorder.");
    } finally {
      setBusy(false);
    }
  }

  // Type-ahead: as the user types a fixed value, type it into the live field and show
  // the dropdown options the ERP returns (debounced).
  function onLiteralChange(text: string) {
    setPopLiteral(text);
    if (!sid || !pending) return;
    if (suggestTimer.current) window.clearTimeout(suggestTimer.current);
    if (!text.trim()) { setSuggestions([]); return; }
    suggestTimer.current = window.setTimeout(async () => {
      setSuggestLoading(true);
      try {
        const res = await erpApi.recorderAutocomplete(scriptId, sid, pending.x, pending.y, text);
        if (res.screenshot) setShot(res.screenshot);
        setSuggestions(res.suggestions ?? []);
      } catch {
        setSuggestions([]);
      } finally {
        setSuggestLoading(false);
      }
    }, 450);
  }

  // Commit a chosen type-ahead suggestion in the LIVE browser, then record the step.
  async function pickSuggestion(text: string) {
    if (!pending || !sid) return;
    setBusy(true);
    setError(null);
    // For a React/Vue dropdown, target the CONTROL by its stable class, not the div you
    // happened to touch. The touched div only has a positional path
    // (`div > div > div > div:nth-of-type(1) > div:nth-of-type(2)`), which breaks as soon as
    // the page renders one wrapper more or fewer — the control carries a hand-authored class
    // such as `react-select__control`, scoped to the nearest id, and that survives rebuilds.
    const target =
      (pending.element.widget_role === "control" && pending.element.control_selector) ||
      pending.element.selector;
    try {
      const res = await erpApi.recorderAutocompletePick(scriptId, sid, text, target, pending.element.frames);
      absorbDialogs((res as { dialogs?: erpApi.LiveDialog[] }).dialogs);
      if (res.screenshot) setShot(res.screenshot);
      if (!res.applied) {
        setError(
          `Could not choose "${text}" in the live browser. The step is recorded, but check it: ` +
          `this control may need a plain click on the option instead.`,
        );
      }
      pushStep({
        // Record the action that ACTUALLY committed the value. A styled dropdown backed by a
        // real <select> needs a select step; recording it as autocomplete would make playback
        // type into an element that cannot be typed into, and fail the same way.
        action: res.action === "select" ? "select" : "autocomplete",
        selector: target,
        frames: pending.element.frames ?? undefined,
        value: text,
        description: pending.element.label || pending.element.text,
      });
      setPending(null);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not select that option.");
    } finally {
      setBusy(false);
    }
  }

  // Insert a saved field name into the AI-rule prompt at the cursor (for building conditions).
  function insertFieldToken(name: string) {
    const el = promptRef.current;
    if (!el) {
      setPopPrompt((p) => (p ? `${p} ${name}` : name));
      return;
    }
    const start = el.selectionStart ?? popPrompt.length;
    const end = el.selectionEnd ?? popPrompt.length;
    const next = popPrompt.slice(0, start) + name + popPrompt.slice(end);
    setPopPrompt(next);
    // restore caret just after the inserted token
    requestAnimationFrame(() => {
      el.focus();
      const pos = start + name.length;
      el.setSelectionRange(pos, pos);
    });
  }

  /** "Manual Entry": make sure a data field of this name exists (creating it as an ordinary
   *  ask-the-operator field if it doesn't), so the rest of the flow can map to it exactly like
   *  any other field. Returns false (and sets an error) if it could not be created. */
  async function ensureManualField(label: string, exampleValue: string): Promise<boolean> {
    if (fields.includes(label)) return true;
    try {
      await Promise.all(
        (script?.template_ids ?? []).map((gid) =>
          onboardingApi.createCustomField(gid, {
            label_name: label,
            kind: "hardcoded",
            hardcoded_value: null,
            ask_operator: true,
            ask_operator_required: true,
            per_row: false,
            ask_operator_hint: "Typed in for this ERP entry — not read off any document.",
            example_value: exampleValue.trim() || null,
          }),
        ),
      );
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not create that field.");
      return false;
    }
    setFields((f) => [...f, label]);
    return true;
  }

  async function confirmPopup() {
    if (!sid || !pending) return;
    const { x, y, element } = pending;
    // The action has to match the KIND of control, or the value never lands. A native <select>
    // takes `select`; a widget dropdown built from divs (react-select, select2, MUI) is not a
    // <select> at all and a plain `fill` only types into it - `autocomplete` runs the chooser
    // that opens the list and picks the matching option, which is what made the Live Impex
    // location field work. Everything else is a text field.
    const act = element.is_select
      ? "select"
      : element.widget_role === "control"
        ? "autocomplete"
        : "fill";
    const desc = element.label || element.text;
    const frames = element.frames ?? undefined;
    // A React/Vue dropdown control has no id of its own, so element.selector is a
    // positional path that breaks on the next render. The control carries a stable
    // hand-authored class (react-select__control, select2-selection, …) scoped to the
    // nearest id — target that instead.
    const sel =
      (element.widget_role === "control" && element.control_selector) || element.selector;
    setBusy(true);
    setError(null);
    try {
      if (popMode === "pick") {
        // Reading OUT of the ERP: no value is typed, so nothing is applied to the live page.
        const label = (pickLabel || "captured").trim().replace(/\s+/g, "_");
        pushStep({
          action: pickKind === "document" ? "download" : "get_text",
          selector: sel,
          frames,
          capture_as: label,
          capture_kind: pickKind,
          capture_description: pickDesc.trim() || null,
          capture_usage: pickUsage,
          value: pickKind === "document" ? null : (popLiteral.trim() || null),
          description: desc,
        });
        setPending(null);
        setPickLabel(""); setPickDesc(""); setPickKind("value"); setPickUsage("both"); setPopLiteral("");
        setBusy(false);
        return;
      }
      if (popMode === "ai") {
        // AI rule: no live value to type — the value is decided at run time from job data.
        if (!popPrompt.trim()) { setError("Write the rule/condition for the AI to follow."); setBusy(false); return; }
        const aiStep: ErpStep = { action: act, selector: sel, frames, prompt: popPrompt.trim(), options: element.options ?? undefined, description: desc };
        pushStep(aiStep);
        // APPLY it, do not just save it. Recording a rule and entering nothing left the ERP box
        // empty, so the form never validated and the steps after it could not be recorded.
        // The rule is evaluated on the server against the template's sample data.
        const air = await erpApi.recorderApplyStep(scriptId, sid, aiStep);
        absorbDialogs(air.dialogs);
        if (air.screenshot) setShot(air.screenshot);
        setStatus(
          air.ok
            ? `AI rule saved and applied — it entered "${air.value ?? ""}".`
            : `AI rule saved, but it could not be applied here: ${air.error ?? "unknown"}`,
        );
      } else if (element.is_select) {
        // dropdown: select the chosen option (literal) or map a field (applied at run time)
        if (popMode === "literal") {
          if (!popOption) { setError("Choose a dropdown option."); setBusy(false); return; }
          const res = await erpApi.recorderSelect(scriptId, sid, x, y, popOption, sel, element.frames);
          absorbDialogs(res.dialogs);
          if (res.screenshot) setShot(res.screenshot);
          if (res.element && res.element.applied === false) {
            setError(`Couldn't apply "${popOption}" to the dropdown (option not found). Step recorded anyway.`);
          }
          pushStep({ action: "select", selector: sel, frames, value: popOption, options: element.options, description: desc });
        } else {
          // Map a data field, or a freshly-named Manual Entry field → the matching option
          // is chosen at run time from whichever the operator typed in on Additional Details.
          const fieldForDropdown = popMode === "manual" ? manualLabel.trim() : popField;
          if (popMode === "manual") {
            if (!fieldForDropdown) { setError("Name this field — it's what the operator will see."); setBusy(false); return; }
            if (!(await ensureManualField(fieldForDropdown, manualExample))) { setBusy(false); return; }
          } else if (!fieldForDropdown) {
            setError("Pick a data field, or choose a fixed option."); setBusy(false); return;
          }
          const selStep: ErpStep = { action: "select", selector: sel, frames, field_label: fieldForDropdown, options: element.options, description: desc };
          pushStep(selStep);
          // Same reason as the AI rule above: a dropdown left unset stops the ERP validating.
          const sr = await erpApi.recorderApplyStep(scriptId, sid, selStep);
          absorbDialogs(sr.dialogs);
          if (sr.screenshot) setShot(sr.screenshot);
          setStatus(
            sr.ok
              ? `Mapped ${fieldForDropdown} and selected "${sr.value ?? ""}".`
              : `Mapped ${fieldForDropdown}, but the option could not be selected: ${sr.error ?? "unknown"}`,
          );
          if (popMode === "manual") { setManualLabel(""); setManualExample(""); }
        }
      } else if (popMode === "literal") {
        const res = await erpApi.recorderType(scriptId, sid, x, y, popLiteral);
        absorbDialogs(res.dialogs);
        if (res.screenshot) setShot(res.screenshot);
        pushStep({ action: "fill", selector: sel, frames, value: popLiteral, description: desc });
      } else if (popMode === "manual") {
        const label = manualLabel.trim();
        if (!label) { setError("Name this field — it's what the operator will see."); setBusy(false); return; }
        if (!(await ensureManualField(label, manualExample))) { setBusy(false); return; }
        const res = await erpApi.recorderType(scriptId, sid, x, y, label, label);
        absorbDialogs(res.dialogs);
        if (res.screenshot) setShot(res.screenshot);
        pushStep({ action: isTypeahead ? "autocomplete" : "fill", selector: sel, frames, field_label: label, description: desc });
        setStatus(`"${label}" is now on Additional Details — the operator types it in there before Submit Entry.`);
        setManualLabel("");
        setManualExample("");
      } else {
        if (!popField) { setError("Pick a data field, or use a fixed value / AI rule."); setBusy(false); return; }
        const res = await erpApi.recorderType(scriptId, sid, x, y, popField, popField);
        absorbDialogs(res.dialogs);
        if (res.screenshot) setShot(res.screenshot);
        // If this field is a searchable dropdown, playback must type + pick the option.
        pushStep({ action: isTypeahead ? "autocomplete" : "fill", selector: sel, frames, field_label: popField, description: desc });
      }
      setPending(null);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not set that value.");
    } finally {
      setBusy(false);
    }
  }

  /** Confirm for Search & Click / Search & Double-click: no element to touch first — this
   *  searches the WHOLE live screen for whatever holds the chosen field's value and clicks
   *  (or double-clicks) it directly, right now, and records a step that does the same on
   *  every real run. There is no recorded position at all to fall back on; a search
   *  result's row is never at a fixed spot twice, so content is the only thing that can
   *  ever find it. */
  async function confirmSearchClick() {
    if (!sid || !searchClickAction) return;
    let fieldLabel: string | undefined;
    let literalValue: string | undefined;
    if (searchPopMode === "manual") {
      const label = searchManualLabel.trim();
      if (!label) { setError("Name this field — it's what the operator will see."); return; }
      if (!(await ensureManualField(label, searchManualExample))) return;
      fieldLabel = label;
    } else if (searchPopMode === "literal") {
      if (!searchLiteral.trim()) { setError("Enter the fixed value to search for."); return; }
      literalValue = searchLiteral.trim();
    } else {
      if (!searchField) { setError("Pick a data field, or switch to Fixed value / Manual Entry."); return; }
      fieldLabel = searchField;
    }
    setBusy(true);
    setError(null);
    try {
      const step: ErpStep = {
        action: searchClickAction,
        field_label: fieldLabel,
        value: literalValue,
        description: fieldLabel ? `search result: ${fieldLabel}` : `search result: ${literalValue}`,
      };
      const res = await erpApi.recorderApplyStep(scriptId, sid, step);
      absorbDialogs(res.dialogs);
      if (res.screenshot) setShot(res.screenshot);
      pushStep(step);
      const named = fieldLabel ?? literalValue ?? "";
      setStatus(
        res.ok
          ? `Recorded a ${searchClickAction === "double_click" ? "double " : ""}click matched to ${named}${res.value ? ` — clicked "${res.value}"` : ""}.`
          : `Recorded, but nothing matching ${named} could be found on screen: ${res.error ?? "unknown"}. The step is saved — make sure the ERP is showing results before trying again.`,
      );
    } finally {
      setBusy(false);
      setSearchClickAction(null);
      setSearchField(""); setSearchLiteral(""); setSearchManualLabel(""); setSearchManualExample("");
    }
  }

  async function handleDrop(e: React.DragEvent<HTMLImageElement>) {
    e.preventDefault();
    if (!sid || busy) return;
    const book = e.dataTransfer.getData("text/excel-book");
    if (book) {
      // The workbook goes into a file input, which cannot be typed into. Find what is under
      // the pointer, then perform a real upload so the ERP accepts the import and the steps
      // after it can be recorded.
      const bpt = toViewport(e);
      if (!bpt) return;
      setBusy(true);
      try {
        const el = (await erpApi.recorderInspect(scriptId, sid, bpt.x, bpt.y, [])).element;
        const step: ErpStep = {
          action: "upload",
          selector: el?.selector ?? "",
          frames: el?.frames ?? undefined,
          value: "excel_import",
          description: el?.text || el?.tag || "import file",
        };
        const res = await erpApi.recorderApplyStep(scriptId, sid, step);
        absorbDialogs(res.dialogs);
        if (res.screenshot) setShot(res.screenshot);
        if (res.ok) {
          pushStep(step);
          setStatus(`Attached ${book}. At run time each job attaches its own workbook here.`);
        } else {
          setError(res.error || "The ERP would not take the workbook there. Is that a file input?");
        }
      } catch (err: any) {
        setError(err?.response?.data?.detail ?? "Could not attach the workbook.");
      } finally {
        setBusy(false);
      }
      return;
    }
    const label = e.dataTransfer.getData("text/field");
    if (!label) return;
    const pt = toViewport(e);
    if (!pt) return;
    setBusy(true);
    try {
      const res = await erpApi.recorderType(scriptId, sid, pt.x, pt.y, label, label);
      absorbDialogs(res.dialogs);
      if (res.screenshot) setShot(res.screenshot);
      const el = res.element;
      const step: ErpStep = el?.is_select
        ? { action: "select", selector: el.selector, frames: el.frames ?? undefined, field_label: label, options: el.options, description: el.text }
        : { action: "fill", selector: el?.selector ?? "", frames: el?.frames ?? undefined, field_label: label, description: el?.text || el?.tag };
      // A line-item field has one value per row, so recording "put the value here" is not
      // enough — ask whether they all go in this box or one at a time round a loop. Nested
      // inside a loop already, don't ask again: a loop within a loop has no meaning here.
      if (multiFields.has(label) && !rowCapture) {
        setMultiDrop({ label, step });
      } else {
        pushStep(step);
      }
      // Say what went into the box. A mapped field types a REAL sample so the ERP validates it
      // and any dependent lookup fires - otherwise the next steps cannot be recorded at all.
      // When there is no sample the field name goes in as before, and that is worth saying out
      // loud rather than leaving it to be discovered three steps later.
      const typed = (res.typed ?? "").trim();
      if (typed && typed !== label) {
        setStatus(`Mapped ${label} and typed the sample "${typed}" (${res.sample_source ?? ""}).`);
      } else {
        setStatus(
          `Mapped ${label}, but there is no sample value to type — ${res.sample_source ?? "none found"}. ` +
            `The ERP may reject the field name and later steps may not record.`,
        );
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not map that field.");
    } finally {
      setBusy(false);
    }
  }

  // ⚡ Capture events — list every clickable element on the live page.
  async function captureEvents() {
    if (!sid) return;
    setEventsLoading(true);
    setAiPick(null);
    setError(null);
    try {
      setEvents(await erpApi.recorderEvents(scriptId, sid));
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "Could not capture events.");
    } finally {
      setEventsLoading(false);
    }
  }

  // Record one captured event as a click step (and click it live).
  async function recordEvent(ev: erpApi.PageEvent) {
    if (!sid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await erpApi.recorderClickSelector(scriptId, sid, ev.selector, ev.frames);
      absorbDialogs(res.dialogs);
      if (res.screenshot) setShot(res.screenshot);
      if (!res.ok) setError(res.error ?? "Could not click that element.");
      pushStep({
        action: ev.tag === "button" || ev.input_type === "submit" ? "submit" : "click",
        selector: ev.selector,
        frames: ev.frames ?? undefined,
        description: ev.text || ev.tag,
      });
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "Could not record that event.");
    } finally {
      setBusy(false);
    }
  }

  // 🤖 Ask AI which event to click for a goal (preview before recording an AI step).
  async function previewAi() {
    if (!sid || !aiGoal.trim()) return;
    setEventsLoading(true);
    setError(null);
    try {
      const res = await erpApi.recorderAiAction(scriptId, sid, aiGoal.trim());
      setEvents(res.events);
      setAiPick(res.suggestion);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "AI could not analyse the page.");
    } finally {
      setEventsLoading(false);
    }
  }

  // Record an AI step: at run time AI decides what to click toward this goal.
  function addAiStep() {
    if (!aiGoal.trim()) { setError("Describe what the AI should do (the goal)."); return; }
    pushStep({ action: "ai_action", goal: aiGoal.trim(), description: `AI: ${aiGoal.trim()}` });
    setAiGoal("");
    setAiPick(null);
  }


  /** Change one field of one step, leaving the rest untouched. */
  /** Press "⚑ Capture checkpoint" while recording: the last step you just performed becomes
   *  the checkpoint. That is the moment the login and navigation are finished and the entry
   *  screen is up — everything recorded above it is setup, everything below is the entry.
   *  Pressing again clears it. */
  function captureCheckpointHere() {
    if (steps.length === 0) return;
    const existing = steps.findIndex((s) => s.is_checkpoint);
    const target = existing >= 0 ? existing : steps.length - 1;
    setCheckpoint(target);
    setStatus(
      existing >= 0
        ? "Checkpoint cleared — this script will run every step from the start again."
        : `Checkpoint set at step ${target + 1}. Steps 1–${target + 1} are the login/setup.`,
    );
  }

  /** Mark one step as the checkpoint, clearing any previous one. Passing the already
   *  flagged step clears it, so the same button sets and unsets. */
  function setCheckpoint(index: number) {
    setSteps((arr) => {
      const on = !arr[index]?.is_checkpoint;
      return arr.map((s, j) => {
        const next = { ...s };
        delete next.is_checkpoint;
        return on && j === index ? { ...next, is_checkpoint: true } : next;
      });
    });
  }

  function patchStep(index: number, patch: Partial<ErpStep>) {
    setSteps((arr) => arr.map((st, j) => (j === index ? { ...st, ...patch } : st)));
  }

  /** Move a step to a new position. Order is the script, so this is a real edit. */
  function moveStep(arr: ErpStep[], from: number, to: number): ErpStep[] {
    if (to < 0 || to >= arr.length) return arr;
    const next = [...arr];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    return next;
  }

  /** Recorded credentials must not sit in the step list in plain sight. A step that filled a
   *  password field shows dots instead — the real value is still saved and still replayed. */
  function displayValue(s: ErpStep): string | null {
    if (!s.value) return null;
    const sel = (s.selector ?? "").toLowerCase();
    const desc = (s.description ?? "").toLowerCase();
    const looksSecret =
      sel.includes("password") || sel.includes("passwd") || sel.includes("pwd") ||
      desc.includes("password") || sel.includes('type="password"');
    return looksSecret ? "••••••••" : s.value;
  }


  /** Record the element/text that proves the ERP accepted the entry. At run time the engine
   *  checks it: present -> the job is completed; absent -> the final screen is sent to AI for a
   *  plain-words explanation, reported on the operator's Failed screen, and the session ends. */
  function recordSuccessEvent(ev: erpApi.PageEvent) {
    const text = (ev.text || "").trim();
    pushStep({
      action: "assert_text",
      selector: text ? "" : ev.selector,   // by text when we have it — markup varies per run
      frames: ev.frames ?? undefined,
      value: text || null,
      is_success_check: true,
      description: `SUCCESS SIGNAL: ${text || ev.selector}`,
    });
  }

  /** Keep a picture of THIS screen for the operator.
   *
   *  The run already takes a screenshot when it finishes, but the screen it finishes on is
   *  often a list the ERP bounced back to rather than the confirmation itself - so the proof an
   *  operator wants has already scrolled away. This marks the screen worth keeping, wherever it
   *  appears in the script. The whole page is captured, however long, at twice the pixel
   *  density, so small print and figures stay readable. */
  function recordSuccessScreenshot() {
    pushStep({
      action: "screenshot",
      selector: "",
      description: "SUCCESS SCREENSHOT: the screen the operator sees",
    });
  }

  function addManualStep() {
    // Strip the fields the chosen action doesn't use, so a step never carries a stale
    // capture_as or timeout left over from a different action the user was looking at.
    const step: ErpStep = { ...draft };
    if (step.action !== "get_text") delete step.capture_as;
    if (step.action !== "wait_for" && step.action !== "wait_gone") delete step.timeout;
    if (step.action !== "dialog") delete step.prompt_text;
    if (step.action === "get_text" && !step.capture_as) step.capture_as = "captured";
    setSteps((s) => [...s, step]);
    setDraft({ action: "click", selector: "", value: "", description: "" });
  }

  async function save(markReady: boolean) {
    setSaving(true);
    setError(null);
    setSaveMsg(null);
    try {
      // Derive the index from whichever step carries the flag. The flag moves with the step
      // when it is reordered, so the stored index is always right at save time.
      const ci = steps.findIndex((s) => s.is_checkpoint);
      const updated = await erpApi.updateErpScript(scriptId, {
        steps,
        checkpoint_index: ci >= 0 ? ci : null,
        stay_open: ci >= 0 ? stayOpen : false,
        ...(markReady ? { status: "ready" } : {}),
      });
      setScript(updated);
      setSteps(updated.steps ?? []);
      const n = updated.steps?.length ?? 0;
      const msg = markReady
        ? `✓ Saved ${n} step${n === 1 ? "" : "s"} and marked the ERP script READY. Next: the Tenant Admin approves the template, then assigns it to an operator.`
        : `✓ Draft saved (${n} step${n === 1 ? "" : "s"}).`;
      setSaveMsg({ ok: true, text: msg });
      setStatus(msg);
    } catch (e: any) {
      const text = e?.response?.data?.detail ?? "Could not save.";
      setSaveMsg({ ok: false, text });
      setError(text);
    } finally {
      setSaving(false);
    }
  }

  async function testPlayback() {
    setPlayLog(null);
    setStatus("Replaying the steps headlessly…");
    // sample values for any [[field]] placeholders so playback can fill inputs
    const values: Record<string, string> = {};
    for (const f of fields) values[f] = `sample_${f}`;
    try {
      const res = await erpApi.playScript(scriptId, values);
      setPlayLog([`status: ${res.status}`, ...(res.final_url ? [`final url: ${res.final_url}`] : []), ...(res.error ? [`error: ${res.error}`] : []), ...res.log]);
      setStatus("Playback finished.");
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "Playback failed.");
    }
  }

  /** Labels already picked out of the ERP by earlier steps. A value read from the ERP is often
   *  needed again in a later screen of the same ERP, so these are offered for mapping exactly
   *  like the template's own data fields. */
  const pickedLabels = steps
    .filter((s) => s.capture_as && s.capture_usage !== "output")
    .map((s) => s.capture_as as string);
  /** What the value popup and the drag list offer: template fields + anything picked so far. */
  const mappableFields = [...fields, ...pickedLabels.filter((l) => !fields.includes(l))];

  const startRecorderCb = useCallback(startRecorder, [scriptId]);

  async function refreshTabs() {
    if (!sidRef.current) return;
    try {
      setTabs(await erpApi.recorderTabs(scriptId, sidRef.current));
    } catch {
      /* non-fatal: the tab bar is a convenience, never block recording on it */
    }
  }

  /** Record a scroll, merging it into the one before it.
   *
   *  Pressing ▼ three times is one move of 1800px, not three steps — a long form would
   *  otherwise bury the real actions under a pile of scrolls. Scrolling back up to where you
   *  started cancels out and removes the step entirely, the same way an empty step is never
   *  recorded. Inside a line-item loop each scroll is kept separate: a row block replays as
   *  recorded and silently merging steps there would change what each row does. */
  function recordScroll(dy: number) {
    if (rowCapture) {
      pushStep({ action: "scroll", value: String(dy), description: scrollLabel(dy) });
      return;
    }
    setSteps((arr) => {
      const last = arr[arr.length - 1];
      if (!last || last.action !== "scroll") {
        return [...arr, { action: "scroll", value: String(dy), description: scrollLabel(dy) }];
      }
      const total = Number(last.value ?? 0) + dy;
      if (total === 0) return arr.slice(0, -1); // back where it started — nothing to replay
      return arr.map((s, i) =>
        i === arr.length - 1 ? { ...s, value: String(total), description: scrollLabel(total) } : s,
      );
    });
  }

  /** Scroll the live page and record it, so playback ends up looking at the same part of the
   *  screen. What gets recorded is how far the page ACTUALLY moved, not what was asked for —
   *  near the end of a form a 600px request may only travel 120px, and replaying the request
   *  rather than the result would drift. */
  async function scrollPage(dy: number) {
    if (!sid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await erpApi.recorderScroll(scriptId, sid, dy);
      absorbDialogs(res.dialogs);
      if (res.screenshot) setShot(res.screenshot);
      if (!res.moved) {
        // Say WHY nothing happened. "Nothing to scroll" and "already at the end" look
        // identical on screen but mean very different things.
        setStatus(
          res.found === 0
            ? "Nothing on this page can scroll (no scrollbar found, iframes included)."
            : `${dy > 0 ? "Already at the bottom" : "Already at the top"} — ` +
              `${res.found} scrollable area(s), ${res.movable} able to move.`,
        );
        return;
      }
      recordScroll(res.moved);
      setStatus(
        `Scrolled ${res.moved > 0 ? "down" : "up"} ${Math.abs(res.moved)}px` +
          ` (${res.mode}${res.via === "wheel" ? ", mouse wheel" : ""})` +
          (res.at_bottom ? " — bottom" : res.at_top ? " — top" : ""),
      );
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not scroll the page.");
    } finally {
      setBusy(false);
    }
  }

  /** Switch the recorder to another tab AND record the switch, so playback follows the same
   *  path. The recorded URL is what playback matches on — tab order can differ between runs,
   *  the destination does not. */
  async function switchToTab(index: number) {
    if (!sid || busy) return;
    setBusy(true);
    try {
      const res = await erpApi.recorderSwitchTab(scriptId, sid, index);
      absorbDialogs(res.dialogs);
      if (res.screenshot) setShot(res.screenshot);
      // THE SIZE HAS TO COME WITH THE SCREENSHOT. A tab the ERP opened is very often a
      // smaller popup window, and toViewport() scales a click through `viewport` — so keeping
      // the FIRST tab's size here skewed every click on the new tab: press a form tab and the
      // point resolved tens of pixels lower, landing on whatever was below it. The server has
      // always sent this; it was simply never read.
      if (res.viewport?.width && res.viewport?.height) setViewport(res.viewport);
      setTabs(res.tabs);
      pushStep({
        action: "switch_tab",
        value: String(index),
        description: res.tab.url,
        field_label: null,
      });
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not switch to that tab.");
    } finally {
      setBusy(false);
    }
  }

  /** All rows into this one input, joined. */
  function chooseAllAtOnce() {
    if (!multiDrop) return;
    pushStep({ ...multiDrop.step, multi_mode: "all_at_once", join_with: joinWith });
    setMultiDrop(null);
  }

  /** One value at a time: record the actions for ONE row, then mark the end. */
  function choosePerRow() {
    if (!multiDrop) return;
    const step: ErpStep = { ...multiDrop.step, multi_mode: "per_row", row_steps: [], end_steps: [] };
    // We append exactly one step, so its index is the current length. Computed out here on
    // purpose — setting state from inside an updater runs twice under StrictMode and would
    // capture the wrong index.
    const index = steps.length;
    setSteps((arr) => [...arr, step]);
    setRowCapture({ index, phase: "row" });
    setMultiDrop(null);
  }

  const capturedStep = rowCapture ? steps[rowCapture.index] : null;

  if (!script) {
    return <AppShell title="ERP Script">{error ? <Alert>{error}</Alert> : <p className="text-sm text-slate-400">Loading…</p>}</AppShell>;
  }

  return (
    <AppShell title={`ERP Script · ${script.name}`} subtitle={script.url}>
      <Link to="/super-admin/erp-scripts" className="mb-4 inline-block text-sm text-indigo-600 hover:underline">← All ERP scripts</Link>
      {error && <div className="mb-6"><Alert>{error}</Alert></div>}
      {status && (
        <div className="mb-4 rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-2 text-sm text-indigo-800 dark:border-indigo-500/20 dark:bg-indigo-500/10 dark:text-indigo-300">
          {status}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ---------- Live browser ---------- */}
        <Card className="p-5 lg:col-span-2">
          {/* An alert the ERP raised. The browser answers it automatically so the page keeps
              moving, but the Super Admin has to KNOW the flow contains a prompt - otherwise
              playback meets it with no dialog step and the entry stalls. */}
          {liveDialogs.length > 0 && (
            <div className="mb-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm dark:border-amber-500/30 dark:bg-amber-500/10">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-semibold text-amber-900 dark:text-amber-200">
                    The ERP showed {liveDialogs.length === 1 ? "an alert" : `${liveDialogs.length} alerts`} — answered OK, and
                    {liveDialogs.length === 1 ? " a dialog step was" : " dialog steps were"} recorded automatically
                  </p>
                  {liveDialogs.map((d, i) => (
                    <p key={i} className="mt-1 text-xs text-amber-800 dark:text-amber-300">
                      <span className="font-mono">{d.type}</span>: “{d.message}”
                    </p>
                  ))}
                  <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                    Playback will answer it the same way. Left unrecorded, the browser answers
                    <b> Cancel</b> on its own and the entry quietly fails — which is why this is no
                    longer something you have to remember to press.
                  </p>
                </div>
                <div className="flex shrink-0 flex-col gap-1">
                  <Button size="sm" variant="secondary" onClick={() => {
                    // Already recorded as accept; flip the newest dialog step to dismiss.
                    setSteps((arr) => {
                      const i = [...arr].reverse().findIndex((x) => x.action === "dialog");
                      if (i < 0) return arr;
                      const at = arr.length - 1 - i;
                      return arr.map((x, j) => (j === at ? { ...x, value: "dismiss" } : x));
                    });
                    setStatus("Changed that dialog step to dismiss (answers Cancel on playback).");
                    setLiveDialogs([]);
                  }}>
                    Change to Cancel
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => setLiveDialogs([])}>Got it</Button>
                </div>
              </div>
            </div>
          )}
          <div className="mb-3 flex items-center gap-3">
            <h2 className="shrink-0 font-semibold text-slate-900 dark:text-slate-50">Live browser</h2>
            {/* This row grows every time a feature lands on it, and it had reached the point
                where ▲ Up and ▼ Down were cut off past the edge of the card with no way to
                reach them. Scroll it sideways rather than clipping or wrapping: wrapping made
                the live view jump down the page as buttons appeared and disappeared. */}
            {/* [&>*]:shrink-0 is the part that matters: without it flexbox compresses the
                controls to fit and they truncate in place ("Auto | Man…"), which is not the
                same as being scrollable. At full width they overflow, and then they scroll. */}
            <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto pb-1 [scrollbar-width:thin] [&>*]:shrink-0">
              {!sid ? (
                <Button type="button" onClick={startRecorderCb} isLoading={starting}>▶ Start recorder</Button>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() => setLive((v) => !v)}
                    className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${live ? "border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300" : "border-slate-200 text-slate-500 dark:border-slate-700 dark:text-slate-400"}`}
                    title={live ? "Live view on — the page refreshes automatically" : "Live view paused"}
                  >
                    <span className={`h-2 w-2 rounded-full ${live ? "animate-pulse bg-emerald-500" : "bg-slate-400"}`} />
                    {live ? "Live" : "Paused"}
                  </button>
                  <Button type="button" variant="secondary" onClick={captureEvents} isLoading={eventsLoading} disabled={busy}>⚡ Capture events</Button>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={recordSuccessScreenshot}
                    disabled={busy}
                    title="Keep a picture of this screen for the operator — the whole page, not just what fits"
                  >
                    📸 Success screenshot
                  </Button>
                  {/* Grids and read-only cells in most ERPs only open on a DOUBLE click — a
                      single one just highlights the row. Arm this, touch the row, and the step
                      is recorded as a double click. */}
                  <button
                    type="button"
                    onClick={() => {
                      const next = !dblArmed;
                      setDblArmed(next);
                      setStatus(
                        next
                          ? "Double click armed — the next thing you touch in the browser gets a double click."
                          : "Double click off. Touches are single clicks again.",
                      );
                    }}
                    disabled={busy}
                    title="Arm a double click: the next touch in the live view is recorded and performed as a double click, for a grid row or a cell that only opens on one"
                    className={`shrink-0 whitespace-nowrap rounded-lg border px-2.5 py-1.5 text-xs font-medium disabled:opacity-40 ${
                      dblArmed
                        ? "border-amber-400 bg-amber-100 text-amber-900 dark:border-amber-500/50 dark:bg-amber-500/20 dark:text-amber-200"
                        : "border-slate-200 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                    }`}
                  >
                    ⌖ {dblArmed ? "Double click — armed" : "Double click"}
                  </button>
                  {/* A search popup's result row is the one place a recorded CLICK position is
                      never trustworthy — a different value returns a different number of
                      rows. Press one of these to open a popup and pick which field decides
                      the row; confirming it searches the WHOLE live screen for that field's
                      value and clicks it directly — no need to touch the row yourself. */}
                  <button
                    type="button"
                    onClick={() => {
                      setSearchClickAction("click");
                      setSearchPopMode(fields.length > 0 ? "field" : "literal");
                      setSearchField(""); setSearchLiteral(""); setSearchManualLabel(""); setSearchManualExample("");
                    }}
                    disabled={busy}
                    title="Search & Click: pick which field's value to search for on screen, then click whatever row holds it — for the row a search returns"
                    className="shrink-0 whitespace-nowrap rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                  >
                    🔎 Search & Click
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setSearchClickAction("double_click");
                      setSearchPopMode(fields.length > 0 ? "field" : "literal");
                      setSearchField(""); setSearchLiteral(""); setSearchManualLabel(""); setSearchManualExample("");
                    }}
                    disabled={busy}
                    title="Search & Double-click: same as Search & Click, but for a grid that only opens a row on a double click"
                    className="shrink-0 whitespace-nowrap rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                  >
                    🔎 Search & Double-click
                  </button>
                  {/* Pressed DURING recording, at the moment the login and navigation are done
                      and the entry screen is up. Marks the step just recorded, so everything
                      above it is setup. Same thing the per-step ⚑ does, but reachable without
                      leaving the live browser. */}
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => captureCheckpointHere()}
                    disabled={busy || steps.length === 0}
                    title={
                      checkpointStep === null
                        ? "Mark the point you have reached: logged in and on the entry screen"
                        : `Checkpoint is on step ${checkpointStep + 1} — press to clear it`
                    }
                  >
                    {checkpointStep === null ? "⚑ Capture checkpoint" : `⚑ Clear checkpoint (step ${checkpointStep + 1})`}
                  </Button>
                  {/* Replay the saved draft back into the browser. Pressing it offers Auto or
                      Manual: Auto runs the whole draft, Manual walks it with Next / Prev so a
                      misbehaving step can be watched at the exact moment it runs. */}
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => setReplayMode((m) => (m === null ? "auto" : null))}
                    disabled={busy || steps.length === 0}
                    title={steps.length === 0
                      ? "Nothing recorded yet"
                      : `Replay the ${steps.length} recorded step(s) to get back where you left off`}
                  >
                    ▶ Replay recorded{replayMode !== null ? " ▴" : " ▾"}
                  </Button>

                  {replayMode !== null && (
                    <div className="flex items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50/60 px-2 py-1 dark:border-indigo-500/30 dark:bg-indigo-500/10">
                      {/* Auto | Manual */}
                      <div className="flex items-center overflow-hidden rounded-md border border-indigo-200 dark:border-indigo-500/30">
                        {(["auto", "manual"] as const).map((m) => (
                          <button
                            key={m}
                            type="button"
                            onClick={() => setReplayMode(m)}
                            className={`px-2 py-1 text-xs font-medium capitalize ${
                              replayMode === m
                                ? "bg-indigo-600 text-white"
                                : "text-indigo-700 hover:bg-indigo-100 dark:text-indigo-300 dark:hover:bg-indigo-500/20"
                            }`}
                          >
                            {m}
                          </button>
                        ))}
                      </div>

                      {replayMode === "auto" ? (
                        replaying ? (
                          <Button
                            type="button"
                            size="sm"
                            variant="danger"
                            onClick={() => { autoAbort.current = true; setStatus("Stopping after this step…"); }}
                            title="Stop the auto replay after the current step finishes"
                          >
                            ⏹ Stop at step {replayAt?.index ?? 0}
                          </Button>
                        ) : (
                          <>
                            <Button
                              type="button"
                              size="sm"
                              onClick={() => runReplay("auto")}
                              disabled={busy}
                              title={`Replay all ${steps.length} step(s), showing each screen as it happens`}
                            >
                              ▶ Run all {steps.length}
                            </Button>
                            {/* Diagnosing a draft: walk the whole thing and list everything
                                that is broken, instead of stopping at the first one and
                                needing another replay for the next. */}
                            <label
                              className="flex cursor-pointer items-center gap-1 whitespace-nowrap text-xs text-indigo-700 dark:text-indigo-300"
                              title="Don't stop at the first broken step — skip it, carry on, and list every failure at the end. For finding problems; a real job run always stops."
                            >
                              <input
                                type="checkbox"
                                checked={keepGoing}
                                onChange={(e) => setKeepGoing(e.target.checked)}
                                className="h-3 w-3 accent-indigo-600"
                              />
                              keep going past failures
                            </label>
                          </>
                        )
                      ) : (
                        <>
                          <button
                            type="button"
                            onClick={() => runReplay("prev")}
                            disabled={busy || replaying || (replayAt?.index ?? 0) <= 0}
                            title="Go back one step. A browser action cannot be undone, so this re-runs the draft from the start with one step fewer — it takes longer than Next."
                            className="rounded-md border border-indigo-200 px-2 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-100 disabled:opacity-40 dark:border-indigo-500/30 dark:text-indigo-300 dark:hover:bg-indigo-500/20"
                          >
                            ◀ Prev
                          </button>
                          <span className="whitespace-nowrap text-xs font-medium text-indigo-700 dark:text-indigo-300">
                            step {replayAt?.index ?? 0} / {replayAt?.total ?? steps.length}
                          </span>
                          <button
                            type="button"
                            onClick={() => runReplay("next")}
                            disabled={busy || replaying || (replayAt !== null && replayAt.index >= replayAt.total)}
                            title="Apply the next recorded step only"
                            className="rounded-md border border-indigo-200 px-2 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-100 disabled:opacity-40 dark:border-indigo-500/30 dark:text-indigo-300 dark:hover:bg-indigo-500/20"
                          >
                            Next ▶
                          </button>
                          <button
                            type="button"
                            onClick={() => runReplay("reset")}
                            disabled={busy || replaying}
                            title="Start the walk over — goes back to the first page so Next replays step 1"
                            className="rounded-md px-1.5 py-1 text-xs text-indigo-600 hover:underline disabled:opacity-40 dark:text-indigo-300"
                          >
                            ⟲
                          </button>
                          {/* Run straight to a chosen step and stop there — the point of this
                              box is skipping past however many ordinary steps sit in front of
                              the one that actually needs watching, without pressing Next once
                              per step to get there. */}
                          <span className="mx-1 h-4 w-px bg-indigo-200 dark:bg-indigo-500/30" />
                          <input
                            type="number"
                            min={1}
                            max={steps.length}
                            value={runToStep}
                            onChange={(e) => setRunToStep(e.target.value)}
                            onKeyDown={(e) => { if (e.key === "Enter") runReplayTo(); }}
                            placeholder="step #"
                            disabled={busy || replaying}
                            title="Run to this step number and stop there"
                            className="w-16 rounded-md border border-indigo-200 px-1.5 py-1 text-xs text-indigo-700 disabled:opacity-40 dark:border-indigo-500/30 dark:bg-slate-900 dark:text-indigo-300"
                          />
                          <button
                            type="button"
                            onClick={runReplayTo}
                            disabled={busy || replaying || !runToStep.trim()}
                            title="Run from the top to that step, then stop and wait"
                            className="rounded-md border border-indigo-200 px-2 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-100 disabled:opacity-40 dark:border-indigo-500/30 dark:text-indigo-300 dark:hover:bg-indigo-500/20"
                          >
                            Run to ▶▶
                          </button>
                        </>
                      )}

                      {/* The ERP has changed since this was recorded and the replay stopped
                          part-way. Do the missing action by hand and it is saved HERE, in the
                          middle of the draft, rather than tacked on at the end. */}
                      {replayAt && replayAt.index < replayAt.total && (
                        insertAt === null ? (
                          <button
                            type="button"
                            onClick={() => {
                              setInsertAt(replayAt.index);
                              setStatus(
                                `Insert mode on. Do the missing action in the browser (or use the AI step) — ` +
                                  `it will be saved as step ${replayAt.index + 1}, before "${replayAt.next || "the next step"}".`,
                              );
                            }}
                            title="The flow changed: record the missing action into this position instead of at the end"
                            className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-800 hover:bg-amber-100 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300"
                          >
                            ➕ Insert here (step {replayAt.index + 1})
                          </button>
                        ) : (
                          <span className="flex items-center gap-1.5 rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300">
                            ➕ inserting at step {insertAt + 1}
                            <button
                              type="button"
                              onClick={() => {
                                setInsertAt(null);
                                setStatus("Insert mode off — new steps go at the end again. Press Next ▶ to carry on.");
                              }}
                              className="underline"
                            >
                              done
                            </button>
                          </span>
                        )
                      )}
                    </div>
                  )}
                  {/* Scroll the live page. The recorder shows one viewport at a time, so on a
                      long ERP form the fields below the fold cannot be seen or clicked without
                      this. Each press is recorded, so playback looks at the same place. */}
                  <div className="flex items-center overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700">
                    <button
                      type="button"
                      onClick={() => scrollPage(-SCROLL_PX)}
                      disabled={busy}
                      title="Scroll up — recorded as a step"
                      className="px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-40 dark:text-slate-300 dark:hover:bg-slate-800"
                    >
                      ▲ Up
                    </button>
                    <span className="h-5 w-px bg-slate-200 dark:bg-slate-700" />
                    <button
                      type="button"
                      onClick={() => scrollPage(SCROLL_PX)}
                      disabled={busy}
                      title="Scroll down — recorded as a step"
                      className="px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-40 dark:text-slate-300 dark:hover:bg-slate-800"
                    >
                      ▼ Down
                    </button>
                  </div>
                  <Button type="button" variant="secondary" onClick={refreshShot} disabled={busy}>Refresh</Button>
                  <Button type="button" variant="danger" onClick={stopRecorder}>Stop</Button>
                </>
              )}
            </div>
          </div>

          {/* Open tabs. An ERP typically opens the next screen in a NEW tab on submit, so the
              recording has to follow it — clicking a tab switches the live view AND records the
              switch, so playback lands on the same tab at the same point in the sequence. */}
          {sid && tabs.length > 0 && (
            <div className="mb-3 flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 p-1.5 dark:border-slate-700 dark:bg-slate-800/60">
              <span className="flex shrink-0 items-center gap-1 px-1 text-[11px] font-medium text-slate-500">
                {tabs.length} tab{tabs.length > 1 ? "s" : ""}
                <HelpDot {...FEATURE_HELP.tabs} />
              </span>
              {/* One sliding row, not a wrapping block: an ERP can open any number of tabs and
                  a growing pile of rows would push the browser view off the screen. */}
              <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto pb-0.5 [scrollbar-width:thin]">
                {tabs.map((t) => (
                  <button
                    key={t.index}
                    type="button"
                    disabled={busy || t.closed}
                    onClick={() => switchToTab(t.index)}
                    title={t.url || `tab ${t.index + 1}`}
                    className={`max-w-[14rem] shrink-0 truncate whitespace-nowrap rounded-md border px-2 py-1 text-xs font-medium ${
                      t.active
                        ? "border-indigo-400 bg-white text-indigo-700 shadow-sm dark:bg-slate-900 dark:text-indigo-300"
                        : "border-transparent text-slate-600 hover:bg-white dark:text-slate-300 dark:hover:bg-slate-900"
                    } ${t.closed ? "line-through opacity-50" : ""}`}
                  >
                    {t.index + 1}. {tabLabel(t)}
                    {t.active ? " ●" : ""}
                  </button>
                ))}
              </div>
              <Button type="button" variant="secondary" size="sm" onClick={refreshTabs} disabled={busy} className="shrink-0">
                ⟳
              </Button>
            </div>
          )}

          {!sid && !shot && (
            <div className="flex h-80 flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 text-center text-sm text-slate-500 dark:border-slate-700">
              <p className="mb-1 font-medium">The recorder opens <span className="font-mono">{script.url}</span> in a server-side browser.</p>
              <p className="max-w-md">Click <b>Start recorder</b>. Then <b>click an input</b> — a popup asks what value goes there and the AI pre-picks the matching data field. Click buttons/links to record clicks. Everything becomes a replayable step.</p>
            </div>
          )}

          {shot && touched && (
            <p className="mb-2 rounded bg-slate-100 px-2 py-1 font-mono text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">
              last touch: {touched}
            </p>
          )}
          {/* Where the manual replay is standing. Kept on screen (not just in the status line)
              so it is still visible after the next click overwrites the status. */}
          {sid && replayMode === "manual" && replayAt && (
            <p className="mb-2 rounded bg-indigo-50 px-2 py-1 font-mono text-[11px] text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300">
              replay {replayAt.index}/{replayAt.total}
              {replayAt.done ? ` · just did: ${replayAt.done}` : ""}
              {replayAt.index < replayAt.total
                ? ` · next: ${replayAt.next || "—"}`
                : " · end of draft, carry on recording"}
            </p>
          )}
          {shot && (
            <div className="relative">
              <img
                ref={imgRef}
                src={`data:image/png;base64,${shot}`}
                alt="live browser"
                onClick={handleImageClick}
                onDragOver={(e) => e.preventDefault()}
                onDrop={handleDrop}
                className={`w-full rounded-lg border border-slate-300 dark:border-slate-700 ${busy ? "cursor-wait opacity-70" : "cursor-crosshair"}`}
                style={{ aspectRatio: `${viewport.width} / ${viewport.height}` }}
              />
              {busy && (
                <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                  <span className="rounded-full bg-slate-900/80 px-3 py-1 text-xs font-medium text-white">working…</span>
                </div>
              )}
            </div>
          )}
        </Card>

        {/* ---------- Data fields ---------- */}
        <Card className="p-5">
          {/* An Excel-import customer still often has a FEW standalone inputs on their ERP
              portal outside the imported workbook (a quotation number box, say) - showing only
              the workbook card here used to hide the individual-field drag list entirely for
              every excel-entry template, so nothing but the workbook could ever be mapped. Both
              sections now show whenever they have something to offer. */}
          {excelBook && (
            <>
              <h3 className="mb-2 text-sm font-semibold text-slate-900 dark:text-slate-50">The import file</h3>
              <p className="mb-3 text-xs text-slate-500">
                This customer imports a spreadsheet, so most values don&apos;t need typing.
                Navigate to the ERP&apos;s import screen and drag this onto its file box &mdash;
                that records the upload. Each job then attaches its own workbook.
              </p>
              <span
                draggable={!!sid}
                onDragStart={(e) => e.dataTransfer.setData("text/excel-book", excelBook)}
                title={sid ? "Drag onto the ERP's file box" : "Start the recorder first"}
                className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium ${
                  sid
                    ? "cursor-grab border-emerald-300 bg-emerald-50 text-emerald-800 hover:bg-emerald-100 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300"
                    : "border-slate-200 bg-slate-100 text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400"
                }`}
              >
                <span aria-hidden="true">▦</span>
                {excelBook}
              </span>
              <p className="mt-3 text-xs text-slate-400">
                While recording it carries the template&apos;s sample values, so the ERP accepts
                the import and you can record what comes after it.
              </p>
            </>
          )}
          {mappableFields.length > 0 && (
            <>
              <h3 className={`mb-2 text-sm font-semibold text-slate-900 dark:text-slate-50 ${excelBook ? "mt-5 border-t border-slate-200 pt-5 dark:border-slate-800" : ""}`}>
                Data fields
              </h3>
              <p className="mb-3 text-xs text-slate-500">
                {excelBook
                  ? "For any input on the ERP that ISN'T part of the imported workbook - click it to map one of these (AI pre-selects), or drag a field onto it."
                  : "Click an input in the browser to map one of these (AI pre-selects). You can also drag a field onto an input."}{" "}
                At run time each maps to the operator&apos;s extracted value.
              </p>
              <div className="flex flex-wrap gap-2">
                {mappableFields.map((f) => (
                  <span
                    key={f}
                    draggable={!!sid}
                    onDragStart={(e) => e.dataTransfer.setData("text/field", f)}
                    className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
                      !sid
                        ? "border-slate-200 bg-slate-100 text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400"
                        : multiFields.has(f)
                          ? "cursor-grab border-violet-300 bg-violet-50 text-violet-700 hover:bg-violet-100 dark:border-violet-500/30 dark:bg-violet-500/10 dark:text-violet-300"
                          : "cursor-grab border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300"
                    }`}
                    title={
                      !sid
                        ? "Start the recorder to drag fields"
                        : multiFields.has(f)
                          ? "Many values (one per line-item row) — you'll be asked how to enter them"
                          : "Drag onto an input"
                    }
                  >
                    {f}
                    {multiFields.has(f) && sid ? " ⋮" : ""}
                  </span>
                ))}
              </div>
            </>
          )}
          {!excelBook && mappableFields.length === 0 && (
            <>
              <h3 className="mb-2 text-sm font-semibold text-slate-900 dark:text-slate-50">Data fields</h3>
              <span className="text-xs text-slate-400">No template fields found.</span>
            </>
          )}
        </Card>
      </div>

      {/* ---------- Events + AI takeover ---------- */}
      {sid && (
        <Card className="mt-6 p-5">
          <div className="grid gap-6 lg:grid-cols-2">
            {/* Captured events */}
            <div>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">Captured events</h3>
                <Button size="sm" variant="secondary" onClick={captureEvents} isLoading={eventsLoading}>⚡ Scan again</Button>
              </div>
              <p className="mb-2 text-xs text-slate-500">Every clickable element on the page right now. Click <b>Record</b> to add it as a step (and press it live).</p>
              {events === null ? (
                <p className="text-xs text-slate-400">Press <b>Scan again</b> to scan the page.</p>
              ) : events.length === 0 ? (
                <p className="text-xs text-slate-400">No clickable elements found.</p>
              ) : (
                /* Grouped by what the element IS — buttons, links, inputs, dropdowns — because
                   a flat list of forty rows tells you nothing about which is which. */
                <div className="max-h-72 space-y-3 overflow-y-auto">
                  {groupEvents(events).map(([groupName, items]) => (
                    <div key={groupName}>
                      <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                        {groupName}
                        <span className="rounded-full bg-slate-100 px-1.5 text-[10px] font-medium text-slate-500 dark:bg-slate-800">
                          {items.length}
                        </span>
                      </p>
                      <div className="divide-y divide-slate-100 rounded-lg border border-slate-200 dark:divide-slate-800 dark:border-slate-700">
                        {items.map(({ ev, i }) => (
                          <div key={i} className={`flex items-center gap-2 px-3 py-1.5 text-sm ${aiPick?.index === i ? "bg-violet-50 dark:bg-violet-500/10" : ""}`}>
                            <span className="shrink-0 rounded bg-slate-100 px-1 py-0.5 text-[10px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                              {ev.input_type || ev.tag}
                            </span>
                            <span className="min-w-0 flex-1 truncate text-slate-700 dark:text-slate-200" title={ev.selector}>
                              {ev.text || <span className="text-slate-400">{ev.selector}</span>}
                              {aiPick?.index === i ? <span className="ml-1 text-[10px] font-semibold text-violet-600">← AI pick</span> : null}
                            </span>
                            <button onClick={() => recordEvent(ev)} disabled={busy} className="shrink-0 text-xs font-medium text-indigo-600 hover:underline disabled:opacity-50">Record</button>
                            {/* Marks this element/text as the proof the ERP accepted the entry.
                                Seen at run time -> completed. Absent -> the screen is handed to
                                AI for a plain-words explanation and the job is reported failed. */}
                            <button
                              onClick={() => recordSuccessEvent(ev)}
                              disabled={busy}
                              title="This element or text proves the entry succeeded"
                              className="shrink-0 text-xs font-medium text-emerald-600 hover:underline disabled:opacity-50"
                            >
                              ✓ Success
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* AI step */}
            <div>
              <h3 className="mb-2 text-sm font-semibold text-slate-900 dark:text-slate-50">🤖 AI step (handles the unexpected)</h3>
              <p className="mb-2 text-xs text-slate-500">
                Describe what should happen if the flow hits something you didn't record — e.g. an
                <i> "already logged in — continue?" </i> dialog. At run time, when a recorded element
                is missing, AI reads the page and clicks toward this goal, then the recording resumes.
              </p>
              {/* The replay is parked on a screen that did not exist when this was recorded.
                  "Preview AI pick" reads THAT screen, so the AI step written here is aimed at
                  the thing that actually blocked the flow. */}
              {insertAt !== null && (
                <p className="mb-2 rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300">
                  Insert mode is on — an AI step added now becomes <b>step {insertAt + 1}</b>, in the
                  middle of the draft. <b>Preview AI pick</b> reads the screen the replay is stopped
                  on, so describe the goal for <i>this</i> screen. Use AI when the new screen varies
                  between jobs; record a plain click when it is always the same.
                </p>
              )}
              <textarea
                value={aiGoal}
                onChange={(e) => setAiGoal(e.target.value)}
                rows={3}
                placeholder={`e.g. If a dialog says the user is already logged in, click "Continue" / "Yes" to proceed.`}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:border-violet-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
              />
              {aiPick && (
                <p className={`mt-1.5 text-xs ${aiPick.index !== null ? "text-violet-600 dark:text-violet-400" : "text-slate-500"}`}>
                  {aiPick.index !== null && events?.[aiPick.index]
                    ? `🤖 AI would click "${events[aiPick.index].text || events[aiPick.index].selector}" — ${aiPick.reason}`
                    : `🤖 AI found nothing to click right now — ${aiPick.reason}`}
                </p>
              )}
              <div className="mt-2 flex gap-2">
                <Button size="sm" variant="secondary" onClick={previewAi} isLoading={eventsLoading} disabled={!aiGoal.trim()}>Preview AI pick</Button>
                <Button size="sm" onClick={addAiStep} disabled={!aiGoal.trim()}>+ Add AI step</Button>
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* ---------- Recorded steps ---------- */}
      <Card className="mt-6 p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="flex items-center gap-2 font-semibold text-slate-900 dark:text-slate-50">
            Recorded steps
            <HelpDot {...FEATURE_HELP.checkpoint} />
          </h2>
          {steps.some((s) => s.is_checkpoint) && (
            <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
              <input
                type="checkbox"
                checked={stayOpen}
                onChange={(e) => setStayOpen(e.target.checked)}
                className="h-3.5 w-3.5 accent-amber-600"
              />
              Stay open at the checkpoint
              <HelpDot {...FEATURE_HELP.stay_open} />
            </label>
          )}
          {stayOpen && checkpointStep !== null && parked && (
            <span
              className={`flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ${PARK_TONE[parked.phase] ?? "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"}`}
              title={parked.detail || ""}
            >
              <span className={parked.phase === "ready" ? "text-emerald-500" : ""}>●</span>
              {PARK_LABEL[parked.phase] ?? parked.phase}
              {parked.phase === "ready" && typeof parked.jobs === "number" && parked.jobs > 0
                ? ` · ${parked.jobs} job${parked.jobs === 1 ? "" : "s"} done`
                : ""}
            </span>
          )}
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${script.status === "ready" ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-300"}`}>{script.status}</span>
        </div>
        <div className="flex flex-col gap-2">
          {steps.length === 0 && <p className="text-sm text-slate-400">No steps yet — start the recorder and interact with the page.</p>}
          {steps.map((s, i) => (
            <div key={i} className="flex flex-col gap-1">
            <div className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-800">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">{i + 1}</span>
              <span className="flex shrink-0 items-center gap-1">
                <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-xs font-medium text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300">{s.action}</span>
                {ACTION_HELP[s.action] && <HelpDot {...ACTION_HELP[s.action]} />}
              </span>
              <span className="min-w-0 flex-1 truncate text-slate-700 dark:text-slate-300">
                {s.field_label ? <span className="text-emerald-600">[{s.field_label}] </span> : null}
                {s.goal ? <span className="text-violet-600">🤖 AI: {s.goal} · </span> : null}
                {s.prompt ? <span className="text-violet-600">🤖 rule: {s.prompt} · </span> : null}
                {s.value ? <span className="font-semibold text-indigo-600 dark:text-indigo-400">= {displayValue(s)} </span> : null}
                {s.description ? <span className="text-slate-500">{s.description} · </span> : null}
                <span className="font-mono text-xs text-slate-400">{s.selector || "—"}</span>
              </span>
              {/* Where in the element this step will press. A recorded click is otherwise
                  invisible until it goes wrong, and "wrong" here meant double-clicking a link
                  in the middle of a results row and leaving the ERP for another site. The
                  little diagram is checkable at a glance: the dot should sit over the column
                  that was aimed at. */}
              {s.click_fx != null && s.click_fy != null && (
                <span
                  className="flex shrink-0 items-center gap-1"
                  title={`Presses ${Math.round((s.click_fx ?? 0) * 100)}% across and `
                    + `${Math.round((s.click_fy ?? 0) * 100)}% down inside the element, `
                    + `not its centre`}
                >
                  <span className="relative inline-block h-3.5 w-7 rounded-sm border border-slate-400 dark:border-slate-500">
                    <span
                      className="absolute h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-rose-500"
                      style={{
                        left: `${Math.min(96, Math.max(4, (s.click_fx ?? 0.5) * 100))}%`,
                        top: `${Math.min(92, Math.max(8, (s.click_fy ?? 0.5) * 100))}%`,
                      }}
                    />
                  </span>
                  <span className="whitespace-nowrap text-[10px] text-slate-400">
                    {Math.round((s.click_fx ?? 0) * 100)}%, {Math.round((s.click_fy ?? 0) * 100)}%
                  </span>
                </span>
              )}
              {s.action === "switch_tab" && (
                <span className="whitespace-nowrap rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium text-sky-700 dark:bg-sky-500/20 dark:text-sky-300">
                  jump to tab {Number(s.value ?? 0) + 1}
                </span>
              )}
              {s.is_optional && (
                <span className="whitespace-nowrap rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium text-sky-700 dark:bg-sky-500/20 dark:text-sky-300">
                  optional — skip if absent
                </span>
              )}
              {s.action === "scroll" && (
                <span className="whitespace-nowrap rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-500/20 dark:text-amber-300">
                  {Number(s.value ?? 0) > 0 ? "▼ down" : "▲ up"} {Math.abs(Number(s.value ?? 0))}px
                </span>
              )}
              {s.multi_mode === "all_at_once" && (
                <span className="whitespace-nowrap rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-medium text-violet-700 dark:bg-violet-500/20 dark:text-violet-300">
                  all rows in one box
                </span>
              )}
              {s.multi_mode === "per_row" && (
                <span
                  className="whitespace-nowrap rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-medium text-violet-700 dark:bg-violet-500/20 dark:text-violet-300"
                  title="Replayed once per line-item row"
                >
                  loop · {(s.row_steps ?? []).length} per row
                  {(s.end_steps ?? []).length ? ` · ${(s.end_steps ?? []).length} to end` : ""}
                </span>
              )}
              {/* Reorder + edit + delete. Until now the only option was delete, so fixing one
                  wrong selector meant re-recording everything after it. */}
              <span className="flex shrink-0 items-center gap-1">
                <button
                  type="button"
                  title="Move up"
                  disabled={i === 0}
                  onClick={() => setSteps((arr) => moveStep(arr, i, i - 1))}
                  className="rounded px-1 text-xs text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30 dark:hover:bg-slate-800"
                >
                  ↑
                </button>
                <button
                  type="button"
                  title="Move down"
                  disabled={i === steps.length - 1}
                  onClick={() => setSteps((arr) => moveStep(arr, i, i + 1))}
                  className="rounded px-1 text-xs text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30 dark:hover:bg-slate-800"
                >
                  ↓
                </button>
                {/* Optional: for a screen that only turns up sometimes. Without this, a
                    dismiss-the-warning click breaks every run where the warning is absent. */}
                <button
                  type="button"
                  title={s.is_optional
                    ? "Optional — skipped when it isn't on screen. Press to make it required again."
                    : "Mark optional: if this element isn't on screen, skip it instead of failing the run"}
                  onClick={() =>
                    setSteps((arr) => arr.map((st, j) => (j === i ? { ...st, is_optional: !st.is_optional } : st)))
                  }
                  className={`rounded px-1 text-xs ${s.is_optional ? "text-sky-600" : "text-slate-300 hover:bg-slate-100 hover:text-sky-600 dark:hover:bg-slate-800"}`}
                >
                  ?
                </button>
                <button
                  type="button"
                  title={s.is_checkpoint ? "Clear the checkpoint" : "Set the checkpoint here — everything above is login/setup"}
                  onClick={() => setCheckpoint(i)}
                  className={`rounded px-1 text-xs ${s.is_checkpoint ? "text-amber-600" : "text-slate-300 hover:bg-slate-100 hover:text-amber-600 dark:hover:bg-slate-800"}`}
                >
                  ⚑
                </button>
                <button
                  type="button"
                  onClick={() => setEditIndex(editIndex === i ? null : i)}
                  className="text-xs font-medium text-indigo-600 hover:underline"
                >
                  {editIndex === i ? "close" : "edit"}
                </button>
                <button
                  onClick={() => {
                    if (!window.confirm(`Delete step ${i + 1} (${s.action})?`)) return;
                    setSteps((arr) => arr.filter((_, j) => j !== i));
                    setEditIndex(null);
                  }}
                  className="text-xs text-rose-600 hover:underline"
                >
                  delete
                </button>
              </span>
            </div>
            {s.is_checkpoint && (
              <div className="flex items-center gap-2 py-0.5">
                <span className="h-px flex-1 bg-amber-300 dark:bg-amber-500/40" />
                <span className="flex items-center gap-1 whitespace-nowrap rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800 dark:bg-amber-500/20 dark:text-amber-200">
                  ⚑ CHECKPOINT · steps 1–{i + 1} are login &amp; setup
                </span>
                <span className="h-px flex-1 bg-amber-300 dark:bg-amber-500/40" />
              </div>
            )}
            {editIndex === i && (
              <div className="-mt-1 rounded-lg border border-indigo-200 bg-indigo-50/40 p-3 dark:border-indigo-500/30 dark:bg-indigo-500/5">
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <Select
                    label="Action"
                    value={s.action}
                    onChange={(e) => patchStep(i, { action: e.target.value })}
                  >
                    {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
                  </Select>
                  <Input
                    label="Selector"
                    value={s.selector ?? ""}
                    onChange={(e) => patchStep(i, { selector: e.target.value })}
                    placeholder="#txtInvoiceNo"
                  />
                  <Input
                    label={s.action === "get_text" ? "Pattern (regex, optional)" : "Value"}
                    value={s.value ?? ""}
                    onChange={(e) => patchStep(i, { value: e.target.value })}
                    placeholder={s.action === "send_keys" ? "Tab+++Enter" : "text, or leave empty"}
                  />
                  <Select
                    label="Or map a data field"
                    value={s.field_label ?? ""}
                    onChange={(e) => patchStep(i, { field_label: e.target.value || null })}
                  >
                    <option value="">— none (use Value) —</option>
                    {mappableFields.map((f) => <option key={f} value={f}>{f}</option>)}
                  </Select>
                  {(s.action === "wait_for" || s.action === "wait_gone") && (
                    <Input
                      label="Timeout (seconds)"
                      value={String(s.timeout ?? "")}
                      onChange={(e) => patchStep(i, { timeout: e.target.value })}
                      placeholder="20"
                    />
                  )}
                  {(s.action === "get_text" || s.action === "download") && (
                    <>
                      <Input
                        label="Capture as (label)"
                        value={s.capture_as ?? ""}
                        onChange={(e) => patchStep(i, { capture_as: e.target.value })}
                        placeholder="be_number"
                      />
                      <Select
                        label="Where used"
                        value={s.capture_usage ?? "both"}
                        onChange={(e) => patchStep(i, { capture_usage: e.target.value as "internal" | "output" | "both" })}
                      >
                        <option value="both">Both</option>
                        <option value="internal">Inside the software only</option>
                        <option value="output">Show in output only</option>
                      </Select>
                      <div className="sm:col-span-2">
                        <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
                          Description for the operator
                        </label>
                        <textarea
                          rows={2}
                          value={s.capture_description ?? ""}
                          onChange={(e) => patchStep(i, { capture_description: e.target.value })}
                          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800"
                        />
                      </div>
                    </>
                  )}
                  {s.action === "dialog" && (
                    <Input
                      label="Prompt text (prompt boxes only)"
                      value={s.prompt_text ?? ""}
                      onChange={(e) => patchStep(i, { prompt_text: e.target.value })}
                    />
                  )}
                </div>
                <p className="mt-2 text-[11px] text-slate-500">
                  Changes apply immediately to the list — press <b>Save draft</b> or{" "}
                  <b>Save &amp; mark ready</b> to persist them.
                </p>
              </div>
            )}
            </div>
          ))}
        </div>

        {/* Manual step add — still available for tweaks */}
        <details className="mt-4">
          <summary className="cursor-pointer text-xs font-medium text-slate-500 hover:text-slate-700 dark:hover:text-slate-300">Add a step manually</summary>
          <div className="mt-2 rounded-lg border border-dashed border-slate-300 p-3 dark:border-slate-700">
            {/* Help for whichever action is selected — the whole point is that you can read
                what a step does and see a worked example before you commit to using it. */}
            {ACTION_HELP[draft.action] && (
              <div className="mb-3 flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-800/60">
                <HelpDot {...ACTION_HELP[draft.action]} className="mt-0.5" />
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-slate-800 dark:text-slate-100">
                    {ACTION_HELP[draft.action].title}
                  </p>
                  <p className="mt-0.5 text-xs text-slate-500">{ACTION_HELP[draft.action].what}</p>
                </div>
              </div>
            )}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-4">
              <Select label="Action" value={draft.action} onChange={(e) => setDraft({ ...draft, action: e.target.value })}>
                {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
              </Select>
              <Input label="Selector" value={draft.selector ?? ""} onChange={(e) => setDraft({ ...draft, selector: e.target.value })} placeholder="#inv-input" />
              <Input
                label={draft.action === "get_text" ? "Value (regex, optional)" : "Value / field"}
                value={draft.value ?? ""}
                onChange={(e) => setDraft({ ...draft, value: e.target.value })}
                placeholder={
                  draft.action === "get_text" ? "BE No:\\s*(\\d+)"
                  : draft.action === "send_keys" ? "Tab+++Enter"
                  : draft.action === "check" ? "true"
                  : draft.action === "dialog" ? "accept"
                  : draft.action === "wait_for" || draft.action === "wait_gone" ? "text to wait for"
                  : "text or [[invoice_no]]"
                }
              />
              <div className="flex items-end"><Button type="button" onClick={addManualStep} className="w-full">Add</Button></div>
            </div>
            {/* Extra inputs that only some actions need. Hidden otherwise so the common case
                stays a single row rather than a form full of empty boxes. */}
            {(draft.action === "get_text" || draft.action === "wait_for" || draft.action === "wait_gone" || draft.action === "dialog") && (
              <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
                {draft.action === "get_text" && (
                  <Input
                    label="Capture as (name)"
                    value={draft.capture_as ?? ""}
                    onChange={(e) => setDraft({ ...draft, capture_as: e.target.value })}
                    placeholder="be_number"
                  />
                )}
                {(draft.action === "wait_for" || draft.action === "wait_gone") && (
                  <Input
                    label="Timeout (seconds)"
                    value={draft.timeout ?? ""}
                    onChange={(e) => setDraft({ ...draft, timeout: e.target.value })}
                    placeholder="20"
                  />
                )}
                {draft.action === "dialog" && (
                  <Input
                    label="Prompt text (prompt boxes only)"
                    value={draft.prompt_text ?? ""}
                    onChange={(e) => setDraft({ ...draft, prompt_text: e.target.value })}
                    placeholder="leave empty for alert/confirm"
                  />
                )}
              </div>
            )}
          </div>
        </details>

        <div className="mt-5 flex flex-wrap items-center justify-end gap-3">
          {saveMsg && (
            <span className={`mr-auto text-sm font-medium ${saveMsg.ok ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>
              {saveMsg.text}
            </span>
          )}
          <Button variant="secondary" onClick={testPlayback} disabled={steps.length === 0}>Test playback</Button>
          <Button variant="secondary" onClick={() => save(false)} isLoading={saving}>Save draft</Button>
          <Button onClick={() => save(true)} isLoading={saving} disabled={steps.length === 0}>Save &amp; mark ready</Button>
        </div>

        {playLog && (
          <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs dark:border-slate-800 dark:bg-slate-900/40">
            <p className="mb-1 font-semibold text-slate-700 dark:text-slate-300">Playback log</p>
            <pre className="whitespace-pre-wrap font-mono text-slate-600 dark:text-slate-400">{playLog.join("\n")}</pre>
          </div>
        )}
      </Card>

      {/* ---------- Line-item field dropped: how should its rows be entered? ---------- */}
      <Modal
        open={!!multiDrop}
        onClose={() => setMultiDrop(null)}
        title={`"${multiDrop?.label}" has many values`}
        maxWidth="max-w-lg"
      >
        {multiDrop && (
          <div className="space-y-4">
            <div className="flex items-start gap-2">
              <HelpDot {...FEATURE_HELP.multi_value} className="mt-1" />
              <p className="text-sm text-slate-600 dark:text-slate-300">
              This field is marked <b>multiple values</b> — the document lists it once per line-item
              row, so a job may bring 1 value or 40. How should they go into{" "}
              <span className="font-mono text-xs">{multiDrop.step.description || "this input"}</span>?
              </p>
            </div>

            <div className="rounded-lg border border-slate-300 p-3 dark:border-slate-700">
              <p className="text-sm font-semibold text-slate-900 dark:text-slate-50">Drop all at once</p>
              <p className="mt-1 text-xs text-slate-500">
                Every value goes into this single box, joined together. Use it when the ERP accepts
                one combined description.
              </p>
              <div className="mt-2 flex items-center gap-2">
                <span className="text-xs text-slate-500">Separator</span>
                <input
                  value={joinWith}
                  onChange={(e) => setJoinWith(e.target.value)}
                  className="w-20 rounded border border-slate-300 px-2 py-1 font-mono text-xs dark:border-slate-700 dark:bg-slate-800"
                />
                <Button onClick={chooseAllAtOnce} className="ml-auto">
                  Use this
                </Button>
              </div>
            </div>

            <button
              onClick={choosePerRow}
              className="w-full rounded-lg border border-violet-300 bg-violet-50 p-3 text-left hover:bg-violet-100 dark:border-violet-500/30 dark:bg-violet-500/10"
            >
              <span className="flex items-center gap-1.5 text-sm font-semibold text-violet-800 dark:text-violet-200">
                One value at a time
              </span>
              <p className="mt-1 text-xs text-violet-700/80 dark:text-violet-300/80">
                Type value 1, then whatever you do next — move to the next input, click “add row” —
                then value 2, and so on. You record <b>one row</b> and mark the end; it repeats for
                however many values the job has.
              </p>
            </button>
          </div>
        )}
      </Modal>

      {/* ---------- Recording one row of a line-item loop ---------- */}
      {rowCapture && capturedStep && (
        <div className="fixed bottom-4 left-1/2 z-50 w-[42rem] -translate-x-1/2 rounded-xl border border-violet-400 bg-white p-4 shadow-xl dark:bg-slate-900">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-violet-800 dark:text-violet-200">
                {rowCapture.phase === "row"
                  ? `Recording ONE row for “${capturedStep.field_label}”`
                  : `Recording what ENDS the table for “${capturedStep.field_label}”`}
              </p>
              <p className="mt-0.5 text-xs text-slate-500">
                {rowCapture.phase === "row"
                  ? "Do what comes after typing a value — click the next input, press an “add row” button. Captured: "
                  : "Do the action that closes the line items — “items done”, a total, a tab change. Captured: "}
                <b>
                  {((rowCapture.phase === "row" ? capturedStep.row_steps : capturedStep.end_steps) ?? []).length}
                </b>{" "}
                action(s)
              </p>
            </div>
            {rowCapture.phase === "row" ? (
              <Button onClick={() => setRowCapture({ ...rowCapture, phase: "end" })}>End of row</Button>
            ) : (
              <Button onClick={() => setRowCapture(null)}>End of table</Button>
            )}
          </div>
          <p className="mt-2 rounded bg-slate-50 px-2 py-1 text-[11px] text-slate-500 dark:bg-slate-800">
            Playback: value 1 → row actions → value 2 → row actions → … → last value → row actions →
            end actions.
          </p>
        </div>
      )}

      {/* ---------- Value popup (opens when an input is touched) ---------- */}
      <Modal
        open={!!pending}
        onClose={() => setPending(null)}
        title={
          popMode === "upload"
            ? "Attach the import file"
            : popMode === "pick"
              ? "Pick data out of the ERP"
              : pending?.element.is_select
                ? "Set dropdown value"
                : "Set field value"
        }
        // Pick mode has four groups of controls. In a narrow modal they stack into a column
        // tall enough to push "Add step" off the screen, so it gets the wide layout too.
        maxWidth={popMode === "ai" || popMode === "pick" ? "max-w-2xl" : "max-w-md"}
      >
        {pending && (() => {
          // BOTH choices are always offered, whatever was touched. The software must not
          // decide for the Super Admin: an ERP can hold a value in a div, a span or an svg,
          // and a "read-only looking" element is often exactly where a value has to go.
          return (
          <div className="space-y-4">
            <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs dark:bg-slate-800/60">
              <span className="text-slate-500">You touched: </span>
              <span className="font-medium text-slate-700 dark:text-slate-200">{pending.element.label || pending.element.text || pending.element.tag}</span>
              <div className="mt-0.5 font-mono text-[11px] text-slate-400">{pending.element.selector}</div>
            </div>

            {/* AI verdict — always shown so it's clear the AI reviewed this input */}
            {fields.length === 0 ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
                🤖 The AI has no template fields to map — <b>no template is linked to this script</b>. Link a template (with marked fields) to this ERP script, or enter a fixed value below.
              </div>
            ) : pending.suggestion?.field ? (
              <div className="flex items-center justify-between gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300">
                <span>
                  🤖 AI matched this to <b>{pending.suggestion.field}</b>
                  <span className="ml-1 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] dark:bg-emerald-500/20">{pending.suggestion.confidence}</span>
                  {pending.suggestion.reason ? <span className="block text-emerald-600/80 dark:text-emerald-400/80">{pending.suggestion.reason}</span> : null}
                </span>
                <button
                  type="button"
                  onClick={() => { setPopMode("field"); setPopField(pending.suggestion!.field!); }}
                  className="shrink-0 rounded-md bg-emerald-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-emerald-500"
                >
                  Use it
                </button>
              </div>
            ) : popMode === "upload" ? null : (
              /* Nothing to match against on a file box, so this banner would only be noise. */
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
                🤖 AI reviewed this input but found <b>no confident template match</b>{pending.suggestion?.reason ? ` — ${pending.suggestion.reason}` : ""}. Pick a field yourself below, or enter a fixed value.
              </div>
            )}

            {/* A step either PUTS data into the ERP, PICKS data back out of it, or just
                CLICKS it. That is the first decision, because everything below it differs — so
                it sits above the value-mode buttons rather than being hidden among them.

                "Just click it" has to be here because the engine cannot always tell. A tab
                header built as a <td> with its handler attached by script — no inline onclick,
                no cursor:pointer — is invisible to any detection: addEventListener handlers
                cannot be read from JavaScript. Such a tab looks exactly like a data cell, so
                pressing Entity/Shipment/Invoice on a Logi-Sys form opened this dialog instead
                of recording the click. One tap here beats guessing wrong. */}
            <div className={`grid gap-2 ${popMode === "upload" ? "grid-cols-2" : "grid-cols-3"}`}>
              {popMode === "upload" && (
                <button
                  type="button"
                  onClick={recordWorkbookUpload}
                  disabled={busy}
                  className="col-span-2 rounded-lg border border-emerald-500 bg-emerald-50 px-3 py-3 text-left disabled:opacity-40 dark:bg-emerald-500/10"
                >
                  <span className="block text-sm font-semibold text-slate-900 dark:text-slate-50">
                    ▦ Upload {excelBook}
                  </span>
                  <span className="mt-0.5 block text-[11px] text-slate-500">
                    Attach this job&apos;s workbook here &mdash; the whole job in one file. Press
                    the ERP&apos;s own Upload button afterwards to record that too.
                  </span>
                </button>
              )}
              <button
                type="button"
                onClick={() => setPopMode(fields.length > 0 ? "field" : "literal")}
                className={`rounded-lg border px-3 py-2.5 text-left ${
                  popMode !== "pick"
                    ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/10"
                    : "border-slate-200 dark:border-slate-700"
                }`}
              >
                <span className="block text-sm font-semibold text-slate-900 dark:text-slate-50">
                  ↓ Enter data
                </span>
                <span className="mt-0.5 block text-[11px] text-slate-500">
                  {popMode === "upload"
                    ? "Not for a file box \u2014 there is nothing to type"
                    : "Put a value into this field"}
                </span>
              </button>
              <button
                type="button"
                onClick={() => setPopMode("pick")}
                className={`rounded-lg border px-3 py-2.5 text-left ${
                  popMode === "pick"
                    ? "border-emerald-500 bg-emerald-50 dark:bg-emerald-500/10"
                    : "border-slate-200 dark:border-slate-700"
                }`}
              >
                <span className="flex items-center gap-1.5 text-sm font-semibold text-slate-900 dark:text-slate-50">
                  ↑ Pick data
                  <HelpDot {...FEATURE_HELP.pick_data} />
                </span>
                <span className="mt-0.5 block text-[11px] text-slate-500">
                  Read this out of the ERP and show it to the operator
                </span>
              </button>
              <button
                type="button"
                onClick={recordPendingClick}
                disabled={busy}
                className="rounded-lg border border-slate-200 px-3 py-2.5 text-left hover:border-amber-400 hover:bg-amber-50 disabled:opacity-40 dark:border-slate-700 dark:hover:bg-amber-500/10"
              >
                <span className="block text-sm font-semibold text-slate-900 dark:text-slate-50">
                  ⊙ Just click it
                </span>
                <span className="mt-0.5 block text-[11px] text-slate-500">
                  It is a tab or a button, not data — record a click and move on
                </span>
              </button>
            </div>

            {/* mode switch — three ways to decide this input's value. A file box has no
                value at all, so none of them applies. */}
            <div className={`grid grid-cols-2 gap-2 text-sm sm:grid-cols-4 ${
              popMode === "pick" || popMode === "upload" ? "hidden" : ""}`}>
              <button
                type="button"
                onClick={() => setPopMode("field")}
                className={`rounded-lg border px-2 py-2 text-xs font-medium ${popMode === "field" ? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300" : "border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"}`}
              >
                Map a data field
              </button>
              <button
                type="button"
                onClick={() => setPopMode("literal")}
                className={`rounded-lg border px-2 py-2 text-xs font-medium ${popMode === "literal" ? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300" : "border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"}`}
              >
                Fixed value
              </button>
              <button
                type="button"
                onClick={() => setPopMode("ai")}
                className={`rounded-lg border px-2 py-2 text-xs font-medium ${popMode === "ai" ? "border-violet-500 bg-violet-50 text-violet-700 dark:bg-violet-500/10 dark:text-violet-300" : "border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"}`}
              >
                🤖 AI rule
              </button>
              <button
                type="button"
                onClick={() => setPopMode("manual")}
                className={`rounded-lg border px-2 py-2 text-xs font-medium ${popMode === "manual" ? "border-emerald-500 bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" : "border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"}`}
              >
                📝 Manual Entry
              </button>
            </div>

            {popMode === "pick" ? (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Input
                  label="Label — what is this?"
                  value={pickLabel}
                  onChange={(e) => setPickLabel(e.target.value)}
                  placeholder="be_number"
                />
                {pickKind !== "document" ? (
                  <Input
                    label="Pattern to extract part of it (optional)"
                    value={popLiteral}
                    onChange={(e) => setPopLiteral(e.target.value)}
                    placeholder="BE No:\s*(\d+)"
                  />
                ) : (
                  <div className="flex items-end text-xs text-slate-500">
                    The file the ERP returns is kept as-is.
                  </div>
                )}
                <div className="sm:col-span-2">
                  <p className="mb-1 text-sm font-medium text-slate-700 dark:text-slate-300">What kind?</p>
                  <div className="grid grid-cols-3 gap-2">
                    {([
                      ["value", "Value", "a reference or amount"],
                      ["text", "Text", "a longer message"],
                      ["document", "Document", "a file the ERP makes"],
                    ] as const).map(([k, title, hint]) => (
                      <button
                        key={k}
                        type="button"
                        onClick={() => setPickKind(k)}
                        className={`rounded-lg border px-2 py-2 text-left ${
                          pickKind === k
                            ? "border-emerald-500 bg-emerald-50 dark:bg-emerald-500/10"
                            : "border-slate-200 dark:border-slate-700"
                        }`}
                      >
                        <span className="block text-xs font-semibold text-slate-800 dark:text-slate-100">{title}</span>
                        <span className="block text-[10px] text-slate-500">{hint}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <div className="sm:col-span-2">
                  <p className="mb-1 text-sm font-medium text-slate-700 dark:text-slate-300">
                    Where will you use it?
                  </p>
                  <div className="grid grid-cols-3 gap-2">
                    {([
                      ["internal", "Inside the software", "reuse it in a later step"],
                      ["output", "Show in output", "operator sees it only"],
                      ["both", "Both", "reuse and show"],
                    ] as const).map(([u, title, hint]) => (
                      <button
                        key={u}
                        type="button"
                        onClick={() => setPickUsage(u)}
                        className={`rounded-lg border px-2 py-2 text-left ${
                          pickUsage === u
                            ? "border-emerald-500 bg-emerald-50 dark:bg-emerald-500/10"
                            : "border-slate-200 dark:border-slate-700"
                        }`}
                      >
                        <span className="block text-xs font-semibold text-slate-800 dark:text-slate-100">{title}</span>
                        <span className="block text-[10px] text-slate-500">{hint}</span>
                      </button>
                    ))}
                  </div>
                  {pickUsage !== "output" && pickLabel.trim() && (
                    <p className="mt-1.5 rounded bg-slate-50 px-2 py-1 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                      Later steps can enter this value by mapping the data field{" "}
                      <span className="font-mono font-semibold">{pickLabel.trim().replace(/\s+/g, "_")}</span>
                      {" "}— it appears in the Data fields list once you add this step.
                    </p>
                  )}
                </div>
                <div className="sm:col-span-2">
                  <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
                    Description for the operator <span className="text-slate-400">(optional)</span>
                  </label>
                  <textarea
                    value={pickDesc}
                    onChange={(e) => setPickDesc(e.target.value)}
                    rows={2}
                    placeholder="e.g. Bill of Entry number generated by ICEGATE — quote this on the duty challan"
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-emerald-500/40 dark:border-slate-700 dark:bg-slate-800"
                  />
                  <p className="mt-1 text-[11px] text-slate-500">
                    Shown beside this item on the operator's Completed screen, so they know what
                    they are looking at.
                  </p>
                </div>
                <div className="rounded-lg bg-emerald-50 px-3 py-2 text-[11px] text-emerald-800 sm:col-span-2 dark:bg-emerald-500/10 dark:text-emerald-200">
                  {pickKind === "document"
                    ? "At run time the script clicks this element and keeps the file the ERP returns. The operator can download it from their Completed screen."
                    : "At run time the script reads this element and stores the text against your label. Leave the pattern empty to keep the whole thing."}
                </div>
              </div>
            ) : popMode === "field" ? (
              <div className="space-y-2">
                <Select label="Data field (AI-assisted — maps to the operator's extracted value)" value={popField} onChange={(e) => setPopField(e.target.value)}>
                  <option value="">— choose a field —</option>
                  {mappableFields.map((f) => (
                    <option key={f} value={f}>{f}{pending.suggestion?.field === f ? "  ← AI pick" : ""}</option>
                  ))}
                </Select>
                {pending.element.is_select ? (
                  <p className="rounded-lg bg-slate-50 px-3 py-2 text-[11px] text-slate-500 dark:bg-slate-800/60">
                    This is a dropdown — at run time the option matching the mapped value is selected automatically. To preview a specific option now, switch to <b>Fixed value</b>.
                  </p>
                ) : (
                  <label className="flex items-start gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 dark:border-slate-700 dark:text-slate-300">
                    <input type="checkbox" checked={isTypeahead} onChange={(e) => setIsTypeahead(e.target.checked)} className="mt-0.5" />
                    <span>
                      <b>This is a searchable dropdown (type-ahead).</b> At run time it will type the value and pick the matching option from the dropdown.
                    </span>
                  </label>
                )}
              </div>
            ) : popMode === "manual" ? (
              <div className="space-y-2">
                <Input
                  label="Name this field — what the operator sees"
                  value={manualLabel}
                  onChange={(e) => setManualLabel(e.target.value)}
                  placeholder="e.g. Sales Person Remarks"
                  autoFocus
                />
                <Input
                  label="Example value — typed here now, so the ERP accepts it and recording can continue"
                  value={manualExample}
                  onChange={(e) => setManualExample(e.target.value)}
                  placeholder="e.g. a realistic sample the ERP will validate"
                />
                <p className="rounded-lg bg-emerald-50 px-3 py-2 text-[11px] text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200">
                  There is no data field for this yet — naming it here creates one. On every real
                  job, the operator will see "{manualLabel.trim() || "…"}" on Additional Details
                  and type a value in before Submit Entry — that real answer is what lands here,
                  never the example above. The example is only so this box validates right now,
                  while you're recording, so the steps after it can be recorded too.
                </p>
              </div>
            ) : popMode === "ai" ? (
              <div>
                <label className="mb-2 block text-xs font-medium text-slate-600 dark:text-slate-300">
                  AI rule — write the business logic; the AI applies it to this job's data at ERP-entry time
                </label>
                <div className="flex flex-col gap-3 sm:flex-row">
                  {/* left: the rule editor */}
                  <div className="min-w-0 flex-1 space-y-2">
                    <textarea
                      ref={promptRef}
                      value={popPrompt}
                      onChange={(e) => setPopPrompt(e.target.value)}
                      rows={7}
                      autoFocus
                      placeholder={pending.element.is_select
                        ? `e.g. If transport_mode is sea, choose "Sea"; if air, choose "Air".`
                        : `e.g. If invoice_no starts with "8", use consignor_no; if it starts with "7", use phone_number; otherwise use invoice_no.`}
                      className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:border-violet-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                    />
                    {pending.element.is_select && (pending.element.options?.length ?? 0) > 0 && (
                      <p className="text-[11px] text-slate-500">
                        Answer is restricted to the dropdown options: {pending.element.options!.join(", ")}.
                      </p>
                    )}
                  </div>
                  {/* right: saved field tags to insert into the rule */}
                  <div className="w-full shrink-0 rounded-lg border border-slate-200 p-2 dark:border-slate-700 sm:w-44">
                    <p className="mb-1.5 text-[11px] font-semibold text-slate-500 dark:text-slate-400">Saved fields — click to insert</p>
                    {mappableFields.length === 0 ? (
                      <p className="text-[11px] text-slate-400">No template fields.</p>
                    ) : (
                      <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
                        {mappableFields.map((f) => (
                          <button
                            key={f}
                            type="button"
                            onClick={() => insertFieldToken(f)}
                            className="rounded-full border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 hover:bg-emerald-100 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300"
                            title={`Insert ${f}`}
                          >
                            {f}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
                <p className="mt-2 text-[11px] text-slate-500">
                  Tip: reference field names in plain English, e.g. <span className="font-mono">if invoice_no starts with "8" use consignor_no</span>.
                </p>
              </div>
            ) : popMode === "upload" ? (
              /* A file box takes a file, not text. The Upload choice above does the whole job,
                 so there is nothing to edit here. */
              null
            ) : pending.element.is_select ? (
              <Select label="Choose the dropdown option" value={popOption} onChange={(e) => setPopOption(e.target.value)}>
                {(pending.element.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
              </Select>
            ) : (
              <div className="space-y-2">
                <Input
                  label="Fixed text to type"
                  value={popLiteral}
                  onChange={(e) => onLiteralChange(e.target.value)}
                  placeholder="Start typing — dropdown matches appear below"
                  autoFocus
                />
                {/* Live type-ahead suggestions from the ERP field */}
                {(suggestLoading || suggestions.length > 0) && (
                  <div className="rounded-lg border border-slate-200 dark:border-slate-700">
                    <div className="border-b border-slate-100 px-3 py-1.5 text-[11px] font-medium text-slate-500 dark:border-slate-800">
                      {suggestLoading ? "Searching the dropdown…" : `Dropdown matches (${suggestions.length}) — click one to record it`}
                    </div>
                    <div className="max-h-44 overflow-y-auto">
                      {suggestions.map((s, i) => (
                        <button
                          key={i}
                          type="button"
                          onClick={() => pickSuggestion(s)}
                          className="block w-full truncate px-3 py-1.5 text-left text-sm text-slate-700 hover:bg-indigo-50 dark:text-slate-200 dark:hover:bg-indigo-500/10"
                          title={s}
                        >
                          {s}
                        </button>
                      ))}
                      {!suggestLoading && suggestions.length === 0 && (
                        <p className="px-3 py-2 text-xs text-slate-400">No matches shown for that text.</p>
                      )}
                    </div>
                  </div>
                )}
                <p className="text-[11px] text-slate-500">
                  If this field shows a dropdown as you type, pick a match above to record a “type &amp; select” step. Otherwise just click <b>Add step</b> to type this exact text.
                </p>
              </div>
            )}

            {/* Stuck to the bottom of the scrolling body, so the action is reachable however
                tall the form gets — the buttons used to sit below the fold on Pick data. */}
            <div className="sticky bottom-0 -mx-6 -mb-5 flex justify-end gap-2 border-t border-slate-100 bg-white px-6 py-3 dark:border-slate-800 dark:bg-slate-900">
              <Button variant="secondary" onClick={() => setPending(null)}>Cancel</Button>
              {popMode === "upload" ? (
                /* The action is the Upload choice above. A generic "Add step" here would
                   record a fill with no text into a box that cannot be typed into. */
                <Button onClick={recordWorkbookUpload} isLoading={busy}>
                  Attach {excelBook}
                </Button>
              ) : (
                <Button onClick={confirmPopup} isLoading={busy}>
                  {popMode === "pick" ? "Add pick step" : "Add step"}
                </Button>
              )}
            </div>
          </div>
          );
        })()}
      </Modal>

      {/* ---------- Search & Click / Search & Double-click popup ---------- */}
      <Modal
        open={!!searchClickAction}
        onClose={() => setSearchClickAction(null)}
        title={`🔎 Search & ${searchClickAction === "double_click" ? "Double-click" : "Click"}`}
      >
        <div className="space-y-4">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            No need to touch anything in the browser first. Pick which field's value to search
            for below — confirming searches the whole live screen for it and{" "}
            {searchClickAction === "double_click" ? "double-clicks" : "clicks"} whatever holds
            it, right now. The recorded step does the same on every real run, matched against
            that job's own value.
          </p>

          <div className="grid grid-cols-3 gap-2 text-sm">
            <button
              type="button"
              onClick={() => setSearchPopMode("field")}
              className={`rounded-lg border px-2 py-2 text-xs font-medium ${searchPopMode === "field" ? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300" : "border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"}`}
            >
              Map a data field
            </button>
            <button
              type="button"
              onClick={() => setSearchPopMode("literal")}
              className={`rounded-lg border px-2 py-2 text-xs font-medium ${searchPopMode === "literal" ? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300" : "border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"}`}
            >
              Fixed value
            </button>
            <button
              type="button"
              onClick={() => setSearchPopMode("manual")}
              className={`rounded-lg border px-2 py-2 text-xs font-medium ${searchPopMode === "manual" ? "border-emerald-500 bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" : "border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"}`}
            >
              📝 Manual Entry
            </button>
          </div>

          {searchPopMode === "field" ? (
            <Select label="Data field" value={searchField} onChange={(e) => setSearchField(e.target.value)}>
              <option value="">— choose a field —</option>
              {mappableFields.map((f) => <option key={f} value={f}>{f}</option>)}
            </Select>
          ) : searchPopMode === "literal" ? (
            <Input
              label="Fixed text to search for"
              value={searchLiteral}
              onChange={(e) => setSearchLiteral(e.target.value)}
              placeholder="e.g. an exact reference number"
              autoFocus
            />
          ) : (
            <div className="space-y-2">
              <Input
                label="Name this field — what the operator sees"
                value={searchManualLabel}
                onChange={(e) => setSearchManualLabel(e.target.value)}
                placeholder="e.g. Sales Person Remarks"
                autoFocus
              />
              <Input
                label="Example value — searched for here now, so a result actually shows up"
                value={searchManualExample}
                onChange={(e) => setSearchManualExample(e.target.value)}
                placeholder="e.g. a realistic sample the search will find"
              />
              <p className="rounded-lg bg-emerald-50 px-3 py-2 text-[11px] text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200">
                There is no data field for this yet — naming it here creates one. On every real
                job, the operator types the real value in on Additional Details; that's what
                gets searched for and clicked then.
              </p>
            </div>
          )}

          <div className="flex justify-end gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
            <Button variant="secondary" onClick={() => setSearchClickAction(null)}>Cancel</Button>
            <Button onClick={confirmSearchClick} isLoading={busy}>
              {searchClickAction === "double_click" ? "Search & double-click" : "Search & click"}
            </Button>
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}
