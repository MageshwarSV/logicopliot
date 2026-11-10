import { useEffect, useState } from "react";
import axios from "axios";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card, StatCard } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import { DocumentIcon, TrashIcon, LayersIcon, ArrowPathIcon } from "../../components/ui/icons";
import { ModeIcon } from "../../components/ModeIcon";
import { JobBillingTable } from "../../components/billing/JobBillingTable";
import { useAuth } from "../../auth/useAuth";
import * as tenantsApi from "../../api/tenants";
import type { BillingUsage, Invoice, JobBillingHighlight } from "../../api/tenants";
import type { Tenant } from "../../types/tenant";

function formatInr(n: number): string {
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function currentMonthStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-IN", { month: "long", year: "numeric" });
}

function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
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

const MODE_TONE: Record<string, string> = {
  sea: "bg-sky-50 text-sky-600 dark:bg-sky-500/10 dark:text-sky-400",
  air: "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400",
  road: "bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400",
  train: "bg-violet-50 text-violet-600 dark:bg-violet-500/10 dark:text-violet-400",
};
function modeTone(mode: string): string {
  const m = mode.toLowerCase();
  for (const key of Object.keys(MODE_TONE)) if (m.includes(key)) return MODE_TONE[key];
  return "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300";
}

/** null means "not available for this already-finalized month" (see BillingUsage's own
 *  field docs) - shown honestly as "—", never guessed at from other fields. */
function statValue(n: number | null | undefined): string {
  return n == null ? "—" : n.toLocaleString();
}

/** The same 5 figures shown both on Usage Summary (wraps to 2 rows in its narrower,
 *  two-thirds-width column) and at the top of Job Details (one row, full width) - one
 *  component so the two can never show different numbers for what looks like the same row. */
function UsageStatRow({
  usage, highlight, onSelect,
}: {
  usage: BillingUsage;
  highlight: JobBillingHighlight | null;
  onSelect: (h: JobBillingHighlight | null) => void;
}) {
  return (
    <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      <StatCard
        icon={<DocumentIcon />} tone="indigo" label="Total Jobs" value={usage.total_jobs.toLocaleString()}
        onClick={() => onSelect(null)} active={highlight === null}
      />
      <StatCard
        icon={<TrashIcon />} tone="rose" label="Deleted Jobs" value={statValue(usage.deleted_jobs_billed)}
        onClick={() => onSelect("deleted_jobs")} active={highlight === "deleted_jobs"}
      />
      <StatCard icon={<DocumentIcon />} tone="violet" label="Original Pages" value={statValue(usage.pages_original)} />
      <StatCard
        icon={<ArrowPathIcon />} tone="violet" label="Rerun Pages" value={statValue(usage.pages_rerun)}
        onClick={() => onSelect("rerun")} active={highlight === "rerun"}
      />
      <StatCard icon={<LayersIcon />} tone="indigo" label="Total Pages" value={usage.total_pages.toLocaleString()} />
    </div>
  );
}

const TABS = [
  { key: "summary", label: "Usage Summary" },
  { key: "jobs", label: "Job Details" },
  { key: "invoices", label: "Invoices" },
  { key: "payments", label: "Payment History" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

/** Billing & Usage — the same page for both audiences: a Super Admin picks any tenant, a
 *  Tenant Admin only ever sees their own (the API itself enforces that; this just never shows
 *  a tenant picker for them). The current month is always live; any earlier month is a frozen,
 *  already-raised invoice. Rebuilt to the user's own reference layout (2026-11-09): tabs
 *  instead of one long scroll, no Required/Not Required stat card or column - the actual
 *  billing math (Not Required pages at their own flat rate) is unchanged, it just no longer
 *  gets a dedicated counter here. */
export function BillingUsagePage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const isSuperAdmin = user?.role === "super_admin";

  const [searchParams] = useSearchParams();

  const [tab, setTab] = useState<TabKey>("summary");
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState<string | null>(user?.tenant_id ?? null);
  // Seeded from ?month=YYYY-MM once, e.g. the Invoices page's own "View" link - never
  // re-read after mount, so the month switcher below still works normally afterward.
  const [month, setMonth] = useState<string>(searchParams.get("month") || currentMonthStr());
  const [usage, setUsage] = useState<BillingUsage | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [markingId, setMarkingId] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<JobBillingHighlight | null>(null);

  useEffect(() => setHighlight(null), [tenantId, month]);

  useEffect(() => {
    if (!isSuperAdmin) return;
    tenantsApi
      .listTenants()
      .then((ts) => {
        setTenants(ts);
        setTenantId((prev) => prev ?? ts[0]?.id ?? null);
      })
      .catch(() => {});
  }, [isSuperAdmin]);

  const isCurrentMonth = month === currentMonthStr();

  useEffect(() => {
    if (!tenantId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
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
        setError(
          axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not load billing." : "Could not load billing.",
        );
      })
      .finally(() => setLoading(false));
  }, [tenantId, month, isCurrentMonth]);

  async function refresh() {
    if (!tenantId) return;
    const [u, inv] = await Promise.all([
      tenantsApi.getBillingUsage(tenantId, isCurrentMonth ? undefined : month),
      tenantsApi.listInvoices(tenantId),
    ]);
    setUsage(u);
    setInvoices(inv);
  }

  async function handleMarkPaid(invoiceId: string) {
    if (!tenantId) return;
    setMarkingId(invoiceId);
    try {
      await tenantsApi.markInvoicePaid(tenantId, invoiceId, true);
      await refresh();
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not mark paid." : "Could not mark paid.");
    } finally {
      setMarkingId(null);
    }
  }

  const paidInvoices = invoices.filter((inv) => inv.status === "paid");

  return (
    <AppShell
      title="Billing & Usage"
      subtitle="Track document usage, jobs, and charges for this month."
    >
      {isSuperAdmin && (
        <Card className="mb-6 flex items-center gap-3 p-4">
          <label className="text-sm font-medium text-slate-600 dark:text-slate-300">Tenant</label>
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
      )}

      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                tab === t.key
                  ? "bg-indigo-600 text-white"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-1 py-1 dark:border-slate-700 dark:bg-slate-900">
          <button
            type="button"
            className="rounded-md px-2 py-1 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
            onClick={() => setMonth((m) => shiftMonth(m, -1))}
            aria-label="Previous month"
          >
            ‹
          </button>
          <span className="min-w-[130px] px-2 text-center text-sm font-medium text-slate-700 dark:text-slate-200">
            {monthLabel(month)}
          </span>
          <button
            type="button"
            className="rounded-md px-2 py-1 text-slate-500 hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-slate-800"
            onClick={() => setMonth((m) => shiftMonth(m, 1))}
            disabled={isCurrentMonth}
            aria-label="Next month"
          >
            ›
          </button>
        </div>
      </div>

      {error && <div className="mb-6"><Alert>{error}</Alert></div>}

      {loading && <p className="py-10 text-center text-sm text-slate-400">Loading…</p>}

      {!loading && usage && tab === "summary" && (
        <div className="flex flex-col gap-6">
          <UsageStatRow usage={usage} highlight={highlight} onSelect={setHighlight} />

          <Card className="p-5">
            <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Pages by Shipment Type</h2>
            {usage.by_shipment_type.length === 0 ? (
              <p className="text-sm text-slate-400">No jobs this period.</p>
            ) : (
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                {usage.by_shipment_type.map((d) => (
                  <div key={d.shipment_type} className={`flex flex-col gap-2 rounded-xl p-4 ${modeTone(d.shipment_type)}`}>
                    <ModeIcon mode={d.shipment_type} className="h-6 w-6" />
                    <span className="text-xs font-medium opacity-80">{d.shipment_type}</span>
                    <span className="text-xl font-bold">
                      {d.pages.toLocaleString()} <span className="text-xs font-normal opacity-70">pages</span>
                    </span>
                    <span className="text-xs opacity-70">{d.jobs.toLocaleString()} job{d.jobs === 1 ? "" : "s"}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card className="p-5">
            <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Page Usage</h2>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                  <th className="py-2 font-medium">&nbsp;</th>
                  <th className="py-2 font-medium">Original Pages</th>
                  <th className="py-2 font-medium">Rerun Pages</th>
                  <th className="py-2 font-medium">Total Pages</th>
                </tr>
              </thead>
              <tbody>
                <tr className="bg-slate-50 dark:bg-slate-800/40">
                  <td className="rounded-l-lg py-3 pl-3 font-semibold text-slate-900 dark:text-slate-50">Total</td>
                  <td className="py-3 font-semibold text-slate-900 dark:text-slate-50">{statValue(usage.pages_original)}</td>
                  <td className="py-3 font-semibold text-slate-900 dark:text-slate-50">{statValue(usage.pages_rerun)}</td>
                  <td className="rounded-r-lg py-3 pr-3 font-semibold text-slate-900 dark:text-slate-50">{usage.total_pages.toLocaleString()}</td>
                </tr>
              </tbody>
            </table>
            {usage.pages_original === null && (
              <p className="mt-3 text-xs text-slate-400">
                Original/Rerun breakdown is not available for this already-finalized month - only the combined Total Pages figure was kept.
              </p>
            )}
          </Card>

          <Card className="p-5">
            <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Billing Summary – {monthLabel(month)}</h2>
            <dl className="grid grid-cols-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
              <div className="flex justify-between sm:col-span-2">
                <dt className="text-slate-500 dark:text-slate-400">
                  Platform Fee (Monthly)
                  {usage.platform_fee_billed_days < usage.platform_fee_month_days && (
                    <span className="ml-1.5 text-xs text-amber-600 dark:text-amber-400">
                      (pro-rated: {usage.platform_fee_billed_days} of {usage.platform_fee_month_days} days)
                    </span>
                  )}
                </dt>
                <dd className="font-medium text-slate-900 dark:text-slate-100">{formatInr(usage.platform_fee)}</dd>
              </div>

              <div className="border-t border-slate-100 pt-3 dark:border-slate-800 sm:col-span-2">
                <p className="mb-2 font-medium text-slate-700 dark:text-slate-200">Processing Charges</p>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500 dark:text-slate-400">
                  Tier 1 ({usage.tier1_pages.toLocaleString()} pages × ₹{usage.tier1_rate})
                </dt>
                <dd className="font-medium text-slate-900 dark:text-slate-100">{formatInr(usage.tier1_amount)}</dd>
              </div>
              {usage.tier2_pages > 0 && (
                <div className="flex justify-between">
                  <dt className="text-slate-500 dark:text-slate-400">
                    Above limit ({usage.tier2_pages.toLocaleString()} × ₹{usage.tier2_rate})
                  </dt>
                  <dd className="font-medium text-slate-900 dark:text-slate-100">{formatInr(usage.tier2_amount)}</dd>
                </div>
              )}
              <div className="flex justify-between rounded-lg bg-indigo-50 px-3 py-2.5 text-base dark:bg-indigo-500/10 sm:col-span-2">
                <dt className="font-semibold text-indigo-900 dark:text-indigo-200">Subtotal (Before GST)</dt>
                <dd className="font-semibold text-indigo-900 dark:text-indigo-200">{formatInr(usage.subtotal)}</dd>
              </div>

              <div className="flex justify-between sm:col-span-2">
                <dt className="text-slate-500 dark:text-slate-400">GST ({usage.gst_percent}%)</dt>
                <dd className="font-medium text-slate-900 dark:text-slate-100">{formatInr(usage.gst_amount)}</dd>
              </div>

              <div className="flex justify-between rounded-lg bg-indigo-600 px-3 py-3 text-base text-white sm:col-span-2">
                <dt className="font-semibold">Total Amount (Incl. GST)</dt>
                <dd className="font-semibold">{formatInr(usage.total_amount)}</dd>
              </div>
            </dl>
          </Card>
        </div>
      )}

      {!loading && usage && tab === "jobs" && tenantId && (
        <>
          <UsageStatRow usage={usage} highlight={highlight} onSelect={setHighlight} />
          <JobBillingTable
            tenantId={tenantId}
            month={month}
            shipmentTypes={usage.by_shipment_type}
            onJobClick={(jobId) => navigate(`/jobs/${jobId}`)}
            highlight={highlight}
            onClearHighlight={() => setHighlight(null)}
          />
        </>
      )}

      {!loading && tab === "invoices" && (
        <Card className="p-5">
          <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Invoices</h2>
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
                  <th className="py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {invoices.map((inv) => (
                  <tr key={inv.id} className="border-b border-slate-100 last:border-0 dark:border-slate-800/60">
                    <td className="py-2 text-slate-700 dark:text-slate-300">{inv.invoice_number}</td>
                    <td className="py-2 text-slate-700 dark:text-slate-300">{monthLabel(inv.period_start.slice(0, 7))}</td>
                    <td className="py-2 font-medium text-slate-900 dark:text-slate-100">{formatInr(inv.total_amount)}</td>
                    <td className="py-2"><StatusPill status={inv.status} /></td>
                    <td className="py-2 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <Button variant="ghost" size="sm" onClick={() => setMonth(inv.period_start.slice(0, 7))}>
                          View
                        </Button>
                        {isSuperAdmin && inv.status === "unpaid" && (
                          <Button
                            variant="secondary"
                            size="sm"
                            isLoading={markingId === inv.id}
                            onClick={() => handleMarkPaid(inv.id)}
                          >
                            Mark Paid
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {!loading && tab === "payments" && (
        <Card className="p-5">
          <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Payment History</h2>
          {paidInvoices.length === 0 ? (
            <p className="text-sm text-slate-400">No payments recorded yet.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                  <th className="py-2 font-medium">Invoice #</th>
                  <th className="py-2 font-medium">Period</th>
                  <th className="py-2 font-medium">Amount Paid</th>
                  <th className="py-2 font-medium">Paid On</th>
                </tr>
              </thead>
              <tbody>
                {paidInvoices.map((inv) => (
                  <tr key={inv.id} className="border-b border-slate-100 last:border-0 dark:border-slate-800/60">
                    <td className="py-2 text-slate-700 dark:text-slate-300">{inv.invoice_number}</td>
                    <td className="py-2 text-slate-700 dark:text-slate-300">{monthLabel(inv.period_start.slice(0, 7))}</td>
                    <td className="py-2 font-medium text-slate-900 dark:text-slate-100">{formatInr(inv.total_amount)}</td>
                    <td className="py-2 text-slate-700 dark:text-slate-300">{formatDate(inv.paid_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </AppShell>
  );
}
