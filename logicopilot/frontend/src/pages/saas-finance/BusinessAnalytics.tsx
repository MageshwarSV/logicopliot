/** SaaS Finance - business performance (investor view). Revenue is net of GST: finished months
 *  from their invoices, the current month live; daily = page-usage revenue spread over the days
 *  by billable pages. Backend: app/core/finance_analytics.py. */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import * as financeApi from "../../api/saasFinance";
import type { FinCustomerRow, FinDaily, FinMonthly, FinOverview, FinProducts } from "../../api/saasFinance";
import {
  BriefcaseIcon, BuildingIcon, ChartBarIcon, CheckBadgeIcon, ClockIcon, CreditCardIcon, DocumentIcon,
  LayersIcon, SparklesIcon, TargetIcon, UsersIcon,
} from "../../components/ui/icons";
import { ChartCard, Donut, FinanceVizStyles, Legend, LineSeries, SERIES, StackedBars } from "./financeCharts";
import { currentMonthStr, formatInr, monthLabel, shiftMonth } from "./financeFormat";

const SHIP = ["Air Import", "Air Export", "Sea Import", "Sea Export"];

function compactInr(v: number): string {
  if (v >= 1e7) return `₹${(v / 1e7).toFixed(v >= 1e8 ? 0 : 1)} Cr`;
  if (v >= 1e5) return `₹${(v / 1e5).toFixed(v >= 1e6 ? 0 : 1)} L`;
  if (v >= 1e3) return `₹${(v / 1e3).toFixed(v >= 1e4 ? 0 : 1)}K`;
  return `₹${Math.round(v)}`;
}
const num = (v: number) => (v >= 1e5 ? `${(v / 1e5).toFixed(1)} L` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : `${Math.round(v)}`);

function Delta({ pct, suffix = "vs last month" }: { pct: number | null; suffix?: string }) {
  if (pct == null) return <span className="text-xs text-slate-400">No comparison yet</span>;
  const up = pct >= 0;
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-semibold ${up ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>
      {up ? "▲" : "▼"} {Math.abs(pct).toFixed(1)}% <span className="font-normal text-slate-500">{suffix}</span>
    </span>
  );
}

function Kpi({ label, value, sub, icon, tint }: { label: string; value: ReactNode; sub?: ReactNode; icon: ReactNode; tint: string }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</p>
        <span className={`flex h-9 w-9 items-center justify-center rounded-xl ${tint}`}>{icon}</span>
      </div>
      <p className="mt-1 text-2xl font-bold tabular-nums text-slate-900 dark:text-slate-50">{value}</p>
      <div className="mt-1 min-h-4">{sub}</div>
    </div>
  );
}

function NavButtons({ label, onPrev, onNext, prevDisabled, nextDisabled }: {
  label: string; onPrev: () => void; onNext: () => void; prevDisabled?: boolean; nextDisabled?: boolean;
}) {
  const b = "rounded-lg border border-slate-200 px-2.5 py-1 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800";
  return (
    <div className="inline-flex items-center gap-2">
      <button type="button" className={b} onClick={onPrev} disabled={prevDisabled} aria-label="Previous">◀</button>
      <span className="min-w-32 text-center text-sm font-semibold text-slate-900 dark:text-slate-50">{label}</span>
      <button type="button" className={b} onClick={onNext} disabled={nextDisabled} aria-label="Next">▶</button>
    </div>
  );
}

function Toggle<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-slate-200 p-0.5 dark:border-slate-700">
      {options.map(([k, l]) => (
        <button key={k} type="button" onClick={() => onChange(k)}
          className={`rounded-md px-2.5 py-1 text-xs font-medium ${value === k ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
          {l}
        </button>
      ))}
    </div>
  );
}

const MILESTONE_ICON: Record<string, ReactNode> = {
  first_customer: <BuildingIcon className="h-4 w-4" />, record_month: <SparklesIcon className="h-4 w-4" />,
  first_lakh: <TargetIcon className="h-4 w-4" />, total_revenue: <CreditCardIcon className="h-4 w-4" />,
  total_pages: <DocumentIcon className="h-4 w-4" />,
};

export function BusinessAnalytics() {
  const navigate = useNavigate();
  const [ov, setOv] = useState<FinOverview | null>(null);
  const [fy, setFy] = useState<number | null>(null);
  const [mon, setMon] = useState<FinMonthly | null>(null);
  const [prod, setProd] = useState<FinProducts | null>(null);
  const [month, setMonth] = useState(currentMonthStr());
  const [day, setDay] = useState<FinDaily | null>(null);
  const [cust, setCust] = useState<FinCustomerRow[] | null>(null);
  const [revView, setRevView] = useState<"daily" | "monthly">("monthly");
  const [dailyMetric, setDailyMetric] = useState<"revenue" | "pages">("revenue");
  const [prodView, setProdView] = useState<"type" | "shipment">("type");
  const [shipMetric, setShipMetric] = useState<"revenue" | "pages">("revenue");

  useEffect(() => { financeApi.getFinOverview().then(setOv).catch(() => undefined); }, []);
  useEffect(() => {
    financeApi.getFinMonthly(fy ?? undefined).then((m) => { setMon(m); if (fy == null) setFy(m.fy_start_year); }).catch(() => undefined);
    financeApi.getFinProducts(fy ?? undefined).then(setProd).catch(() => undefined);
  }, [fy]);
  useEffect(() => {
    financeApi.getFinDaily(month).then(setDay).catch(() => undefined);
    financeApi.getFinCustomers(month).then((c) => setCust(c.rows)).catch(() => undefined);
  }, [month]);

  const k = ov?.kpis;
  const years = mon?.available_years ?? [];
  const curMonth = day?.current_month ?? currentMonthStr();
  const earliest = day?.earliest_month ?? curMonth;

  const monthlySeries = useMemo(() => (mon ? [
    { key: "platform", label: "Platform fee", color: SERIES[0], values: mon.months.map((m) => m.platform) },
    { key: "usage", label: "Page usage", color: SERIES[1], values: mon.months.map((m) => m.usage) },
    { key: "other", label: "Other services", color: SERIES[2], values: mon.months.map((m) => m.other) },
  ] : []), [mon]);
  const fyGrowth = mon && mon.totals.last_year_revenue ? (100 * (mon.totals.revenue - mon.totals.last_year_revenue)) / mon.totals.last_year_revenue : null;

  return (
    <div className="fin-viz space-y-5">
      <FinanceVizStyles />

      {/* KPIs */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Revenue this month" value={k?.revenue_month != null ? formatInr(k.revenue_month) : "—"}
          icon={<CreditCardIcon className="h-5 w-5" />} tint="bg-indigo-50 text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-300"
          sub={k?.month_to_date_pct_of_last != null ? <span className="text-xs text-slate-500">So far, {(100 + k.month_to_date_pct_of_last).toFixed(0)}% of last month</span> : undefined} />
        <Kpi label="Last month revenue" value={k?.revenue_prev_month != null ? formatInr(k.revenue_prev_month) : "—"}
          icon={<ChartBarIcon className="h-5 w-5" />} tint="bg-sky-50 text-sky-600 dark:bg-sky-500/15 dark:text-sky-300"
          sub={<Delta pct={k?.mom_pct ?? null} suffix="vs month before" />} />
        <Kpi label="Annual run-rate (ARR)" value={k ? compactInr(k.arr) : "—"}
          icon={<TargetIcon className="h-5 w-5" />} tint="bg-violet-50 text-violet-600 dark:bg-violet-500/15 dark:text-violet-300"
          sub={<span className="text-xs text-slate-500">Last full month × 12</span>} />
        <Kpi label={`FY ${ov?.fy ?? ""} to date`} value={k ? formatInr(k.fy_to_date) : "—"}
          icon={<LayersIcon className="h-5 w-5" />} tint="bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300"
          sub={<Delta pct={k?.fy_growth_pct ?? null} suffix="vs same time last FY" />} />
        <Kpi label="Active customers" value={k?.active_customers ?? "—"}
          icon={<UsersIcon className="h-5 w-5" />} tint="bg-orange-50 text-orange-600 dark:bg-orange-500/15 dark:text-orange-300"
          sub={k ? <span className="text-xs font-semibold text-emerald-600 dark:text-emerald-400">+{k.new_customers_fy} new this FY</span> : undefined} />
        <Kpi label="Billable pages this month" value={k?.pages_month != null ? k.pages_month.toLocaleString("en-IN") : "—"}
          icon={<DocumentIcon className="h-5 w-5" />} tint="bg-teal-50 text-teal-600 dark:bg-teal-500/15 dark:text-teal-300"
          sub={<Delta pct={k?.pages_pct ?? null} suffix="vs last month (so far)" />} />
        <Kpi label="Revenue per customer" value={k?.arpc != null ? formatInr(Math.round(k.arpc)) : "—"}
          icon={<BriefcaseIcon className="h-5 w-5" />} tint="bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300"
          sub={<span className="text-xs text-slate-500">Last full month, per paying customer</span>} />
        <Kpi label="Outstanding" value={k ? formatInr(k.outstanding) : "—"}
          icon={<ClockIcon className="h-5 w-5" />} tint="bg-rose-50 text-rose-600 dark:bg-rose-500/15 dark:text-rose-300"
          sub={<span className="text-xs text-slate-500">Unpaid invoices incl. GST</span>} />
      </div>

      {/* Milestones */}
      {ov && ov.milestones.length > 0 && (
        <div className="flex flex-wrap gap-3">
          {ov.milestones.map((m) => (
            <div key={m.key} className="flex items-center gap-2.5 rounded-xl border border-slate-200 bg-gradient-to-br from-white to-slate-50 px-3.5 py-2 shadow-sm dark:border-slate-800 dark:from-slate-900 dark:to-slate-900">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-300">{MILESTONE_ICON[m.key] ?? <CheckBadgeIcon className="h-4 w-4" />}</span>
              <div>
                <p className="text-[11px] uppercase tracking-wide text-slate-500">{m.label}</p>
                <p className="text-sm font-semibold text-slate-900 dark:text-slate-50">
                  {m.amount != null ? formatInr(m.amount) : m.count != null ? m.count.toLocaleString("en-IN") : ""}
                  {m.value ? `${m.amount != null ? " · " : ""}${m.value.length === 7 ? monthLabel(m.value) : new Date(m.value).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })}` : ""}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Revenue analytics */}
      <ChartCard title="Revenue" icon={<ChartBarIcon className="h-5 w-5" />}
        extra={<>
          <Toggle value={revView} onChange={setRevView} options={[["monthly", "Monthly"], ["daily", "Daily"]]} />
          {revView === "monthly" ? (
            <NavButtons label={`FY ${mon?.fy ?? ""}`} onPrev={() => setFy((fy ?? 0) - 1)} onNext={() => setFy((fy ?? 0) + 1)}
              prevDisabled={!mon || fy == null || fy <= (years[0]?.start_year ?? fy)}
              nextDisabled={!mon || fy == null || fy >= (years[years.length - 1]?.start_year ?? fy)} />
          ) : (
            <NavButtons label={monthLabel(month)} onPrev={() => setMonth(shiftMonth(month, -1))} onNext={() => setMonth(shiftMonth(month, 1))}
              prevDisabled={month <= earliest} nextDisabled={month >= curMonth} />
          )}
        </>}
        footer={revView === "monthly"
          ? "Net of GST. Finished months from their invoices; the current month is live. Dashed line = same month last financial year."
          : "Page-usage revenue per day (each customer's month spread over the days by billable pages). The platform fee is monthly and not spread."}>
        {revView === "monthly" ? (
          <>
            <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="text-3xl font-bold tabular-nums text-slate-900 dark:text-slate-50">{mon ? formatInr(mon.totals.revenue) : "—"}</p>
                <p className="text-xs text-slate-500">FY {mon?.fy} revenue · last FY {mon ? formatInr(mon.totals.last_year_revenue) : "—"}{" "}
                  {fyGrowth != null && <Delta pct={fyGrowth} suffix="vs last FY" />}</p>
              </div>
              <Legend items={[...monthlySeries.map((s) => ({ label: s.label, color: s.color })), { label: "Last FY", color: "var(--fv-cmp)", dashed: true }]} />
            </div>
            {mon && <StackedBars labels={mon.months.map((m) => m.label + (m.partial ? "*" : ""))} series={monthlySeries}
              compare={mon.last_year.map((m) => m.revenue)} compareLabel="Same month last FY" format={compactInr}
              tooltipTitle={(i) => `${monthLabel(mon.months[i].month)}${mon.months[i].partial ? " (so far)" : ""}`} emptyText="No revenue in this financial year." />}
          </>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
              <div className="flex flex-wrap gap-6">
                <div>
                  <p className="text-3xl font-bold tabular-nums text-slate-900 dark:text-slate-50">{day?.totals.revenue != null ? formatInr(day.totals.revenue) : "—"}</p>
                  <p className="text-xs text-slate-500">{monthLabel(month)} revenue{day?.partial ? " so far" : ""} (incl. platform fee {day?.totals.platform != null ? formatInr(day.totals.platform) : "—"})</p>
                </div>
                <div>
                  <p className="text-xl font-semibold tabular-nums text-slate-900 dark:text-slate-50">{day?.totals.pages != null ? day.totals.pages.toLocaleString("en-IN") : "—"}</p>
                  <p className="text-xs text-slate-500">billable pages · last month {day?.prev_month_totals.pages?.toLocaleString("en-IN") ?? "—"}</p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <Toggle value={dailyMetric} onChange={setDailyMetric} options={[["revenue", "₹ Revenue"], ["pages", "Pages"]]} />
              </div>
            </div>
            {day && (dailyMetric === "revenue" ? (
              <StackedBars labels={day.days.map((d) => String(d.day))}
                series={[{ key: "rev", label: "Page-usage revenue", color: SERIES[0], values: day.days.map((d) => d.revenue) }]}
                format={compactInr} tooltipTitle={(i) => new Date(day.days[i].date).toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short" })}
                emptyText="No billable pages in this month." />
            ) : (
              <>
                <div className="mb-2"><Legend items={SHIP.map((s, i) => ({ label: s, color: SERIES[i] }))} /></div>
                <StackedBars labels={day.days.map((d) => String(d.day))}
                  series={SHIP.map((s, i) => ({ key: s, label: s, color: SERIES[i], values: day.days.map((d) => (d.pages == null ? null : (d[s] as number) ?? 0)) }))}
                  format={(v) => num(v)} tooltipTitle={(i) => new Date(day.days[i].date).toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short" })}
                  emptyText="No billable pages in this month." />
              </>
            ))}
          </>
        )}
      </ChartCard>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        {/* Products */}
        <ChartCard title={<>Revenue by product <span className="text-sm font-normal text-slate-500">· FY {prod?.fy ?? ""}</span></>}
          icon={<LayersIcon className="h-5 w-5" />}
          extra={<Toggle value={prodView} onChange={setProdView} options={[["type", "By revenue type"], ["shipment", "By shipment type"]]} />}
          footer={prodView === "shipment" ? "Page-usage revenue split by each customer's billable pages per shipment type." : "Other services = manual invoices (e.g. a separate ERP server)."}>
          {prod && (prodView === "type" ? (
            <Donut centerLabel="FY revenue" format={compactInr}
              items={prod.by_revenue_type.map((r, i) => ({ label: r.key, value: r.revenue, color: SERIES[i] }))} />
          ) : (
            <>
              <div className="mb-2 flex justify-end"><Toggle value={shipMetric} onChange={setShipMetric} options={[["revenue", "₹ Revenue"], ["pages", "Pages"]]} /></div>
              <Donut centerLabel={shipMetric === "revenue" ? "Page revenue" : "Pages"} format={shipMetric === "revenue" ? compactInr : num}
                items={prod.by_shipment_type.map((r, i) => ({ label: r.key, value: shipMetric === "revenue" ? r.revenue : r.pages, color: SERIES[i] }))} />
            </>
          ))}
        </ChartCard>

        {/* Customer growth */}
        <ChartCard title={<>Customer growth <span className="text-sm font-normal text-slate-500">· FY {mon?.fy ?? ""}</span></>}
          icon={<UsersIcon className="h-5 w-5" />}
          footer="Customers counted from their billing start date (or first billable page).">
          {mon && (
            <>
              <LineSeries labels={mon.customers.map((c) => c.label)} values={mon.customers.map((c) => c.customers)} format={(v) => String(Math.round(v))} />
              <div className="mt-3 flex flex-wrap gap-1.5">
                {mon.customers.filter((c) => (c.new ?? 0) > 0).map((c) => (
                  <span key={c.month} className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300">
                    +{c.new} in {c.label}
                  </span>
                ))}
              </div>
            </>
          )}
        </ChartCard>
      </div>

      {/* Pages by shipment type per month */}
      <ChartCard title={<>Billable pages by month <span className="text-sm font-normal text-slate-500">· FY {mon?.fy ?? ""}</span></>}
        icon={<DocumentIcon className="h-5 w-5" />} extra={<Legend items={SHIP.map((s, i) => ({ label: s, color: SERIES[i] }))} />}
        footer="Product usage: every page read and billed, by shipment type.">
        {mon && <StackedBars labels={mon.months.map((m) => m.label)} format={num}
          series={SHIP.map((s, i) => ({ key: s, label: s, color: SERIES[i], values: (mon.by_shipment[s] ?? []).map((x) => (x == null ? null : x.pages)) }))}
          tooltipTitle={(i) => monthLabel(mon.months[i].month)} emptyText="No pages in this financial year." />}
      </ChartCard>

      {/* Customer-wise revenue */}
      <ChartCard title={<>Customer-wise revenue & usage <span className="text-sm font-normal text-slate-500">· {monthLabel(month)}</span></>}
        icon={<BuildingIcon className="h-5 w-5" />}
        extra={<NavButtons label={monthLabel(month)} onPrev={() => setMonth(shiftMonth(month, -1))} onNext={() => setMonth(shiftMonth(month, 1))}
          prevDisabled={month <= earliest} nextDisabled={month >= curMonth} />}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 dark:bg-slate-800/70">
              <tr>
                <th className="w-14 px-3 py-2.5 text-center">S.No</th><th className="px-3 py-2.5">Customer</th>
                <th className="px-3 py-2.5 text-right">Revenue</th><th className="px-3 py-2.5 text-right">Platform</th>
                <th className="px-3 py-2.5 text-right">Usage</th><th className="px-3 py-2.5 text-right">Other</th>
                <th className="px-3 py-2.5 text-right">Pages</th><th className="w-48 px-3 py-2.5">Share</th><th className="px-3 py-2.5">vs last month</th>
              </tr>
            </thead>
            <tbody>
              {cust && cust.length === 0 && <tr><td colSpan={9} className="px-3 py-8 text-center text-slate-500">No revenue in this month.</td></tr>}
              {(cust ?? []).map((r, i) => (
                <tr key={r.tenant_id} className="cursor-pointer border-b border-slate-100 hover:bg-indigo-50/40 dark:border-slate-800 dark:hover:bg-slate-800/40"
                  onClick={() => navigate(`/saas-finance/customers/${r.tenant_id}`)}>
                  <td className="px-3 py-2.5 text-center text-slate-500">{i + 1}</td>
                  <td className="px-3 py-2.5 font-medium text-slate-900 dark:text-slate-50">
                    {r.name}{!r.is_active && <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500 dark:bg-slate-800">Disabled</span>}
                  </td>
                  <td className="px-3 py-2.5 text-right font-semibold tabular-nums text-slate-900 dark:text-slate-50">{formatInr(r.revenue)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-slate-600 dark:text-slate-300">{formatInr(r.platform)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-slate-600 dark:text-slate-300">{formatInr(r.usage)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-slate-600 dark:text-slate-300">{formatInr(r.other)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-slate-600 dark:text-slate-300">{r.pages.toLocaleString("en-IN")}</td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                        <div className="h-full rounded-full" style={{ width: `${r.share_pct}%`, background: SERIES[0] }} />
                      </div>
                      <span className="w-12 text-right text-xs tabular-nums text-slate-600 dark:text-slate-300">{r.share_pct}%</span>
                    </div>
                  </td>
                  <td className="px-3 py-2.5"><Delta pct={r.growth_pct} suffix="" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </ChartCard>
    </div>
  );
}
