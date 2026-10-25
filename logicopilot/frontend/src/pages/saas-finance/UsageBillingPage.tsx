import { useEffect, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card, StatCard } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import * as tenantsApi from "../../api/tenants";
import type { BillingUsage } from "../../api/tenants";
import type { Tenant } from "../../types/tenant";
import { currentMonthStr, formatInr, monthLabel, shiftMonth } from "./financeFormat";

/** Platform-wide "Usage & Billing": a tenant picker + that tenant's own figures, reusing the
 *  EXISTING getBillingUsage (tenants.ts) unchanged. Deliberately a SEPARATE page from
 *  BillingUsagePage.tsx (not a third role added to that shared component's own
 *  role-conditionals) - that file is the tenant-facing page the spec says must be preserved
 *  as-is, and a few dozen extra lines here is a cheap trade against ever risking a regression
 *  on it. */
export function SaasFinanceUsageBillingPage() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [month, setMonth] = useState(currentMonthStr());
  const [usage, setUsage] = useState<BillingUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    tenantsApi
      .listTenants()
      .then((ts) => {
        setTenants(ts);
        setTenantId((prev) => prev ?? ts[0]?.id ?? null);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!tenantId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    const isCurrentMonth = month === currentMonthStr();
    tenantsApi
      .getBillingUsage(tenantId, isCurrentMonth ? undefined : month)
      .then(setUsage)
      .catch((err) => {
        setUsage(null);
        setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not load billing." : "Could not load billing.");
      })
      .finally(() => setLoading(false));
  }, [tenantId, month]);

  return (
    <AppShell title="Usage & Billing" subtitle="Monthly usage and platform-fee calculation, per customer.">
      <Card className="mb-4 flex items-center gap-3 p-4">
        <label className="text-sm font-medium text-slate-600 dark:text-slate-300">Customer</label>
        <select
          value={tenantId ?? ""}
          onChange={(e) => setTenantId(e.target.value || null)}
          className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
        >
          {tenants.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </Card>

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

          <Card className="p-5">
            <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Bill Calculation</h2>
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-slate-500 dark:text-slate-400">
                  Platform fee
                  {usage.platform_fee_billed_days < usage.platform_fee_month_days && (
                    <span className="ml-1.5 text-xs text-amber-600 dark:text-amber-400">
                      (pro-rated: {usage.platform_fee_billed_days} of {usage.platform_fee_month_days} days)
                    </span>
                  )}
                </dt>
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
    </AppShell>
  );
}
