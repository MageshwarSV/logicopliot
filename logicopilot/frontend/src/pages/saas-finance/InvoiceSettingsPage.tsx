import { useEffect, useState, type FormEvent } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import * as financeApi from "../../api/saasFinance";

const field = "w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";

/** SaaS Finance -> Invoice settings: Axiora's own details on every Cargora tax invoice.
 *  Used for invoices raised from now on - one already raised keeps the details it was raised with. */
export function SaasFinanceInvoiceSettingsPage() {
  const [s, setS] = useState<financeApi.InvoiceSettings>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    financeApi.getInvoiceSettings().then(setS).catch(() => setError("Could not load the invoice settings.")).finally(() => setLoading(false));
  }, []);

  const set = (k: keyof financeApi.InvoiceSettings, v: string | number) => { setS((x) => ({ ...x, [k]: v })); setSaved(false); };

  async function save(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      setS(await financeApi.saveInvoiceSettings(s));
      setSaved(true);
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not save." : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  const label = (text: string, node: React.ReactNode, hint?: string) => (
    <label className="block text-sm text-slate-700 dark:text-slate-200">
      <span className="font-medium">{text}</span>
      {hint && <span className="block text-xs text-slate-500">{hint}</span>}
      <div className="mt-1">{node}</div>
    </label>
  );

  return (
    <AppShell title="Invoice Settings" subtitle="Axiora's details printed on every Cargora tax invoice - used for invoices raised from now on.">
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}
      {loading ? <p className="text-sm text-slate-400">Loading…</p> : (
        <form onSubmit={save} className="space-y-6">
          <Card className="p-5">
            <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Company</h2>
            <div className="grid gap-4 md:grid-cols-2">
              {label("Legal name", <input className={field} value={s.seller_name ?? ""} onChange={(e) => set("seller_name", e.target.value)} placeholder="AXIORA LABS AI (OPC) PRIVATE LIMITED" />)}
              {label("GSTIN", <input className={field} value={s.gstin ?? ""} onChange={(e) => set("gstin", e.target.value.toUpperCase())} placeholder="33ABGCA3833P1ZF" maxLength={15} />, "The first two digits set the state.")}
              {label("Address", <textarea className={field} rows={3} value={s.address ?? ""} onChange={(e) => set("address", e.target.value)} placeholder={"Street, area\nCity - PIN"} />)}
              <div className="grid gap-4">
                {label("State", (
                  <select className={field} value={s.state_code ?? ""} onChange={(e) => {
                    const st = financeApi.GST_STATES.find(([c]) => c === e.target.value);
                    setS((x) => ({ ...x, state_code: e.target.value, state_name: st ? st[1] : "" }));
                    setSaved(false);
                  }}>
                    <option value="">— select —</option>
                    {financeApi.GST_STATES.map(([c, n]) => <option key={c} value={c}>{n} ({c})</option>)}
                  </select>
                ))}
                {label("Country", <input className={field} value={s.country ?? ""} onChange={(e) => set("country", e.target.value)} />)}
              </div>
              {label("Email", <input className={field} value={s.email ?? ""} onChange={(e) => set("email", e.target.value)} />)}
              {label("Phone", <input className={field} value={s.phone ?? ""} onChange={(e) => set("phone", e.target.value)} />)}
            </div>
          </Card>

          <Card className="p-5">
            <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Invoice</h2>
            <div className="grid gap-4 md:grid-cols-3">
              {label("Number prefix", <input className={field} value={s.number_prefix ?? ""} onChange={(e) => set("number_prefix", e.target.value)} />, "Numbers run per financial year: INV/2026-27/0001")}
              {label("Terms", <input className={field} value={s.terms ?? ""} onChange={(e) => set("terms", e.target.value)} placeholder="Due on Receipt" />)}
              {label("Due in (days)", <input className={field} type="number" min={0} max={365} value={s.due_days ?? 0} onChange={(e) => set("due_days", Number(e.target.value) || 0)} />, "0 = due on the invoice date")}
              {label("SAC – platform fee", <input className={field} value={s.sac_platform ?? ""} onChange={(e) => set("sac_platform", e.target.value)} placeholder="998315" />)}
              {label("SAC – document processing", <input className={field} value={s.sac_usage ?? ""} onChange={(e) => set("sac_usage", e.target.value)} placeholder="998313" />)}
            </div>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              {label("Notes", <textarea className={field} rows={3} value={s.notes ?? ""} onChange={(e) => set("notes", e.target.value)} />)}
              {label("Bank details", <textarea className={field} rows={3} value={s.bank_details ?? ""} onChange={(e) => set("bank_details", e.target.value)} placeholder={"Bank, A/c no, IFSC"} />, "Printed at the bottom of the invoice (optional).")}
            </div>
          </Card>

          <div className="flex items-center gap-3">
            <button type="submit" disabled={saving} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
              {saving ? "Saving…" : "Save"}
            </button>
            {saved && <span className="text-sm text-emerald-600">Saved ✓</span>}
          </div>
        </form>
      )}
    </AppShell>
  );
}
