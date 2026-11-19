import { useEffect, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import * as onboardingApi from "../../api/onboarding";
import * as tenantsApi from "../../api/tenants";
import type { TemplateGroup } from "../../types/onboarding";

// Same as app/core/excel_intake.DEFAULT_IGNORE_PATTERNS - shown while a template has its own list unset.
const DEFAULT_EXCEL_IGNORE = ["DSRImportTemplate", "Consol Planning Sheet", "PDFSummary", "ShipNotice"];

type Draft = {
  auto_extract: boolean;
  large_zip_hold: boolean;
  large_zip_mb: number;
  excel_sheet: boolean;
  ignore: string;
};

function draftOf(g: TemplateGroup): Draft {
  return {
    auto_extract: g.auto_extract !== false,
    large_zip_hold: g.large_zip_hold === true,
    large_zip_mb: g.large_zip_mb ?? 5,
    excel_sheet: (g.excel_mode ?? "legacy") === "sheet",
    ignore: (g.excel_ignore_patterns ?? DEFAULT_EXCEL_IGNORE).join("\n"),
  };
}

/** Super Admin -> Document Processing: how each template's documents are processed - every job
 *  of the template, email and Smart Upload alike. */
export function DocumentProcessingPage() {
  const [groups, setGroups] = useState<TemplateGroup[]>([]);
  const [tenants, setTenants] = useState<Record<string, string>>({});
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([onboardingApi.listGroups(), tenantsApi.listTenants()])
      .then(([g, t]) => {
        setGroups(g);
        setTenants(Object.fromEntries(t.map((x) => [x.id, x.name])));
        setDrafts(Object.fromEntries(g.map((x) => [x.id, draftOf(x)])));
      })
      .catch(() => setError("Could not load the templates."))
      .finally(() => setLoading(false));
  }, []);

  const set = (id: string, patch: Partial<Draft>) => setDrafts((d) => ({ ...d, [id]: { ...d[id], ...patch } }));

  async function save(g: TemplateGroup) {
    const d = drafts[g.id];
    setSaving(g.id);
    setError(null);
    setSaved(null);
    try {
      const out = await onboardingApi.setProcessingSettings(g.id, {
        auto_extract: d.auto_extract,
        large_zip_hold: d.large_zip_hold,
        large_zip_mb: d.large_zip_mb,
        excel_mode: d.excel_sheet ? "sheet" : "legacy",
        excel_ignore_patterns: d.ignore.split("\n").map((x) => x.trim()).filter(Boolean),
      });
      setGroups((gs) => gs.map((x) => (x.id === g.id ? { ...x, ...out } : x)));
      setDrafts((ds) => ({ ...ds, [g.id]: draftOf({ ...g, ...out }) }));
      setSaved(g.id);
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not save." : "Could not save.");
    } finally {
      setSaving(null);
    }
  }

  const byTenant: Record<string, TemplateGroup[]> = {};
  for (const g of groups) (byTenant[g.tenant_id] ??= []).push(g);
  const tenantIds = Object.keys(byTenant).sort((a, b) => (tenants[a] ?? "").localeCompare(tenants[b] ?? ""));
  const changed = (g: TemplateGroup) => JSON.stringify(drafts[g.id]) !== JSON.stringify(draftOf(g));

  return (
    <AppShell title="Document Processing" subtitle="How each template's documents are read and extracted - applies to every job of the template, email and Smart Upload.">
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      {loading && <p className="text-sm text-slate-400">Loading…</p>}
      {tenantIds.map((tid) => (
        <div key={tid} className="mb-6">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">{tenants[tid] ?? "Tenant"}</p>
          <div className="grid gap-4 lg:grid-cols-2">
            {byTenant[tid].map((g) => {
              const d = drafts[g.id];
              if (!d) return null;
              return (
                <Card key={g.id} className="p-5">
                  <div className="mb-3 flex items-center justify-between gap-2">
                    <div>
                      <p className="font-medium text-slate-900 dark:text-slate-50">{g.name}</p>
                      <p className="text-xs text-slate-500">{g.mode ?? "No mode set"}</p>
                    </div>
                    <button
                      onClick={() => save(g)}
                      disabled={saving === g.id || !changed(g)}
                      className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {saving === g.id ? "Saving…" : saved === g.id && !changed(g) ? "Saved ✓" : "Save"}
                    </button>
                  </div>

                  <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Extraction</p>
                  <label className="mb-2 flex items-start gap-2 text-sm text-slate-700 dark:text-slate-200">
                    <input type="checkbox" className="mt-1" checked={d.auto_extract} onChange={(e) => set(g.id, { auto_extract: e.target.checked })} />
                    <span>
                      <span className="font-medium">Start extraction automatically</span>
                      <span className="block text-xs text-slate-500">Off: every job is classified and placed, then waits for Run extraction.</span>
                    </span>
                  </label>
                  <label className="mb-3 flex items-start gap-2 text-sm text-slate-700 dark:text-slate-200">
                    <input type="checkbox" className="mt-1" checked={d.large_zip_hold} onChange={(e) => set(g.id, { large_zip_hold: e.target.checked })} />
                    <span>
                      <span className="font-medium">Hold extraction for a large zip</span>
                      <span className="mt-0.5 flex items-center gap-1 text-xs text-slate-500">
                        A mail or upload with a zip larger than
                        <input type="number" min={1} max={500} value={d.large_zip_mb}
                          onChange={(e) => set(g.id, { large_zip_mb: Math.max(1, Number(e.target.value) || 1) })}
                          className="w-16 rounded border border-slate-300 px-1 py-0.5 text-xs dark:border-slate-700 dark:bg-slate-800" />
                        MB is placed, then waits for Run extraction. Every other job extracts automatically.
                      </span>
                    </span>
                  </label>

                  <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Excel files</p>
                  <label className="mb-2 flex items-start gap-2 text-sm text-slate-700 dark:text-slate-200">
                    <input type="checkbox" className="mt-1" checked={d.excel_sheet} onChange={(e) => set(g.id, { excel_sheet: e.target.checked })} />
                    <span>
                      <span className="font-medium">Read Excel by sheet</span>
                      <span className="block text-xs text-slate-500">Each sheet read from its cells and placed, or sent to "Excel files not used". Off: converted to PDF, as before.</span>
                    </span>
                  </label>
                  <label className="block text-sm text-slate-700 dark:text-slate-200">
                    <span className="font-medium">Never use these Excel files</span>
                    <span className="block text-xs text-slate-500">One per line - a file whose name contains it goes straight to "not used", without being read by the AI.</span>
                    <textarea value={d.ignore} onChange={(e) => set(g.id, { ignore: e.target.value })} rows={4}
                      className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 font-mono text-xs dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100" />
                  </label>
                </Card>
              );
            })}
          </div>
        </div>
      ))}
    </AppShell>
  );
}
