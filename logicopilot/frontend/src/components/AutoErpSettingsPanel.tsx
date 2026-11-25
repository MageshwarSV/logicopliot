import { useEffect, useState } from "react";
import axios from "axios";
import * as api from "../api/autoErp";
import type { AutoErpSettings, AutoErpTemplate } from "../api/autoErp";

function errText(e: unknown) {
  return axios.isAxiosError(e) && typeof e.response?.data?.detail === "string" ? e.response.data.detail : "Could not save. Try again.";
}

/** An on/off switch - reads at a glance where a checkbox does not. */
function Toggle({ on, disabled, onChange, label }: {
  on: boolean; disabled?: boolean; onChange: (v: boolean) => void; label: string;
}) {
  return (
    <button
      type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 disabled:cursor-not-allowed disabled:opacity-50 ${
        on ? "bg-indigo-600" : "bg-slate-300 dark:bg-slate-600"}`}
    >
      <span className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${on ? "translate-x-5" : "translate-x-0.5"}`} />
    </button>
  );
}

const MODE_ICON: Record<string, string> = { "Air Import": "✈︎", "Air Export": "✈︎", "Sea Import": "⚓︎", "Sea Export": "⚓︎" };

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
  const onCount = data.templates.filter((t) => t.enabled).length;

  return (
    <div className="space-y-5">
      {/* Customer switch */}
      <div className={`flex items-center gap-4 rounded-2xl border p-4 ${data.allowed
        ? "border-emerald-200 bg-emerald-50/70 dark:border-emerald-500/30 dark:bg-emerald-500/10"
        : "border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/40"}`}>
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-lg ${data.allowed
          ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300"
          : "bg-slate-200 text-slate-500 dark:bg-slate-700 dark:text-slate-300"}`}>⚡</div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-semibold text-slate-900 dark:text-slate-50">Auto ERP Submission for {data.tenant_name}</p>
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${data.allowed
              ? "bg-emerald-600 text-white" : "bg-slate-300 text-slate-700 dark:bg-slate-600 dark:text-slate-100"}`}>
              {data.allowed ? "Allowed" : "Not allowed"}
            </span>
          </div>
          <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
            {canAllow
              ? "SaaS Admin switch. Turning it off stops every template at once; the choices below are kept."
              : data.allowed ? "Allowed by the SaaS Admin. Choose the templates below." : "Ask the SaaS Admin to allow it for your company."}
          </p>
        </div>
        {canAllow && (
          <Toggle on={data.allowed} disabled={busy === "allowed"} label="Allow Auto ERP Submission"
            onChange={(v) => run("allowed", () => api.setAutoErpAllowed(tenantId, v))} />
        )}
      </div>

      {error && <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}

      {/* Templates */}
      <div>
        <div className="mb-2 flex items-baseline justify-between gap-3">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">Templates</h3>
          <span className="text-xs text-slate-500 dark:text-slate-400">
            {onCount} of {data.templates.length} send to the ERP automatically
          </span>
        </div>
        <div className={`overflow-hidden rounded-2xl border border-slate-200 dark:border-slate-700 ${data.allowed ? "" : "pointer-events-none opacity-50"}`}>
          {data.templates.length === 0 && <p className="px-4 py-8 text-center text-sm text-slate-400">No templates yet.</p>}
          {data.templates.map((t, i) => (
            <TemplateRow key={t.group_id} t={t} options={data.branch_options} busy={busy === t.group_id}
              first={i === 0} onSave={saveTemplate} />
          ))}
        </div>
      </div>

      {/* Rules, folded */}
      <details className="group rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm dark:border-slate-700 dark:bg-slate-900">
        <summary className="cursor-pointer list-none font-medium text-slate-700 marker:hidden dark:text-slate-200">
          <span className="mr-1 inline-block transition-transform group-open:rotate-90">›</span> How Auto ERP Submission works
        </summary>
        <ul className="mt-3 space-y-1.5 pl-4 text-xs leading-relaxed text-slate-600 dark:text-slate-400">
          <li>• The job goes to the ERP as soon as its documents are read. There is no review, validation or approval in Cargora; the customer checks and corrects the entry in the ERP.</li>
          <li>• Quotation customer and importer are matched from the names on the documents, and branches from the job's branch and the consignee address.</li>
          <li>• An empty list field (like the product unit) takes the other lines' value or the value most used on past jobs. A required field with no value gets <b>NA</b> (text) or <b>0</b> (numbers).</li>
          <li>• Missing documents and cross-document rule failures do not stop it. Only a possible duplicate is not sent, so the ERP never gets the same shipment twice.</li>
          <li>• Each job shows an <b>AUTO</b> tag and a note listing what was picked and what to correct in the ERP. A failed ERP run shows as Failed, with Retry.</li>
        </ul>
      </details>
    </div>
  );
}

const chip = (on: boolean) =>
  `rounded-full border px-3 py-1 text-xs font-medium transition-colors disabled:opacity-60 ${on
    ? "border-indigo-300 bg-indigo-600 text-white dark:border-indigo-400"
    : "border-slate-200 bg-white text-slate-600 hover:border-indigo-300 hover:text-indigo-700 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-300"}`;

function TemplateRow({ t, options, busy, first, onSave }: {
  t: AutoErpTemplate; options: string[]; busy: boolean; first: boolean;
  onSave: (t: AutoErpTemplate, enabled: boolean, branches: string[]) => void;
}) {
  const [extra, setExtra] = useState("");
  const allBranches = t.branches.length === 0;
  const choices = [...new Set([...options, ...t.branches])];
  const toggleBranch = (b: string, on: boolean) =>
    onSave(t, t.enabled, on ? [...t.branches, b] : t.branches.filter((x) => x !== b));
  const addBranch = () => { const b = extra.trim(); if (b) { toggleBranch(b, true); setExtra(""); } };

  return (
    <div className={`${first ? "" : "border-t border-slate-100 dark:border-slate-800"} ${t.enabled ? "bg-indigo-50/40 dark:bg-indigo-500/5" : "bg-white dark:bg-slate-900"}`}>
      <div className="flex items-center gap-4 px-4 py-3.5">
        <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-base ${t.enabled
          ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300"
          : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"}`}>
          {MODE_ICON[t.mode ?? ""] ?? "▦"}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate font-medium text-slate-900 dark:text-slate-50">{t.name}</span>
            {t.mode && <span className="text-xs text-slate-400">{t.mode}</span>}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
            {t.enabled ? (
              <span className="rounded-full bg-indigo-600 px-2 py-0.5 font-semibold text-white">
                Auto · {allBranches ? "all branches" : t.branches.join(", ")}
              </span>
            ) : (
              <span className="rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                Manual review
              </span>
            )}
            {t.script && <span className="text-slate-400">Script: {t.script.name}</span>}
          </div>
        </div>
        <Toggle on={t.enabled} disabled={busy} label={`Auto ERP Submission for ${t.name}`}
          onChange={(v) => onSave(t, v, t.branches)} />
      </div>

      {t.enabled && t.problems.length > 0 && (
        <div className="mx-4 mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
          ⚠ Jobs cannot be sent yet: {t.problems.join(" ")}
        </div>
      )}

      {t.enabled && (
        <div className="mx-4 mb-4 rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
          {t.script?.branch_field ? (
            <>
              <p className="mb-2 text-xs font-medium text-slate-600 dark:text-slate-300">
                Send automatically for these branches <span className="font-normal text-slate-400">({t.script.branch_field})</span>
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" disabled={busy || allBranches} onClick={() => onSave(t, true, [])} className={chip(allBranches)}>
                  All branches
                </button>
                <span className="h-4 w-px bg-slate-200 dark:bg-slate-700" />
                {choices.map((b) => {
                  const on = t.branches.includes(b);
                  return (
                    <button key={b} type="button" disabled={busy} onClick={() => toggleBranch(b, !on)} className={chip(on)}>
                      {on ? "✓ " : ""}{b}
                    </button>
                  );
                })}
                <form className="flex items-center" onSubmit={(e) => { e.preventDefault(); addBranch(); }}>
                  <input value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="Other branch"
                    className="w-28 rounded-l-full border border-slate-200 bg-white px-3 py-1 text-xs outline-none focus:border-indigo-300 dark:border-slate-600 dark:bg-slate-900" />
                  <button type="submit" disabled={busy || !extra.trim()}
                    className="rounded-r-full border border-l-0 border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    Add
                  </button>
                </form>
              </div>
              {!allBranches && (
                <p className="mt-2 text-[11px] text-slate-400">Jobs for other branches keep the normal manual review.</p>
              )}
            </>
          ) : (
            <p className="text-xs text-slate-500">Every branch: this template's ERP script has no branch field.</p>
          )}
        </div>
      )}
    </div>
  );
}
