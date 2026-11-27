import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import * as financeApi from "../../api/saasFinance";
import type { TaxInvoice } from "../../api/saasFinance";

/** A proper GST tax invoice, laid out like Axiora's own (seller block, invoice #/date/terms/due
 *  date, place of supply, Bill To / Ship To, HSN/SAC lines with CGST+SGST or IGST, totals, amount
 *  in words, balance due). "Download" is the browser's Print -> Save as PDF; no AppShell here so
 *  only the invoice is printed. Data from GET .../billing/invoices/{id}/print. */
const money = (n: number) => n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const ddmmyyyy = (iso: string) => (iso && iso.length >= 10 ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : iso);

export function SaasFinanceInvoicePrintPage() {
  const { tenantId, invoiceId } = useParams<{ tenantId: string; invoiceId: string }>();
  const manual = useSearchParams()[0].get("manual") === "1";
  const [inv, setInv] = useState<TaxInvoice | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId || !invoiceId) return;
    (manual ? financeApi.getManualTaxInvoice(invoiceId) : financeApi.getTaxInvoice(tenantId, invoiceId))
      .then(setInv).catch(() => setError("Could not load this invoice."));
  }, [tenantId, invoiceId, manual]);

  if (error) return <div className="p-8 text-sm text-rose-600">{error}</div>;
  if (!inv) return <div className="p-8 text-sm text-slate-400">Loading…</div>;

  const igst = inv.tax_mode === "igst";
  const half = inv.gst_percent / 2;
  const cell = "border border-slate-400 px-2 py-1.5 align-top";
  const head = "border border-slate-400 bg-slate-100 px-2 py-1.5 font-semibold";
  const s = inv.seller;
  const b = inv.buyer;

  return (
    <div className="mx-auto max-w-4xl bg-white p-8 text-[13px] text-slate-900 print:max-w-none print:p-0">
      <div className="mb-6 flex items-center justify-between print:hidden">
        <h1 className="text-lg font-semibold">
          Invoice {inv.invoice_number}
          {inv.status === "draft" && <span className="ml-2 rounded bg-slate-200 px-2 py-0.5 text-xs font-bold text-slate-700">DRAFT - not issued</span>}
          {inv.status === "cancelled" && <span className="ml-2 rounded bg-rose-100 px-2 py-0.5 text-xs font-bold text-rose-700">CANCELLED</span>}
        </h1>
        <button type="button" onClick={() => window.print()}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500">
          Print / Save as PDF
        </button>
      </div>

      {(inv.status === "draft" || inv.status === "cancelled") && (
        <div className={`mb-2 border-2 py-1.5 text-center text-sm font-bold uppercase tracking-widest ${inv.status === "draft" ? "border-slate-400 text-slate-600" : "border-rose-500 text-rose-600"}`}>
          {inv.status === "draft" ? "Draft - not a valid tax invoice" : "Cancelled"}
        </div>
      )}
      <div className="border border-slate-400">
        {/* Seller + title */}
        <div className="flex items-start justify-between border-b border-slate-400 p-4">
          <div>
            <p className="text-xl font-bold">{s.name || "—"}</p>
            {s.address && <p className="whitespace-pre-line">{s.address}</p>}
            <p>{[s.state_name, s.country].filter(Boolean).join(", ")}</p>
            {s.gstin && <p>GSTIN {s.gstin}</p>}
            {s.email && <p>{s.email}</p>}
            {s.phone && <p>{s.phone}</p>}
          </div>
          <p className="self-end text-3xl font-light tracking-wide">TAX INVOICE</p>
        </div>

        {/* Invoice details + place of supply */}
        <div className="grid grid-cols-2 border-b border-slate-400">
          <div className="border-r border-slate-400 p-3">
            {[["#", inv.invoice_number], ["Invoice Date", ddmmyyyy(inv.invoice_date)], ["Terms", inv.terms],
              ["Due Date", ddmmyyyy(inv.due_date)], ["Period", inv.period]].map(([k, v]) => (
              <div key={k} className="grid grid-cols-[120px_1fr]"><span>{k}</span><span className="font-semibold">: {v}</span></div>
            ))}
          </div>
          <div className="p-3">
            <div className="grid grid-cols-[120px_1fr]"><span>Place Of Supply</span><span className="font-semibold">: {inv.place_of_supply}</span></div>
          </div>
        </div>

        {/* Bill to / Ship to */}
        <div className="grid grid-cols-2 border-b border-slate-400">
          <div className="border-r border-slate-400">
            <p className="border-b border-slate-400 bg-slate-100 px-3 py-1 font-semibold">Bill To</p>
            <div className="p-3">
              <p className="font-bold uppercase">{b.name}</p>
              {b.address && <p className="whitespace-pre-line">{b.address}</p>}
              {b.state_name && <p>{b.state_name}</p>}
              {b.gstin && <p>GSTIN {b.gstin}</p>}
            </div>
          </div>
          <div>
            <p className="border-b border-slate-400 bg-slate-100 px-3 py-1 font-semibold">Ship To</p>
            <div className="p-3">
              {b.ship_to ? <p className="whitespace-pre-line">{b.ship_to}</p> : b.gstin ? <p>GSTIN {b.gstin}</p> : <p>{b.name}</p>}
            </div>
          </div>
        </div>

        {/* Lines */}
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th rowSpan={2} className={`${head} w-8`}>#</th>
              <th rowSpan={2} className={`${head} text-left`}>Item &amp; Description</th>
              <th rowSpan={2} className={head}>HSN /SAC</th>
              <th rowSpan={2} className={`${head} text-right`}>Qty</th>
              <th rowSpan={2} className={`${head} text-right`}>Rate</th>
              {igst ? <th colSpan={2} className={head}>IGST</th> : <><th colSpan={2} className={head}>CGST</th><th colSpan={2} className={head}>SGST</th></>}
              <th rowSpan={2} className={`${head} text-right`}>Amount</th>
            </tr>
            <tr>
              {(igst ? ["%", "Amt"] : ["%", "Amt", "%", "Amt"]).map((h, i) => <th key={i} className={`${head} text-right`}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {inv.lines.map((l, i) => (
              <tr key={i}>
                <td className={`${cell} text-center`}>{i + 1}</td>
                <td className={cell}>{l.description}</td>
                <td className={`${cell} text-center`}>{l.sac}</td>
                <td className={`${cell} text-right`}>{money(l.qty)}</td>
                <td className={`${cell} text-right`}>{money(l.rate)}</td>
                {igst ? (
                  <><td className={`${cell} text-right`}>{l.igst_percent}%</td><td className={`${cell} text-right`}>{money(l.igst)}</td></>
                ) : (
                  <>
                    <td className={`${cell} text-right`}>{l.cgst_percent}%</td><td className={`${cell} text-right`}>{money(l.cgst)}</td>
                    <td className={`${cell} text-right`}>{l.sgst_percent}%</td><td className={`${cell} text-right`}>{money(l.sgst)}</td>
                  </>
                )}
                <td className={`${cell} text-right`}>{money(l.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* Words + totals */}
        <div className="grid grid-cols-[1fr_340px]">
          <div className="border-r border-slate-400 p-3">
            <p>Total In Words</p>
            <p className="font-semibold italic">{inv.amount_in_words}</p>
            <p className="mt-4">Notes</p>
            <p className="whitespace-pre-line">{inv.notes}</p>
            {inv.bank_details && (<><p className="mt-4">Bank details</p><p className="whitespace-pre-line">{inv.bank_details}</p></>)}
          </div>
          <div className="p-3">
            {[["Sub Total", money(inv.subtotal)],
              ...(igst ? [[`IGST (${inv.gst_percent}%)`, money(inv.igst)]]
                : [[`CGST (${half}%)`, money(inv.cgst)], [`SGST (${half}%)`, money(inv.sgst)]])].map(([k, v]) => (
              <div key={k} className="flex justify-between py-0.5"><span>{k}</span><span>{v}</span></div>
            ))}
            <div className="mt-1 flex justify-between py-0.5 font-bold"><span>Total</span><span>₹{money(inv.total)}</span></div>
            <div className="flex justify-between border-t border-slate-300 py-1 text-base font-bold">
              <span>Balance Due</span><span>₹{money(inv.balance_due)}</span>
            </div>
            {inv.status === "paid" && <p className="text-right text-xs font-medium text-emerald-700">Paid{inv.paid_at ? ` on ${new Date(inv.paid_at).toLocaleDateString("en-IN")}` : ""}</p>}
          </div>
        </div>
      </div>
    </div>
  );
}
