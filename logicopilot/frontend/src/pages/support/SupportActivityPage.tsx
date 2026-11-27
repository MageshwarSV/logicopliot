/** Production Support - Issues & Resolution: the support log - notes, customer calls, issues
 *  marked handled, retries and alerts - and a form to log a call or a note. */
import { useCallback, useEffect, useState, type FormEvent } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Alert } from "../../components/ui/Alert";
import { CheckBadgeIcon, PencilIcon } from "../../components/ui/icons";
import * as supportApi from "../../api/support";
import type { SupportActivity } from "../../api/support";
import { ActivityList, Pager, SectionCard, usePaged } from "./supportUi";

const FILTERS: { key: string; label: string }[] = [
  { key: "", label: "All" }, { key: "customer_call", label: "Customer calls" }, { key: "note", label: "Notes" },
  { key: "handled", label: "Handled" }, { key: "retry_erp", label: "Retry ERP" },
  { key: "retry_extraction", label: "Retry extraction" }, { key: "alert", label: "Alerts" },
];

export function SupportActivityPage() {
  const [filter, setFilter] = useState("");
  const [items, setItems] = useState<SupportActivity[]>([]);
  const [customers, setCustomers] = useState<{ id: string; name: string }[]>([]);
  const [kind, setKind] = useState<"customer_call" | "note">("customer_call");
  const [tenantId, setTenantId] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setItems(await supportApi.getActivity({ limit: 500, kind: filter || undefined }));
    } catch (err) {
      setError(axios.isAxiosError(err) ? (err.response?.data?.detail ?? "Could not load.") : "Could not load.");
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10);
    supportApi.getJobs({ date_from: today, date_to: today }).then((d) => setCustomers(d.customers)).catch(() => undefined);
  }, []);
  const paged = usePaged(items, filter);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await supportApi.addActivity({ kind, text, tenant_id: tenantId || null });
      setText("");
      await load();
    } catch (err) {
      setError(axios.isAxiosError(err) ? (err.response?.data?.detail ?? "Could not save.") : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  const field = "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm dark:border-slate-700 dark:bg-slate-900";
  return (
    <AppShell title="Issues & Resolution" icon={<CheckBadgeIcon className="h-6 w-6" />}
      subtitle="Everything the support desk did and heard - calls, notes, handled issues, retries, alerts.">
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <SectionCard title="Log a call or note" icon={<PencilIcon className="h-5 w-5" />} className="h-fit">
          <form onSubmit={submit} className="space-y-3">
            <div className="inline-flex rounded-xl border border-slate-200 p-1 dark:border-slate-700">
              {(["customer_call", "note"] as const).map((k) => (
                <button key={k} type="button" onClick={() => setKind(k)}
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium ${kind === k ? "bg-indigo-600 text-white" : "text-slate-600 dark:text-slate-300"}`}>
                  {k === "customer_call" ? "☎ Customer call" : "✎ Note"}
                </button>
              ))}
            </div>
            <select value={tenantId} onChange={(e) => setTenantId(e.target.value)} className={field}>
              <option value="">Customer (optional)</option>
              {customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={5} required
              placeholder={kind === "customer_call" ? "Who called, about what, what we told them" : "What happened"} className={field} />
            <button type="submit" disabled={busy} className="w-full rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-60">Save</button>
          </form>
          <p className="mt-3 text-xs text-slate-500">To note something about one job, use "Handled" on Failed / Delayed Jobs.</p>
        </SectionCard>
        <SectionCard padded={false} className="lg:col-span-2" title={<>Log <span className="text-sm font-normal text-slate-500">({items.length})</span></>}>
          <div className="flex flex-wrap gap-1.5 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
            {FILTERS.map((f) => (
              <button key={f.key} type="button" onClick={() => setFilter(f.key)}
                className={`rounded-full px-3 py-1 text-xs font-medium ${filter === f.key ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"}`}>
                {f.label}
              </button>
            ))}
          </div>
          {error && <div className="px-4 pt-3"><Alert>{error}</Alert></div>}
          <div className="px-4"><ActivityList items={paged.shown} /></div>
          <Pager {...paged} />
        </SectionCard>
      </div>
    </AppShell>
  );
}
