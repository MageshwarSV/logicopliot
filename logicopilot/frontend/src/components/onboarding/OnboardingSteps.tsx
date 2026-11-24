import { Link } from "react-router-dom";

// The Customer Onboarding steps, shown at the top of each onboarding page - the same step bar as
// the template wizard (1 Declare → 2 Upload → ...). Steps before the current one read as done.

const STEPS = [
  { n: 1, label: "ERP Field Mapping", path: "/super-admin/onboarding/erp-fields", param: "customer" },
  { n: 2, label: "Document Mapping", path: "/super-admin/onboarding/document-mapping", param: "customer" },
  { n: 3, label: "Screen Designer", path: "/super-admin/onboarding/screen-designer", param: "group" },
  { n: 4, label: "AI Prompts & Validation", path: null, param: "" },
] as const;

export function OnboardingSteps({ current, groupId }: { current: 1 | 2 | 3 | 4; groupId: string }) {
  return (
    <nav aria-label="Onboarding steps" className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2 px-1 py-2">
      {STEPS.map((s, i) => {
        const done = s.n < current;
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
            {s.path && !on ? (
              <Link to={groupId ? `${s.path}?${s.param}=${groupId}` : s.path} className="flex items-center gap-2 rounded-lg px-1 py-0.5 hover:bg-slate-100 dark:hover:bg-slate-800">
                {circle}{text}
              </Link>
            ) : (
              <span className={`flex items-center gap-2 px-1 py-0.5 ${!s.path ? "opacity-60" : ""}`} aria-current={on ? "step" : undefined}>
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
