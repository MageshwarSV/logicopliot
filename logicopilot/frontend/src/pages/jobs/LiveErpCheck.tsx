/** P4 - while the operator reviews: the values the ERP would refuse, from the same check that
 *  runs before the ERP (backend app/core/erp_rules.py via GET /jobs/{id}/erp-check). Shown on
 *  Required Details Review and ERP Submission, re-checked after every save, so a wrong value
 *  is fixed here instead of stopping the submit. */
import type { ErpHelpItem } from "../../types/jobs";

export function LiveErpCheck({ items, onFix }: { items: ErpHelpItem[]; onFix?: (item: ErpHelpItem) => void }) {
  if (items.length === 0) return null;
  return (
    <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm dark:border-amber-500/30 dark:bg-amber-500/10">
      <p className="font-semibold text-amber-900 dark:text-amber-200">
        ⚠ ERP will refuse {items.length === 1 ? "this value" : `these ${items.length} values`} — fix before submitting
      </p>
      <ul className="mt-2 space-y-1.5">
        {items.map((it, i) => (
          <li key={i} className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-amber-900 dark:text-amber-100">
              <b>{it.display}</b>
              {it.where ? <span className="text-xs text-amber-700 dark:text-amber-300"> · {it.where}</span> : null}
              {" — "}
              {it.sent.length ? `"${it.sent.join('", "')}"` : "(empty)"} → {it.needs}
              {it.suggest ? <span className="font-semibold"> Try {it.suggest}.</span> : null}
            </span>
            {it.fixable && onFix ? (
              <button type="button" onClick={() => onFix(it)} className="rounded-md bg-amber-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-amber-500">
                Fix →
              </button>
            ) : !it.fixable ? (
              <span className="text-xs text-amber-800 dark:text-amber-300">{it.support_reason} Contact Cargora support.</span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
