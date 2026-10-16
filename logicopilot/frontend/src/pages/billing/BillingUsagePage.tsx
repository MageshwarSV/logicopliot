import { useEffect, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card, StatCard } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import { useAuth } from "../../auth/useAuth";
import * as tenantsApi from "../../api/tenants";
import type { BillingUsage, Invoice, ShipmentTypeUsage } from "../../api/tenants";
import type { Tenant } from "../../types/tenant";

const PALETTE = ["#6366f1", "#0ea5e9", "#14b8a6", "#f59e0b", "#ec4899", "#f43f5e", "#84cc16", "#a855f7"];

function colorFor(index: number): string {
  return PALETTE[index % PALETTE.length];
}

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

/** Pages by shipment type — each bar answers "which document type is actually driving the
 *  AI-processing bill this month," the number the tiered rate is computed from. */
function ShipmentBarChart({ data }: { data: ShipmentTypeUsage[] }) {
  const width = 560;
  const height = 220;
  const padding = { top: 10, right: 10, bottom: 36, left: 10 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const max = Math.max(1, ...data.map((d) => d.pages));
  const gap = 16;
  const barW = data.length > 0 ? Math.max(8, plotW / data.length - gap) : 0;

  if (data.length === 0) {
    return (
      <div className="flex h-[220px] items-center justify-center text-sm text-slate-400">
        No pages processed this period.
      </div>
    );
  }

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Pages by shipment type">
      {data.map((d, i) => {
        const barH = (d.pages / max) * plotH;
        const x = padding.left + i * (plotW / data.length) + gap / 2;
        const y = padding.top + (plotH - barH);
        return (
          <g key={d.shipment_type}>
            <rect x={x} y={y} width={barW} height={Math.max(barH, 1)} fill={colorFor(i)} rx={4}>
              <title>{`${d.shipment_type}: ${d.pages.toLocaleString()} pages`}</title>
            </rect>
            <text x={x + barW / 2} y={padding.top + plotH - barH - 6} textAnchor="middle" fontSize={11} fontWeight={600} fill="currentColor" className="text-slate-600 dark:text-slate-300">
              {d.pages.toLocaleString()}
            </text>
            <text x={x + barW / 2} y={height - 14} textAnchor="middle" fontSize={10} fill="currentColor" className="text-slate-400">
              {d.shipment_type.length > 14 ? `${d.shipment_type.slice(0, 13)}…` : d.shipment_type}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** Job distribution by shipment type — of everything run this month, what share is which kind. */
function ShipmentPieChart({ data }: { data: ShipmentTypeUsage[] }) {
  const total = data.reduce((sum, d) => sum + d.jobs, 0);
  const size = 180;
  const r = 80;
  const cx = size / 2;
  const cy = size / 2;

  if (total === 0) {
    return (
      <div className="flex h-[180px] items-center justify-center text-sm text-slate-400">
        No jobs this period.
      </div>
    );
  }

  let angle = -90;
  const arcs = data
    .filter((d) => d.jobs > 0)
    .map((d, i) => {
      const fraction = d.jobs / total;
      const startAngle = angle;
      const endAngle = angle + fraction * 360;
      angle = endAngle;
      const toXY = (deg: number): [number, number] => {
        const rad = (deg * Math.PI) / 180;
        return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
      };
      const [x1, y1] = toXY(startAngle);
      const [x2, y2] = toXY(endAngle);
      const largeArc = fraction > 0.5 ? 1 : 0;
      const path =
        fraction >= 0.9999
          ? `M ${cx - r} ${cy} A ${r} ${r} 0 1 1 ${cx + r} ${cy} A ${r} ${r} 0 1 1 ${cx - r} ${cy} Z`
          : `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2} Z`;
      return { ...d, path, fraction, color: colorFor(i) };
    });

  return (
    <div className="flex flex-col items-center gap-5 sm:flex-row">
      <svg viewBox={`0 0 ${size} ${size}`} className="h-40 w-40 shrink-0" role="img" aria-label="Job distribution">
        {arcs.map((a) => (
          <path key={a.shipment_type} d={a.path} fill={a.color}>
            <title>{`${a.shipment_type}: ${a.jobs.toLocaleString()} (${(a.fraction * 100).toFixed(1)}%)`}</title>
          </path>
        ))}
      </svg>
      <div className="flex flex-col gap-2">
        {data.map((d, i) => (
          <div key={d.shipment_type} className="flex items-center gap-2 text-sm">
            <span className="h-3 w-3 shrink-0 rounded-sm" style={{ backgroundColor: colorFor(i) }} />
            <span className="text-slate-700 dark:text-slate-300">{d.shipment_type}</span>
            <span className="font-medium text-slate-900 dark:text-slate-100">{d.jobs.toLocaleString()}</span>
            <span className="text-xs text-slate-400">({total > 0 ? Math.round((d.jobs / total) * 100) : 0}%)</span>
          </div>
        ))}
      </div>
    </div>
  );
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

/** Billing & Usage — the same page for both audiences: a Super Admin picks any tenant, a
 *  Tenant Admin only ever sees their own (the API itself enforces that; this just never shows
 *  a tenant picker for them). The current month is always live; any earlier month is a frozen,
 *  already-raised invoice. */
export function BillingUsagePage() {
  const { user } = useAuth();
  const isSuperAdmin = user?.role === "super_admin";

  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState<string | null>(user?.tenant_id ?? null);
  const [month, setMonth] = useState<string>(currentMonthStr());
  const [usage, setUsage] = useState<BillingUsage | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [markingId, setMarkingId] = useState<string | null>(null);

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

  return (
    <AppShell
      title="Billing & Usage"
      subtitle="This month's live usage, and the invoice history behind it."
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

      <Card className="mb-6 flex items-center justify-between p-4">
        <Button variant="ghost" size="sm" onClick={() => setMonth((m) => shiftMonth(m, -1))}>
          ← Previous
        </Button>
        <div className="text-center">
          <p className="font-semibold text-slate-900 dark:text-slate-50">{monthLabel(month)}</p>
          <p className="text-xs text-slate-400">
            {isCurrentMonth ? "Live — still accumulating" : usage ? (usage.status === "paid" ? "Finalized — Paid" : "Finalized — Unpaid") : ""}
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => setMonth((m) => shiftMonth(m, 1))} disabled={isCurrentMonth}>
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

          <div className="mb-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card className="p-5">
              <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Pages by Shipment Type</h2>
              <ShipmentBarChart data={usage.by_shipment_type} />
            </Card>
            <Card className="p-5">
              <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Job Distribution</h2>
              <ShipmentPieChart data={usage.by_shipment_type} />
            </Card>
          </div>

          <div className="mb-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card className="p-5">
              <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Usage by Shipment Type</h2>
              {usage.by_shipment_type.length === 0 ? (
                <p className="text-sm text-slate-400">No jobs this period.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                      <th className="py-2 font-medium">Shipment Type</th>
                      <th className="py-2 font-medium">Jobs</th>
                      <th className="py-2 font-medium">Pages</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.by_shipment_type.map((d) => (
                      <tr key={d.shipment_type} className="border-b border-slate-100 last:border-0 dark:border-slate-800/60">
                        <td className="py-2 text-slate-700 dark:text-slate-300">{d.shipment_type}</td>
                        <td className="py-2 text-slate-900 dark:text-slate-100">{d.jobs.toLocaleString()}</td>
                        <td className="py-2 text-slate-900 dark:text-slate-100">{d.pages.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>

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
          </div>
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
    </AppShell>
  );
}
