/** Manual (one-off) invoices - SaaS Finance > Invoices > Manual. Draft (editable, no number) ->
 *  Issue (next number from the same series as usage invoices, locked) -> Paid / Unpaid ->
 *  Cancel (number kept). Backend: app/api/v1/manual_invoices.py. */
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import { Modal } from "../../components/ui/Modal";
import { Alert } from "../../components/ui/Alert";
import { DocumentIcon, PlusIcon, TrashIcon } from "../../components/ui/icons";
import * as financeApi from "../../api/saasFinance";
import * as tenantsApi from "../../api/tenants";
import type { ManualInvoice, ManualInvoiceDraft, ManualInvoiceLine } from "../../api/saasFinance";
import type { Tenant } from "../../types/tenant";
import { formatInr } from "./financeFormat";

function errText(err: unknown, fallback: string): string {
  return axios.isAxiosError(err) && typeof err.response?.data?.detail === "string" ? err.response.data.detail : fallback;
}

function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function plusDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const ddmmyyyy = (iso: string | null) => (iso && iso.length >= 10 ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : "—");

const STATUS: Record<ManualInvoice["status"], string> = {
  draft: "bg-slate-100 text-slate-700 ring-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700",
  issued: "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:ring-amber-500/30",
  paid: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30",
  cancelled: "bg-rose-50 text-rose-700 ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-500/30",
};
const STATUS_LABEL: Record<ManualInvoice["status"], string> = { draft: "Draft", issued: "Unpaid", paid: "Paid", cancelled: "Cancelled" };

function emptyDraft(): ManualInvoiceDraft {
  const t = todayIso();
  return { tenant_id: "", invoice_date: t, due_date: plusDays(t, 7), period_label: "", gst_percent: 18, notes: "",
    lines: [{ description: "", sac: "", qty: 1, rate: 0 }] };
}

function Editor({ open, initial, editingId, tenants, onClose, onSaved }: {
  open: boolean; initial: ManualInvoiceDraft; editingId: string | null; tenants: Tenant[];
  onClose: () => void; onSaved: () => void;
}) {
  const [d, setD] = useState<ManualInvoiceDraft>(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setD(initial); setError(null); } }, [open, initial]);

  const subtotal = d.lines.reduce((s, l) => s + (Number(l.qty) || 0) * (Number(l.rate) || 0), 0);
  const gst = Math.round(subtotal * (Number(d.gst_percent) || 0)) / 100;
  const setLine = (i: number, patch: Partial<ManualInvoiceLine>) =>
    setD({ ...d, lines: d.lines.map((l, n) => (n === i ? { ...l, ...patch } : l)) });

  async function save(issueAfter: boolean) {
    setBusy(true);
    setError(null);
    try {
      if (!d.tenant_id) throw new Error("Choose the customer");
      const saved = editingId ? await financeApi.updateManualInvoice(editingId, d) : await financeApi.createManualInvoice(d);
      if (issueAfter) {
        if (!window.confirm("Issue this invoice? It gets the next invoice number and can no longer be edited (only cancelled).")) {
          onSaved();
          return;
        }
        await financeApi.issueManualInvoice(saved.id);
      }
      onSaved();
    } catch (err) {
      setError(err instanceof Error && !axios.isAxiosError(err) ? err.message : errText(err, "Could not save."));
    } finally {
      setBusy(false);
    }
  }

  const field = "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900";
  const label = "mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300";
  return (
    <Modal open={open} onClose={onClose} title={editingId ? "Edit draft invoice" : "New invoice"} maxWidth="max-w-4xl">
      <div className="space-y-4">
        {error && <Alert>{error}</Alert>}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="sm:col-span-2">
            <label className={label}>Customer</label>
            <select value={d.tenant_id} onChange={(e) => setD({ ...d, tenant_id: e.target.value })} className={field}>
              <option value="">Choose…</option>
              {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>
          <div>
            <label className={label}>Invoice date</label>
            <input type="date" value={d.invoice_date} className={field}
              onChange={(e) => setD({ ...d, invoice_date: e.target.value, due_date: d.due_date < e.target.value ? e.target.value : d.due_date })} />
          </div>
          <div>
            <label className={label}>Due date <span className="text-slate-400">(7 days by default)</span></label>
            <input type="date" value={d.due_date} min={d.invoice_date} onChange={(e) => setD({ ...d, due_date: e.target.value })} className={field} />
          </div>
          <div className="sm:col-span-2">
            <label className={label}>Service period (optional)</label>
            <input value={d.period_label} onChange={(e) => setD({ ...d, period_label: e.target.value })} placeholder="November 2026" className={field} />
          </div>
          <div>
            <label className={label}>GST %</label>
            <input type="number" min={0} max={28} step="0.01" value={d.gst_percent} onChange={(e) => setD({ ...d, gst_percent: Number(e.target.value) })} className={field} />
          </div>
        </div>

        <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-700">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="w-10 px-3 py-2 text-center">#</th><th className="px-3 py-2">Description</th>
                <th className="w-28 px-3 py-2">SAC</th><th className="w-20 px-3 py-2 text-right">Qty</th>
                <th className="w-32 px-3 py-2 text-right">Rate (₹)</th><th className="w-32 px-3 py-2 text-right">Amount</th><th className="w-10" />
              </tr>
            </thead>
            <tbody>
              {d.lines.map((l, i) => (
                <tr key={i} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="px-3 py-2 text-center text-slate-500">{i + 1}</td>
                  <td className="px-2 py-1.5"><input value={l.description} onChange={(e) => setLine(i, { description: e.target.value })}
                    placeholder="Separate ERP server - November 2026" className={field} /></td>
                  <td className="px-2 py-1.5"><input value={l.sac} onChange={(e) => setLine(i, { sac: e.target.value })} placeholder="998316" className={field} /></td>
                  <td className="px-2 py-1.5"><input type="number" min={0} step="0.01" value={l.qty} onChange={(e) => setLine(i, { qty: Number(e.target.value) })} className={`${field} text-right`} /></td>
                  <td className="px-2 py-1.5"><input type="number" min={0} step="0.01" value={l.rate} onChange={(e) => setLine(i, { rate: Number(e.target.value) })} className={`${field} text-right`} /></td>
                  <td className="px-3 py-2 text-right font-medium tabular-nums">{formatInr((Number(l.qty) || 0) * (Number(l.rate) || 0))}</td>
                  <td className="px-2 text-center">
                    {d.lines.length > 1 && (
                      <button type="button" onClick={() => setD({ ...d, lines: d.lines.filter((_, n) => n !== i) })} className="text-slate-400 hover:text-rose-600" title="Remove line">
                        <TrashIcon className="h-4 w-4" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button type="button" onClick={() => setD({ ...d, lines: [...d.lines, { description: "", sac: "", qty: 1, rate: 0 }] })}
            className="m-2 inline-flex items-center gap-1 rounded-lg px-2 py-1 text-sm font-medium text-indigo-700 hover:bg-indigo-50 dark:text-indigo-300 dark:hover:bg-indigo-500/10">
            <PlusIcon className="h-4 w-4" />Add line
          </button>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div>
            <label className={label}>Notes on the invoice (optional)</label>
            <textarea rows={3} value={d.notes} onChange={(e) => setD({ ...d, notes: e.target.value })} className={field} placeholder="Leave empty for the standard note" />
          </div>
          <div className="rounded-xl bg-slate-50 p-4 text-sm dark:bg-slate-800/60">
            <div className="flex justify-between py-0.5"><span className="text-slate-600 dark:text-slate-300">Subtotal</span><span className="tabular-nums">{formatInr(subtotal)}</span></div>
            <div className="flex justify-between py-0.5"><span className="text-slate-600 dark:text-slate-300">GST {d.gst_percent}%</span><span className="tabular-nums">{formatInr(gst)}</span></div>
            <div className="mt-1 flex justify-between border-t border-slate-200 pt-2 text-base font-bold dark:border-slate-700"><span>Total</span><span className="tabular-nums">{formatInr(subtotal + gst)}</span></div>
            <p className="mt-1 text-xs text-slate-500">CGST + SGST or IGST is chosen from the customer's state when it is issued.</p>
          </div>
        </div>

        <div className="flex flex-wrap justify-end gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
          <button type="button" onClick={onClose} className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium dark:border-slate-700">Close</button>
          <button type="button" disabled={busy} onClick={() => save(false)} className="rounded-lg border border-indigo-300 px-4 py-2 text-sm font-semibold text-indigo-700 hover:bg-indigo-50 disabled:opacity-60 dark:text-indigo-300">Save draft</button>
          <button type="button" disabled={busy} onClick={() => save(true)} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-60">Save & issue</button>
        </div>
      </div>
    </Modal>
  );
}

export function ManualInvoicesPanel() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<ManualInvoice[] | null>(null);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [editor, setEditor] = useState<{ open: boolean; id: string | null; draft: ManualInvoiceDraft }>({ open: false, id: null, draft: emptyDraft() });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function load() {
    try {
      setRows(await financeApi.listManualInvoices());
    } catch (err) {
      setError(errText(err, "Could not load manual invoices."));
    }
  }
  useEffect(() => {
    load();
    tenantsApi.listTenants().then((t) => setTenants(t.filter((x) => x.is_active))).catch(() => undefined);
  }, []);

  async function act(row: ManualInvoice, fn: () => Promise<unknown>) {
    setBusy(row.id);
    setError(null);
    try {
      await fn();
      await load();
    } catch (err) {
      setError(errText(err, "That did not work."));
    } finally {
      setBusy(null);
    }
  }

  const totals = useMemo(() => {
    const live = (rows ?? []).filter((r) => r.status === "issued" || r.status === "paid");
    return { unpaid: live.filter((r) => r.status === "issued").reduce((s, r) => s + r.total_amount, 0),
             paid: live.filter((r) => r.status === "paid").reduce((s, r) => s + r.total_amount, 0) };
  }, [rows]);

  const btn = "rounded-lg px-2.5 py-1 text-xs font-semibold disabled:opacity-50";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-slate-600 dark:text-slate-300">
          Unpaid <b className="tabular-nums">{formatInr(totals.unpaid)}</b> · Paid <b className="tabular-nums">{formatInr(totals.paid)}</b>
        </p>
        <button type="button" onClick={() => setEditor({ open: true, id: null, draft: emptyDraft() })}
          className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500">
          <PlusIcon className="h-4 w-4" />New invoice
        </button>
      </div>
      {error && <Alert>{error}</Alert>}
      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 dark:bg-slate-800/80">
            <tr>
              <th className="w-14 px-3 py-3 text-center">S.No</th><th className="px-4 py-3">Customer</th><th className="px-4 py-3">Invoice #</th>
              <th className="px-4 py-3">For</th><th className="px-4 py-3">Date</th><th className="px-4 py-3">Due</th>
              <th className="px-4 py-3 text-right">Total</th><th className="px-4 py-3">Status</th><th className="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows !== null && rows.length === 0 && (
              <tr><td colSpan={9} className="px-4 py-10 text-center text-slate-500">No manual invoices yet - press New invoice.</td></tr>
            )}
            {rows === null && <tr><td colSpan={9} className="px-4 py-10 text-center text-slate-500">Loading…</td></tr>}
            {(rows ?? []).map((r, i) => (
              <tr key={r.id} className="border-t border-slate-100 align-top dark:border-slate-800">
                <td className="px-3 py-3 text-center text-slate-500">{i + 1}</td>
                <td className="px-4 py-3 font-medium text-slate-900 dark:text-slate-100">{r.tenant_name}</td>
                <td className="whitespace-nowrap px-4 py-3">{r.invoice_number ?? <span className="text-slate-400">— draft —</span>}</td>
                <td className="max-w-xs px-4 py-3 text-slate-600 dark:text-slate-300">
                  {r.lines[0]?.description ?? "—"}{r.lines.length > 1 ? ` (+${r.lines.length - 1} more)` : ""}
                  {r.status === "cancelled" && r.cancel_reason && <span className="block text-xs text-rose-600">Cancelled: {r.cancel_reason}</span>}
                </td>
                <td className="whitespace-nowrap px-4 py-3">{ddmmyyyy(r.invoice_date)}</td>
                <td className="whitespace-nowrap px-4 py-3">{ddmmyyyy(r.due_date)}</td>
                <td className="whitespace-nowrap px-4 py-3 text-right font-semibold tabular-nums">{formatInr(r.total_amount)}</td>
                <td className="px-4 py-3"><span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${STATUS[r.status]}`}>{STATUS_LABEL[r.status]}</span></td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap justify-end gap-1.5">
                    <button type="button" onClick={() => navigate(`/saas-finance/invoices/${r.tenant_id}/${r.id}/print?manual=1`)}
                      className={`${btn} inline-flex items-center gap-1 border border-slate-200 text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200`}>
                      <DocumentIcon className="h-3.5 w-3.5" />{r.status === "draft" ? "Preview" : "Preview / Download"}
                    </button>
                    {r.status === "draft" && (
                      <>
                        <button type="button" disabled={busy === r.id} className={`${btn} border border-indigo-300 text-indigo-700 dark:text-indigo-300`}
                          onClick={() => setEditor({ open: true, id: r.id, draft: { tenant_id: r.tenant_id, invoice_date: r.invoice_date, due_date: r.due_date,
                            period_label: r.period_label ?? "", gst_percent: r.gst_percent, notes: r.notes ?? "",
                            lines: r.lines.map((l) => ({ description: l.description, sac: l.sac, qty: l.qty, rate: l.rate })) } })}>Edit</button>
                        <button type="button" disabled={busy === r.id} className={`${btn} bg-indigo-600 text-white hover:bg-indigo-500`}
                          onClick={() => window.confirm(`Issue this invoice to ${r.tenant_name}? It gets the next number and is locked.`) && act(r, () => financeApi.issueManualInvoice(r.id))}>Issue</button>
                        <button type="button" disabled={busy === r.id} className={`${btn} text-rose-600 hover:bg-rose-50`}
                          onClick={() => window.confirm("Delete this draft?") && act(r, () => financeApi.deleteManualInvoice(r.id))}>Delete</button>
                      </>
                    )}
                    {r.status === "issued" && (
                      <button type="button" disabled={busy === r.id} className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
                        onClick={() => act(r, () => financeApi.markManualInvoicePaid(r.id, true))}>Mark paid</button>
                    )}
                    {r.status === "paid" && (
                      <button type="button" disabled={busy === r.id} className={`${btn} border border-amber-300 text-amber-800 dark:text-amber-300`}
                        onClick={() => act(r, () => financeApi.markManualInvoicePaid(r.id, false))}>Mark unpaid</button>
                    )}
                    {(r.status === "issued" || r.status === "paid") && (
                      <button type="button" disabled={busy === r.id} className={`${btn} text-rose-600 hover:bg-rose-50`}
                        onClick={() => {
                          const reason = window.prompt(`Cancel ${r.invoice_number}? Its number stays used. Why is it cancelled?`, "");
                          if (reason && reason.trim()) act(r, () => financeApi.cancelManualInvoice(r.id, reason));
                        }}>Cancel</button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Editor open={editor.open} initial={editor.draft} editingId={editor.id} tenants={tenants}
        onClose={() => setEditor({ ...editor, open: false })}
        onSaved={() => { setEditor({ ...editor, open: false }); load(); }} />
    </div>
  );
}
