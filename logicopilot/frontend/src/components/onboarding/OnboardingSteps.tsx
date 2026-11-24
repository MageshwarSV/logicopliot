import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

const CUSTOMER_KEY = "onboarding.customer";

/** The customer template being onboarded - chosen once in Step 0 and carried through every
 *  step: from the page's own link (?customer= / ?group=), else the one chosen last in this tab. */
export function useOnboardingCustomer(param: "customer" | "group" = "customer"): string {
  const [params] = useSearchParams();
  const fromUrl = params.get(param) ?? "";
  const [stored] = useState<string>(() => {
    try { return window.sessionStorage.getItem(CUSTOMER_KEY) ?? ""; } catch { return ""; }
  });
  useEffect(() => {
    if (fromUrl) { try { window.sessionStorage.setItem(CUSTOMER_KEY, fromUrl); } catch { /* convenience only */ } }
  }, [fromUrl]);
  return fromUrl || stored;
}

export function rememberOnboardingCustomer(groupId: string) {
  try { window.sessionStorage.setItem(CUSTOMER_KEY, groupId); } catch { /* convenience only */ }
}

const FULL_SCREEN_KEY = "onboarding.fullScreen";

/** Full screen, shared by every onboarding step: moving to another step through the step bar
 *  keeps it on, until Close full screen / Esc. Kept for this browser tab only. */
export function useOnboardingFullScreen(): [boolean, (v: boolean | ((cur: boolean) => boolean)) => void] {
  const [on, setOn] = useState<boolean>(() => {
    try { return window.sessionStorage.getItem(FULL_SCREEN_KEY) === "1"; } catch { return false; }
  });
  const set = useCallback((v: boolean | ((cur: boolean) => boolean)) => {
    setOn((cur) => {
      const next = typeof v === "function" ? v(cur) : v;
      try { window.sessionStorage.setItem(FULL_SCREEN_KEY, next ? "1" : "0"); } catch { /* convenience only */ }
      return next;
    });
  }, []);
  return [on, set];
}

// The Customer Onboarding steps, shown at the top of each onboarding page - the same step bar as
// the template wizard (1 Declare → 2 Upload → ...). Steps before the current one read as done.

const STEPS = [
  { n: 0, label: "Choose Template", path: "/super-admin/onboarding/start", param: "customer" },
  { n: 1, label: "ERP Field Mapping", path: "/super-admin/onboarding/erp-fields", param: "customer" },
  { n: 2, label: "Document Mapping", path: "/super-admin/onboarding/document-mapping", param: "customer" },
  { n: 3, label: "Screen Designer", path: "/super-admin/onboarding/screen-designer", param: "group" },
  { n: 4, label: "AI Prompts & Validation", path: null, param: "" },
] as const;

export function OnboardingSteps({ current, groupId }: { current: 0 | 1 | 2 | 3 | 4; groupId: string }) {
  return (
    <nav aria-label="Onboarding steps" className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2 px-1 py-2">
      {STEPS.map((s, i) => {
        // A step after Choose Template is reachable only once a customer is chosen.
        const done = s.n < current;
        const locked = s.n > 0 && !groupId;
        const on = s.n === current;
        const circle = (
          <span
            className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${
              on ? "bg-indigo-600 text-white" : done ? "bg-emerald-500 text-white" : "bg-slate-200 text-slate-500 dark:bg-slate-700 dark:text-slate-300"
            }`}
          >
            {done ? "✓" : s.n}
          </span>
        );
        const text = (
          <span className={`text-sm ${on ? "font-semibold text-slate-900 dark:text-slate-50" : "text-slate-500 dark:text-slate-400"}`}>
            {s.label}
            {!s.path && <span className="ml-1 text-[11px] text-slate-400">(coming next)</span>}
          </span>
        );
        return (
          <div key={s.n} className="flex items-center gap-3">
            {s.path && !on && !locked ? (
              <Link to={groupId ? `${s.path}?${s.param}=${groupId}` : s.path} className="flex items-center gap-2 rounded-lg px-1 py-0.5 hover:bg-slate-100 dark:hover:bg-slate-800">
                {circle}{text}
              </Link>
            ) : (
              <span className={`flex items-center gap-2 px-1 py-0.5 ${!s.path || (locked && !on) ? "opacity-60" : ""}`} aria-current={on ? "step" : undefined}>
                {circle}{text}
              </span>
            )}
            {i < STEPS.length - 1 && <span className="text-slate-300 dark:text-slate-600">→</span>}
          </div>
        );
      })}
    </nav>
  );
}

/** "Customer: Sea Export · 4S Logistics · change" - the customer is chosen in Step 0 only. */
export function OnboardingCustomer({ name, tenant, groupId }: { name?: string; tenant?: string; groupId: string }) {
  return (
    <span className="flex items-center gap-2 text-sm text-slate-500">
      Customer:
      <b className="text-slate-800 dark:text-slate-100">{name || "—"}</b>
      {tenant && <span className="text-slate-400">· {tenant}</span>}
      <Link to={`/super-admin/onboarding/start${groupId ? `?customer=${groupId}` : ""}`} className="text-xs text-indigo-600 hover:underline">change</Link>
    </span>
  );
}
