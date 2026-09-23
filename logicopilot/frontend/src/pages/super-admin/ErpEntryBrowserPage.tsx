import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import { Modal } from "../../components/ui/Modal";
import { Input, Select } from "../../components/ui/Input";
import * as eb from "../../api/entryBrowser";

function jobStatusBadge(status: string) {
  const map: Record<string, string> = {
    failed: "bg-rose-100 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300",
    duplicate: "bg-orange-100 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300",
    completed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300",
  };
  const label = status === "completed" ? "completed" : status;
  return <span className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${map[status] ?? "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"}`}>{label}</span>;
}

export function ErpEntryBrowserPage() {
  const [params] = useSearchParams();
  const jobParam = params.get("job");

  const [companies, setCompanies] = useState<eb.EbCompany[]>([]);
  const [company, setCompany] = useState<eb.EbCompany | null>(null);
  const [templates, setTemplates] = useState<eb.EbTemplate[]>([]);
  const [template, setTemplate] = useState<eb.EbTemplate | null>(null);
  const [jobs, setJobs] = useState<eb.EbFailedJob[]>([]);
  const [job, setJob] = useState<eb.EbFailedJob | null>(null);
  const [error, setError] = useState<string | null>(null);

  // live playback
  // The id was discarded before; Stop needs it to reach the run.
  const [sid, setSid] = useState<string | null>(null);
  const [summary, setSummary] = useState<eb.PlaybackSummary | null>(null);
  const [stopping, setStopping] = useState(false);
  const [shot, setShot] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<eb.PlaybackState["result"]>(null);
  const [erpUrl, setErpUrl] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  // step-by-step (manual) mode
  const [stepSid, setStepSid] = useState<string | null>(null);
  const [stepShot, setStepShot] = useState<string | null>(null);
  const [stepIdx, setStepIdx] = useState(0);
  const [stepTotal, setStepTotal] = useState(0);
  const [stepNote, setStepNote] = useState<string | null>(null);
  const [stepErr, setStepErr] = useState(false);
  const [stepBusy, setStepBusy] = useState(false);
  const stepImgRef = useRef<HTMLImageElement | null>(null);
  const stepSidRef = useRef<string | null>(null);
  const VIEWPORT = { width: 1280, height: 800 };
  // value popup when an input is touched on the live page
  const [pending, setPending] = useState<{ x: number; y: number; el: eb.StepElement } | null>(null);
  const [popValue, setPopValue] = useState("");
  const [popOption, setPopOption] = useState("");

  useEffect(() => {
    eb.ebCompanies().then(setCompanies).catch(() => setError("Failed to load companies."));
  }, []);

  // Deep link from the inbox: ?job=<id> → jump straight to running that job's rerun.
  useEffect(() => {
    if (jobParam) startRerun(jobParam);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobParam]);

  async function openCompany(c: eb.EbCompany) {
    setCompany(c); setTemplate(null); setJobs([]); setJob(null); setError(null);
    try { setTemplates(await eb.ebTemplates(c.id)); } catch { setError("Failed to load templates."); }
  }
  async function openTemplate(t: eb.EbTemplate) {
    setTemplate(t); setJob(null); setError(null);
    try { setJobs(await eb.ebFailedJobs(t.id)); } catch { setError("Failed to load failed jobs."); }
  }

  function stopPoll() {
    if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
  }

  async function startRerun(jobId: string) {
    setError(null); setResult(null); setLog([]); setShot(null); setRunning(true);
    try {
      const { session_id, erp_url } = await eb.ebRerunLive(jobId);
      setSid(session_id); setErpUrl(erp_url);
      stopPoll();
      pollRef.current = window.setInterval(async () => {
        try {
          const st = await eb.ebPlayback(session_id);
          if (st.screenshot) setShot(st.screenshot);
          setLog(st.log ?? []);
          if (st.summary) setSummary(st.summary);
          if (st.done) {
            setRunning(false);
            setStopping(false);
            setResult(st.result);
            stopPoll();
            // The job's own row now says failed/stopped, so refresh the list beside it.
            if (template) {
              eb.ebFailedJobs(template.id).then(setJobs).catch(() => { /* the panel still shows the summary */ });
            }
          }
        } catch { /* transient */ }
      }, 1000);
    } catch (err: any) {
      setRunning(false);
      setError(err?.response?.data?.detail ?? "Could not start the rerun.");
    }
  }

  useEffect(() => () => stopPoll(), []);
  // stop the stepped browser session when leaving the page
  useEffect(() => () => { if (stepSidRef.current) eb.ebSteppedStop(stepSidRef.current).catch(() => {}); }, []);

  async function startStepped(jobId: string) {
    stopPoll(); setSid(null); setShot(null); setResult(null); setSummary(null); setStopping(false);
    setError(null); setStepBusy(true); setStepNote(null);
    try {
      const s = await eb.ebSteppedStart(jobId);
      setStepSid(s.session_id); stepSidRef.current = s.session_id;
      setStepShot(s.screenshot); setStepTotal(s.total); setStepIdx(0);
      setErpUrl(s.erp_url);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Could not start step-by-step.");
    } finally {
      setStepBusy(false);
    }
  }

  async function nextStep() {
    if (!stepSid) return;
    setStepBusy(true); setStepNote(null);
    try {
      const r = await eb.ebSteppedNext(stepSid);
      if (r.screenshot) setStepShot(r.screenshot);
      setStepIdx(r.idx); setStepTotal(r.total);
      setStepErr(r.status === "error" || r.status === "duplicated");
      setStepNote(r.done ? "All recorded steps done." : (r.note || `${r.action}: ${r.description}${r.value ? " = " + r.value : ""} — ${r.status}`));
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Step failed.");
    } finally {
      setStepBusy(false);
    }
  }

  function stepCoords(e: React.MouseEvent<HTMLImageElement>): { x: number; y: number } | null {
    const img = stepImgRef.current;
    if (!img) return null;
    const r = img.getBoundingClientRect();
    return {
      x: Math.max(0, Math.round(((e.clientX - r.left) / r.width) * VIEWPORT.width)),
      y: Math.max(0, Math.round(((e.clientY - r.top) / r.height) * VIEWPORT.height)),
    };
  }

  async function stepClickAt(e: React.MouseEvent<HTMLImageElement>) {
    if (!stepSid || stepBusy) return;
    const pt = stepCoords(e); if (!pt) return;
    setStepBusy(true);
    try {
      const res = await eb.ebSteppedInspect(stepSid, pt.x, pt.y);
      if (res.screenshot) setStepShot(res.screenshot);
      const el = res.element;
      if (el && (el.is_input || el.is_select)) {
        // touched an input/dropdown → ask for the value
        setPending({ x: pt.x, y: pt.y, el });
        setPopValue("");
        setPopOption(el.options?.[0] ?? "");
      }
      // buttons/links: the inspect already clicked them (focus/press)
    } finally { setStepBusy(false); }
  }

  async function confirmPopupValue() {
    if (!stepSid || !pending) return;
    setStepBusy(true);
    try {
      const { x, y, el } = pending;
      const res = el.is_select
        ? await eb.ebSteppedSelect(stepSid, x, y, popOption)
        : await eb.ebSteppedType(stepSid, x, y, popValue);
      if (res.screenshot) setStepShot(res.screenshot);
      // A manual entry counts as a step — advance the counter.
      if (typeof res.idx === "number") setStepIdx(res.idx);
      if (typeof res.total === "number") setStepTotal(res.total);
      setStepErr(false);
      setStepNote(`Entered by hand: ${popValue || popOption}`);
      setPending(null);
    } finally { setStepBusy(false); }
  }

  async function stopStepped() {
    if (stepSid) await eb.ebSteppedStop(stepSid).catch(() => {});
    setStepSid(null); stepSidRef.current = null; setStepShot(null); setStepNote(null);
  }

  return (
    <AppShell title="Entry Browser" subtitle="Re-run failed web-entries and watch them live.">
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}

      {/* Breadcrumb */}
      <div className="mb-3 flex flex-wrap items-center gap-1 text-sm text-slate-500">
        <button className="hover:text-indigo-600" onClick={() => { setCompany(null); setTemplate(null); setJob(null); }}>Companies</button>
        {company && <><span>›</span><button className="hover:text-indigo-600" onClick={() => { setTemplate(null); setJob(null); }}>{company.name}</button></>}
        {template && <><span>›</span><span className="text-slate-700 dark:text-slate-300">{template.name}</span></>}
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* LEFT: browse companies → templates → failed jobs */}
        <Card className="p-3 lg:col-span-1">
          {!company && (
            <div>
              <p className="mb-2 text-xs font-semibold text-slate-500">Companies with ERP scripts</p>
              {companies.length === 0 ? <p className="text-xs text-slate-400">None yet.</p> : companies.map((c) => (
                <button key={c.id} onClick={() => openCompany(c)} className="block w-full truncate rounded-md px-2 py-1.5 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-800">{c.name}</button>
              ))}
            </div>
          )}
          {company && !template && (
            <div>
              <p className="mb-2 text-xs font-semibold text-slate-500">Templates for {company.name}</p>
              {templates.length === 0 ? <p className="text-xs text-slate-400">No templates.</p> : templates.map((t) => (
                <button key={t.id} onClick={() => openTemplate(t)} className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-800">
                  <span className="truncate">{t.name}</span>
                  <span className="flex shrink-0 gap-1">
                    {t.failed_count > 0 && <span className="rounded-full bg-rose-100 px-1.5 text-xs font-medium text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{t.failed_count} failed</span>}
                    {typeof t.job_count === "number" && t.job_count > 0 && <span className="rounded-full bg-slate-100 px-1.5 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">{t.job_count} jobs</span>}
                  </span>
                </button>
              ))}
            </div>
          )}
          {template && (
            <div>
              <p className="mb-2 text-xs font-semibold text-slate-500">Jobs · {template.name}</p>
              {jobs.length === 0 ? <p className="text-xs text-slate-400">No jobs yet.</p> : jobs.map((j) => (
                <div key={j.id} className={`rounded-md px-2 py-1.5 text-sm ${job?.id === j.id ? "bg-indigo-50 dark:bg-indigo-500/10" : "hover:bg-slate-100 dark:hover:bg-slate-800"}`}>
                  <button onClick={() => setJob(j)} className="flex w-full items-center justify-between gap-2 text-left">
                    <span className="min-w-0">
                      <span className="block truncate font-mono font-medium text-slate-800 dark:text-slate-100">{j.reference}</span>
                      {j.operator && <span className="block text-xs text-slate-400">{j.operator}</span>}
                    </span>
                    {j.status && jobStatusBadge(j.status)}
                  </button>
                  {job?.id === j.id && (
                    <div className="mt-1 flex flex-col gap-1">
                      <Button size="sm" className="w-full" isLoading={running} onClick={() => startRerun(j.id)}>▶ Rerun (auto)</Button>
                      <Button size="sm" variant="secondary" className="w-full" isLoading={stepBusy && !stepSid} onClick={() => startStepped(j.id)}>Step-by-step</Button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* RIGHT: live browser window (auto or step-by-step) */}
        <Card className="p-3 lg:col-span-2">
          {stepSid ? (
            <>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold text-slate-900 dark:text-slate-50">Step-by-step · {stepIdx}/{stepTotal}</p>
                <div className="flex items-center gap-2">
                  <Button size="sm" onClick={nextStep} isLoading={stepBusy} disabled={stepIdx >= stepTotal}>▶ Next step</Button>
                  <Button size="sm" variant="danger" onClick={stopStepped}>Stop</Button>
                </div>
              </div>
              <p className="mb-2 text-[11px] text-slate-500"><b>Click any input</b> on the page below — a box pops up to enter its value. Click a button/link to press it. Or press <b>▶ Next step</b> to enter the next recorded value automatically.</p>
              {stepShot ? (
                <img
                  ref={stepImgRef}
                  src={`data:image/png;base64,${stepShot}`}
                  alt="live browser"
                  onClick={stepClickAt}
                  className={`w-full rounded-lg border border-slate-300 dark:border-slate-700 ${stepBusy ? "cursor-wait opacity-70" : "cursor-crosshair"}`}
                  style={{ aspectRatio: "1280 / 800" }}
                />
              ) : (
                <div className="flex h-80 items-center justify-center rounded-lg border border-dashed border-slate-300 text-sm text-slate-400 dark:border-slate-700">Opening the ERP…</div>
              )}
              {stepNote && <p className={`mt-2 text-xs ${stepErr ? "font-medium text-rose-600 dark:text-rose-400" : "text-slate-600 dark:text-slate-300"}`}>{stepNote}</p>}
            </>
          ) : (
            <>
              <div className="mb-2 flex items-center justify-between">
                <p className="text-sm font-semibold text-slate-900 dark:text-slate-50">Live browser</p>
                {running ? (
                  <span className="flex items-center gap-2">
                    <span className="flex items-center gap-1.5 text-xs text-emerald-600">
                      <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-500" />
                      running…{summary?.steps_total ? ` step ${summary.steps_done}/${summary.steps_total}` : ""}
                    </span>
                    {/* Stops at the end of the step it is on - never mid-action, which could
                        leave half a value typed into an ERP field. */}
                    <Button
                      size="sm"
                      variant="ghost"
                      isLoading={stopping}
                      className="text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-500/10"
                      onClick={async () => {
                        if (!sid) return;
                        setStopping(true);
                        try {
                          await eb.ebPlaybackStop(sid, job?.id);
                        } catch (err: any) {
                          setError(err?.response?.data?.detail ?? "Could not stop it.");
                          setStopping(false);
                        }
                      }}
                    >
                      ■ Stop
                    </Button>
                  </span>
                )
                  : result ? <span className={`text-xs font-medium ${result.status === "ok" ? "text-emerald-600" : "text-rose-600"}`}>{result.status === "ok" ? "✓ completed" : `✗ ${result.status}${result.reason ? " — " + result.reason : ""}`}</span>
                  : erpUrl ? <span className="truncate text-xs text-slate-400">{erpUrl}</span> : null}
              </div>
              {shot ? (
                <img src={`data:image/png;base64,${shot}`} alt="live browser" className="w-full rounded-lg border border-slate-300 dark:border-slate-700" style={{ aspectRatio: "1280 / 800" }} />
              ) : (
                <div className="flex h-80 items-center justify-center rounded-lg border border-dashed border-slate-300 text-sm text-slate-400 dark:border-slate-700">
                  Pick a failed job on the left — <b className="mx-1">Rerun (auto)</b> to watch it, or <b className="mx-1">Step-by-step</b> to drive it yourself.
                </div>
              )}
              {!running && summary && (summary.steps_total > 0 || summary.reason) && (
                <div className={`mt-2 rounded-lg border p-3 text-sm ${
                  summary.stopped
                    ? "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-500/10 dark:text-amber-200"
                    : result?.status === "ok"
                      ? "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-200"
                      : "border-rose-300 bg-rose-50 text-rose-900 dark:border-rose-700 dark:bg-rose-500/10 dark:text-rose-200"
                }`}>
                  <p className="font-semibold">
                    {summary.stopped ? "■ Stopped by hand" : result?.status === "ok" ? "✓ Completed" : "✗ Did not complete"}
                    {summary.steps_total > 0 && ` — ${summary.steps_done} of ${summary.steps_total} steps done`}
                  </p>
                  {summary.reason && <p className="mt-1 text-xs opacity-90">{summary.reason}</p>}
                  {summary.failed_steps.length > 0 && (
                    <p className="mt-1 text-xs opacity-90">
                      Not done: {summary.failed_steps.slice(0, 6).join(", ")}
                      {summary.failed_steps.length > 6 ? ` and ${summary.failed_steps.length - 6} more` : ""}
                    </p>
                  )}
                  {summary.final_url && <p className="mt-1 truncate text-xs opacity-70">Ended on {summary.final_url}</p>}
                </div>
              )}
              {log.length > 0 && (
                <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 font-mono text-[11px] text-slate-600 dark:bg-slate-900 dark:text-slate-400">{log.join("\n")}</pre>
              )}
            </>
          )}
        </Card>
      </div>

      {/* Value popup when an input on the live page is touched */}
      <Modal open={!!pending} onClose={() => setPending(null)} title={pending?.el.is_select ? "Set dropdown value" : "Enter value"}>
        {pending && (
          <div className="space-y-3">
            <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs dark:bg-slate-800/60">
              <span className="text-slate-500">Field: </span>
              <span className="font-medium text-slate-700 dark:text-slate-200">{pending.el.label || pending.el.text || pending.el.tag}</span>
            </div>
            {pending.el.is_select ? (
              <Select label="Choose the dropdown option" value={popOption} onChange={(e) => setPopOption(e.target.value)}>
                {(pending.el.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
              </Select>
            ) : (
              <Input label="Value to enter" value={popValue} onChange={(e) => setPopValue(e.target.value)} autoFocus placeholder="Type the value for this field" />
            )}
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setPending(null)}>Cancel</Button>
              <Button onClick={confirmPopupValue} isLoading={stepBusy}>Enter</Button>
            </div>
          </div>
        )}
      </Modal>
    </AppShell>
  );
}
