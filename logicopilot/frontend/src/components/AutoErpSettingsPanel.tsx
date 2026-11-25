import { useEffect, useState } from "react";
import axios from "axios";
import * as api from "../api/autoErp";
import type { AutoErpSettings, AutoErpTemplate } from "../api/autoErp";

function errText(e: unknown) {
  return axios.isAxiosError(e) && typeof e.response?.data?.detail === "string" ? e.response.data.detail : "Could not save. Try again.";
}

/** Auto ERP Submission settings for one tenant - shared by the SaaS Admin (Tenants → Auto ERP,
 *  `canAllow`) and the Tenant Admin (Masters). The SaaS Admin's switch gates everything; under
 *  it each template is on/off, for every branch or only the branches picked. */
export function AutoErpSettingsPanel({ tenantId, canAllow }: { tenantId: string; canAllow: boolean }) {
  const [data, setData] = useState<AutoErpSettings | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getAutoErp(tenantId).then(setData).catch((e) => setError(errText(e)));
  }, [tenantId]);

  async function run(key: string, fn: () => Promise<AutoErpSettings>) {
    setBusy(key);
    setError(null);
    try { setData(await fn()); } catch (e) { setError(errText(e)); } finally { setBusy(null); }
  }

  if (!data) return <p className="text-sm text-slate-400">{error ?? "Loading…"}</p>;

  const saveTemplate = (t: AutoErpTemplate, enabled: boolean, branches: string[]) =>
    run(t.group_id, () => api.setAutoErpTemplate(tenantId, t.group_id, enabled, branches));

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500 dark:text-slate-400">
        When on, nothing in Cargora stops the job: it goes to the ERP the moment extraction finishes,
        with no review, validation or approval here, and the customer corrects anything in the ERP.
        Empty pick fields are filled from the documents (quotation customer, importer, branch), else
        with the first option. Missing documents and cross-document rule failures do not stop it. Only
        a job flagged as a possible duplicate is not sent, so the ERP never gets the same shipment
        twice. Failed ERP runs show as Failed in Cargora for someone to retry.
      </p>

      <label className={`flex items-center gap-3 rounded-xl border px-4 py-3 ${data.allowed ? "border-emerald-200 bg-emerald-50 dark:border-emerald-500/30 dark:bg-emerald-500/10" : "border-slate-200 dark:border-slate-700"}`}>
        <input type="checkbox" className="h-4 w-4 rounded border-slate-300 disabled:opacity-60"
          checked={data.allowed} disabled={!canAllow || busy === "allowed"}
          onChange={(e) => run("allowed", () => api.setAutoErpAllowed(tenantId, e.target.checked))} />
        <span className="text-sm">
          <span className="font-medium text-slate-800 dark:text-slate-100">Allowed for {data.tenant_name}</span>
          <span className="block text-xs text-slate-500 dark:text-slate-400">
            {canAllow ? "SaaS Admin switch. Off stops every template at once; the choices below are kept."
              : data.allowed ? "Allowed by the SaaS Admin." : "Not allowed yet. Ask the SaaS Admin to allow it."}
          </span>
        </span>
      </label>

      {error && <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}

      <div className={`divide-y divide-slate-100 rounded-xl border border-slate-200 dark:divide-slate-800 dark:border-slate-700 ${data.allowed ? "" : "pointer-events-none opacity-50"}`}>
        {data.templates.length === 0 && <p className="px-4 py-6 text-center text-sm text-slate-400">No templates yet.</p>}
        {data.templates.map((t) => (
          <TemplateRow key={t.group_id} t={t} options={data.branch_options} busy={busy === t.group_id} onSave={saveTemplate} />
        ))}
      </div>
    </div>
  );
}

function TemplateRow({ t, options, busy, onSave }: {
  t: AutoErpTemplate; options: string[]; busy: boolean;
  onSave: (t: AutoErpTemplate, enabled: boolean, branches: string[]) => void;
}) {
  const [extra, setExtra] = useState("");
  const allBranches = t.branches.length === 0;
  const choices = [...new Set([...options, ...t.branches])];
  const toggleBranch = (b: string, on: boolean) =>
    onSave(t, t.enabled, on ? [...t.branches, b] : t.branches.filter((x) => x !== b));

  return (
    <div className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex min-w-0 flex-1 items-center gap-3">
          <input type="checkbox" className="h-4 w-4 rounded border-slate-300 disabled:opacity-60"
            checked={t.enabled} disabled={busy} onChange={(e) => onSave(t, e.target.checked, t.branches)} />
          <span className="min-w-0">
            <span className="font-medium text-slate-800 dark:text-slate-100">{t.name}</span>
            {t.mode && <span className="ml-2 text-xs text-slate-400">{t.mode}</span>}
            <span className="block text-xs text-slate-500 dark:text-slate-400">
              {t.enabled ? (allBranches ? "Auto submit · every branch" : `Auto submit · ${t.branches.join(", ")} only`) : "Manual review (normal flow)"}
              {t.script && ` · script ${t.script.name}`}
            </span>
          </span>
        </label>
        {t.enabled && t.problems.length > 0 && (
          <span className="rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
            Jobs will be held: {t.problems.join(" ")}
          </span>
        )}
      </div>

      {t.enabled && (
        <div className="mt-2 ml-7 flex flex-wrap items-center gap-2 text-xs">
          {t.script?.branch_field ? (
            <>
              <span className="text-slate-500">Branches ({t.script.branch_field}):</span>
              <button type="button" disabled={busy || allBranches} onClick={() => onSave(t, true, [])}
                className={`rounded-full border px-2.5 py-1 ${allBranches ? "border-indigo-300 bg-indigo-50 text-indigo-700 dark:border-indigo-500/40 dark:bg-indigo-500/10 dark:text-indigo-300" : "border-slate-200 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300"}`}>
                All
              </button>
              {choices.map((b) => {
                const on = t.branches.includes(b);
                return (
                  <button key={b} type="button" disabled={busy} onClick={() => toggleBranch(b, !on)}
                    className={`rounded-full border px-2.5 py-1 ${on ? "border-indigo-300 bg-indigo-50 text-indigo-700 dark:border-indigo-500/40 dark:bg-indigo-500/10 dark:text-indigo-300" : "border-slate-200 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300"}`}>
                    {on ? "✓ " : ""}{b}
                  </button>
                );
              })}
              <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); if (extra.trim()) { toggleBranch(extra.trim(), true); setExtra(""); } }}>
                <input value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="Add branch"
                  className="w-28 rounded-md border border-slate-200 bg-white px-2 py-1 dark:border-slate-700 dark:bg-slate-900" />
              </form>
            </>
          ) : (
            <span className="text-slate-400">Every branch: the ERP script has no branch field.</span>
          )}
        </div>
      )}
    </div>
  );
}
