import { Fragment, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { formatDateTime } from "../../utils/dateTime";
import * as api from "../../api/apiCost";
import { SpendAnalyticsPanel } from "./SpendAnalyticsPage";
import type { CostRow, CostTotals, CustomerPages, DayDetail, MonthRow } from "../../api/apiCost";

const FIELD = "rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900";

const usd = (v: number | null | undefined, digits = 2) => (v == null ? "—" : `$${v.toFixed(digits)}`);
const inr = (v: number | null | undefined, digits = 2) => (v == null ? "—" : `₹${v.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`);
const num = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("en-IN"));

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

type Period = "today" | "yesterday" | "month" | "custom";
const PERIODS: [Period, string][] = [["today", "Today"], ["yesterday", "Yesterday"], ["month", "This month"], ["custom", "Custom"]];

function ymd(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function rangeFor(p: Period, from: string, to: string): { from: string; to: string } | null {
  const now = new Date();
  if (p === "today") return { from: ymd(now), to: ymd(now) };
  if (p === "yesterday") { const y = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1); return { from: ymd(y), to: ymd(y) }; }
  if (p === "month") return { from: ymd(new Date(now.getFullYear(), now.getMonth(), 1)), to: ymd(now) };
  return from && to && from <= to ? { from, to } : null;
}

export function ApiCostPage() {
  // Two views of the same OpenAI account: live from OpenAI with charts (Spend Analytics), and
  // the day-by-day table - also fetched live when opened, and saved as daily records.
  const [view, setView] = useState<"live" | "daily">("live");
  const [period, setPeriod] = useState<Period>("month");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [days, setDays] = useState<CostRow[]>([]);
  const [totals, setTotals] = useState<CostTotals | null>(null);
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);
  const [months, setMonths] = useState<MonthRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<DayDetail | null>(null);
  const [reload, setReload] = useState(0);

  const range = useMemo(() => rangeFor(period, customFrom, customTo), [period, customFrom, customTo]);

  // Every time the tab opens or the period changes: ask OpenAI now (and save what came back).
  useEffect(() => {
    if (view !== "daily" || !range) return;
    let live = true;
    setLoading(true);
    setError(null);
    api.live(range.from, range.to)
      .then((d) => { if (!live) return; setDays(d.days); setTotals(d.totals); setFetchedAt(d.fetched_at); })
      .catch((e) => { if (live) setError(errorText(e, "Could not get the cost from OpenAI. Try again.")); })
      .finally(() => { if (live) setLoading(false); });
    api.getMonthly().then((m) => { if (live) setMonths(m.months); }).catch(() => undefined);
    return () => { live = false; };
  }, [view, range?.from, range?.to, reload]);

  useEffect(() => {
    if (!open) { setDetail(null); return; }
    api.getDay(open).then(setDetail).catch(() => setDetail(null));
  }, [open, reload]);

  const customers = useMemo(() => {
    const m = new Map<string, CustomerPages>();
    for (const d of days) for (const c of d.customers ?? []) {
      const x = m.get(c.tenant_id) ?? { ...c, pages_billed: 0, original_pages: 0, rerun_pages: 0, total_jobs: 0 };
      x.pages_billed += c.pages_billed; x.original_pages += c.original_pages; x.rerun_pages += c.rerun_pages; x.total_jobs += c.total_jobs;
      m.set(c.tenant_id, x);
    }
    return [...m.values()].sort((a, b) => b.pages_billed - a.pages_billed);
  }, [days]);

  const periodLabel = PERIODS.find(([k]) => k === period)?.[1] ?? "";

  return (
    <AppShell title="API Cost & Usage" subtitle="OpenAI's real cost (Admin API key) next to the jobs and pages it paid for - pulled from OpenAI when you open it.">
      <div className="mb-4 flex flex-wrap gap-1 rounded-xl border border-slate-200 bg-white p-1 dark:border-slate-700 dark:bg-slate-900" role="tablist">
        {([["live", "Live from OpenAI"], ["daily", "Daily cost & pages"]] as const).map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={view === k} onClick={() => setView(k)}
            className={`rounded-lg px-4 py-2 text-sm font-medium ${view === k ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
            {label}
          </button>
        ))}
      </div>
      {view === "live" ? <SpendAnalyticsPanel /> : <>
      <Card className="mb-4 p-4">
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-wrap gap-1 rounded-lg border border-slate-200 p-1 dark:border-slate-700">
            {PERIODS.map(([k, label]) => (
              <button key={k} type="button" onClick={() => setPeriod(k)}
                className={`rounded-md px-3 py-1.5 text-sm font-medium ${period === k ? "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200 dark:bg-indigo-500/10 dark:text-indigo-200 dark:ring-indigo-500/30" : "text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
                {label}
              </button>
            ))}
          </div>
          {period === "custom" && (
            <div className="flex items-end gap-2">
              <label className="grid gap-1 text-xs font-medium text-slate-500">From
                <input id="cost-from" type="date" value={customFrom} max={customTo || undefined} onChange={(e) => setCustomFrom(e.target.value)} className={FIELD} />
              </label>
              <label className="grid gap-1 text-xs font-medium text-slate-500">To
                <input id="cost-to" type="date" value={customTo} min={customFrom || undefined} onChange={(e) => setCustomTo(e.target.value)} className={FIELD} />
              </label>
            </div>
          )}
          <button type="button" disabled={loading || !range} onClick={() => setReload((n) => n + 1)}
            className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-sm font-semibold text-indigo-700 hover:bg-indigo-100 disabled:opacity-50 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-200">
            {loading ? "Getting from OpenAI…" : "Refresh"}
          </button>
          <p className="ml-auto max-w-md text-xs text-slate-500 dark:text-slate-400">
            Pulled from OpenAI each time{fetchedAt ? ` (last at ${formatDateTime(fetchedAt)})` : ""} and saved day by day. A day is OpenAI's billing day: 05:30 IST to 05:30 IST next day. All OpenAI projects of this account; Google Document AI not included.
          </p>
        </div>
      </Card>

      {error && <Card className="mb-4 p-4 text-sm text-rose-600">{error}</Card>}

      {totals && (
        <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Tile label={`OpenAI spend · ${periodLabel}`} value={usd(totals.cost_usd)} sub={inr(totals.cost_inr)} tone="border-indigo-200 bg-indigo-50 text-indigo-900 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-100" />
          <Tile label="Pages billed" value={num(totals.pages_billed)} sub={`${num(totals.original_pages)} original · ${num(totals.rerun_pages)} re-run`} tone="border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-100" />
          <Tile label="Cost per page" value={inr(totals.cost_per_page_inr, 3)} sub={usd(totals.cost_per_page_usd, 4)} tone="border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-100" />
          <Tile label="Classified / unclassified pages" value={`${num(totals.classified_pages)} / ${num(totals.unclassified_pages)}`}
            sub={`${num(totals.total_jobs)} jobs${totals.provisional_days ? ` · ${totals.provisional_days} day(s) still settling` : ""}`}
            tone="border-slate-200 bg-white text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100" />
        </div>
      )}

      <Card className="mb-4">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3 dark:border-slate-800">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">Daily · {periodLabel}</h2>
          <span className="text-xs text-slate-500">{loading ? "Getting from OpenAI…" : `${days.length} day(s)`}</span>
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
              <th className="px-5 py-2.5 font-medium">Status</th>
            </tr></thead>
            <tbody>
              {!loading && days.length === 0 && !error && (
                <tr><td colSpan={11} className="px-5 py-12 text-center text-sm text-slate-500">Nothing for this period yet.</td></tr>
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
                    <td className="px-5 py-2.5"><Status s={d.status} /></td>
                  </tr>
                  {open === d.usage_date && (
                    <tr className="border-b border-slate-100 bg-slate-50 dark:border-slate-800 dark:bg-slate-800/40">
                      <td colSpan={11} className="px-5 py-4">
                        <div className="grid gap-5 lg:grid-cols-2">
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
          <div className="border-b border-slate-200 px-5 py-3 dark:border-slate-800">
            <h2 className="font-semibold text-slate-900 dark:text-slate-50">Monthly</h2>
            <p className="text-xs text-slate-500">From the days saved so far.</p>
          </div>
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
                  <tr key={m.month} className="border-b border-slate-100 dark:border-slate-800">
                    <td className="px-5 py-2.5 font-medium">{m.month}<span className="ml-2 text-xs text-slate-400">{m.days_recorded} day(s)</span></td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{num(m.pages_billed)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{num(m.rerun_pages)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{num(m.total_jobs)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums font-semibold">{usd(m.cost_usd)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{inr(m.cost_inr)}</td>
                    <td className="px-5 py-2.5 text-right tabular-nums">{inr(m.cost_per_page_inr, 3)}</td>
                  </tr>
                ))}
                {!months.length && <tr><td colSpan={7} className="px-5 py-8 text-center text-sm text-slate-500">No days saved yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </Card>
        <Card className="p-5">
          <h2 className="mb-3 font-semibold text-slate-900 dark:text-slate-50">Pages by customer · {periodLabel}</h2>
          <CustomerTable rows={customers} />
          <p className="mt-3 text-xs text-slate-500">Pages only. OpenAI cost stays at account level and is not divided between customers.</p>
        </Card>
      </div>
      </>}
    </AppShell>
  );
}
