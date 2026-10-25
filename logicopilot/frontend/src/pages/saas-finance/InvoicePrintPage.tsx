import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import * as tenantsApi from "../../api/tenants";
import type { Invoice } from "../../api/tenants";
import type { Tenant } from "../../types/tenant";
import { formatInr, monthLabel } from "./financeFormat";

/** No PDF generator exists anywhere in this backend - "download" is this clean, print-styled
 *  page and the browser's own "Print to PDF". No AppShell here on purpose: nothing but the
 *  invoice itself should ever end up on the printed page. */
export function SaasFinanceInvoicePrintPage() {
  const { tenantId, invoiceId } = useParams<{ tenantId: string; invoiceId: string }>();
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId || !invoiceId) return;
    Promise.all([tenantsApi.getTenant(tenantId), tenantsApi.listInvoices(tenantId)])
      .then(([t, invoices]) => {
        setTenant(t);
        const inv = invoices.find((i) => i.id === invoiceId);
        if (!inv) {
          setError("Invoice not found.");
          return;
        }
        setInvoice(inv);
      })
      .catch(() => setError("Could not load this invoice."));
  }, [tenantId, invoiceId]);

  if (error) {
    return <div className="p-8 text-sm text-rose-600">{error}</div>;
  }
  if (!tenant || !invoice) {
    return <div className="p-8 text-sm text-slate-400">Loading…</div>;
  }

  return (
    <div className="mx-auto max-w-2xl bg-white p-10 text-slate-900 print:p-0">
      <div className="mb-8 flex items-center justify-between print:hidden">
        <h1 className="text-lg font-semibold">Invoice {invoice.invoice_number}</h1>
        <button
          type="button"
          onClick={() => window.print()}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
        >
          Print / Save as PDF
        </button>
      </div>

      <div className="mb-8 flex items-start justify-between">
        <div>
          <p className="text-2xl font-bold">Cargora</p>
          <p className="text-sm text-slate-500">Tax Invoice</p>
        </div>
        <div className="text-right text-sm">
          <p className="font-semibold">{invoice.invoice_number}</p>
          <p className="text-slate-500">{monthLabel(invoice.period_start.slice(0, 7))}</p>
        </div>
      </div>

      <div className="mb-8">
        <p className="text-xs uppercase tracking-wide text-slate-400">Billed To</p>
        <p className="text-base font-medium">{tenant.name}</p>
      </div>

      <table className="mb-8 w-full text-sm">
        <thead>
          <tr className="border-b border-slate-300 text-left">
            <th className="py-2 font-medium">Description</th>
            <th className="py-2 text-right font-medium">Amount</th>
          </tr>
        </thead>
        <tbody>
          <tr className="border-b border-slate-100">
            <td className="py-2">Platform fee ({invoice.platform_fee_billed_days} of {invoice.platform_fee_month_days} days)</td>
            <td className="py-2 text-right">{formatInr(invoice.platform_fee)}</td>
          </tr>
          <tr className="border-b border-slate-100">
            <td className="py-2">Tier 1 usage ({invoice.tier1_pages.toLocaleString()} pages × ₹{invoice.tier1_rate})</td>
            <td className="py-2 text-right">{formatInr(invoice.tier1_amount)}</td>
          </tr>
          <tr className="border-b border-slate-100">
            <td className="py-2">Tier 2 usage ({invoice.tier2_pages.toLocaleString()} pages × ₹{invoice.tier2_rate})</td>
            <td className="py-2 text-right">{formatInr(invoice.tier2_amount)}</td>
          </tr>
          <tr className="border-b border-slate-100 font-medium">
            <td className="py-2">Subtotal</td>
            <td className="py-2 text-right">{formatInr(invoice.subtotal)}</td>
          </tr>
          <tr className="border-b border-slate-100">
            <td className="py-2">GST ({invoice.gst_percent}%)</td>
            <td className="py-2 text-right">{formatInr(invoice.gst_amount)}</td>
          </tr>
          <tr className="text-base font-semibold">
            <td className="py-3">Total</td>
            <td className="py-3 text-right">{formatInr(invoice.total_amount)}</td>
          </tr>
        </tbody>
      </table>

      <div className="flex items-center justify-between text-sm">
        <span className="text-slate-500">Status</span>
        <span className={`font-medium ${invoice.status === "paid" ? "text-emerald-600" : "text-amber-600"}`}>
          {invoice.status === "paid" ? `Paid${invoice.paid_at ? ` on ${new Date(invoice.paid_at).toLocaleDateString("en-IN")}` : ""}` : "Unpaid"}
        </span>
      </div>
    </div>
  );
}
