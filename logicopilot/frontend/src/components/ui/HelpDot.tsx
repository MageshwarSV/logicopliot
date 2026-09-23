import { useEffect, useRef, useState } from "react";

/** A circled "i" next to a feature. Tap or click it and a panel explains what the feature is,
 *  how to use it, and shows a worked example.
 *
 *  Built as click-to-open rather than hover-only on purpose: hover does not exist on a tablet,
 *  and the recorder is used on one. It closes on a second tap, on Escape, or on any click
 *  outside — so it never gets stranded open over the live browser view.
 */
export function HelpDot({
  title,
  what,
  how,
  example,
  className = "",
}: {
  /** Feature name, e.g. "Read value (get_text)" */
  title: string;
  /** One or two sentences: what it does and when you'd reach for it. */
  what: string;
  /** How to use it, as ordered steps. */
  how: string[];
  /** A concrete worked example — real selectors/values, not placeholders. */
  example?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    // `capture` so it fires before the live-browser image's own click handler, which would
    // otherwise record a stray click on the ERP page while you were only closing the help.
    document.addEventListener("mousedown", onDocClick, true);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span ref={wrapRef} className={`relative inline-flex ${className}`}>
      <button
        type="button"
        aria-label={`What is ${title}?`}
        aria-expanded={open}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className={`inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[10px] font-bold leading-none transition-colors ${
          open
            ? "border-indigo-500 bg-indigo-600 text-white"
            : "border-slate-400 text-slate-500 hover:border-indigo-500 hover:text-indigo-600 dark:border-slate-500 dark:text-slate-400"
        }`}
      >
        i
      </button>

      {open && (
        <span
          role="tooltip"
          onClick={(e) => e.stopPropagation()}
          className="absolute left-5 top-0 z-50 w-[22rem] max-w-[80vw] cursor-default rounded-xl border border-slate-200 bg-white p-3 text-left shadow-xl dark:border-slate-700 dark:bg-slate-900"
        >
          <span className="flex items-start justify-between gap-2">
            <span className="text-sm font-semibold text-slate-900 dark:text-slate-50">{title}</span>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="-mt-0.5 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
              aria-label="Close"
            >
              ×
            </button>
          </span>

          <span className="mt-1.5 block text-xs leading-relaxed text-slate-600 dark:text-slate-300">
            {what}
          </span>

          {how.length > 0 && (
            <>
              <span className="mt-2 block text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                How to use
              </span>
              <ol className="mt-1 list-decimal space-y-0.5 pl-4 text-xs text-slate-600 dark:text-slate-300">
                {how.map((h, i) => (
                  <li key={i}>{h}</li>
                ))}
              </ol>
            </>
          )}

          {example && (
            <>
              <span className="mt-2 block text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                Example
              </span>
              <span className="mt-1 block whitespace-pre-wrap rounded-lg bg-slate-50 px-2 py-1.5 font-mono text-[11px] leading-relaxed text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                {example}
              </span>
            </>
          )}
        </span>
      )}
    </span>
  );
}
