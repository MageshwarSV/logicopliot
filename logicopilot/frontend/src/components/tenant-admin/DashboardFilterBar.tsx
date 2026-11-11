import { useEffect, useState } from "react";
import * as onboardingApi from "../../api/onboarding";
import * as tenantsApi from "../../api/tenants";

export interface DashboardRange {
  /** YYYY-MM-DD, inclusive. */
  dateFrom: string;
  /** YYYY-MM-DD, inclusive - never projects into the future. */
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

type QuickKey = "today" | "yesterday" | "7" | "30" | "this_month" | "prev_month" | "all" | "custom";

/** What each named range actually means, as two dates - same convention (and the same
 *  reasoning for using the Date constructor's own month rollover rather than day arithmetic)
 *  as the Jobs list's own Quick filter, so the two screens can never show different windows
 *  for what reads as the same label. "All time" is a real, explicit (not omitted) range: the
 *  dashboard endpoints default an omitted date_from/date_to to "this month so far", so leaving
 *  the params out would silently turn "All time" back into "This month".
 *
 *  `allFrom` is this tenant's own first-ever completed job's date (fetched once, see
 *  earliestDate below) - "All time" means exactly that, not an arbitrary fixed floor. Falls
 *  back to today (an empty, one-day window) only before that fetch has resolved; the backend's
 *  own 366-day range cap means a guessed floor far in the past fails outright instead of
 *  quietly showing the wrong window, so "not loaded yet" is the safer default to render while
 *  waiting, never a guess. */
function rangeFor(key: QuickKey, allFrom: string | null): { from: string; to: string } {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const back = (days: number) => {
    const d = new Date(today);
    d.setDate(d.getDate() - days);
    return d;
  };
  switch (key) {
    case "today":
      return { from: localIso(today), to: localIso(today) };
    case "yesterday":
      return { from: localIso(back(1)), to: localIso(back(1)) };
    case "7":
      return { from: localIso(back(6)), to: localIso(today) };
    case "30":
      return { from: localIso(back(29)), to: localIso(today) };
    case "this_month":
      return { from: localIso(new Date(today.getFullYear(), today.getMonth(), 1)), to: localIso(today) };
    case "prev_month":
      return {
        from: localIso(new Date(today.getFullYear(), today.getMonth() - 1, 1)),
        to: localIso(new Date(today.getFullYear(), today.getMonth(), 0)),
      };
    case "all":
      return { from: allFrom ?? localIso(today), to: localIso(today) };
    default:
      return { from: "", to: "" };
  }
}

/** Which named key, if any, the CURRENT dateFrom/dateTo matches - so the dropdown reopens
 *  showing the right label instead of always resetting to "custom" after a parent re-renders
 *  with a restored range. */
function quickKeyFor(dateFrom: string, dateTo: string, allFrom: string | null): QuickKey {
  for (const key of ["today", "yesterday", "7", "30", "this_month", "prev_month", "all"] as const) {
    const r = rangeFor(key, allFrom);
    if (r.from === dateFrom && r.to === dateTo) return key;
  }
  return "custom";
}

/** Shared date-range + shipment-mode filter for every Executive Dashboard page (Overview, Data
 *  Accuracy, Job Processing Time, Workforce Productivity) - one control so the pages can never
 *  drift into showing different date conventions for what looks like the same picker. Same
 *  Quick filter options (and the same From/To boxes underneath) as the Jobs list's own filter,
 *  by request - the shipment-mode list is this tenant's own configured templates, not the full
 *  global MODES list, since a tenant with only a Sea Import template has no reason to see Road
 *  Export/Train Import/etc. in its own dashboard filter. */
export function DashboardFilterBar({
  value, onChange, tenantId,
}: {
  value: DashboardRange;
  onChange: (next: DashboardRange) => void;
  /** Fetches this tenant's own first-ever completed job's date once, so "All time" means
   *  exactly that instead of an arbitrary fixed floor. */
  tenantId: string;
}) {
  const [modes, setModes] = useState<string[]>([]);
  const [allFrom, setAllFrom] = useState<string | null>(null);

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

  useEffect(() => {
    if (!tenantId) return;
    tenantsApi.getEarliestCompletedJobDate(tenantId).then(setAllFrom).catch(() => setAllFrom(null));
  }, [tenantId]);

  const quick = quickKeyFor(value.dateFrom, value.dateTo, allFrom);

  const fieldClass =
    "rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 focus:border-indigo-400 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

  return (
    <div className="mb-5 flex flex-wrap items-end gap-3">
      <div>
        <label className="mb-1 block text-xs font-medium text-slate-500">Quick filter</label>
        <select
          className={fieldClass}
          value={quick}
          onChange={(e) => {
            // Fills From/To instead of blanking them, so the admin can SEE the window being
            // applied and can then nudge either end by hand without first working out what
            // "Last 30 days" started on.
            const { from, to } = rangeFor(e.target.value as QuickKey, allFrom);
            onChange({ ...value, dateFrom: from, dateTo: to });
          }}
        >
          <option value="today">Today</option>
          <option value="yesterday">Yesterday</option>
          <option value="7">Last 7 days</option>
          <option value="30">Last 30 days</option>
          <option value="this_month">This month</option>
          <option value="prev_month">Previous month</option>
          <option value="all">All time</option>
          {quick === "custom" && <option value="custom">Custom range</option>}
        </select>
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-slate-500">From</label>
        {/* Typing a date of your own stops the dropdown claiming a named range it no longer
            describes - the dashboard refreshes off these two boxes either way. */}
        <input
          type="date"
          className={fieldClass}
          value={value.dateFrom}
          onChange={(e) => onChange({ ...value, dateFrom: e.target.value })}
        />
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-slate-500">To</label>
        <input
          type="date"
          className={fieldClass}
          value={value.dateTo}
          max={localIso(new Date())}
          onChange={(e) => onChange({ ...value, dateTo: e.target.value })}
        />
      </div>
      <select
        value={value.mode}
        onChange={(e) => onChange({ ...value, mode: e.target.value })}
        className={fieldClass}
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
  const { from, to } = rangeFor("this_month", null);
  return { dateFrom: from, dateTo: to, mode: "" };
}
