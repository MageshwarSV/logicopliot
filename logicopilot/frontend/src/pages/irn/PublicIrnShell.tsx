import type { ReactNode } from "react";

/** Visual-only shell shared by the two standalone IRN Pending pages - the same sidebar +
 *  sticky header layout the real AppShell uses, hand-rolled here on purpose rather than
 *  imported: AppShell's rail carries live links to /operator, /jobs, etc. and a "Log out"
 *  button, all of which assume a real logged-in session. A visitor on this page never has
 *  one, so none of that belongs here - just the look. */
export function PublicIrnShell({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="flex h-svh overflow-hidden bg-slate-50 dark:bg-slate-950">
      <aside className="flex h-svh w-64 shrink-0 flex-col border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center gap-2.5 px-6 py-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 text-sm font-bold text-white">
            C
          </div>
          <span className="text-lg font-semibold tracking-tight text-slate-900 dark:text-slate-50">Cargora</span>
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-4">
          <div className="rounded-lg bg-slate-50 px-3 py-2 text-sm font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-200">
            IRN Document Process
          </div>
        </div>

        <div className="border-t border-slate-200 p-4 text-xs text-slate-400 dark:border-slate-800 dark:text-slate-500">
          Shared link — no login required.
        </div>
      </aside>

      <div className="flex-1 overflow-y-auto">
        <header className="sticky top-0 z-20 flex items-center justify-between gap-4 border-b border-slate-200 bg-white/90 px-8 py-4 backdrop-blur-sm dark:border-slate-800 dark:bg-slate-900/90">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-slate-900 dark:text-slate-50">{title}</h1>
            {subtitle && <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
          </div>
        </header>
        <main className="p-8">{children}</main>
      </div>
    </div>
  );
}
