import { useEffect, useState } from "react";
import axios from "axios";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import * as settingsApi from "../../api/systemSettings";
import type { LargeJobLaneStatus, SystemSettings } from "../../api/systemSettings";
import { formatDateTime } from "../../utils/dateTime";

/** Platform Settings -> Large jobs: what the large-job lane is doing, parallel calls per large job,
 *  and practice AI (free testing - only on a server whose .env allows it, never production). */
export function LargeJobLaneSection({ settings }: { settings: SystemSettings }) {
  const [status, setStatus] = useState<LargeJobLaneStatus | null>(null);
  const [calls, setCalls] = useState(String(settings.large_job_parallel_calls ?? 6));
  const [mode, setMode] = useState<"off" | "record" | "replay">(settings.practice_ai_mode ?? "off");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => settingsApi.getLargeJobLaneStatus().then((s) => alive && setStatus(s)).catch(() => undefined);
    load();
    const t = setInterval(load, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  async function save(body: Parameters<typeof settingsApi.setLargeJobSettings>[0], ok: string) {
    setBusy(true);
    setMsg(null);
    try {
      const out = await settingsApi.setLargeJobSettings(body);
      setCalls(String(out.large_job_parallel_calls));
      setMode(out.practice_ai_mode);
      setMsg({ ok: true, text: ok });
    } catch (err) {
      setMsg({ ok: false, text: axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not save." : "Could not save." });
    } finally {
      setBusy(false);
    }
  }

  const waiting = (status?.waiting_emails.length ?? 0) + (status?.waiting_extractions.filter((w) => status?.current?.label !== `Extraction ${w.reference}`).length ?? 0);

  return (
    <div className="border-t border-slate-200 px-5 py-5 dark:border-slate-800">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">Large jobs</h3>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        A template with "Use the large-job lane" on (Document Processing) sends a mail with a big zip to its own
        worker, one large job at a time - the mail cycle does not wait for it and small jobs keep their workers.
      </p>

      <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs dark:border-slate-700 dark:bg-slate-800/60">
        {status?.current ? (
          <p className="text-slate-700 dark:text-slate-200">
            <span className="mr-1 inline-block h-2 w-2 animate-pulse rounded-full bg-emerald-500" />
            Processing <b>{status.current.label}</b> since {formatDateTime(status.current.started_at)}
          </p>
        ) : (
          <p className="text-slate-500">Idle - no large job running.</p>
        )}
        {waiting > 0 && (
          <ul className="mt-1 list-disc pl-5 text-slate-600 dark:text-slate-300">
            {status?.waiting_emails.map((w) => <li key={w.queued_at + w.label}>Mail: {w.label}</li>)}
            {status?.waiting_extractions
              .filter((w) => status?.current?.label !== `Extraction ${w.reference}`)
              .map((w) => <li key={w.job_id}>Extraction: {w.reference}</li>)}
          </ul>
        )}
      </div>

      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="w-40">
          <Input label="Parallel calls per large job" type="number" min={1} max={12} value={calls}
            onChange={(e) => setCalls(e.target.value)} />
        </div>
        <Button size="sm" disabled={busy}
          onClick={() => save({ parallel_calls: Number(calls) || 6 }, "Saved.")}>
          Save
        </Button>
      </div>
      <p className="mt-1 text-[11px] text-slate-400">Used once large jobs read pages and documents in parallel (next step).</p>

      {settings.practice_ai_allowed && (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 dark:border-amber-500/30 dark:bg-amber-500/10">
          <p className="text-xs font-semibold text-amber-900 dark:text-amber-200">Practice AI (this server only - never production)</p>
          <p className="mt-0.5 text-[11px] text-amber-800 dark:text-amber-300">
            Record: normal AI calls, each answer also saved. Replay: no AI calls at all - saved answers are returned
            (an empty answer when none was saved), so nothing is charged.
          </p>
          <div className="mt-2 inline-flex rounded-lg border border-amber-200 bg-white p-0.5 dark:border-amber-500/30 dark:bg-slate-900">
            {(["off", "record", "replay"] as const).map((m) => (
              <button key={m} type="button" disabled={busy}
                onClick={() => save({ practice_ai_mode: m }, m === "off" ? "Practice AI off - real AI calls." : `Practice AI: ${m}.`)}
                className={`rounded-md px-3 py-1 text-xs font-medium capitalize ${mode === m ? (m === "off" ? "bg-slate-700 text-white" : "bg-amber-600 text-white") : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
                {m}
              </button>
            ))}
          </div>
        </div>
      )}

      {msg && (
        <p className={`mt-2 rounded-lg px-3 py-2 text-xs ${msg.ok ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"}`}>
          {msg.text}
        </p>
      )}
    </div>
  );
}
