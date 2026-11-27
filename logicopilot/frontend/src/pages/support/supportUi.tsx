/** Small pieces shared by the Production Support pages. */
import { Link } from "react-router-dom";
import type { HealthBox, SupportActivity } from "../../api/support";

export type Period = "today" | "7d" | "month";
export const PERIODS: { key: Period; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "7d", label: "Last 7 days" },
  { key: "month", label: "This month" },
];

function istToday(): Date {
  const now = new Date();
  return new Date(now.getTime() + (now.getTimezoneOffset() + 330) * 60000);
}

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function rangeFor(p: Period): [string, string] {
  const t = istToday();
  if (p === "today") return [iso(t), iso(t)];
  if (p === "7d") return [iso(new Date(t.getFullYear(), t.getMonth(), t.getDate() - 6)), iso(t)];
  return [iso(new Date(t.getFullYear(), t.getMonth(), 1)), iso(t)];
}

export function PeriodPicker({ value, onChange }: { value: Period; onChange: (p: Period) => void }) {
  return (
    <div className="flex gap-2">
      {PERIODS.map((p) => (
        <button key={p.key} type="button" onClick={() => onChange(p.key)}
          className={`rounded-lg px-3 py-1.5 text-sm font-medium ${value === p.key ? "bg-indigo-600 text-white" : "border border-slate-200 text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
          {p.label}
        </button>
      ))}
    </div>
  );
}

export const STATUS_STYLE: Record<HealthBox["status"], { dot: string; pill: string; label: string }> = {
  ok: { dot: "bg-emerald-500", pill: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300", label: "Healthy" },
  warn: { dot: "bg-amber-500", pill: "bg-amber-50 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300", label: "Warning" },
  down: { dot: "bg-rose-500", pill: "bg-rose-50 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300", label: "Down" },
  unknown: { dot: "bg-slate-400", pill: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300", label: "Unknown" },
};

export function formatWhen(isoText: string | null): string {
  if (!isoText) return "—";
  const d = new Date(isoText);
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function mins(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v < 1) return `${Math.round(v * 60)} s`;
  if (v < 120) return `${v.toFixed(1)} min`;
  return `${(v / 60).toFixed(1)} h`;
}

const KIND_STYLE: Record<SupportActivity["kind"], { icon: string; cls: string; label: string }> = {
  note: { icon: "✎", cls: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300", label: "Note" },
  customer_call: { icon: "☎", cls: "bg-orange-100 text-orange-700 dark:bg-orange-500/15 dark:text-orange-300", label: "Customer call" },
  handled: { icon: "✓", cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300", label: "Handled" },
  retry_erp: { icon: "↻", cls: "bg-indigo-100 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300", label: "Retry ERP" },
  retry_extraction: { icon: "↻", cls: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300", label: "Retry extraction" },
  alert: { icon: "!", cls: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300", label: "Alert" },
};

export function ActivityList({ items, empty = "Nothing logged yet." }: { items: SupportActivity[]; empty?: string }) {
  if (items.length === 0) return <p className="py-4 text-sm text-slate-500">{empty}</p>;
  return (
    <ul className="divide-y divide-slate-100 dark:divide-slate-800">
      {items.map((a) => {
        const k = KIND_STYLE[a.kind] ?? KIND_STYLE.note;
        return (
          <li key={a.id} className="flex gap-3 py-2.5">
            <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-bold ${k.cls}`} title={k.label}>{k.icon}</span>
            <div className="min-w-0 flex-1">
              <p className="text-sm text-slate-800 dark:text-slate-100">
                {a.reference && a.job_id ? (
                  <Link to={`/jobs/${a.job_id}`} className="mr-1 font-semibold text-indigo-700 hover:underline dark:text-indigo-300">{a.reference}</Link>
                ) : null}
                {a.text.replace(a.reference ? `${a.reference} - ` : "", "")}
              </p>
              <p className="text-xs text-slate-500">
                {k.label}
                {a.client ? ` · ${a.client}` : ""}
                {a.by ? ` · ${a.by}` : ""}
              </p>
            </div>
            <span className="shrink-0 text-xs text-slate-500">{formatWhen(a.at)}</span>
          </li>
        );
      })}
    </ul>
  );
}
