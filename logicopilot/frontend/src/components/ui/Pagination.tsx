import type { ReactNode } from "react";
import { Button } from "./Button";

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  /** What's being counted in "Showing 1–5 of N ___". Defaults to "jobs" so existing call sites keep working. */
  noun?: string;
  /** Extra control shown just before the page buttons, e.g. a rows-per-page select. */
  extra?: ReactNode;
}

/** Windowed page-number control: first, last, current ±2, "…" for gaps. No existing precedent
 *  in this codebase to reuse - every other "page" control here is a plain prev/next pair. */
function pageWindow(current: number, lastPage: number): (number | "…")[] {
  const pages = new Set<number>([1, lastPage, current, current - 1, current + 1, current - 2, current + 2]);
  const sorted = [...pages].filter((p) => p >= 1 && p <= lastPage).sort((a, b) => a - b);
  const out: (number | "…")[] = [];
  let prev = 0;
  for (const p of sorted) {
    if (prev && p - prev > 1) out.push("…");
    out.push(p);
    prev = p;
  }
  return out;
}

export function Pagination({ page, pageSize, total, onPageChange, noun = "jobs", extra }: PaginationProps) {
  if (total === 0) return null;
  const lastPage = Math.max(1, Math.ceil(total / pageSize));
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);

  return (
    <div className="flex flex-col items-center justify-between gap-3 border-t border-slate-100 pt-4 sm:flex-row dark:border-slate-800">
      <p className="text-xs text-slate-500 dark:text-slate-400">
        Showing {from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()} {noun}
      </p>
      <div className="flex flex-wrap items-center justify-center gap-1">
        {extra}
        <Button variant="ghost" size="sm" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
          ‹
        </Button>
        {pageWindow(page, lastPage).map((p, i) =>
          p === "…" ? (
            <span key={`ellipsis-${i}`} className="px-1.5 text-sm text-slate-400">
              …
            </span>
          ) : (
            <button
              key={p}
              type="button"
              onClick={() => onPageChange(p)}
              className={`h-8 min-w-8 rounded-lg px-2 text-sm font-medium transition-colors ${
                p === page
                  ? "bg-indigo-600 text-white"
                  : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
              }`}
            >
              {p}
            </button>
          ),
        )}
        <Button variant="ghost" size="sm" disabled={page >= lastPage} onClick={() => onPageChange(page + 1)}>
          ›
        </Button>
      </div>
    </div>
  );
}
