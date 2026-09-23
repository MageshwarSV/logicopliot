import { useEffect, useState } from "react";
import { Button } from "../../components/ui/Button";
import * as api from "../../api/onboarding";
import type { ReferenceSheet } from "../../api/onboarding";
import type { TemplateGroup } from "../../types/onboarding";

function errText(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

/** Every customer's bulk reference sheet (material code -> CTH, e.g. Nokia's 22,000-row
 *  export) in one place, instead of having to open each template's own wizard to check or
 *  replace one. Same upload as the wizard's own "Reference sheet" card (Step 4) - this is a
 *  second front door onto the identical /template-groups/{id}/material-master routes, not a
 *  separate feature. */
export function ReferenceSheetsSection() {
  const [groups, setGroups] = useState<TemplateGroup[]>([]);
  const [sheets, setSheets] = useState<Record<string, ReferenceSheet | null>>({});
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [errorId, setErrorId] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const gs = await api.listGroups();
        if (cancelled) return;
        setGroups(gs);
        const entries = await Promise.all(
          gs.map(async (g) => {
            try {
              return [g.id, await api.getReferenceSheet(g.id)] as const;
            } catch {
              return [g.id, null] as const;
            }
          }),
        );
        if (!cancelled) setSheets(Object.fromEntries(entries));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  async function pollUntilDone(groupId: string) {
    // Same 25-30+ second background parse the wizard's own upload waits on - see
    // app/core/material_master.start_background_parse for why this is never done inline.
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      const sheet = await api.getReferenceSheet(groupId);
      setSheets((prev) => ({ ...prev, [groupId]: sheet }));
      if (!sheet.processing) return;
    }
  }

  async function upload(groupId: string, file: File) {
    setBusyId(groupId);
    setErrorId((prev) => ({ ...prev, [groupId]: "" }));
    try {
      const res = await api.uploadReferenceSheet(groupId, file);
      setSheets((prev) => ({ ...prev, [groupId]: res }));
      await pollUntilDone(groupId);
    } catch (err) {
      setErrorId((prev) => ({ ...prev, [groupId]: errText(err, "That sheet could not be read.") }));
    } finally {
      setBusyId(null);
    }
  }

  async function remove(groupId: string) {
    if (!window.confirm("Remove this reference sheet? Fields that look values up in it will come out blank until a new one is uploaded.")) return;
    setBusyId(groupId);
    try {
      await api.deleteReferenceSheet(groupId);
      setSheets((prev) => ({ ...prev, [groupId]: { attached: false, materials: 0 } }));
    } catch {
      setErrorId((prev) => ({ ...prev, [groupId]: "The sheet could not be removed." }));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="border-t border-slate-200 px-5 py-5 dark:border-slate-800">
      <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">Customer reference sheets</h3>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Every template's own bulk export — a part code against the CTH it is declared under
        (e.g. Nokia's material master). A "lookup" custom field reads this instead of asking an
        operator to type the same code on every line. A large sheet is read in the background —
        this can take up to a minute; the row updates on its own once it's done.
      </p>

      {loading && <p className="mt-3 text-xs text-slate-400">Loading templates…</p>}

      {!loading && groups.length === 0 && (
        <p className="mt-3 text-xs text-slate-400">No templates yet.</p>
      )}

      {!loading && groups.length > 0 && (
        <div className="mt-3 divide-y divide-slate-100 rounded-lg border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
          {groups.map((g) => {
            const sheet = sheets[g.id];
            const busy = busyId === g.id;
            return (
              <div key={g.id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-2.5 text-xs">
                <div className="min-w-0">
                  <p className="font-medium text-slate-800 dark:text-slate-100">
                    {g.name}
                    {g.mode && <span className="ml-2 font-normal text-slate-400">{g.mode}</span>}
                  </p>
                  {sheet?.processing ? (
                    <p className="mt-0.5 text-slate-500">{sheet.file_name} — reading it now…</p>
                  ) : sheet?.attached ? (
                    <p className="mt-0.5 text-slate-500">
                      {sheet.file_name} — {sheet.materials.toLocaleString()} usable key(s)
                      {sheet.detail && <span className="text-amber-700 dark:text-amber-400"> · {sheet.detail}</span>}
                    </p>
                  ) : (
                    <p className="mt-0.5 text-slate-400">No sheet attached</p>
                  )}
                  {errorId[g.id] && <p className="mt-0.5 text-rose-600 dark:text-rose-400">{errorId[g.id]}</p>}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <label className="cursor-pointer rounded-md border border-slate-300 px-2.5 py-1 font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800">
                    {busy ? "Working…" : sheet?.attached ? "Replace" : "Upload"}
                    <input
                      type="file"
                      accept=".xlsx,.xlsm"
                      className="hidden"
                      disabled={busy}
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        e.target.value = "";
                        if (f) void upload(g.id, f);
                      }}
                    />
                  </label>
                  {sheet?.attached && !sheet.processing && (
                    <Button size="sm" variant="ghost" onClick={() => void remove(g.id)} disabled={busy}>
                      Remove
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
