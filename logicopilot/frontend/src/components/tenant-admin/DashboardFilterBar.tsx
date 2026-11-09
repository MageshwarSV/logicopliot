import { useEffect, useState } from "react";
import * as onboardingApi from "../../api/onboarding";

export interface DashboardRange {
  /** First day of the selected month, or (for the current month) still the 1st - dateTo is
   *  what actually stops at today. YYYY-MM-DD. */
  dateFrom: string;
  /** Last day of the selected month, or today for the current month (never projects into the
   *  future). YYYY-MM-DD. */
  dateTo: string;
  mode: string;
}

/** Local YYYY-MM-DD - NOT d.toISOString().slice(0, 10), which converts to UTC first and
 *  reads back a day early for any viewer east of UTC (IST included) once the local date's
 *  own midnight has already rolled past into the next UTC day. */
export function localIso(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function currentMonthStr(): string {
  return localIso(new Date()).slice(0, 7);
}

function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** The DashboardRange for calendar month `month` (YYYY-MM) - dateTo stops at today rather
 *  than running past it when `month` is the current, still-running month. */
function rangeForMonth(month: string, mode: string): DashboardRange {
  const [y, m] = month.split("-").map(Number);
  const first = new Date(y, m - 1, 1);
  const lastOfMonth = new Date(y, m, 0);
  const today = new Date();
  const isCurrentMonth = month === currentMonthStr();
  return {
    dateFrom: localIso(first),
    dateTo: localIso(isCurrentMonth && today < lastOfMonth ? today : lastOfMonth),
    mode,
  };
}

/** Shared month + shipment-mode filter for every Executive Dashboard page (Overview, Data
 *  Accuracy, Job Processing Time, Workforce Productivity) - one control so the pages can
 *  never drift into showing different date conventions for what looks like the same picker.
 *  Month navigation (not a free date range) to match the Billing & Usage page's own
 *  convention; the shipment-mode list is this tenant's own configured templates, not the
 *  full global MODES list - a tenant with only a Sea Import template has no reason to see
 *  Road Export/Train Import/etc. in its own dashboard filter. */
export function DashboardFilterBar({
  value, onChange,
}: {
  value: DashboardRange;
  onChange: (next: DashboardRange) => void;
}) {
  const month = value.dateFrom.slice(0, 7);
  const isCurrentMonth = month === currentMonthStr();
  const [modes, setModes] = useState<string[]>([]);

  useEffect(() => {
    onboardingApi
      .listGroups()
      .then((groups) => {
        const seen = new Set<string>();
        for (const g of groups) if (g.mode) seen.add(g.mode);
        setModes([...seen].sort());
      })
      .catch(() => setModes([]));
  }, []);

  function changeMonth(delta: number) {
    onChange(rangeForMonth(shiftMonth(month, delta), value.mode));
  }

  return (
    <div className="mb-5 flex flex-wrap items-center gap-2">
      <div className="flex items-center gap-1">
        <button
          type="button"
          className="rounded-lg border border-slate-200 px-2 py-1.5 text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
          onClick={() => changeMonth(-1)}
          aria-label="Previous month"
        >
          ‹
        </button>
        <span className="min-w-[150px] rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-center text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200">
          {monthLabel(month)}
        </span>
        <button
          type="button"
          className="rounded-lg border border-slate-200 px-2 py-1.5 text-slate-500 hover:bg-slate-50 disabled:opacity-30 dark:border-slate-700 dark:hover:bg-slate-800"
          onClick={() => changeMonth(1)}
          disabled={isCurrentMonth}
          aria-label="Next month"
        >
          ›
        </button>
      </div>
      <select
        value={value.mode}
        onChange={(e) => onChange({ ...value, mode: e.target.value })}
        className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
      >
        <option value="">All Shipment Types</option>
        {modes.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    </div>
  );
}

/** This calendar month so far - matches the Billing & Usage page's own default. */
export function defaultDashboardRange(): DashboardRange {
  return rangeForMonth(currentMonthStr(), "");
}
