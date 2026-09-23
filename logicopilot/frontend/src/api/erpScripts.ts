import { apiClient } from "./client";
import type { ErpScript, ErpStep } from "../types/erpScript";

export async function listErpScripts(tenantId?: string): Promise<ErpScript[]> {
  const { data } = await apiClient.get<ErpScript[]>("/erp-scripts", {
    params: tenantId ? { tenant_id: tenantId } : undefined,
  });
  return data;
}

export async function createErpScript(
  tenantId: string,
  payload: {
    name: string;
    url: string;
    has_login: boolean;
    login_username?: string | null;
    login_password?: string | null;
    template_ids: string[];
  },
): Promise<ErpScript> {
  const { data } = await apiClient.post<ErpScript>("/erp-scripts", payload, {
    params: { tenant_id: tenantId },
  });
  return data;
}

export async function getErpScript(scriptId: string): Promise<ErpScript> {
  const { data } = await apiClient.get<ErpScript>(`/erp-scripts/${scriptId}`);
  return data;
}

export async function updateErpScript(
  scriptId: string,
  patch: Partial<{
    name: string;
    url: string;
    has_login: boolean;
    login_username: string | null;
    login_password: string | null;
    template_ids: string[];
    steps: ErpStep[];
    status: string;
    notes: string | null;
    checkpoint_index: number | null;
    stay_open: boolean;
  }>,
): Promise<ErpScript> {
  const { data } = await apiClient.patch<ErpScript>(`/erp-scripts/${scriptId}`, patch);
  return data;
}

export async function deleteErpScript(scriptId: string): Promise<void> {
  await apiClient.delete(`/erp-scripts/${scriptId}`);
}

// --------------------------------------------------------------------------- //
// Live browser recorder
// --------------------------------------------------------------------------- //
export interface ElementInfo {
  tag: string;
  selector: string;
  frames?: string[] | null;
  text: string;
  input_type: string;
  is_select: boolean;
  is_input: boolean;
  is_button?: boolean;
  label?: string;
  applied?: boolean; // for select: whether the option was actually applied to the live field
  options: string[] | null;
  /** '' | 'control' | 'option' — a React/Vue dropdown built from divs rather than a <select>. */
  widget_role?: string;
  /** When an OPTION was touched: what it reads. Recorded as the value. */
  option_text?: string;
  /** The stable element a step should target — the control, never the generated option id. */
  control_selector?: string;
  /** Decided from the live page (role, onclick, cursor:pointer), not from the tag name. */
  is_clickable?: boolean;
  /** The interactive ancestor to click — the <a> around an icon, not the <img>. */
  click_selector?: string;
  /** That element's caption, for the step description. */
  click_text?: string;
  /** Where inside the element the press landed, as a fraction of its width/height. A replay
   *  clicks the CENTRE otherwise — which on a results row is the middle column, often a link. */
  click_fx?: number | null;
  click_fy?: number | null;
  /** What the element read when it was touched, so a row is found by its reference rather than
   *  by its position in the table. */
  click_in_text?: string;
  // what a tickbox is, and the state it was in BEFORE the press - see toggleStep()
  is_toggle?: "checkbox" | "radio" | null;
  checked_now?: boolean | null;
}

export interface RecorderStart {
  session_id: string;
  viewport: { width: number; height: number };
  screenshot: string;
  /** The very first navigation can raise an alert; this is the earliest chance to see it. */
  dialogs?: LiveDialog[];
}

export interface RecorderResult {
  element: ElementInfo | null;
  screenshot: string;
  value?: string;
  /** What was actually typed into the ERP box. For a mapped field this is a REAL sample value
   *  (a hardcoded custom field, or a marked example with the tenant's format rule applied) -
   *  not the field name, which the ERP rejects, leaving dependent lookups unfired and the rest
   *  of the flow impossible to record. */
  typed?: string;
  /** Where that sample came from, or why there wasn't one. Shown in the status line so a field
   *  with no usable sample is obvious rather than silently typing its own name. */
  sample_source?: string;
  /** Any JS dialog the page raised while this action ran. Every acting endpoint returns
   *  these now: a confirm fired by a click used to be invisible to the UI, because only the
   *  screenshot poll carried dialogs and that poll is paused while an action is in flight. */
  dialogs?: LiveDialog[];
}

export async function recorderStart(scriptId: string): Promise<RecorderStart> {
  const { data } = await apiClient.post<RecorderStart>(`/erp-scripts/${scriptId}/recorder/start`);
  return data;
}

/** An alert the ERP raised in the live browser. Playwright answers it so the page can carry
 *  on; this is how the Super Admin gets to know it happened. */
export interface LiveDialog {
  type: string;
  message: string;
}

/** The size the screenshot was actually taken at. A click on the live view is turned into
 *  page coordinates by scaling through this, so when the two disagree every click lands
 *  somewhere other than where it was aimed. It is optional only because an older server
 *  might not send it. */
export interface Viewport {
  width: number;
  height: number;
}

export async function recorderScreenshot(
  scriptId: string,
  sid: string,
): Promise<{ screenshot: string; dialogs: LiveDialog[]; viewport?: Viewport }> {
  const { data } = await apiClient.get<{
    screenshot: string;
    dialogs?: LiveDialog[];
    viewport?: Viewport;
  }>(`/erp-scripts/${scriptId}/recorder/${sid}/screenshot`);
  return { screenshot: data.screenshot, dialogs: data.dialogs ?? [], viewport: data.viewport };
}

/** How a draft is replayed back into the live browser.
 *  auto  — run the whole draft straight through
 *  next  — apply just the next recorded step
 *  prev  — step one back (re-runs from the start with one step fewer)
 *  reset — start over: position to zero and back to the first page
 *  seek  — set the position to `index` without touching the browser (used after a step is
 *          inserted mid-draft and performed by hand) */
export type ReplayMode = "auto" | "next" | "prev" | "reset" | "seek";

export interface ReplayResult {
  log: string[];
  screenshot: string | null;
  dialogs: LiveDialog[];
  /** How many recorded steps are now applied to the live screen. */
  index: number;
  total: number;
  at_end: boolean;
  at_start: boolean;
  /** Short description of the step just applied, and the one queued next. */
  done_label: string;
  next_label: string;
}

/** Walk the already-recorded steps in the live browser, so recording can carry on from where
 *  it left off after the recorder was stopped and reopened. */
export async function recorderReplay(
  scriptId: string,
  sid: string,
  steps: ErpStep[],
  values: Record<string, string> = {},
  mode: ReplayMode = "auto",
  index?: number,
  keepGoing = false,
): Promise<ReplayResult> {
  const { data } = await apiClient.post<Partial<ReplayResult>>(
    `/erp-scripts/${scriptId}/recorder/${sid}/replay`,
    { steps, values, mode, index, keep_going: keepGoing },
  );
  return {
    log: data.log ?? [],
    screenshot: data.screenshot ?? null,
    dialogs: data.dialogs ?? [],
    index: data.index ?? 0,
    total: data.total ?? steps.length,
    at_end: data.at_end ?? false,
    at_start: data.at_start ?? true,
    done_label: data.done_label ?? "",
    next_label: data.next_label ?? "",
  };
}

/** A tab open in the recorder's browser. An ERP that opens the next screen in a new tab on
 *  submit needs these — the recorder used to watch only the first page and lose the flow. */
export interface BrowserTab {
  index: number;
  url: string;
  title: string;
  active: boolean;
  closed: boolean;
}

export async function recorderTabs(scriptId: string, sid: string): Promise<BrowserTab[]> {
  const { data } = await apiClient.get<{ tabs: BrowserTab[] }>(
    `/erp-scripts/${scriptId}/recorder/${sid}/tabs`,
  );
  return data.tabs;
}

export async function recorderSwitchTab(
  scriptId: string,
  sid: string,
  index: number,
): Promise<{
  tab: { index: number; url: string; title: string };
  tabs: BrowserTab[];
  screenshot: string;
  dialogs?: LiveDialog[];
  // A tab the ERP opened is very often a smaller popup window. The server has always sent
  // this; it simply was not declared here, so the UI could not read it and went on scaling
  // clicks through the FIRST tab's size.
  viewport?: Viewport;
}> {
  const { data } = await apiClient.post(`/erp-scripts/${scriptId}/recorder/${sid}/switch-tab`, { index });
  return data;
}

export async function recorderClick(scriptId: string, sid: string, x: number, y: number): Promise<RecorderResult> {
  const { data } = await apiClient.post<RecorderResult>(`/erp-scripts/${scriptId}/recorder/${sid}/click`, { x, y });
  return data;
}

export interface FieldSuggestion {
  field: string | null;
  confidence: "high" | "medium" | "low" | string;
  option: string | null;
  reason: string;
}

export interface InspectResult {
  element: ElementInfo | null;
  suggestion: FieldSuggestion | null;
  screenshot: string;
  dialogs?: LiveDialog[];
}

export async function recorderInspect(
  scriptId: string,
  sid: string,
  x: number,
  y: number,
  fields: string[],
): Promise<InspectResult> {
  const { data } = await apiClient.post<InspectResult>(`/erp-scripts/${scriptId}/recorder/${sid}/inspect`, { x, y, fields });
  return data;
}

/** Double-click a POINT as one gesture, and report what was under it.
 *
 *  Deliberately not recorderInspect + recorderDoubleClick: inspect clicks to focus before it
 *  answers, so that pair sends a single click and then a double. On a grid row that opens on
 *  the single click, the double then lands on the screen that just opened. */
export async function recorderDoubleClickAt(
  scriptId: string,
  sid: string,
  x: number,
  y: number,
): Promise<InspectResult> {
  const { data } = await apiClient.post<InspectResult>(
    `/erp-scripts/${scriptId}/recorder/${sid}/double-click-at`,
    { x, y },
  );
  return data;
}

/** Resolve a just-built step the way a real run would - an AI rule evaluated, a mapped field
 *  turned into its sample - and PERFORM it on the screen the recorder is standing on.
 *
 *  Needed because an AI rule and a field mapped onto a dropdown used to record a step and enter
 *  nothing at all. An empty ERP box means the form never validates, so the steps after it could
 *  not be recorded. Not a replay: a replay goes back to the start URL first. */
export async function recorderApplyStep(
  scriptId: string,
  sid: string,
  step: ErpStep,
): Promise<{ ok: boolean; value?: string; error?: string; screenshot: string; dialogs?: LiveDialog[] }> {
  const { data } = await apiClient.post(
    `/erp-scripts/${scriptId}/recorder/${sid}/apply-step`,
    { step },
  );
  return data;
}

export async function recorderType(
  scriptId: string,
  sid: string,
  x: number,
  y: number,
  value: string,
  fieldLabel?: string,
): Promise<RecorderResult> {
  const { data } = await apiClient.post<RecorderResult>(`/erp-scripts/${scriptId}/recorder/${sid}/type`, {
    x,
    y,
    value,
    field_label: fieldLabel ?? null,
  });
  return data;
}

export async function recorderAutocomplete(
  scriptId: string,
  sid: string,
  x: number,
  y: number,
  value: string,
): Promise<{ suggestions: string[]; screenshot: string }> {
  const { data } = await apiClient.post<{ suggestions: string[]; screenshot: string }>(
    `/erp-scripts/${scriptId}/recorder/${sid}/autocomplete`,
    { x, y, value },
  );
  return data;
}

export async function recorderAutocompletePick(
  scriptId: string,
  sid: string,
  value: string,
  selector: string,
  frames?: string[] | null,
): Promise<{ applied: boolean; action?: string; screenshot: string }> {
  const { data } = await apiClient.post<{ applied: boolean; action?: string; screenshot: string }>(
    `/erp-scripts/${scriptId}/recorder/${sid}/autocomplete-pick`,
    { value, selector, frames: frames ?? null },
  );
  return data;
}

export async function recorderSelect(
  scriptId: string,
  sid: string,
  x: number,
  y: number,
  value: string,
  selector?: string,
  frames?: string[] | null,
): Promise<RecorderResult> {
  const { data } = await apiClient.post<RecorderResult>(`/erp-scripts/${scriptId}/recorder/${sid}/select`, {
    x,
    y,
    value,
    selector: selector ?? null,
    frames: frames ?? null,
  });
  return data;
}

export async function recorderStop(scriptId: string, sid: string): Promise<void> {
  await apiClient.post(`/erp-scripts/${scriptId}/recorder/${sid}/stop`);
}

export interface PageEvent {
  tag: string;
  text: string;
  selector: string;
  frames: string[] | null;
  input_type: string;
}

export async function recorderEvents(scriptId: string, sid: string): Promise<PageEvent[]> {
  const { data } = await apiClient.post<{ events: PageEvent[] }>(`/erp-scripts/${scriptId}/recorder/${sid}/events`);
  return data.events ?? [];
}

export async function recorderClickSelector(
  scriptId: string,
  sid: string,
  selector: string,
  frames?: string[] | null,
): Promise<{ ok: boolean; error?: string; screenshot: string; dialogs?: LiveDialog[] }> {
  const { data } = await apiClient.post<{ ok: boolean; error?: string; screenshot: string; dialogs?: LiveDialog[] }>(
    `/erp-scripts/${scriptId}/recorder/${sid}/click-selector`,
    { selector, frames: frames ?? null },
  );
  return data;
}

export async function recorderAiAction(
  scriptId: string,
  sid: string,
  goal: string,
): Promise<{ events: PageEvent[]; suggestion: { index: number | null; reason: string } }> {
  const { data } = await apiClient.post<{ events: PageEvent[]; suggestion: { index: number | null; reason: string } }>(
    `/erp-scripts/${scriptId}/recorder/${sid}/ai-action`,
    { goal },
  );
  return data;
}

export interface PlayResult {
  status: string;
  final_url?: string;
  error?: string;
  log: string[];
}

export async function playScript(scriptId: string, values: Record<string, string> = {}): Promise<PlayResult> {
  const { data } = await apiClient.post<PlayResult>(`/erp-scripts/${scriptId}/play`, { values });
  return data;
}


/** What the parked browser for this script is doing right now.
 *  phase: none | disabled | refused | starting | parking | ready | busy | backoff | stopped */
export interface ParkedStatus {
  script_id: string;
  phase: string;
  detail?: string;
  waiting_for?: number;
  parks?: number;
  jobs?: number;
}

export async function getParkedStatus(scriptId: string): Promise<ParkedStatus> {
  const { data } = await apiClient.get(`/erp-scripts/${scriptId}/parked`);
  return data;
}

/** Tear down the parked browser and park a fresh one (after a login or ERP change). */
export async function restartParked(scriptId: string): Promise<ParkedStatus> {
  const { data } = await apiClient.post(`/erp-scripts/${scriptId}/parked/restart`);
  return data;
}


export interface ScrollResult {
  /** What actually moved: the window, an inner scrollable panel, or nothing. */
  mode: "window" | "element" | "none";
  y: number;
  max: number;
  moved: number;
  at_top: boolean;
  at_bottom: boolean;
  dialogs?: LiveDialog[];
  /** Scrollable areas found on the page, and how many could still move. Shown when a press
   *  does nothing, so "no scrollbar anywhere" is distinguishable from "already at the end". */
  found: number;
  movable: number;
  /** "js" = moved the container directly, "wheel" = needed a real mouse wheel. */
  via: string;
  screenshot: string;
}

/** Scroll the live recorder page. The recorder only renders one viewport of the ERP at a
 *  time, so on a long entry form this is the only way to reach the fields below the fold. */
export async function recorderScroll(
  scriptId: string,
  sid: string,
  dy: number,
): Promise<ScrollResult> {
  const { data } = await apiClient.post<ScrollResult>(
    `/erp-scripts/${scriptId}/recorder/${sid}/scroll`,
    { dy },
  );
  return data;
}

/** Double-click a known element in the live browser. Some grids and read-only fields only
 *  open on a double click — a single one just selects the row. */
export async function recorderDoubleClick(
  scriptId: string,
  sid: string,
  selector: string,
  frames?: string[] | null,
): Promise<{ ok: boolean; error?: string; screenshot: string; dialogs?: LiveDialog[] }> {
  const { data } = await apiClient.post<{ ok: boolean; error?: string; screenshot: string; dialogs?: LiveDialog[] }>(
    `/erp-scripts/${scriptId}/recorder/${sid}/double-click`,
    { selector, frames: frames ?? null },
  );
  return data;
}
