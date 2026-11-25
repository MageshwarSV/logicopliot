import { useEffect, useState } from "react";
import * as erpApi from "../../api/onboardingErp";
import type { ErpScriptOption, ErpWorkbook } from "../../api/onboardingErp";

function errorText(err: unknown, fallback: string): string {
  return (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;
}


/** On the ERP UI sheet: add the fields an ERP script picks on screen that the Excel does not
 *  carry. A copied template is linked to no script, so the admin picks the one it will run
 *  with; the template's own scripts are listed first. */
export function ErpUiPicker({ workbookId, onAdded, onError }: {
  workbookId: string;
  onAdded: (w: ErpWorkbook, text: string) => void;
  onError: (text: string) => void;
}) {
  const [scripts, setScripts] = useState<ErpScriptOption[] | null>(null);
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    erpApi.listErpScripts(workbookId)
      .then((list) => { setScripts(list); setPick((p) => p || list[0]?.id || ""); })
      .catch(() => setScripts([]));
  }, [workbookId]);

  const chosen = scripts?.find((x) => x.id === pick);
  const run = async () => {
    if (!pick) return;
    setBusy(true);
    try {
      const r = await erpApi.erpUiFromScript(workbookId, pick);
      const parts = [
        r.added.length ? `added ${r.added.join(", ")}` : "nothing new to add",
        r.already.length ? `already here: ${r.already.join(", ")}` : "",
        r.in_excel.length ? `${r.in_excel.length} already in the Excel` : "",
        r.not_in_template.length ? `not on this template (skipped): ${r.not_in_template.join(", ")}` : "",
      ].filter(Boolean);
      onAdded(r.workbook, `${chosen?.name ?? "Script"}: ${parts.join(" · ")}.`);
    } catch (err) {
      onError(errorText(err, "Could not read the ERP script."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-2 rounded-lg border border-indigo-100 bg-indigo-50/50 p-2 text-[11px] text-slate-600 dark:border-indigo-500/20 dark:bg-indigo-500/5 dark:text-slate-300">
      <p className="mb-1">Fields the ERP UI script picks on screen, not in the Excel.</p>
      {scripts === null && <p className="text-slate-400">Loading scripts…</p>}
      {scripts?.length === 0 && <p className="text-slate-400">This customer has no ERP script yet - add fields with "+ Add new field".</p>}
      {scripts && scripts.length > 0 && (
        <>
          <select value={pick} onChange={(e) => setPick(e.target.value)}
            className="mb-1 w-full rounded border border-slate-300 bg-white px-1 py-1 text-xs dark:border-slate-700 dark:bg-slate-900">
            {scripts.map((x) => (
              <option key={x.id} value={x.id}>{x.name}{x.linked ? " (this template)" : ""} - {x.not_in_excel.length} not in Excel</option>
            ))}
          </select>
          {chosen && chosen.not_in_excel.length > 0 && (
            <p className="mb-1 truncate text-slate-400" title={chosen.not_in_excel.join(", ")}>Uses: {chosen.not_in_excel.join(", ")}</p>
          )}
          <button type="button" disabled={busy || !pick} onClick={() => void run()}
            className="rounded bg-indigo-600 px-2 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50">
            {busy ? "Reading…" : "Pick up from ERP script"}
          </button>
        </>
      )}
    </div>
  );
}
