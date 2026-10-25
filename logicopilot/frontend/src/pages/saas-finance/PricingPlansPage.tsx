import { useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import * as tenantsApi from "../../api/tenants";
import type { TenantBillingConfig } from "../../api/tenants";
import type { Tenant } from "../../types/tenant";
import { formatInr } from "./financeFormat";

/** Read-only by design - Finance can VIEW what each tenant is billed, but only Super Admin
 *  can change it (app/api/v1/tenants.py::update_tenant_billing_config stays Super-Admin-only).
 *  No Save button, no PUT call, anywhere on this page. */
export function SaasFinancePricingPlansPage() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [config, setConfig] = useState<TenantBillingConfig | null>(null);
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
    tenantsApi
      .getTenantBillingConfig(tenantId)
      .then(setConfig)
      .catch(() => setError("Could not load pricing for this customer."))
      .finally(() => setLoading(false));
  }, [tenantId]);

  return (
    <AppShell title="Pricing & Plans" subtitle="What each customer is billed for CARGORA - view only.">
      <Card className="mb-6 flex items-center gap-3 p-4">
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

      {error && <div className="mb-6"><Alert>{error}</Alert></div>}

      {!loading && config && (
        <Card className="p-5">
          <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">CARGORA Pricing</h2>
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <dt className="text-sm text-slate-500 dark:text-slate-400">Monthly platform fee</dt>
              <dd className="text-lg font-semibold text-slate-900 dark:text-slate-50">{formatInr(config.cargora_monthly_fee)}</dd>
            </div>
            <div>
              <dt className="text-sm text-slate-500 dark:text-slate-400">GST</dt>
              <dd className="text-lg font-semibold text-slate-900 dark:text-slate-50">{config.gst_percent}%</dd>
            </div>
            <div>
              <dt className="text-sm text-slate-500 dark:text-slate-400">
                Tier 1 rate (up to {config.cargora_tier1_page_limit.toLocaleString()} pages/month)
              </dt>
              <dd className="text-lg font-semibold text-slate-900 dark:text-slate-50">{formatInr(config.cargora_tier1_rate_per_page)} / page</dd>
            </div>
            <div>
              <dt className="text-sm text-slate-500 dark:text-slate-400">
                Tier 2 rate (above {config.cargora_tier1_page_limit.toLocaleString()} pages/month)
              </dt>
              <dd className="text-lg font-semibold text-slate-900 dark:text-slate-50">{formatInr(config.cargora_tier2_rate_per_page)} / page</dd>
            </div>
            {config.billing_start_date && (
              <div>
                <dt className="text-sm text-slate-500 dark:text-slate-400">Billing start date</dt>
                <dd className="text-lg font-semibold text-slate-900 dark:text-slate-50">{config.billing_start_date}</dd>
              </div>
            )}
          </dl>
          <p className="mt-6 text-xs text-slate-400">
            To change pricing for a customer, ask a Super Admin - Finance has view-only access here.
          </p>
        </Card>
      )}
    </AppShell>
  );
}
