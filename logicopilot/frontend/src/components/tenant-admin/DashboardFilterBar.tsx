import { MODES } from "../../types/onboarding";
import { CalendarIcon } from "../ui/icons";

export interface DashboardRange {
  dateFrom: string;
  dateTo: string;
  mode: string;
}

/** Shared date-range + shipment-mode filter for every Executive Dashboard page (Overview,
 *  Data Accuracy, Job Processing Time) - one control so the three pages can never drift into
 *  showing different date conventions for what looks like the same picker. */
export function DashboardFilterBar({
  value, onChange, bounds,
}: {
  value: DashboardRange;
  onChange: (next: DashboardRange) => void;
  bounds: { min: string; max: string };
}) {
  return (
    <div className="mb-5 flex flex-wrap items-center gap-2">
      <div className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 dark:border-slate-700 dark:bg-slate-900">
        <CalendarIcon className="h-4 w-4 text-slate-400" />
        <input
          type="date"
          value={value.dateFrom}
          min={bounds.min}
          max={bounds.max}
          onChange={(e) => onChange({ ...value, dateFrom: e.target.value })}
          className="bg-transparent text-sm text-slate-700 dark:text-slate-200"
        />
        <span className="text-slate-400">–</span>
        <input
          type="date"
          value={value.dateTo}
          min={bounds.min}
          max={bounds.max}
          onChange={(e) => onChange({ ...value, dateTo: e.target.value })}
          className="bg-transparent text-sm text-slate-700 dark:text-slate-200"
        />
      </div>
      <select
        value={value.mode}
        onChange={(e) => onChange({ ...value, mode: e.target.value })}
        className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
      >
        <option value="">All Shipment Types</option>
        {MODES.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    </div>
  );
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

/** Trailing 90 days, not "this calendar month so far" - a tenant with only a handful of
 *  completed jobs total (the common case early on) can easily have ZERO of them land in the
 *  first few days of a new month, and a dashboard that opens to an all-zero screen reads as
 *  broken rather than as "nothing happened yet in this narrow window." 90 days is wide enough
 *  to almost always show something real on first open; the date pickers are still right there
 *  to narrow or widen it. */
export function defaultDashboardRange(): DashboardRange {
  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - 89);
  return { dateFrom: localIso(start), dateTo: localIso(today), mode: "" };
}
