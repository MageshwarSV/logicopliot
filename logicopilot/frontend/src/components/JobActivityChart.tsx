import { useEffect, useState } from "react";
import { Card } from "./ui/Card";
import * as jobsApi from "../api/jobs";
import type { JobActivityDay } from "../api/jobs";
import * as usersApi from "../api/users";
import type { User } from "../types/auth";

const fieldClass =
  "rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-indigo-400 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

function currentMonthStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-US", { month: "long", year: "numeric" });
}

function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function dayLabel(iso: string): { num: string; mon: string } {
  const d = new Date(`${iso}T00:00:00`);
  return { num: String(d.getDate()).padStart(2, "0"), mon: d.toLocaleDateString("en-US", { month: "short" }) };
}

/** A "nice" axis step (1/2/5 x a power of 10) for a given max value, so the gridlines read as
 *  round numbers (0/50/100/...) instead of whatever the data happens to top out at. Job counts
 *  are always whole numbers, so the step itself is never allowed below 1 - without that floor,
 *  a quiet day (max count of 1) produced a fractional step like 0.2, labelling the axis
 *  0/0.2/0.4/0.6/0.8/1 for a value that can only ever be a whole job count. */
function niceTicks(max: number): { step: number; top: number; ticks: number[] } {
  const safeMax = Math.max(1, Math.ceil(max));
  const rawStep = Math.max(1, Math.ceil(safeMax / 5));
  const pow10 = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / pow10;
  const step = Math.max(1, (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * pow10);
  const top = Math.ceil(safeMax / step) * step;
  const ticks: number[] = [];
  for (let v = 0; v <= top; v += step) ticks.push(v);
  return { step, top, ticks };
}

/** Daily New Jobs vs ERP Submitted Jobs for a month - two bars per day, the exact count shown
 *  above each bar, filterable to one operator or every operator ("All Users"). Deliberately a
 *  plain <svg>, no charting library - matches this codebase's own convention (see
 *  SpendAnalyticsPage's BarChart). A 31-day month gets unreadably thin bars if squeezed into a
 *  fixed width, so the chart itself scrolls horizontally instead - that native scrollbar IS the
 *  "bottom slider to scroll/navigate across the monthly data" the spec asks for. */
export function JobActivityChart() {
  // Defaults to "All Users" - confirmed with the user (2026-11-03): defaulting to whoever is
  // signed in made the chart's own total look wrong at a glance (an operator opening their
  // dashboard saw a Nov 3 bar of 3, while the Jobs list's own "ERP Submitted Jobs" badge for
  // the same day read 5 - the other 2 just belonged to a different operator, filtered out by
  // this default). All Users is also what every other count on this page (the sidebar badges)
  // already shows unfiltered, so this keeps the chart consistent with them by default.
  const [month, setMonth] = useState(currentMonthStr());
  const [assignedTo, setAssignedTo] = useState("");
  const [operators, setOperators] = useState<User[]>([]);
  const [days, setDays] = useState<JobActivityDay[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    usersApi.listUsers().then(
      (users) => setOperators(users.filter((u) => u.role === "operator" && u.is_active)),
      () => setOperators([]),
    );
  }, []);

  useEffect(() => {
    let cancelled = false;
    // "Loading…" only covers the FIRST fetch after a month/Assigned To change - the 30s
    // background refresh (same interval the sidebar badges already poll at, AppShell.tsx)
    // updates the bars silently, and keeps the last good data on screen rather than wiping it
    // to "No data" if one refresh happens to fail.
    let first = true;
    const load = () => {
      if (first) setLoading(true);
      jobsApi
        .getJobActivityChart(month, assignedTo || undefined)
        .then((d) => { if (!cancelled) setDays(d); })
        .catch(() => { if (!cancelled && first) setDays([]); })
        .finally(() => { if (!cancelled) { setLoading(false); first = false; } });
    };
    load();
    const t = window.setInterval(load, 30000);
    return () => { cancelled = true; window.clearInterval(t); };
  }, [month, assignedTo]);

  const isCurrentMonth = month === currentMonthStr();
  const max = Math.max(1, ...days.flatMap((d) => [d.new_jobs, d.erp_submitted_jobs]));
  const { top: niceMax, ticks } = niceTicks(max);
  const totalNew = days.reduce((sum, d) => sum + d.new_jobs, 0);
  const totalSubmitted = days.reduce((sum, d) => sum + d.erp_submitted_jobs, 0);

  const dayWidth = 72;
  const barWidth = 22;
  const barGap = 6;
  const chartHeight = 520;
  const topPadding = 28;
  const bottomPadding = 40;
  const plotHeight = chartHeight - topPadding - bottomPadding;
  const innerWidth = Math.max(days.length * dayWidth, dayWidth);
  const yFor = (v: number) => topPadding + (plotHeight - (v / niceMax) * plotHeight);

  return (
    <Card className="flex min-h-[calc(100vh-11rem)] flex-col p-5">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-slate-50">Job Activity</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400">Job activity – New Jobs and ERP Submitted Jobs.</p>
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">Month</label>
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="rounded-lg border border-slate-200 px-2 py-2 text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
                onClick={() => setMonth((m) => shiftMonth(m, -1))}
                aria-label="Previous month"
              >
                ‹
              </button>
              <span className={`${fieldClass} min-w-[150px] text-center`}>{monthLabel(month)}</span>
              <button
                type="button"
                className="rounded-lg border border-slate-200 px-2 py-2 text-slate-500 hover:bg-slate-50 disabled:opacity-30 dark:border-slate-700 dark:hover:bg-slate-800"
                onClick={() => setMonth((m) => shiftMonth(m, 1))}
                disabled={isCurrentMonth}
                aria-label="Next month"
              >
                ›
              </button>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-500">Assigned To</label>
            <select className={fieldClass} value={assignedTo} onChange={(e) => setAssignedTo(e.target.value)}>
              <option value="">All Users</option>
              {operators.map((o) => (
                <option key={o.id} value={o.full_name}>{o.full_name}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      <div className="mb-3 flex items-center gap-4 text-sm">
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded-sm bg-[#3b82f6]" /> New Jobs
          <span className="font-semibold text-slate-700 dark:text-slate-200">{totalNew}</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-3 w-3 rounded-sm bg-[#22c55e]" /> ERP Submitted Jobs
          <span className="font-semibold text-slate-700 dark:text-slate-200">{totalSubmitted}</span>
        </span>
      </div>

      {loading ? (
        <div className="flex flex-1 items-center justify-center text-sm text-slate-400">Loading…</div>
      ) : days.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-sm text-slate-400">No data for this month.</div>
      ) : (
        <div className="flex flex-1 gap-1">
          {/* Y-axis labels sit outside the scroll area so they stay fixed while the chart scrolls. */}
          <svg width={32} height={chartHeight} className="flex-shrink-0">
            {ticks.map((t) => (
              <text key={t} x={28} y={yFor(t) + 3} textAnchor="end" fontSize={10} className="fill-slate-400">
                {t}
              </text>
            ))}
          </svg>
          <div className="overflow-x-auto">
            <svg width={innerWidth} height={chartHeight} role="img" aria-label="Daily job activity">
              {ticks.map((t) => (
                <line
                  key={t} x1={0} x2={innerWidth} y1={yFor(t)} y2={yFor(t)}
                  stroke="currentColor" className="text-slate-100 dark:text-slate-800"
                />
              ))}
              {days.map((d, i) => {
                const { num, mon } = dayLabel(d.date);
                const x = i * dayWidth + (dayWidth - (barWidth * 2 + barGap)) / 2;
                const hNew = (d.new_jobs / niceMax) * plotHeight;
                const hSub = (d.erp_submitted_jobs / niceMax) * plotHeight;
                const yNew = topPadding + plotHeight - hNew;
                const ySub = topPadding + plotHeight - hSub;
                return (
                  <g key={d.date}>
                    {d.new_jobs > 0 && (
                      <text x={x + barWidth / 2} y={yNew - 4} textAnchor="middle" fontSize={10} fontWeight={600} className="fill-slate-700 dark:fill-slate-200">
                        {d.new_jobs}
                      </text>
                    )}
                    <rect x={x} y={yNew} width={barWidth} height={hNew} fill="#3b82f6" rx={2}>
                      <title>{`${d.date}: ${d.new_jobs} new jobs`}</title>
                    </rect>
                    {d.erp_submitted_jobs > 0 && (
                      <text x={x + barWidth + barGap + barWidth / 2} y={ySub - 4} textAnchor="middle" fontSize={10} fontWeight={600} className="fill-slate-700 dark:fill-slate-200">
                        {d.erp_submitted_jobs}
                      </text>
                    )}
                    <rect x={x + barWidth + barGap} y={ySub} width={barWidth} height={hSub} fill="#22c55e" rx={2}>
                      <title>{`${d.date}: ${d.erp_submitted_jobs} ERP submitted jobs`}</title>
                    </rect>
                    <text x={x + barWidth + barGap / 2} y={chartHeight - bottomPadding + 14} textAnchor="middle" fontSize={10} className="fill-slate-400">
                      {num}
                    </text>
                    <text x={x + barWidth + barGap / 2} y={chartHeight - bottomPadding + 26} textAnchor="middle" fontSize={10} className="fill-slate-400">
                      {mon}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
        </div>
      )}
    </Card>
  );
}
