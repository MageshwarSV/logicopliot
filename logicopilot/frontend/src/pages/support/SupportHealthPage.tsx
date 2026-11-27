/** Production Support - Application Health (S2) and alert settings (S5): every part of the
 *  system, the queues and servers, the SLA minutes and who gets alert emails. */
import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import * as supportApi from "../../api/support";
import type { SupportActivity, SupportHealth, SupportSettings } from "../../api/support";
import { MonitorIcon } from "../../components/ui/icons";
import { ActivityList, Badge, SectionCard, STATUS_STYLE, formatWhen } from "./supportUi";

function errText(err: unknown, fallback: string): string {
  return axios.isAxiosError(err) && typeof err.response?.data?.detail === "string" ? err.response.data.detail : fallback;
}

export function SupportHealthPage() {
  const [health, setHealth] = useState<SupportHealth | null>(null);
  const [settings, setSettings] = useState<SupportSettings | null>(null);
  const [alerts, setAlerts] = useState<SupportActivity[]>([]);
  const [emails, setEmails] = useState("");
  const [sla, setSla] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [h, s, a] = await Promise.all([supportApi.getHealth(), supportApi.getSettings(), supportApi.getActivity({ kind: "alert", limit: 20 })]);
      setHealth(h);
      setAlerts(a);
      setSettings((prev) => {
        if (!prev) {
          setEmails(s.alert_emails.join(", "));
          setSla(s.sla);
        }
        return s;
      });
    } catch (err) {
      setError(errText(err, "Could not load health."));
    }
  }, []);

  useEffect(() => {
    load();
    const t = window.setInterval(load, 30000);
    return () => window.clearInterval(t);
  }, [load]);

  async function save() {
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      const s = await supportApi.saveSettings({
        sla, alert_emails: emails.split(/[,\s;]+/).map((e) => e.trim()).filter(Boolean),
      });
      setSettings(s);
      setEmails(s.alert_emails.join(", "));
      setSla(s.sla);
      setNote("Saved.");
    } catch (err) {
      setError(errText(err, "Could not save."));
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      const r = await supportApi.testAlert();
      setNote(`Test email sent to ${r.sent_to.join(", ")}.`);
    } catch (err) {
      setError(errText(err, "Could not send the test email."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell title="Application Health" icon={<MonitorIcon className="h-6 w-6" />} subtitle="Every part of Cargora, checked every 30 seconds.">
      <div className="space-y-5">
        {error && <Alert>{error}</Alert>}
        <SectionCard title="Systems" icon={<MonitorIcon className="h-5 w-5" />}
          extra={<>
            {health && <Badge tone={STATUS_STYLE[health.status].tone}>{health.status === "ok" ? "All systems healthy" : STATUS_STYLE[health.status].label}</Badge>}
            <span className="text-xs text-slate-500">Checked {formatWhen(health?.checked_at ?? null)}</span>
          </>}>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {(health?.components ?? []).map((c) => (
              <div key={c.key} className={`rounded-xl border-l-4 border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900 ${
                c.status === "ok" ? "border-l-emerald-500" : c.status === "down" ? "border-l-rose-500 bg-rose-50/40" : c.status === "warn" ? "border-l-amber-400" : "border-l-slate-300"}`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-2 font-semibold text-slate-900 dark:text-slate-50">
                    <span className={`h-2.5 w-2.5 rounded-full ${STATUS_STYLE[c.status].dot}`} />
                    {c.label}
                  </span>
                  <Badge tone={STATUS_STYLE[c.status].tone} dot={false}>{STATUS_STYLE[c.status].label}</Badge>
                </div>
                <p className="mt-1.5 text-sm text-slate-600 dark:text-slate-300">{c.detail}</p>
              </div>
            ))}
          </div>
        </SectionCard>

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <Card className="p-5">
            <h2 className="mb-3 font-semibold text-slate-900 dark:text-slate-50">Queues</h2>
            {(health?.queues ?? []).length === 0 ? (
              <p className="text-sm text-slate-500">No Redis queues on this server.</p>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-left text-slate-500"><tr><th>Queue</th><th className="text-right">Waiting</th><th className="text-right">Running</th><th className="text-right">Oldest wait</th></tr></thead>
                <tbody>
                  {health!.queues.map((q) => (
                    <tr key={q.name} className={`border-t border-slate-100 dark:border-slate-800 ${q.stale ? "text-rose-700 dark:text-rose-300" : ""}`}>
                      <td className="py-1.5">{q.label}</td>
                      <td className="text-right tabular-nums">{q.waiting}</td>
                      <td className="text-right tabular-nums">{q.running}</td>
                      <td className="text-right tabular-nums">{q.oldest_wait_seconds != null ? `${Math.round(q.oldest_wait_seconds / 60)} min` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <h3 className="mb-2 mt-5 text-sm font-semibold text-slate-900 dark:text-slate-50">Servers reporting</h3>
            {(health?.servers ?? []).length === 0 ? (
              <p className="text-sm text-slate-500">No heartbeats (needs Redis).</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {health!.servers.map((s) => (
                  <li key={`${s.host}-${s.role}`} className="flex justify-between">
                    <span>{s.host} <span className="text-slate-500">({s.role})</span></span>
                    <span className="text-slate-500">seen {s.seen_seconds_ago}s ago</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card className="p-5">
            <h2 className="mb-1 font-semibold text-slate-900 dark:text-slate-50">Alerts &amp; SLA</h2>
            <p className="mb-3 text-xs text-slate-500">
              Checked every 5 minutes: a part down, a stuck queue, new failed jobs, jobs past the SLA. Each alert shows below and is emailed (once an hour at most per problem).
            </p>
            {settings && !settings.email_configured && (
              <p className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
                Email is not set up on the server yet (SMTP settings). Alerts are still listed here.
              </p>
            )}
            <label className="block text-sm font-medium text-slate-700 dark:text-slate-300">Alert emails (comma separated)</label>
            <textarea value={emails} onChange={(e) => setEmails(e.target.value)} rows={2}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900" placeholder="support@axioralabs.ai" />
            <div className="mt-3 grid grid-cols-2 gap-3">
              {Object.keys(settings?.defaults ?? {}).map((k) => (
                <label key={k} className="text-sm">
                  <span className="text-slate-700 dark:text-slate-300">{settings?.labels?.[k] ?? k} (min)</span>
                  <input type="number" min={1} value={sla[k] ?? ""} onChange={(e) => setSla({ ...sla, [k]: Number(e.target.value) })}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 dark:border-slate-700 dark:bg-slate-900" />
                </label>
              ))}
            </div>
            <div className="mt-4 flex items-center gap-2">
              <button type="button" onClick={save} disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-60">Save</button>
              <button type="button" onClick={test} disabled={busy} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold hover:bg-slate-50 disabled:opacity-60 dark:border-slate-700 dark:hover:bg-slate-800">Send test email</button>
              {note && <span className="text-sm text-emerald-700 dark:text-emerald-300">{note}</span>}
            </div>
          </Card>
        </div>

        <Card className="p-5">
          <h2 className="mb-2 font-semibold text-slate-900 dark:text-slate-50">Recent alerts</h2>
          <ActivityList items={alerts} empty="No alerts raised." />
        </Card>
      </div>
    </AppShell>
  );
}
