import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card, StatCard } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import * as tenantsApi from "../../api/tenants";
import type { BillingUsage, Invoice } from "../../api/tenants";
import type { Tenant } from "../../types/tenant";
import { currentMonthStr, formatInr, monthLabel, shiftMonth } from "./financeFormat";

/** One customer's own billing breakdown, reusing the EXISTING getBillingUsage/listInvoices
 *  (frontend/src/api/tenants.ts) completely unchanged - no new API client code needed here,
 *  the backend already allows saas_finance to call these for any tenant. */
export function SaasFinanceCustomerDetailPage() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const navigate = useNavigate();
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [month, setMonth] = useState(currentMonthStr());
  const [usage, setUsage] = useState<BillingUsage | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    tenantsApi.getTenant(tenantId).then(setTenant).catch(() => {});
  }, [tenantId]);

  useEffect(() => {
    if (!tenantId) return;
    setLoading(true);
    setError(null);
    const isCurrentMonth = month === currentMonthStr();
    Promise.all([
      tenantsApi.getBillingUsage(tenantId, isCurrentMonth ? undefined : month),
      tenantsApi.listInvoices(tenantId),
    ])
      .then(([u, inv]) => {
        setUsage(u);
        setInvoices(inv);
      })
      .catch((err) => {
        setUsage(null);
        setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not load this customer's billing." : "Could not load this customer's billing.");
      })
      .finally(() => setLoading(false));
  }, [tenantId, month]);

  return (
    <AppShell
      title={tenant?.name ?? "Customer"}
      subtitle="Monthly usage and billing breakdown."
      actions={
        <Button variant="ghost" size="sm" onClick={() => navigate("/saas-finance/customers")}>
          ← All customers
        </Button>
      }
    >
      <Card className="mb-6 flex items-center justify-between p-4">
        <Button variant="ghost" size="sm" onClick={() => setMonth((m) => shiftMonth(m, -1))}>
          ← Previous
        </Button>
        <p className="font-semibold text-slate-900 dark:text-slate-50">{monthLabel(month)}</p>
        <Button variant="ghost" size="sm" onClick={() => setMonth((m) => shiftMonth(m, 1))} disabled={month === currentMonthStr()}>
          Next →
        </Button>
      </Card>

      {error && <div className="mb-6"><Alert>{error}</Alert></div>}

      {!loading && usage && (
        <>
          <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatCard label="Total Jobs" value={usage.total_jobs.toLocaleString()} />
            <StatCard label="Total Pages" value={usage.total_pages.toLocaleString()} />
            <StatCard label="Usage Charges" value={formatInr(usage.usage_charges)} />
            <StatCard label="Total Amount" value={formatInr(usage.total_amount)} />
          </div>

          <Card className="mb-6 p-5">
            <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Bill Calculation</h2>
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-slate-500 dark:text-slate-400">Platform fee</dt>
                <dd className="font-medium text-slate-900 dark:text-slate-100">{formatInr(usage.platform_fee)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500 dark:text-slate-400">
                  Tier 1 ({usage.tier1_pages.toLocaleString()} pages × ₹{usage.tier1_rate})
                </dt>
                <dd className="font-medium text-slate-900 dark:text-slate-100">{formatInr(usage.tier1_amount)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500 dark:text-slate-400">
                  Tier 2 ({usage.tier2_pages.toLocaleString()} pages × ₹{usage.tier2_rate})
                </dt>
                <dd className="font-medium text-slate-900 dark:text-slate-100">{formatInr(usage.tier2_amount)}</dd>
              </div>
              <div className="flex justify-between border-t border-slate-200 pt-2 dark:border-slate-800">
                <dt className="text-slate-500 dark:text-slate-400">Subtotal</dt>
                <dd className="font-medium text-slate-900 dark:text-slate-100">{formatInr(usage.subtotal)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500 dark:text-slate-400">GST ({usage.gst_percent}%)</dt>
                <dd className="font-medium text-slate-900 dark:text-slate-100">{formatInr(usage.gst_amount)}</dd>
              </div>
              <div className="flex justify-between border-t border-slate-200 pt-2 text-base dark:border-slate-800">
                <dt className="font-semibold text-slate-900 dark:text-slate-50">Total</dt>
                <dd className="font-semibold text-slate-900 dark:text-slate-50">{formatInr(usage.total_amount)}</dd>
              </div>
            </dl>
          </Card>
        </>
      )}

      <Card className="p-5">
        <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Invoice History</h2>
        {invoices.length === 0 ? (
          <p className="text-sm text-slate-400">No invoices raised yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                <th className="py-2 font-medium">Invoice #</th>
                <th className="py-2 font-medium">Period</th>
                <th className="py-2 font-medium">Total</th>
                <th className="py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((inv) => (
                <tr key={inv.id} className="border-b border-slate-100 last:border-0 dark:border-slate-800/60">
                  <td className="py-2 text-slate-700 dark:text-slate-300">{inv.invoice_number}</td>
                  <td className="py-2 text-slate-700 dark:text-slate-300">{monthLabel(inv.period_start.slice(0, 7))}</td>
                  <td className="py-2 font-medium text-slate-900 dark:text-slate-100">{formatInr(inv.total_amount)}</td>
                  <td className="py-2">
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${inv.status === "paid" ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300"}`}>
                      {inv.status === "paid" ? "Paid" : "Unpaid"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </AppShell>
  );
}
