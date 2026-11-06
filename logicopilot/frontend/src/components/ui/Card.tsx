import type { HTMLAttributes, ReactNode } from "react";

export function Card({ className = "", ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`rounded-2xl border border-slate-200 bg-white shadow-sm shadow-slate-900/5 dark:border-slate-800 dark:bg-slate-900 ${className}`}
      {...rest}
    />
  );
}

const STAT_CARD_TONES = {
  indigo: "bg-indigo-50 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-400",
  blue: "bg-sky-50 text-sky-600 dark:bg-sky-500/10 dark:text-sky-400",
  emerald: "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400",
  amber: "bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400",
  rose: "bg-rose-50 text-rose-600 dark:bg-rose-500/10 dark:text-rose-400",
  violet: "bg-violet-50 text-violet-600 dark:bg-violet-500/10 dark:text-violet-400",
} as const;

export function StatCard({
  label, value, icon, tone = "indigo", labelExtra,
}: {
  label: string;
  value: string | number;
  icon?: ReactNode;
  /** Defaults to indigo - every existing call site keeps its current look unchanged. */
  tone?: keyof typeof STAT_CARD_TONES;
  /** Small extra content beside the label, e.g. an info tooltip trigger. */
  labelExtra?: ReactNode;
}) {
  return (
    <Card className="flex h-full min-h-[92px] items-center gap-4 p-5">
      {icon && (
        <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ${STAT_CARD_TONES[tone]}`}>
          {icon}
        </div>
      )}
      <div>
        <p className="flex flex-wrap items-center gap-x-1 text-sm leading-tight text-slate-500 dark:text-slate-400">
          {label}
          {labelExtra}
        </p>
        <p className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-50">{value}</p>
      </div>
    </Card>
  );
}
