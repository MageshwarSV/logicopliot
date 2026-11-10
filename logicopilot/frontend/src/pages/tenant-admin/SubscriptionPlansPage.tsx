import { useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { Card, StatCard } from "../../components/ui/Card";
import { LayersIcon, CheckCircleIcon } from "../../components/ui/icons";
import { useAuth } from "../../auth/useAuth";
import * as tenantsApi from "../../api/tenants";
import type { TenantBillingConfig, BillingUsage } from "../../api/tenants";

function formatInr(n: number): string {
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function ProgressBar({ used, limit }: { used: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const over = used > limit;
  return (
    <div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div
          className={`h-full rounded-full ${over ? "bg-rose-500" : "bg-indigo-500"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
        {used.toLocaleString()} of {limit.toLocaleString()} pages this month
        {over && <span className="ml-1 font-medium text-rose-600 dark:text-rose-400">— over Tier 1, billing at Tier 2 rate</span>}
      </p>
    </div>
  );
}

/** Read-only: a tenant admin can SEE their plan and this month's usage against it, but cannot
 *  change anything here - the rate/plan itself is still Super-Admin-only
 *  (update_tenant_billing_config stays gated). */
export function SubscriptionPlansPage() {
  const { user } = useAuth();
  const [config, setConfig] = useState<TenantBillingConfig | null>(null);
  const [usage, setUsage] = useState<BillingUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user?.tenant_id) return;
    setLoading(true);
    Promise.all([tenantsApi.getTenantBillingConfig(user.tenant_id), tenantsApi.getBillingUsage(user.tenant_id)])
      .then(([c, u]) => {
        setConfig(c);
        setUsage(u);
      })
      .catch(() => setError("Could not load your plan."))
      .finally(() => setLoading(false));
  }, [user?.tenant_id]);

  return (
    <AppShell title="Subscription & Plans" subtitle="Your organization's current plan and this month's usage against it.">
      {error && <p className="mb-4 text-sm text-rose-600 dark:text-rose-400">{error}</p>}
      {loading || !config ? (
        <p className="py-10 text-center text-sm text-slate-400">Loading…</p>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card className="p-5">
            <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Cargora Plan</h2>
            <div className="mb-5 grid grid-cols-2 gap-4">
              <StatCard label="Platform Fee" value={formatInr(config.cargora_monthly_fee)} valueCaption="per month" icon={<LayersIcon />} tone="indigo" />
              <StatCard label="GST" value={`${config.gst_percent}%`} icon={<CheckCircleIcon />} tone="emerald" />
            </div>
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-slate-500 dark:text-slate-400">Tier 1 rate</dt>
                <dd className="text-slate-900 dark:text-slate-100">
                  {formatInr(config.cargora_tier1_rate_per_page)} / page, up to {config.cargora_tier1_page_limit.toLocaleString()} pages
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500 dark:text-slate-400">Tier 2 rate</dt>
                <dd className="text-slate-900 dark:text-slate-100">{formatInr(config.cargora_tier2_rate_per_page)} / page, beyond that</dd>
              </div>
              {config.billing_start_date && (
                <div className="flex justify-between">
                  <dt className="text-slate-500 dark:text-slate-400">Billing start date</dt>
                  <dd className="text-slate-900 dark:text-slate-100">{config.billing_start_date}</dd>
                </div>
              )}
            </dl>
            {usage && (
              <div className="mt-5 border-t border-slate-100 pt-4 dark:border-slate-800">
                <ProgressBar used={usage.total_pages} limit={config.cargora_tier1_page_limit} />
              </div>
            )}
          </Card>

          <Card className="p-5">
            <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">TrackContainer.in</h2>
            {config.trackcontainer_plan == null ? (
              <p className="text-sm text-slate-400">Not subscribed to TrackContainer.in.</p>
            ) : (
              <>
                <div className="mb-4 inline-flex items-center gap-2 rounded-full bg-violet-50 px-3 py-1.5 text-sm font-semibold capitalize text-violet-700 dark:bg-violet-500/10 dark:text-violet-300">
                  {config.trackcontainer_plan} plan
                </div>
                <dl className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <dt className="text-slate-500 dark:text-slate-400">Price</dt>
                    <dd className="text-slate-900 dark:text-slate-100">
                      {formatInr(
                        config.trackcontainer_plan === "professional"
                          ? config.trackcontainer_professional_price
                          : config.trackcontainer_starter_price,
                      )}{" "}
                      / month
                    </dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-slate-500 dark:text-slate-400">Container limit</dt>
                    <dd className="text-slate-900 dark:text-slate-100">
                      {(config.trackcontainer_plan === "professional"
                        ? config.trackcontainer_professional_container_limit
                        : config.trackcontainer_starter_container_limit
                      ).toLocaleString()}
                    </dd>
                  </div>
                </dl>
              </>
            )}
          </Card>
        </div>
      )}
    </AppShell>
  );
}
