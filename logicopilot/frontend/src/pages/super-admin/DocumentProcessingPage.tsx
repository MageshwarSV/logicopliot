import { useEffect, useMemo, useState, type ReactNode } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Alert } from "../../components/ui/Alert";
import {
  ClockIcon, DocumentIcon, LayersIcon, PlaneIcon, SearchIcon, ShipIcon, SparklesIcon,
} from "../../components/ui/icons";
import * as onboardingApi from "../../api/onboarding";
import * as tenantsApi from "../../api/tenants";
import type { TemplateGroup } from "../../types/onboarding";

// Same as app/core/excel_intake.DEFAULT_IGNORE_PATTERNS - shown while a template has its own list unset.
const DEFAULT_EXCEL_IGNORE = ["DSRImportTemplate", "Consol Planning Sheet", "PDFSummary", "ShipNotice"];

type Draft = {
  auto_extract: boolean;
  large_zip_hold: boolean;
  large_zip_mb: number;
  large_job_lane: boolean;
  excel_sheet: boolean;
  ignore: string;
};

function draftOf(g: TemplateGroup): Draft {
  return {
    auto_extract: g.auto_extract !== false,
    large_zip_hold: g.large_zip_hold === true,
    large_zip_mb: g.large_zip_mb ?? 5,
    large_job_lane: g.large_job_lane === true,
    excel_sheet: (g.excel_mode ?? "legacy") === "sheet",
    ignore: (g.excel_ignore_patterns ?? DEFAULT_EXCEL_IGNORE).join("\n"),
  };
}

const listOf = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

/** Super Admin -> Document Processing: how each template's documents are processed - every job
 *  of the template, email and Smart Upload alike. Pick a template on the left; its settings on
 *  the right; one save per template (the same single call as before). */
export function DocumentProcessingPage() {
  const [groups, setGroups] = useState<TemplateGroup[]>([]);
  const [tenants, setTenants] = useState<Record<string, string>>({});
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [selected, setSelected] = useState<string>("");
  const [q, setQ] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [newPattern, setNewPattern] = useState("");

  useEffect(() => {
    Promise.all([onboardingApi.listGroups(), tenantsApi.listTenants()])
      .then(([g, t]) => {
        setGroups(g);
        setTenants(Object.fromEntries(t.map((x) => [x.id, x.name])));
        setDrafts(Object.fromEntries(g.map((x) => [x.id, draftOf(x)])));
        setSelected((cur) => cur || g[0]?.id || "");
      })
      .catch(() => setError("Could not load the templates."))
      .finally(() => setLoading(false));
  }, []);

  const set = (id: string, patch: Partial<Draft>) => setDrafts((d) => ({ ...d, [id]: { ...d[id], ...patch } }));
  const changed = (g: TemplateGroup) => !!drafts[g.id] && JSON.stringify(drafts[g.id]) !== JSON.stringify(draftOf(g));

  async function save(g: TemplateGroup) {
    const d = drafts[g.id];
    setSaving(true);
    setError(null);
    setSavedId(null);
    try {
      const out = await onboardingApi.setProcessingSettings(g.id, {
        auto_extract: d.auto_extract,
        large_zip_hold: d.large_zip_hold,
        large_zip_mb: d.large_zip_mb,
        large_job_lane: d.large_job_lane,
        excel_mode: d.excel_sheet ? "sheet" : "legacy",
        excel_ignore_patterns: listOf(d.ignore),
      });
      setGroups((gs) => gs.map((x) => (x.id === g.id ? { ...x, ...out } : x)));
      setDrafts((ds) => ({ ...ds, [g.id]: draftOf({ ...g, ...out }) }));
      setSavedId(g.id);
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not save." : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  const grouped = useMemo(() => {
    const f = q.trim().toLowerCase();
    const out: { tid: string; name: string; items: TemplateGroup[] }[] = [];
    const byTenant: Record<string, TemplateGroup[]> = {};
    for (const g of groups) {
      if (f && !`${g.name} ${g.mode ?? ""} ${tenants[g.tenant_id] ?? ""}`.toLowerCase().includes(f)) continue;
      (byTenant[g.tenant_id] ??= []).push(g);
    }
    for (const tid of Object.keys(byTenant).sort((a, b) => (tenants[a] ?? "").localeCompare(tenants[b] ?? ""))) {
      out.push({ tid, name: tenants[tid] ?? "Tenant", items: byTenant[tid].sort((a, b) => a.name.localeCompare(b.name)) });
    }
    return out;
  }, [groups, tenants, q]);

  const g = groups.find((x) => x.id === selected);
  const d = g ? drafts[g.id] : undefined;
  const unsavedCount = groups.filter(changed).length;
  const patterns = d ? listOf(d.ignore) : [];
  const setPatterns = (list: string[]) => g && set(g.id, { ignore: list.join("\n") });
  const addPattern = () => {
    const v = newPattern.trim();
    if (!v || !g) return;
    if (!patterns.some((p) => p.toLowerCase() === v.toLowerCase())) setPatterns([...patterns, v]);
    setNewPattern("");
  };

  return (
    <AppShell title="Document Processing" subtitle="How each template's documents are read and extracted - applies to every job of the template, email and Smart Upload.">
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      {loading && <p className="text-sm text-slate-400">Loading…</p>}

      {!loading && (
        <div className="grid gap-5 pb-24 lg:grid-cols-[20rem_1fr]">
          {/* ---------- template list */}
          <aside className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900 lg:sticky lg:top-4 lg:self-start">
            <div className="relative mb-3">
              <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a template…"
                className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-900 dark:focus:ring-indigo-500/20" />
            </div>
            {unsavedCount > 0 && (
              <p className="mb-2 rounded-lg bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
                {unsavedCount} template(s) with unsaved changes
              </p>
            )}
            <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
              {grouped.map((grp) => (
                <div key={grp.tid}>
                  <p className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">{grp.name}</p>
                  <div className="space-y-1">
                    {grp.items.map((t) => {
                      const td = drafts[t.id];
                      const active = t.id === selected;
                      const air = (t.mode ?? "").toLowerCase().startsWith("air");
                      const Icon = air ? PlaneIcon : ShipIcon;
                      return (
                        <button key={t.id} type="button" onClick={() => setSelected(t.id)}
                          className={`flex w-full items-start gap-2.5 rounded-xl px-2.5 py-2 text-left transition ${active
                            ? "bg-indigo-50 ring-1 ring-indigo-200 dark:bg-indigo-500/10 dark:ring-indigo-500/30"
                            : "hover:bg-slate-50 dark:hover:bg-slate-800/60"}`}>
                          <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${air
                            ? "bg-sky-50 text-sky-600 dark:bg-sky-500/10 dark:text-sky-300" : "bg-teal-50 text-teal-600 dark:bg-teal-500/10 dark:text-teal-300"}`}>
                            <Icon className="h-3.5 w-3.5" />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-1.5">
                              <span className={`truncate text-sm font-medium ${active ? "text-indigo-800 dark:text-indigo-200" : "text-slate-800 dark:text-slate-100"}`}>{t.name}</span>
                              {changed(t) && <span className="h-2 w-2 shrink-0 rounded-full bg-amber-500" title="Unsaved changes" />}
                            </span>
                            {td && (
                              <span className="mt-1 flex flex-wrap gap-1">
                                <Chip on={td.auto_extract} label="Auto" />
                                {td.large_job_lane && <Chip on label="Large lane" />}
                                {td.large_zip_hold && <Chip on label={`Hold > ${td.large_zip_mb} MB`} />}
                                {td.excel_sheet && <Chip on label="Excel by sheet" />}
                              </span>
                            )}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
              {grouped.length === 0 && <p className="px-2 py-6 text-center text-sm text-slate-400">No template matches.</p>}
            </div>
          </aside>

          {/* ---------- settings of the selected template */}
          {g && d ? (
            <section className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">{tenants[g.tenant_id] ?? "Tenant"}</p>
                  <h2 className="text-lg font-bold text-slate-900 dark:text-slate-50">{g.name}</h2>
                  <p className="text-sm text-slate-500">{g.mode ?? "No shipment mode set"}</p>
                </div>
                {savedId === g.id && !changed(g) && (
                  <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">✓ Saved</span>
                )}
              </div>

              <Panel title="Extraction" icon={<SparklesIcon className="h-4 w-4" />}>
                <SettingRow
                  title="Start extraction automatically"
                  text="Every job is read by the AI as soon as its documents are placed. Off: jobs are classified and placed, then wait for someone to press Run extraction."
                  on={d.auto_extract} onChange={(v) => set(g.id, { auto_extract: v })} />
              </Panel>

              <Panel title="Large zip files" icon={<LayersIcon className="h-4 w-4" />}>
                <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 pb-4 dark:border-slate-800">
                  <span className="text-sm font-medium text-slate-800 dark:text-slate-100">A zip is "large" when it is bigger than</span>
                  <span className="inline-flex items-center overflow-hidden rounded-lg border border-slate-300 dark:border-slate-700">
                    <input type="number" min={1} max={500} value={d.large_zip_mb}
                      onChange={(e) => set(g.id, { large_zip_mb: Math.max(1, Math.min(500, Number(e.target.value) || 1)) })}
                      className="w-20 bg-white px-3 py-1.5 text-sm tabular-nums focus:outline-none dark:bg-slate-900" />
                    <span className="border-l border-slate-300 bg-slate-50 px-2.5 py-1.5 text-xs font-semibold text-slate-500 dark:border-slate-700 dark:bg-slate-800">MB</span>
                  </span>
                  <span className="text-xs text-slate-500">Used by both options below.</span>
                </div>
                <SettingRow
                  title="Hold extraction for a large zip"
                  text={`A mail or upload with a zip over ${d.large_zip_mb} MB is placed, then waits for Run extraction. Every other job still extracts automatically.`}
                  on={d.large_zip_hold} onChange={(v) => set(g.id, { large_zip_hold: v })} />
                <SettingRow
                  title="Use the large-job lane"
                  text={`A mail with a zip over ${d.large_zip_mb} MB gets its own worker, one large job at a time - the mail cycle does not wait for it and small jobs keep their workers. Its Run extraction also waits its turn there.`}
                  on={d.large_job_lane} onChange={(v) => set(g.id, { large_job_lane: v })} />
              </Panel>

              <Panel title="Excel files" icon={<DocumentIcon className="h-4 w-4" />}>
                <SettingRow
                  title="Read Excel by sheet"
                  text={'Each sheet is read from its cells and placed, or sent to "Excel files not used". Off: each Excel file is turned into a PDF first, as before.'}
                  on={d.excel_sheet} onChange={(v) => set(g.id, { excel_sheet: v })} />
                <div className="pt-4">
                  <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
                    <div>
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">Never use these Excel files</p>
                      <p className="text-xs text-slate-500">A file whose name contains one of these goes straight to "not used", without being read by the AI.</p>
                    </div>
                    <button type="button" onClick={() => setPatterns(DEFAULT_EXCEL_IGNORE)}
                      className="text-xs font-semibold text-indigo-600 hover:underline dark:text-indigo-400">Reset to default</button>
                  </div>
                  <div className="flex flex-wrap gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/40">
                    {patterns.map((p) => (
                      <span key={p} className="inline-flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1 font-mono text-xs text-slate-700 shadow-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:text-slate-200 dark:ring-slate-700">
                        {p}
                        <button type="button" onClick={() => setPatterns(patterns.filter((x) => x !== p))} aria-label={`Remove ${p}`}
                          className="text-slate-400 hover:text-rose-600">×</button>
                      </span>
                    ))}
                    {patterns.length === 0 && <span className="text-xs text-slate-400">None - every Excel file is read.</span>}
                    <span className="inline-flex items-center gap-1">
                      <input value={newPattern} onChange={(e) => setNewPattern(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addPattern(); } }}
                        placeholder="Add a file name part…"
                        className="w-48 rounded-lg border border-dashed border-slate-300 bg-white px-2.5 py-1 text-xs focus:border-indigo-400 focus:outline-none dark:border-slate-600 dark:bg-slate-900" />
                      <button type="button" onClick={addPattern} disabled={!newPattern.trim()}
                        className="rounded-lg bg-slate-900 px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900">Add</button>
                    </span>
                  </div>
                </div>
              </Panel>

              <p className="flex items-center gap-1.5 px-1 text-xs text-slate-500">
                <ClockIcon className="h-3.5 w-3.5" /> Changes apply from the next email or upload of this template.
              </p>
            </section>
          ) : (
            !loading && <p className="text-sm text-slate-500">Pick a template on the left.</p>
          )}
        </div>
      )}

      {/* ---------- save bar for the selected template */}
      {g && changed(g) && (
        <div className="fixed inset-x-0 bottom-4 z-30 flex justify-center px-4">
          <div className="flex items-center gap-4 rounded-2xl bg-slate-900 px-5 py-3 text-sm text-white shadow-2xl dark:bg-slate-100 dark:text-slate-900">
            <span>Unsaved changes to <b>{g.name}</b></span>
            <button type="button" onClick={() => set(g.id, draftOf(g))}
              className="rounded-lg px-3 py-1.5 font-medium text-slate-300 hover:text-white dark:text-slate-600 dark:hover:text-slate-900">Discard</button>
            <button type="button" onClick={() => save(g)} disabled={saving}
              className="rounded-lg bg-indigo-500 px-4 py-1.5 font-semibold text-white hover:bg-indigo-400 disabled:opacity-60">{saving ? "Saving…" : "Save"}</button>
          </div>
        </div>
      )}
    </AppShell>
  );
}

function Panel({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-300">{icon}</span>
        {title}
      </h3>
      <div className="divide-y divide-slate-100 dark:divide-slate-800">{children}</div>
    </div>
  );
}

function SettingRow({ title, text, on, onChange }: { title: string; text: string; on: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-start justify-between gap-4 py-3.5 first:pt-0 last:pb-0">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">{title}</p>
        <p className="mt-0.5 text-xs leading-relaxed text-slate-500 dark:text-slate-400">{text}</p>
      </div>
      <button type="button" role="switch" aria-checked={on} aria-label={title} onClick={() => onChange(!on)}
        className={`relative mt-0.5 inline-flex h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-indigo-600" : "bg-slate-300 dark:bg-slate-600"}`}>
        <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${on ? "translate-x-5" : "translate-x-0.5"}`} />
      </button>
    </div>
  );
}

function Chip({ on, label }: { on: boolean; label: string }) {
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${on
      ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
      : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"}`}>
      {on ? label : `${label} off`}
    </span>
  );
}
