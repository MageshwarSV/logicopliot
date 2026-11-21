import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Modal } from "../../components/ui/Modal";
import { useAuth } from "../../auth/useAuth";
import * as tenantsApi from "../../api/tenants";
import type { Invoice } from "../../api/tenants";
import * as paymentsApi from "../../api/payments";
import type { PaymentOptions } from "../../api/payments";

function formatInr(n: number): string {
  return `₹${n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-IN", { month: "long", year: "numeric" });
}

const round2 = (n: number) => Math.round((n + Number.EPSILON) * 100) / 100;

/** The same sum the server makes (app/core/razorpay_gateway.compute_charge) - shown before the
 *  payment window opens. The server's figure is the one actually charged. */
function preview(total: number, opts: PaymentOptions) {
  const fee = opts.fee_percent > 0 ? round2((total * opts.fee_percent) / 100) : 0;
  const gst = fee > 0 ? round2((fee * opts.fee_gst_percent) / 100) : 0;
  return { fee, gst, pay: round2(total + fee + gst) };
}

function errText(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) return err.response?.data?.detail ?? fallback;
  return err instanceof Error ? err.message : fallback;
}

function StatusPill({ status }: { status: "unpaid" | "paid" }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${
        status === "paid"
          ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"
          : "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300"
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${status === "paid" ? "bg-emerald-500" : "bg-amber-500"}`} />
      {status === "paid" ? "Paid" : "Unpaid"}
    </span>
  );
}

interface RazorpayResponse {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
}
interface RazorpayInstance {
  open: () => void;
  on: (event: string, cb: (resp: { error?: { description?: string } }) => void) => void;
}
type RazorpayCtor = new (options: Record<string, unknown>) => RazorpayInstance;

/** Invoices, split out of Billing & Usage into its own sidebar destination. "View" jumps to the
 *  Usage page scoped to that invoice's month. A Tenant Admin can pay an unpaid invoice online
 *  (Pay now, Razorpay); bank transfer details stay shown for anyone who prefers that. */
export function InvoicesPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const tenantId = user?.tenant_id ?? null;
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [options, setOptions] = useState<PaymentOptions | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<Invoice | null>(null);
  const [paying, setPaying] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!tenantId) return;
    setLoading(true);
    // Options first: loading them also settles any payment Razorpay confirmed that we had not
    // heard back on yet, so the list below already shows it as paid.
    paymentsApi
      .getPaymentOptions(tenantId)
      .then(setOptions, () => setOptions(null))
      .then(() => tenantsApi.listInvoices(tenantId))
      .then(setInvoices)
      .catch((err) => setError(errText(err, "Could not load invoices.")))
      .finally(() => setLoading(false));
  }, [tenantId]);

  useEffect(() => { load(); }, [load]);

  async function pay(inv: Invoice) {
    if (!tenantId) return;
    setConfirming(null);
    setError(null);
    setNotice(null);
    setPaying(inv.id);
    try {
      await paymentsApi.loadCheckout();
      const order = await paymentsApi.startPayment(tenantId, inv.id);
      const Razorpay = (window as unknown as { Razorpay: RazorpayCtor }).Razorpay;
      const rzp = new Razorpay({
        key: order.key_id,
        amount: order.amount_paise,
        currency: order.currency,
        order_id: order.order_id,
        name: order.business_name,
        description: `Invoice ${order.invoice_number}`,
        prefill: { name: order.prefill_name, email: order.prefill_email },
        notes: { invoice_number: order.invoice_number },
        theme: { color: "#4f46e5" },
        handler: async (resp: RazorpayResponse) => {
          try {
            const result = await paymentsApi.verifyPayment(tenantId, inv.id, resp);
            setNotice(
              `Payment received for ${order.invoice_number} - ${formatInr(order.total_amount)}` +
                (result.payment_reference ? ` (reference ${result.payment_reference})` : "") + ".",
            );
          } catch (err) {
            setError(errText(err, "The payment could not be confirmed. If money was taken, it will show as paid within a few minutes."));
          } finally {
            setPaying(null);
            load();
          }
        },
        modal: {
          ondismiss: () => {
            setPaying(null);
            setNotice("Payment window closed. Nothing was charged unless you completed the payment.");
          },
        },
      });
      rzp.on("payment.failed", (resp) => {
        setError(`Payment failed: ${resp.error?.description ?? "please try again or pay by bank transfer."}`);
      });
      rzp.open();
    } catch (err) {
      setPaying(null);
      setError(errText(err, "Could not start the payment."));
    }
  }

  const canPay = !!options?.can_pay;
  const p = confirming && options ? preview(confirming.total_amount, options) : null;

  return (
    <AppShell title="Invoices" subtitle="Every invoice raised for your organization, most recent first.">
      {error && <p className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">{error}</p>}
      {notice && <p className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200">{notice}</p>}
      <Card className="p-5">
        {loading ? (
          <p className="py-10 text-center text-sm text-slate-400">Loading…</p>
        ) : invoices.length === 0 ? (
          <p className="py-10 text-center text-sm text-slate-400">No invoices raised yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                <th className="py-2 font-medium">Invoice #</th>
                <th className="py-2 font-medium">Period</th>
                <th className="py-2 font-medium">Total</th>
                <th className="py-2 font-medium">Status</th>
                <th className="py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {invoices.map((inv) => (
                <tr key={inv.id} className="border-b border-slate-100 last:border-0 dark:border-slate-800/60">
                  <td className="py-2 text-slate-700 dark:text-slate-300">{inv.invoice_number}</td>
                  <td className="py-2 text-slate-700 dark:text-slate-300">{monthLabel(inv.period_start.slice(0, 7))}</td>
                  <td className="py-2 font-medium text-slate-900 dark:text-slate-100">{formatInr(inv.total_amount)}</td>
                  <td className="py-2">
                    <StatusPill status={inv.status} />
                    {inv.status === "paid" && inv.payment_reference && (
                      <span className="ml-2 text-xs text-slate-400" title="Razorpay payment reference">Paid online · {inv.payment_reference}</span>
                    )}
                  </td>
                  <td className="py-2 text-right whitespace-nowrap">
                    {canPay && inv.status === "unpaid" && inv.total_amount > 0 && (
                      <Button size="sm" className="mr-2" isLoading={paying === inv.id} disabled={paying !== null}
                        onClick={() => setConfirming(inv)}>
                        Pay now
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => navigate(`/tenant-admin/billing?month=${inv.period_start.slice(0, 7)}`)}
                    >
                      View
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {options?.bank_details && invoices.some((i) => i.status === "unpaid") && (
        <Card className="mt-4 p-5">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            {canPay ? "Prefer a bank transfer?" : "Pay by bank transfer"}
          </h3>
          <pre className="mt-2 whitespace-pre-wrap font-sans text-sm text-slate-700 dark:text-slate-300">{options.bank_details}</pre>
          <p className="mt-2 text-xs text-slate-500">Quote the invoice number. The invoice is marked paid once the transfer is received.</p>
        </Card>
      )}

      <Modal open={!!confirming} onClose={() => setConfirming(null)} title={confirming ? `Pay invoice ${confirming.invoice_number}` : "Pay invoice"}>
        {confirming && p && options && (
          <div className="space-y-4 text-sm">
            <dl className="space-y-2">
              <div className="flex justify-between"><dt className="text-slate-500">Invoice amount</dt><dd className="tabular-nums">{formatInr(confirming.total_amount)}</dd></div>
              {p.fee > 0 && (
                <>
                  <div className="flex justify-between"><dt className="text-slate-500">Payment gateway charge ({options.fee_percent}%)</dt><dd className="tabular-nums">{formatInr(p.fee)}</dd></div>
                  <div className="flex justify-between"><dt className="text-slate-500">GST on gateway charge ({options.fee_gst_percent}%)</dt><dd className="tabular-nums">{formatInr(p.gst)}</dd></div>
                </>
              )}
              <div className="flex justify-between border-t border-slate-200 pt-2 font-semibold dark:border-slate-700"><dt>Total to pay</dt><dd className="tabular-nums">{formatInr(p.pay)}</dd></div>
            </dl>
            <p className="text-xs text-slate-500">
              You can pay by UPI, card or netbanking in the secure Razorpay window.
              {p.fee > 0 && options.bank_details ? " A bank transfer has no gateway charge." : ""}
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setConfirming(null)}>Cancel</Button>
              <Button onClick={() => pay(confirming)}>Continue to payment</Button>
            </div>
          </div>
        )}
      </Modal>
    </AppShell>
  );
}
