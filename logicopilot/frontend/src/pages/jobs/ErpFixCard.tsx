/** The ERP refused the Excel import - what to fix, written for the operator who has to fix it.
 *
 *  Replaces the long red line of ERP column names ("ITEMS Country_of_Origin Invoice No. #1
 *  Product No. #1 Entry should be of 2 characters | ...") that made operators phone support.
 *  One row per Cargora box (two ERP columns fed by one box are one fix), what was sent, what
 *  the ERP wants, a Fix button that opens the box outlined in red, a tick once it is changed,
 *  and Retry. A column no Cargora box feeds says plainly that it needs Cargora support.
 *  Built from job.erp_help (backend erp_rejection.py). */
import { useMemo, useState } from "react";
import type { ErpHelp, ErpHelpItem, JobFieldValue } from "../../types/jobs";

function currentValue(fv: JobFieldValue | undefined): string {
  return ((fv?.corrected_value ?? fv?.extracted_value ?? fv?.value) || "").trim();
}

/** Changed since the failed run: every box of the item now holds a value that was not sent. */
function isFixed(item: ErpHelpItem, byId: Map<string, JobFieldValue>): boolean {
  if (!item.fixable || item.field_value_ids.length === 0) return false;
  const sent = new Set(item.sent.map((s) => s.trim()));
  return item.field_value_ids.every((id) => {
    const fv = byId.get(id);
    const v = currentValue(fv);
    if (!v) return false;
    return sent.size > 0 ? !sent.has(v) : !!(fv?.corrected_value ?? "").trim();
  });
}

export function ErpFixCard({
  help,
  jobReference,
  fieldValues,
  onFix,
  onRetry,
  retryBusy,
  onOpenExcel,
  onResetExcel,
}: {
  help: ErpHelp;
  jobReference: string;
  fieldValues: JobFieldValue[];
  onFix?: (item: ErpHelpItem) => void;
  onRetry?: () => void;
  retryBusy?: boolean;
  onOpenExcel?: () => void;
  onResetExcel?: () => void;
}) {
  const [showRaw, setShowRaw] = useState(false);
  const [copied, setCopied] = useState(false);
  const byId = useMemo(() => new Map(fieldValues.map((fv) => [fv.id, fv])), [fieldValues]);
  const items = help.items ?? [];
  const fixable = items.filter((i) => i.fixable);
  const support = items.filter((i) => !i.fixable);
  const done = fixable.filter((i) => isFixed(i, byId)).length;
  const allDone = fixable.length > 0 && done === fixable.length && support.length === 0;
  const n = items.length;
  const preCheck = help.kind === "pre_check";
  // The ERP's own screen refused ONE value (its dropdown has no such option), rather than
  // its Excel import refusing a table of them. Same card, different words.
  const fieldRejected = help.kind === "field_rejected";

  const copyForSupport = async () => {
    const text = [
      `Job ${jobReference}: ERP did not accept the Excel import.`,
      ...items.map(
        (i) =>
          `- ${i.display}${i.where ? ` (${i.where})` : ""}: sent ${i.sent.map((s) => `"${s}"`).join(", ") || "(empty)"}; ERP says: ${i.rules.join(" / ")}${i.fixable ? "" : " [cannot be fixed on the job]"}`,
      ),
      "",
      "ERP message:",
      ...(help.erp_rows ?? []).map((r) => `${r.sheet} | ${r.column} | ${r.text}`),
    ].join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2500);
    } catch {
      window.prompt("Copy this and send it to Cargora support:", text);
    }
  };

  return (
    <div className="rounded-xl border border-rose-200 bg-white p-4 text-slate-800 shadow-sm dark:border-rose-500/30 dark:bg-slate-900 dark:text-slate-100">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-base font-semibold text-slate-900 dark:text-slate-50">
            {preCheck
              ? "Not sent to ERP - ERP would refuse these values"
              : fieldRejected
                ? "The ERP would not accept one of the values"
                : "ERP did not accept this job"}
          </p>
          <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-300">
            {preCheck
              ? "Cargora checked the ERP Excel before sending it. "
              : fieldRejected
                ? "Nothing was saved in ERP. The ERP kept clearing this box, because its own list has no such value. "
                : "Nothing was saved in ERP. "}
            {fixable.length > 0 ? "Fix the detail" + (n === 1 ? "" : "s") + " below and press Retry." : "See below."}
          </p>
        </div>
        <span className="rounded-full bg-rose-100 px-2.5 py-1 text-xs font-semibold text-rose-700 dark:bg-rose-500/20 dark:text-rose-300">
          {allDone ? "Ready to retry" : "Action required"}
        </span>
      </div>

      <p className="mt-3 text-sm font-semibold text-slate-900 dark:text-slate-50">
        {n} detail{n === 1 ? "" : "s"} to correct
        {fixable.length > 0 && (
          <span className="ml-2 text-xs font-medium text-slate-500">
            {done} of {fixable.length} done
          </span>
        )}
      </p>

      {help.excel_edited && (
        <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          This job uses an Excel that was edited by hand, so changes on the job do not reach ERP.
          Fix it in{" "}
          {onOpenExcel ? (
            <button type="button" onClick={onOpenExcel} className="font-semibold underline">Edit ERP Excel</button>
          ) : (
            "Edit ERP Excel"
          )}
          , or fix the boxes below and press{" "}
          {onResetExcel ? (
            <button type="button" onClick={onResetExcel} className="font-semibold underline">Reset to generated</button>
          ) : (
            "Reset to generated"
          )}
          .
        </p>
      )}

      <ul className="mt-2 space-y-2">
        {items.map((item, idx) => {
          const fixed = isFixed(item, byId);
          return (
            <li
              key={idx}
              className={`rounded-lg border px-3 py-2.5 ${
                fixed
                  ? "border-emerald-200 bg-emerald-50/60 dark:border-emerald-500/30 dark:bg-emerald-500/10"
                  : item.fixable
                    ? "border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/60"
                    : "border-amber-200 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-500/10"
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-semibold">
                  {fixed && <span className="mr-1 text-emerald-600">✓</span>}
                  {item.display}
                  {item.erp_columns.length > 1 && (
                    <span className="ml-2 text-xs font-normal text-slate-500">ERP uses it in {item.erp_columns.length} places</span>
                  )}
                </p>
                {item.where && <span className="text-xs text-slate-500 dark:text-slate-400">{item.where}</span>}
              </div>
              <dl className="mt-1 grid grid-cols-[6.5rem_1fr] gap-x-2 gap-y-0.5 text-sm">
                <dt className="text-slate-500 dark:text-slate-400">You sent</dt>
                <dd className="font-medium">
                  {item.sent.length ? item.sent.map((s) => (s ? `"${s}"` : "(empty)")).join(", ") : "—"}
                </dd>
                <dt className="text-slate-500 dark:text-slate-400">ERP needs</dt>
                <dd>
                  {item.needs}
                  {item.example && <span className="text-slate-500 dark:text-slate-400"> — e.g. {item.example}</span>}
                </dd>
                {item.suggest && !fixed && (
                  <>
                    <dt className="text-slate-500 dark:text-slate-400">Try</dt>
                    <dd>
                      <b>{item.suggest}</b>
                      <span className="text-xs text-slate-500"> (check before saving)</span>
                    </dd>
                  </>
                )}
              </dl>
              {item.fixable ? (
                !fixed && onFix && (
                  <div className="mt-2 flex justify-end">
                    <button
                      type="button"
                      onClick={() => onFix(item)}
                      className="rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-500"
                    >
                      Fix →
                    </button>
                  </div>
                )
              ) : item.support_action === "excel" ? (
                /* There is genuinely no box on the job screen for this one, but the ERP
                   Excel has the cell - so send them there instead of to support. */
                <p className="mt-1.5 text-xs text-amber-800 dark:text-amber-200">
                  {item.support_reason} Type it in{" "}
                  {onOpenExcel ? (
                    <button type="button" onClick={onOpenExcel} className="font-semibold underline">
                      Edit ERP Excel
                    </button>
                  ) : (
                    "Edit ERP Excel"
                  )}
                  {item.sheet ? <> — sheet <b>{item.sheet}</b></> : null}
                  {item.erp_columns.length ? <>, column <b>{item.erp_columns[0]}</b></> : null}
                  {item.where ? `, ${item.where}` : ""} — then press Retry.
                </p>
              ) : (
                <p className="mt-1.5 text-xs text-amber-800 dark:text-amber-200">
                  {item.support_reason} You can't change it here — please contact Cargora support.
                </p>
              )}
            </li>
          );
        })}
      </ul>

      {fixable.length > 0 && (
        <div className="mt-3 text-sm">
          <p className="font-semibold text-slate-900 dark:text-slate-50">How to fix</p>
          <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-slate-700 dark:text-slate-300">
            <li>Press <b>Fix</b>. The box opens, outlined in red.</li>
            <li>
              {fieldRejected ? (
                <>Pick a value from the list and press <b>Save</b>.</>
              ) : (
                <>Type the correct value and press <b>Save</b>.</>
              )}
            </li>
            <li>Come back here and press <b>Retry</b>.</li>
          </ol>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <button type="button" onClick={() => setShowRaw((v) => !v)} className="text-slate-500 underline hover:text-slate-700 dark:hover:text-slate-300">
            {showRaw ? "Hide" : "Show"} {preCheck ? "the check details" : "ERP's original message"}
          </button>
          <span className="text-slate-400">Still stuck?</span>
          <button type="button" onClick={copyForSupport} className="font-medium text-sky-700 underline dark:text-sky-300">
            {copied ? "Copied — paste it to Cargora support" : "Copy details for Cargora support"}
          </button>
        </div>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            disabled={retryBusy}
            className={`rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:cursor-wait disabled:opacity-60 ${
              allDone ? "bg-emerald-600 hover:bg-emerald-500" : "bg-slate-500 hover:bg-slate-400"
            }`}
            title={allDone ? "Send the job to ERP again" : "Fix the details above first"}
          >
            {retryBusy ? "Retrying…" : "Retry"}
          </button>
        )}
      </div>

      {showRaw && (
        <table className="mt-2 w-full text-left text-xs">
          <thead className="text-slate-500">
            <tr><th className="pr-2">Sheet</th><th className="pr-2">Field</th><th>Description</th></tr>
          </thead>
          <tbody>
            {(help.erp_rows ?? []).map((r, i) => (
              <tr key={i} className="border-t border-slate-100 dark:border-slate-800">
                <td className="pr-2">{r.sheet}</td>
                <td className="pr-2">{r.column}</td>
                <td>{r.rule}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
