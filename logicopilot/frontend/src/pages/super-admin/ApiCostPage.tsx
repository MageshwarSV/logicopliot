import { Fragment, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { formatDateTime } from "../../utils/dateTime";
import * as api from "../../api/apiCost";
import type { CostRow, CostTotals, CustomerPages, DayDetail, MonthRow } from "../../api/apiCost";

const FIELD = "rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900";

const usd = (v: number | null | undefined, digits = 2) => (v == null ? "—" : `$${v.toFixed(digits)}`);
const inr = (v: number | null | undefined, digits = 2) => (v == null ? "—" : `₹${v.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`);
const num = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("en-IN"));

function thisMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function Status({ s }: { s: CostRow["status"] }) {
  const style = {
    final: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300",
    provisional: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300",
    failed: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300",
  }[s];
  const label = { final: "Final", provisional: "Provisional", failed: "Failed" }[s];
  return <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${style}`}>{label}</span>;
}

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone: string }) {
  return (
    <div className={`rounded-xl border p-4 ${tone}`}>
      <div className="text-xs font-medium opacity-80">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 text-xs opacity-75">{sub}</div>}
    </div>
  );
}

function errorText(e: unknown, fallback: string) {
  if (axios.isAxiosError(e) && typeof e.response?.data?.detail === "string") return e.response.data.detail;
  return fallback;
}

function CustomerTable({ rows }: { rows: CustomerPages[] }) {
  if (!rows.length) return <p className="text-xs text-slate-500">No pages that day.</p>;
  return (
    <table className="w-full text-left text-xs">
      <thead className="text-slate-500"><tr>
        <th className="py-1 pr-3 font-medium">Customer</th><th className="py-1 pr-3 text-right font-medium">Pages</th>
        <th className="py-1 pr-3 text-right font-medium">Original</th><th className="py-1 pr-3 text-right font-medium">Re-run</th>
        <th className="py-1 pr-3 text-right font-medium">Jobs</th>
      </tr></thead>
      <tbody>{rows.map((c) => (
        <tr key={c.tenant_id} className="border-t border-slate-100 dark:border-slate-800">
          <td className="py-1 pr-3">{c.tenant_name ?? c.tenant_id}</td>
          <td className="py-1 pr-3 text-right tabular-nums">{num(c.pages_billed)}</td>
          <td className="py-1 pr-3 text-right tabular-nums">{num(c.original_pages)}</td>
          <td className="py-1 pr-3 text-right tabular-nums">{num(c.rerun_pages)}</td>
          <td className="py-1 pr-3 text-right tabular-nums">{num(c.total_jobs)}</td>
        </tr>))}</tbody>
    </table>
  );
}

export function ApiCostPage() {
  const [month, setMonth] = useState(thisMonth());
  const [days, setDays] = useState<CostRow[]>([]);
  const [totals, setTotals] = useState<CostTotals | null>(null);
  const [months, setMonths] = useState<MonthRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<DayDetail | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    Promise.all([api.getDaily(month), api.getMonthly()])
      .then(([d, m]) => { if (!live) return; setDays(d.days); setTotals(d.totals); setMonths(m.months); })
      .catch(() => { if (live) setError("Could not load API cost records."); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [month, reload]);

  useEffect(() => {
    if (!open) { setDetail(null); return; }
    api.getDay(open).then(setDetail).catch(() => setDetail(null));
  }, [open, reload]);

  async function collect(dateFrom: string, dateTo?: string) {
    setNote(null);
    try {
      const r = await api.runDays(dateFrom, dateTo);
      setNote(`Collecting ${r.days} day(s) from OpenAI in the background. Refresh in a minute.`);
      setTimeout(() => setReload((n) => n + 1), 20000);
    } catch (e) {
      setNote(errorText(e, "Could not start the collection."));
    }
  }

  const monthCustomers = useMemo(() => {
    const m = new Map<string, CustomerPages>();
    for (const d of days) for (const c of d.customers ?? []) {
      const x = m.get(c.tenant_id) ?? { ...c, pages_billed: 0, original_pages: 0, rerun_pages: 0, total_jobs: 0 };
      x.pages_billed += c.pages_billed; x.original_pages += c.original_pages; x.rerun_pages += c.rerun_pages; x.total_jobs += c.total_jobs;
      m.set(c.tenant_id, x);
    }
    return [...m.values()].sort((a, b) => b.pages_billed - a.pages_billed);
  }, [days]);

  return (
    <AppShell title="API Cost & Usage" subtitle="OpenAI's real daily cost next to the pages it paid for. Collected every night at 11:55 PM IST and reconciled the next morning.">
      <Card className="mb-4 p-4">
        <div className="flex flex-wrap items-end gap-4">
          <label className="grid gap-1 text-xs font-medium text-slate-500">Month
            <input id="cost-month" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} className={FIELD} />
          </label>
          <div className="flex flex-wrap items-end gap-2">
            <label className="grid gap-1 text-xs font-medium text-slate-500">Collect / retry from
              <input id="run-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={FIELD} />
            </label>
            <label className="grid gap-1 text-xs font-medium text-slate-500">to
              <input id="run-to" type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} className={FIELD} />
            </label>
            <button type="button" disabled={!from} onClick={() => collect(from, to)}
              className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-sm font-semibold text-indigo-700 hover:bg-indigo-100 disabled:opacity-50 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-200">
              Collect
            </button>
          </div>
          <p className="ml-auto max-w-md text-xs text-slate-500 dark:text-slate-400">
            A day is OpenAI's billing day: 05:30 IST to 05:30 IST next day. Cost is all 12 OpenAI projects of this account, never split by customer. Google Document AI is not included.
          </p>
        </div>
        {note && <p className="mt-3 rounded-lg bg-slate-50 p-2 text-sm text-slate-700 dark:bg-slate-800 dark:text-slate-200">{note}</p>}
      </Card>

      {error && <Card className="mb-4 p-4 text-sm text-rose-600">{error}</Card>}

      {totals && (
        <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Tile label="OpenAI spend this month" value={usd(totals.cost_usd)} sub={inr(totals.cost_inr)} tone="border-indigo-200 bg-indigo-50 text-indigo-900 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-100" />
          <Tile label="Pages billed" value={num(totals.pages_billed)} sub={`${num(totals.original_pages)} original · ${num(totals.rerun_pages)} re-run`} tone="border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-100" />
          <Tile label="Cost per page" value={inr(totals.cost_per_page_inr, 3)} sub={usd(totals.cost_per_page_usd, 4)} tone="border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-100" />
          <Tile label="Classified / unclassified pages" value={`${num(totals.classified_pages)} / ${num(totals.unclassified_pages)}`}
            sub={`${num(totals.total_jobs)} jobs${totals.provisional_days ? ` · ${totals.provisional_days} provisional day(s)` : ""}${totals.failed_days ? ` · ${totals.failed_days} failed` : ""}`}
            tone="border-slate-200 bg-white text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100" />
        </div>
      )}

      <Card className="mb-4">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3 dark:border-slate-800">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">Daily</h2>
          <span className="text-xs text-slate-500">{loading ? "Loading…" : `${days.length} day(s) recorded`}</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead><tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800">
              <th className="px-5 py-2.5 font-medium">Date</th>
              <th className="px-3 py-2.5 text-right font-medium">Pages</th>
              <th className="px-3 py-2.5 text-right font-medium">Original</th>
              <th className="px-3 py-2.5 text-right font-medium">Re-run</th>
              <th className="px-3 py-2.5 text-right font-medium">Classified</th>
              <th className="px-3 py-2.5 text-right font-medium">Unclassified</th>
              <th className="px-3 py-2.5 text-right font-medium">Jobs</th>
              <th className="px-3 py-2.5 text-right font-medium">Cost (USD)</th>
              <th className="px-3 py-2.5 text-right font-medium">Cost (INR)</th>
              <th className="px-3 py-2.5 text-right font-medium">Per page</th>
              <th className="px-3 py-2.5 font-medium">Status</th>
              <th className="px-5 py-2.5" />
            </tr></thead>
            <tbody>
              {!loading && days.length === 0 && (
                <tr><td colSpan={12} className="px-5 py-12 text-center text-sm text-slate-500">
                  No days recorded for this month yet. Use Collect above to fetch them from OpenAI.
                </td></tr>
              )}
              {days.map((d) => (
                <Fragment key={d.usage_date}>
                  <tr onClick={() => setOpen(open === d.usage_date ? null : d.usage_date)}
                    className={`cursor-pointer border-b border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50 ${open === d.usage_date ? "bg-indigo-50/60 dark:bg-indigo-500/10" : ""}`}>
                    <td className="whitespace-nowrap px-5 py-2.5 font-medium">{d.usage_date}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums font-semibold">{num(d.pages_billed)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{num(d.original_pages)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{num(d.rerun_pages)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{num(d.classified_pages)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{num(d.unclassified_pages)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{num(d.total_jobs)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{usd(d.cost_usd, 3)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{inr(d.cost_inr)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{inr(d.cost_per_page_inr, 3)}</td>
                    <td className="px-3 py-2.5"><Status s={d.status} /></td>
                    <td className="px-5 py-2.5 text-right">
                      <button type="button" onClick={(e) => { e.stopPropagation(); collect(d.usage_date); }}
                        className="text-xs font-semibold text-indigo-600 hover:underline dark:text-indigo-300">Retry</button>
                    </td>
                  </tr>
                  {open === d.usage_date && (
                    <tr className="border-b border-slate-100 bg-slate-50 dark:border-slate-800 dark:bg-slate-800/40">
                      <td colSpan={12} className="px-5 py-4">
                        <div className="grid gap-5 lg:grid-cols-3">
                          <div className="min-w-0">
                            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Cost by OpenAI project</h3>
                            <table className="w-full text-left text-xs"><tbody>
                              {(detail?.projects ?? []).filter((p) => p.project_id !== "__account_total__").map((p) => (
                                <tr key={p.project_id} className="border-t border-slate-100 dark:border-slate-700">
                                  <td className="py-1 pr-3">{p.project_name}</td>
                                  <td className="py-1 pr-3 text-right tabular-nums">{usd(p.cost_usd, 3)}</td>
                                  <td className="py-1 text-right tabular-nums text-slate-500">{num(p.requests)} calls</td>
                                </tr>))}
                            </tbody></table>
                            <p className="mt-2 text-xs text-slate-500">
                              Rate {d.usd_inr_rate ? `₹${d.usd_inr_rate.toFixed(2)}/$` : "—"} ({d.rate_source ?? "—"}) · tokens {num(d.input_tokens)} in / {num(d.output_tokens)} out
                            </p>
                          </div>
                          <div className="min-w-0">
                            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Pages by customer</h3>
                            <CustomerTable rows={d.customers ?? []} />
                          </div>
                          <div className="min-w-0">
                            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Runs</h3>
                            <ol className="grid gap-1 text-xs">
                              {(detail?.runs ?? []).map((r, i) => (
                                <li key={i} className="flex flex-wrap gap-2">
                                  <span className="text-slate-500">{formatDateTime(r.started_at)}</span>
                                  <span className="font-medium">{r.kind}</span>
                                  <span className={r.status === "ok" ? "text-emerald-600" : r.status === "failed" ? "text-rose-600" : "text-amber-600"}>{r.status}</span>
                                  {r.error && <span className="w-full break-all text-rose-600">{r.error}</span>}
                                </li>))}
                            </ol>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <div className="border-b border-slate-200 px-5 py-3 dark:border-slate-800"><h2 className="font-semibold text-slate-900 dark:text-slate-50">Monthly</h2></div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead><tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800">
                <th className="px-5 py-2.5 font-medium">Month</th>
                <th className="px-3 py-2.5 text-right font-medium">Pages</th>
                <th className="px-3 py-2.5 text-right font-medium">Re-run</th>
                <th className="px-3 py-2.5 text-right font-medium">Jobs</th>
                <th className="px-3 py-2.5 text-right font-medium">Cost (USD)</th>
                <th className="px-3 py-2.5 text-right font-medium">Cost (INR)</th>
                <th className="px-5 py-2.5 text-right font-medium">Per page</th>
              </tr></thead>
              <tbody>
                {months.map((m) => (
                  <tr key={m.month} onClick={() => setMonth(m.month)} className="cursor-pointer border-b border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50">
                    <td className="px-5 py-2.5 font-medium">{m.month}<span className="ml-2 text-xs text-slate-400">{m.days_recorded} day(s)</span></td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{num(m.pages_billed)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{num(m.rerun_pages)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{num(m.total_jobs)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums font-semibold">{usd(m.cost_usd)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{inr(m.cost_inr)}</td>
                    <td className="px-5 py-2.5 text-right tabular-nums">{inr(m.cost_per_page_inr, 3)}</td>
                  </tr>
                ))}
                {!months.length && <tr><td colSpan={7} className="px-5 py-8 text-center text-sm text-slate-500">No months recorded yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </Card>
        <Card className="p-5">
          <h2 className="mb-3 font-semibold text-slate-900 dark:text-slate-50">Pages by customer · {month}</h2>
          <CustomerTable rows={monthCustomers} />
          <p className="mt-3 text-xs text-slate-500">Pages only. OpenAI cost stays at account level and is not divided between customers.</p>
        </Card>
      </div>
    </AppShell>
  );
}
