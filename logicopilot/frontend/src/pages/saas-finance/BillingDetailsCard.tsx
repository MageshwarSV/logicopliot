import { useEffect, useState, type FormEvent } from "react";
import axios from "axios";
import { Card } from "../../components/ui/Card";
import * as financeApi from "../../api/saasFinance";

const field = "w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";

/** The customer's details on their Cargora tax invoice (Bill To / Ship To / place of supply).
 *  Used for invoices raised from now on. Saving here never starts invoicing. */
export function BillingDetailsCard({ tenantId }: { tenantId: string }) {
  const [d, setD] = useState<financeApi.BillingDetails>({});
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    financeApi.getBillingDetails(tenantId).then(setD).catch(() => setError("Could not load the billing details."));
  }, [tenantId]);

  const set = (k: keyof financeApi.BillingDetails, v: string) => { setD((x) => ({ ...x, [k]: v })); setMsg(null); };

  async function save(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      setD(await financeApi.saveBillingDetails(tenantId, d));
      setMsg("Saved ✓");
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
    <Card className="mb-6 p-5">
      <h2 className="mb-1 font-semibold text-slate-900 dark:text-slate-50">Billing details (on the invoice)</h2>
      <p className="mb-4 text-xs text-slate-500">Bill To, GSTIN and place of supply for invoices raised from now on.</p>
      <form onSubmit={save} className="grid gap-4 md:grid-cols-2">
        {label("Legal name", <input className={field} value={d.billing_legal_name ?? ""} onChange={(e) => set("billing_legal_name", e.target.value)} placeholder="KSS ROADWAYS PRIVATE LIMITED" />)}
        {label("GSTIN", <input className={field} value={d.billing_gstin ?? ""} maxLength={15}
          onChange={(e) => {
            const g = e.target.value.toUpperCase();
            const st = financeApi.GST_STATES.find(([c]) => c === g.slice(0, 2));
            setD((x) => ({ ...x, billing_gstin: g, ...(st ? { billing_state_code: st[0], billing_state_name: st[1] } : {}) }));
            setMsg(null);
          }} placeholder="33AAFCK1245B1Z7" />, "The first two digits set the state.")}
        {label("Billing address", <textarea className={field} rows={3} value={d.billing_address ?? ""} onChange={(e) => set("billing_address", e.target.value)} />)}
        <div className="grid gap-4">
          {label("State (place of supply)", (
            <select className={field} value={d.billing_state_code ?? ""} onChange={(e) => {
              const st = financeApi.GST_STATES.find(([c]) => c === e.target.value);
              setD((x) => ({ ...x, billing_state_code: e.target.value, billing_state_name: st ? st[1] : "" }));
              setMsg(null);
            }}>
              <option value="">— select —</option>
              {financeApi.GST_STATES.map(([c, n]) => <option key={c} value={c}>{n} ({c})</option>)}
            </select>
          ), "Same state as Axiora: CGST + SGST. Other state: IGST.")}
          {label("Billing email", <input className={field} value={d.billing_email ?? ""} onChange={(e) => set("billing_email", e.target.value)} />)}
        </div>
        {label("Ship to (optional)", <textarea className={field} rows={2} value={d.billing_ship_to ?? ""} onChange={(e) => set("billing_ship_to", e.target.value)} />, "Left empty: the GSTIN is shown under Ship To.")}
        <div className="flex items-end gap-3">
          <button type="submit" disabled={saving} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
            {saving ? "Saving…" : "Save billing details"}
          </button>
          {msg && <span className="text-sm text-emerald-600">{msg}</span>}
          {error && <span className="text-sm text-rose-600">{error}</span>}
        </div>
      </form>
    </Card>
  );
}
