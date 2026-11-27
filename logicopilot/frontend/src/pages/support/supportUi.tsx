/** Shared look for the Production Support pages: period picker (Today by default), stage and
 *  type badges with one colour meaning everywhere (red = failed, green = in the ERP, blue =
 *  working, amber = waiting on a person), tables, pagination, tiles and the activity list. */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { HealthBox, SupportActivity } from "../../api/support";
import { ModeIcon } from "../../components/ModeIcon";
import { CalendarIcon } from "../../components/ui/icons";

// ---------------------------------------------------------------- period

export type Period = "today" | "7d" | "month";
export const DEFAULT_PERIOD: Period = "today";
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
    <div className="inline-flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm dark:border-slate-700 dark:bg-slate-900">
      <CalendarIcon className="ml-2 mr-1 h-4 w-4 text-slate-400" />
      {PERIODS.map((p) => (
        <button key={p.key} type="button" onClick={() => onChange(p.key)}
          className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${value === p.key
            ? "bg-indigo-600 text-white shadow-sm"
            : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
          {p.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- badges

type Tone = "red" | "green" | "blue" | "amber" | "violet" | "slate" | "orange";
const TONE: Record<Tone, string> = {
  red: "bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30",
  green: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30",
  blue: "bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-500/15 dark:text-sky-300 dark:ring-sky-500/30",
  amber: "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30",
  violet: "bg-violet-50 text-violet-700 ring-violet-200 dark:bg-violet-500/15 dark:text-violet-300 dark:ring-violet-500/30",
  orange: "bg-orange-50 text-orange-700 ring-orange-200 dark:bg-orange-500/15 dark:text-orange-300 dark:ring-orange-500/30",
  slate: "bg-slate-100 text-slate-600 ring-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700",
};
const DOT: Record<Tone, string> = {
  red: "bg-rose-500", green: "bg-emerald-500", blue: "bg-sky-500", amber: "bg-amber-500",
  violet: "bg-violet-500", orange: "bg-orange-500", slate: "bg-slate-400",
};
/** The left edge of a table row, so a failed row is red at a glance. */
export const ROW_EDGE: Record<Tone, string> = {
  red: "border-l-rose-500", green: "border-l-emerald-500", blue: "border-l-sky-500", amber: "border-l-amber-400",
  violet: "border-l-violet-500", orange: "border-l-orange-500", slate: "border-l-slate-300 dark:border-l-slate-600",
};

export function Badge({ tone, children, dot = true, title }: { tone: Tone; children: ReactNode; dot?: boolean; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${TONE[tone]}`}>
      {dot && <span className={`h-1.5 w-1.5 rounded-full ${DOT[tone]}`} />}
      {children}
    </span>
  );
}

/** Pipeline stage key -> colour. */
export const STAGE_TONE: Record<string, Tone> = {
  failed: "red", erp_submitted: "green", erp_submission: "blue", ai_processing: "blue",
  waiting_approval: "violet", in_review: "amber", ready_for_review: "amber", received: "slate", other: "slate",
};

export function StageBadge({ stage, label }: { stage: string; label: string }) {
  return <Badge tone={STAGE_TONE[stage] ?? "slate"}>{label}</Badge>;
}

/** Problem kind (Failed / Delayed list) -> colour. */
export const KIND_TONE: Record<string, Tone> = {
  erp_failed: "red", erp_slow: "orange", extraction_failed: "red", extraction_slow: "orange", missing_documents: "amber",
};

export function TypeCell({ type }: { type: string }) {
  if (!type) return <span className="text-slate-400">—</span>;
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-slate-700 dark:text-slate-200">
      <ModeIcon mode={type} className={type.toLowerCase().includes("air") ? "text-sky-600" : "text-indigo-600"} />
      {type}
    </span>
  );
}

export const STATUS_STYLE: Record<HealthBox["status"], { dot: string; pill: string; label: string; tone: Tone }> = {
  ok: { dot: "bg-emerald-500", pill: TONE.green, label: "Healthy", tone: "green" },
  warn: { dot: "bg-amber-500", pill: TONE.amber, label: "Warning", tone: "amber" },
  down: { dot: "bg-rose-500", pill: TONE.red, label: "Down", tone: "red" },
  unknown: { dot: "bg-slate-400", pill: TONE.slate, label: "Unknown", tone: "slate" },
};

// ---------------------------------------------------------------- layout pieces

export function SectionCard({ title, icon, extra, children, className = "", padded = true }: {
  title?: ReactNode; icon?: ReactNode; extra?: ReactNode; children: ReactNode; className?: string; padded?: boolean;
}) {
  return (
    <section className={`overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900 ${className}`}>
      {(title || extra) && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3.5 dark:border-slate-800">
          <h2 className="flex items-center gap-2 font-semibold text-slate-900 dark:text-slate-50">
            {icon && <span className="text-indigo-600 dark:text-indigo-300">{icon}</span>}
            {title}
          </h2>
          {extra && <div className="flex items-center gap-3">{extra}</div>}
        </div>
      )}
      <div className={padded ? "p-5" : ""}>{children}</div>
    </section>
  );
}

export function StatTile({ label, value, sub, icon, tone }: { label: string; value: ReactNode; sub?: ReactNode; icon: ReactNode; tone: Tone }) {
  return (
    <div className="flex items-center gap-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ring-1 ring-inset ${TONE[tone]}`}>{icon}</div>
      <div className="min-w-0">
        <p className="truncate text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</p>
        <p className="text-2xl font-bold tabular-nums text-slate-900 dark:text-slate-50">{value}</p>
        {sub && <p className="text-xs text-slate-500 dark:text-slate-400">{sub}</p>}
      </div>
    </div>
  );
}

/** Table classes - one look for every support table. */
export const T = {
  wrap: "overflow-x-auto",
  table: "w-full text-sm",
  thead: "sticky top-0 z-10 bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 dark:bg-slate-800/80 dark:text-slate-400",
  th: "px-4 py-3",
  thRight: "px-4 py-3 text-right",
  row: "border-l-4 border-b border-slate-100 transition-colors hover:bg-indigo-50/40 dark:border-b-slate-800 dark:hover:bg-slate-800/40",
  td: "px-4 py-3 align-middle",
  tdRight: "px-4 py-3 text-right align-middle tabular-nums",
  link: "font-semibold text-indigo-700 hover:underline dark:text-indigo-300",
};

export function EmptyRow({ cols, loading, text }: { cols: number; loading: boolean; text: string }) {
  return (
    <tr>
      <td colSpan={cols} className="px-4 py-12 text-center text-sm text-slate-500">
        {loading ? <span className="inline-flex items-center gap-2"><span className="h-2 w-2 animate-pulse rounded-full bg-indigo-500" />Loading…</span> : text}
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------- pagination

export const PAGE_SIZES = [25, 50, 100];

export function usePaged<T>(items: T[], resetKey: unknown, initialSize = 25) {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(initialSize);
  useEffect(() => { setPage(1); }, [resetKey, size]);
  const pages = Math.max(1, Math.ceil(items.length / size));
  const safe = Math.min(page, pages);
  const shown = useMemo(() => items.slice((safe - 1) * size, safe * size), [items, safe, size]);
  return { shown, page: safe, pages, size, setPage, setSize, total: items.length };
}

export function Pager({ page, pages, size, total, setPage, setSize }: {
  page: number; pages: number; size: number; total: number; setPage: (p: number) => void; setSize: (s: number) => void;
}) {
  if (total === 0) return null;
  const from = (page - 1) * size + 1;
  const to = Math.min(total, page * size);
  const btn = "rounded-lg border border-slate-200 px-2.5 py-1 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800";
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 text-sm text-slate-600 dark:border-slate-800 dark:text-slate-300">
      <span>Showing <b className="tabular-nums">{from}–{to}</b> of <b className="tabular-nums">{total}</b></span>
      <div className="flex items-center gap-2">
        <select value={size} onChange={(e) => setSize(Number(e.target.value))}
          className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-900">
          {PAGE_SIZES.map((n) => <option key={n} value={n}>{n} / page</option>)}
        </select>
        <button type="button" className={btn} disabled={page <= 1} onClick={() => setPage(1)}>«</button>
        <button type="button" className={btn} disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Prev</button>
        <span className="tabular-nums">Page {page} / {pages}</span>
        <button type="button" className={btn} disabled={page >= pages} onClick={() => setPage(page + 1)}>Next ›</button>
        <button type="button" className={btn} disabled={page >= pages} onClick={() => setPage(pages)}>»</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- formatting

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

// ---------------------------------------------------------------- activity

const KIND_STYLE: Record<SupportActivity["kind"], { icon: string; tone: Tone; label: string }> = {
  note: { icon: "✎", tone: "slate", label: "Note" },
  customer_call: { icon: "☎", tone: "orange", label: "Customer call" },
  handled: { icon: "✓", tone: "green", label: "Handled" },
  retry_erp: { icon: "↻", tone: "blue", label: "Retry ERP" },
  retry_extraction: { icon: "↻", tone: "violet", label: "Retry extraction" },
  alert: { icon: "!", tone: "red", label: "Alert" },
};

export function ActivityList({ items, empty = "Nothing logged yet." }: { items: SupportActivity[]; empty?: string }) {
  if (items.length === 0) return <p className="py-6 text-center text-sm text-slate-500">{empty}</p>;
  return (
    <ul className="divide-y divide-slate-100 dark:divide-slate-800">
      {items.map((a) => {
        const k = KIND_STYLE[a.kind] ?? KIND_STYLE.note;
        return (
          <li key={a.id} className="flex gap-3 py-3">
            <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-bold ring-1 ring-inset ${TONE[k.tone]}`} title={k.label}>{k.icon}</span>
            <div className="min-w-0 flex-1">
              <p className="text-sm text-slate-800 dark:text-slate-100">
                {a.reference && a.job_id ? (
                  <Link to={`/jobs/${a.job_id}`} className="mr-1 font-semibold text-indigo-700 hover:underline dark:text-indigo-300">{a.reference}</Link>
                ) : null}
                {a.text.replace(a.reference ? `${a.reference} - ` : "", "")}
              </p>
              <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <Badge tone={k.tone} dot={false}>{k.label}</Badge>
                {a.client && <span>{a.client}</span>}
                {a.by && <span>· {a.by}</span>}
              </p>
            </div>
            <span className="shrink-0 whitespace-nowrap text-xs text-slate-500">{formatWhen(a.at)}</span>
          </li>
        );
      })}
    </ul>
  );
}
