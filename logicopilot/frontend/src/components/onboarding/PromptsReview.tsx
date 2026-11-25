import { useEffect, useMemo, useState } from "react";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import * as erpApi from "../../api/onboardingErp";
import * as onboardingApi from "../../api/onboarding";
import { apiError, generatePrompts, getLinks, type FieldRef, type LiveGroup, type LiveMark } from "../../api/onboardingTemplate";

// Customer Onboarding Step 3 - Prompts & Test, the review list (recommended workflow, phase D).
// Every box on the template with its reading prompt and label variations, read on the template
// sample and compared with the customer's ERP Excel value:
//   To review - no prompt yet, or not tested yet
//   Check     - tested, and the value read differs from the Excel (or nothing was read)
//   Done      - tested, and it matches (or there is no Excel value to compare with)
// "Generate missing prompts" builds the prompt for every box without one; a single box can be
// regenerated (its old prompt goes into the history). Test results live on this screen only.

type Status = "review" | "check" | "done";
type Read = { value: string | null; anchor: string | null } | "error";

const norm = (v: string | null | undefined) => (v ?? "").toUpperCase().replace(/[^A-Z0-9.]/g, "");
function same(a: string | null | undefined, b: string | null | undefined): boolean {
  const x = norm(a), y = norm(b);
  if (!x || !y) return false;
  if (x === y) return true;
  const nx = Number(x.replace(/,/g, "")), ny = Number(y.replace(/,/g, ""));
  return !Number.isNaN(nx) && !Number.isNaN(ny) && nx === ny;
}

export function PromptsReview({ group, groupId, onChanged, openField, beforeSave, onMessage }: {
  group: LiveGroup;
  groupId: string;
  onChanged: () => Promise<void> | void;
  openField: (ref: FieldRef) => void;
  beforeSave: () => boolean;
  onMessage: (tone: "ok" | "warn", text: string) => void;
}) {
  const [excel, setExcel] = useState<Record<string, string>>({});       // mark id -> ERP Excel value
  const [reads, setReads] = useState<Record<string, Read>>({});        // mark id -> value read
  const [view, setView] = useState<Status | "all">("review");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  // The ERP Excel value of the onboarding field each box fills (Step 1's first row).
  useEffect(() => {
    Promise.all([getLinks(groupId), erpApi.getWorkbook(groupId)]).then(([links, wb]) => {
      const sample = new Map((wb?.fields ?? []).map((f) => [f.id, f.samples[0] != null ? String(f.samples[0]) : ""]));
      const out: Record<string, string> = {};
      for (const l of links.fields) {
        if (l.ref?.startsWith("m:") && sample.get(l.erp_field_id)) out[l.ref.slice(2)] = sample.get(l.erp_field_id)!;
      }
      setExcel(out);
    }).catch(() => setExcel({}));
  }, [groupId]);

  const rows = useMemo(() => group.documents.flatMap((d) =>
    d.marks.filter((m) => m.width > 0).map((m) => ({ d, m }))), [group]);

  const statusOf = (m: LiveMark): Status => {
    const r = reads[m.id];
    if (!(m.extraction_prompt ?? "").trim() || r === undefined) return "review";
    if (r === "error" || !r.value) return "check";
    if (excel[m.id] && !same(r.value, excel[m.id])) return "check";
    return "done";
  };
  const counts = { review: 0, check: 0, done: 0 } as Record<Status, number>;
  for (const { m } of rows) counts[statusOf(m)] += 1;
  const missing = rows.filter(({ m }) => !(m.extraction_prompt ?? "").trim());
  const q = search.trim().toLowerCase();
  const shown = rows.filter(({ m }) => (view === "all" || statusOf(m) === view)
    && (!q || `${m.label_name} ${m.display_label ?? ""}`.toLowerCase().includes(q)));

  async function generate(markIds?: string[], onlyMissing = true) {
    if (!beforeSave()) return;
    setBusy(markIds?.length === 1 ? markIds[0] : "generate");
    try {
      const r = await generatePrompts(groupId, { mark_ids: markIds, only_missing: onlyMissing });
      await onChanged();
      setReads((x) => { const n = { ...x }; for (const id of r.mark_ids) delete n[id]; return n; });
      onMessage("ok", r.generated ? `Built the prompt for ${r.generated} field(s). Test them next.` : "Every field already has a prompt.");
    } catch (e) { onMessage("warn", apiError(e, "Could not build the prompts.")); }
    finally { setBusy(null); }
  }

  async function testAll() {
    setBusy("test");
    const next: Record<string, Read> = {};
    for (const d of group.documents) {
      if (!d.marks.some((m) => m.width > 0)) continue;
      try {
        const out = await onboardingApi.demoExtract(d.id);
        for (const r of out.results) {
          const value = r.extracted_value ?? (r.extracted_values ?? []).find((v) => v) ?? null;
          next[r.mark_id] = { value, anchor: r.matched_anchor };
        }
      } catch {
        for (const m of d.marks) next[m.id] = "error";
      }
    }
    setReads(next);
    setBusy(null);
    setView("check");
    onMessage("ok", "Read every document's sample - fields to look at are under Check.");
  }

  const TABS: [Status | "all", string][] = [["review", "To review"], ["check", "Check"], ["done", "Done"], ["all", "All"]];

  return (
    <Card className="p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={!missing.length} isLoading={busy === "generate"} onClick={() => void generate()}>
          Generate missing prompts{missing.length ? ` (${missing.length})` : ""}
        </Button>
        <Button size="sm" variant="secondary" isLoading={busy === "test"} onClick={() => void testAll()}>
          Test all on the samples
        </Button>
        <span className="text-xs text-slate-500">Each value read is compared with the customer's ERP Excel.</span>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-1">
        {TABS.map(([k, label]) => (
          <button key={k} type="button" onClick={() => setView(k)}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium ${view === k ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"}`}>
            {label}{k !== "all" ? ` ${counts[k]}` : ` ${rows.length}`}
          </button>
        ))}
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search field…"
          className="ml-auto w-48 rounded-lg border border-slate-300 px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900" />
      </div>

      {shown.length === 0 && (
        <p className="py-8 text-center text-sm text-slate-500">
          {view === "review" ? "Nothing to review - every field has a prompt and has been tested." : view === "check" ? "Nothing to check." : "No fields here."}
        </p>
      )}
      <div className="flex flex-col divide-y divide-slate-100 dark:divide-slate-800">
        {shown.map(({ d, m }) => {
          const st = statusOf(m);
          const r = reads[m.id];
          const read = r && r !== "error" ? r.value : null;
          const ex = excel[m.id];
          return (
            <div key={m.id} className="grid gap-2 py-2.5 text-sm md:grid-cols-[14rem_minmax(0,1fr)_14rem]">
              <div className="min-w-0">
                <button type="button" className="truncate font-medium text-indigo-700 hover:underline dark:text-indigo-300" onClick={() => openField(`m:${m.id}`)}>
                  {m.display_label || m.label_name}
                </button>
                <p className="truncate text-[11px] text-slate-400">{d.name}{m.is_multi_value ? " · line item" : ""}</p>
              </div>
              <div className="min-w-0">
                {(m.extraction_prompt ?? "").trim()
                  ? <p className="line-clamp-2 text-xs text-slate-600 dark:text-slate-300">{m.extraction_prompt}</p>
                  : <p className="text-xs text-amber-700 dark:text-amber-300">No prompt yet</p>}
                {(m.anchor_variations ?? []).length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {(m.anchor_variations ?? []).slice(0, 5).map((a) => (
                      <span key={a} className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">{a}</span>
                    ))}
                  </div>
                )}
              </div>
              <div className="flex flex-col gap-1 text-xs">
                <span className="truncate text-slate-500">Excel: <span className="font-mono text-slate-700 dark:text-slate-200">{ex || "—"}</span></span>
                <span className="truncate">
                  {r === undefined ? <span className="text-slate-400">Not tested</span>
                    : r === "error" ? <span className="text-rose-600">Could not read the sample</span>
                    : <>Read: <span className="font-mono">{read || "nothing"}</span></>}
                </span>
                <span className={`font-semibold ${st === "done" ? "text-emerald-600" : st === "check" ? "text-amber-600" : "text-slate-400"}`}>
                  {st === "done" ? (ex ? "✓ matches the Excel" : "✓ read (no Excel value)") : st === "check" ? (read ? "⚠ differs from the Excel" : "⚠ nothing read") : "To review"}
                </span>
                <div className="flex gap-3">
                  <button type="button" className="text-indigo-600 hover:underline dark:text-indigo-300" onClick={() => openField(`m:${m.id}`)}>Edit prompt</button>
                  <button type="button" className="text-slate-500 hover:underline" disabled={busy === m.id} onClick={() => {
                    if ((m.extraction_prompt ?? "").trim() && !window.confirm(`Rebuild the prompt for "${m.label_name}"? The current one goes into the prompt history.`)) return;
                    void generate([m.id], false);
                  }}>{busy === m.id ? "Building…" : (m.extraction_prompt ?? "").trim() ? "Regenerate" : "Generate"}</button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
