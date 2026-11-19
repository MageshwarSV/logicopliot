import { useEffect, useState } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card, StatCard } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import {
  DocumentIcon, TrashIcon, LayersIcon,
  ArrowPathIcon,
} from "../../components/ui/icons";
import { DonutChart } from "../../components/charts/DonutChart";
import { ShipmentBarChart, ShipmentPieChart } from "../../components/charts/ShipmentCharts";
import { JobBillingTable } from "../../components/billing/JobBillingTable";
import * as tenantsApi from "../../api/tenants";
import type { BillingUsage, JobBillingHighlight } from "../../api/tenants";
import type { Tenant } from "../../types/tenant";
import { currentMonthStr, formatInr, monthLabel, shiftMonth } from "./financeFormat";

/** Platform-wide "Usage & Billing": a tenant picker + that tenant's own figures, reusing the
 *  EXISTING getBillingUsage (tenants.ts) unchanged. Deliberately a SEPARATE page from
 *  BillingUsagePage.tsx (not a third role added to that shared component's own
 *  role-conditionals) - that file is the tenant-facing page the spec says must be preserved
 *  as-is, and a few dozen extra lines here is a cheap trade against ever risking a regression
 *  on it. */
const fmtDay = (iso: string) =>
  iso ? new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "";

export function SaasFinanceUsageBillingPage() {
  const navigate = useNavigate();
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [month, setMonth] = useState(currentMonthStr());
  // Today / This week / a month - Today and This week are usage only (a bill is per month).
  const [period, setPeriod] = useState<"today" | "week" | "month">("month");
  const isRange = period !== "month";
  const [usage, setUsage] = useState<BillingUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<JobBillingHighlight | null>(null);

  function toggleHighlight(value: JobBillingHighlight) {
    setHighlight((prev) => (prev === value ? null : value));
  }

  useEffect(() => setHighlight(null), [tenantId, month, period]);

  useEffect(() => {
    tenantsApi
      .listTenants()
      .then((ts) => {
        setTenants(ts);
        // 4S Logistics first by default; any other customer is one pick away.
        const fourS = ts.find((t) => /^\s*4\s*s\b/i.test(t.name) || /4s\s*logist/i.test(t.name));
        setTenantId((prev) => prev ?? fourS?.id ?? ts[0]?.id ?? null);
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
      .getBillingUsage(tenantId, isCurrentMonth ? undefined : month, isRange ? (period as "today" | "week") : undefined)
      .then(setUsage)
      .catch((err) => {
        setUsage(null);
        setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not load billing." : "Could not load billing.");
      })
      .finally(() => setLoading(false));
  }, [tenantId, month, period, isRange]);

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

      <Card className="mb-6 flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-1 dark:border-slate-700 dark:bg-slate-800">
          {([["today", "Today"], ["week", "This week"], ["month", "Month"]] as const).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => {
                setPeriod(key);
                if (key !== "month") setMonth(currentMonthStr());
              }}
              className={`rounded-md px-3.5 py-1.5 text-sm font-medium transition-colors ${period === key ? "bg-white text-indigo-700 shadow-sm dark:bg-slate-900 dark:text-indigo-300" : "text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white"}`}
            >
              {label}
            </button>
          ))}
        </div>
        {isRange ? (
          <p className="font-semibold text-slate-900 dark:text-slate-50">
            {period === "today" ? "Today" : "This week"}
            {usage?.range_from && (
              <span className="ml-2 text-sm font-normal text-slate-500">
                {usage.range_from === usage.range_to ? fmtDay(usage.range_from) : `${fmtDay(usage.range_from)} – ${fmtDay(usage.range_to ?? "")}`}
              </span>
            )}
          </p>
        ) : (
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => setMonth((m) => shiftMonth(m, -1))}>
              ← Previous
            </Button>
            <p className="min-w-[9rem] text-center font-semibold text-slate-900 dark:text-slate-50">{monthLabel(month)}</p>
            <Button variant="ghost" size="sm" onClick={() => setMonth((m) => shiftMonth(m, 1))} disabled={month === currentMonthStr()}>
              Next →
            </Button>
          </div>
        )}
      </Card>

      {error && <div className="mb-6"><Alert>{error}</Alert></div>}

      {!loading && usage && (
        <>
          {usage.pages_original !== null ? (
            <div className="mb-6 grid grid-cols-2 items-stretch gap-4 sm:grid-cols-3 lg:grid-cols-5">
              <StatCard
                icon={<DocumentIcon />} tone="indigo" label="Total Jobs" value={usage.total_jobs.toLocaleString()}
                onClick={() => setHighlight(null)} active={highlight === null}
              />
              <StatCard
                icon={<TrashIcon />} tone="rose" label="Deleted Jobs" value={(usage.deleted_jobs_billed ?? 0).toLocaleString()}
                onClick={() => toggleHighlight("deleted_jobs")} active={highlight === "deleted_jobs"}
              />
              <StatCard icon={<LayersIcon />} tone="blue" label="Original Pages" value={usage.pages_original.toLocaleString()} />
              <StatCard
                icon={<TrashIcon />} tone="rose" label="Deleted Pages" value={(usage.deleted_pages ?? 0).toLocaleString()}
                onClick={() => toggleHighlight("deleted_pages")} active={highlight === "deleted_pages"}
              />
              <StatCard
                icon={<ArrowPathIcon />} tone="violet" label="Rerun Pages" value={(usage.pages_rerun ?? 0).toLocaleString()}
                onClick={() => toggleHighlight("rerun")} active={highlight === "rerun"}
              />
              <StatCard
                icon={<LayersIcon />}
                tone="indigo"
                label="Total Pages"
                value={usage.total_pages.toLocaleString()}
                valueCaption={`${usage.pages_original.toLocaleString()} + ${(usage.pages_rerun ?? 0).toLocaleString()}`}
              />
            </div>
          ) : (
            <>
              <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
                <StatCard icon={<DocumentIcon />} tone="indigo" label="Total Jobs" value={usage.total_jobs.toLocaleString()} />
                <StatCard icon={<LayersIcon />} tone="blue" label="Total Pages" value={usage.total_pages.toLocaleString()} />
                <StatCard label="Usage Charges" value={formatInr(usage.usage_charges)} />
                <StatCard label="Total Amount" value={formatInr(usage.total_amount)} />
              </div>
              <p className="mb-6 text-xs text-slate-400">
                Original / Rerun breakdown is not available for this already-finalized month.
              </p>
            </>
          )}

          <div className="mb-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card className="p-5">
              <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Pages by Shipment Type</h2>
              <ShipmentBarChart data={usage.by_shipment_type} />
            </Card>
            <Card className="p-5">
              <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Job Distribution</h2>
              {usage.pages_original !== null ? (
                <>
                  <DonutChart active={usage.active_jobs_billed ?? 0} deleted={usage.deleted_jobs_billed ?? 0} />
                  <p className="mt-4 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
                    Deleted jobs are included in page counts and billing.
                  </p>
                </>
              ) : (
                <ShipmentPieChart data={usage.by_shipment_type} />
              )}
            </Card>
          </div>

          {tenantId && (
            <div className="mb-6">
              <JobBillingTable
                tenantId={tenantId}
                month={month}
                range={isRange && usage.range_from && usage.range_to ? { from: usage.range_from, to: usage.range_to } : null}
                shipmentTypes={usage.by_shipment_type}
                onJobClick={(jobId) => navigate(`/jobs/${jobId}`)}
                highlight={highlight}
                onClearHighlight={() => setHighlight(null)}
              />
            </div>
          )}

          {isRange ? (
            <p className="rounded-lg bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
              Charges are calculated for a whole month — choose <b>Month</b> to see the bill.
            </p>
          ) : (
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
          )}
        </>
      )}
    </AppShell>
  );
}
