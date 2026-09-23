import type { ReactNode } from "react";

export function Alert({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
      {children}
    </div>
  );
}
