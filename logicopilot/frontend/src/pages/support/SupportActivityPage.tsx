/** Production Support - Issues & Resolution (S4): the support log - notes, customer calls,
 *  issues marked handled, retries and alerts - and a form to log a call or a note. */
import { useCallback, useEffect, useState, type FormEvent } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import * as supportApi from "../../api/support";
import type { SupportActivity } from "../../api/support";
import { ActivityList } from "./supportUi";

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
      setItems(await supportApi.getActivity({ limit: 200, kind: filter || undefined }));
    } catch (err) {
      setError(axios.isAxiosError(err) ? (err.response?.data?.detail ?? "Could not load.") : "Could not load.");
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10);
    supportApi.getJobs({ date_from: today, date_to: today }).then((d) => setCustomers(d.customers)).catch(() => undefined);
  }, []);

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

  const field = "rounded-lg border border-slate-300 px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900";
  return (
    <AppShell title="Issues & Resolution" subtitle="Everything the support desk did and heard - calls, notes, handled issues, retries, alerts.">
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-1">
          <h2 className="mb-3 font-semibold text-slate-900 dark:text-slate-50">Log a call or note</h2>
          <form onSubmit={submit} className="space-y-3">
            <div className="flex gap-2">
              {(["customer_call", "note"] as const).map((k) => (
                <button key={k} type="button" onClick={() => setKind(k)}
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium ${kind === k ? "bg-indigo-600 text-white" : "border border-slate-200 dark:border-slate-700"}`}>
                  {k === "customer_call" ? "Customer call" : "Note"}
                </button>
              ))}
            </div>
            <select value={tenantId} onChange={(e) => setTenantId(e.target.value)} className={`${field} w-full`}>
              <option value="">Customer (optional)</option>
              {customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} required
              placeholder={kind === "customer_call" ? "Who called, about what, what we told them" : "What happened"}
              className={`${field} w-full`} />
            <button type="submit" disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-60">Save</button>
          </form>
          <p className="mt-3 text-xs text-slate-500">To note something about one job, use "Mark handled" on Failed / Delayed Jobs.</p>
        </Card>
        <Card className="p-5 lg:col-span-2">
          <div className="mb-3 flex flex-wrap gap-2">
            {FILTERS.map((f) => (
              <button key={f.key} type="button" onClick={() => setFilter(f.key)}
                className={`rounded-lg px-3 py-1 text-xs font-medium ${filter === f.key ? "bg-indigo-600 text-white" : "border border-slate-200 text-slate-700 dark:border-slate-700 dark:text-slate-300"}`}>
                {f.label}
              </button>
            ))}
          </div>
          {error && <Alert>{error}</Alert>}
          <ActivityList items={items} />
        </Card>
      </div>
    </AppShell>
  );
}
