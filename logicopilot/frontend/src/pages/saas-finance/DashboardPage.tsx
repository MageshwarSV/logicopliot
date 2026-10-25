import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card, StatCard } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import { DataTable, type Column } from "../../components/ui/DataTable";
import * as financeApi from "../../api/saasFinance";
import type { FinanceCustomerRow, FinanceDashboard, FinanceRevenuePoint } from "../../api/saasFinance";
import { colorFor, currentMonthStr, formatInr, monthLabel, shiftMonth } from "./financeFormat";

/** Pages processed per customer this month - which customer is actually driving usage.
 *  Hand-rolled inline SVG, matching house style (SpendAnalyticsPage.tsx/
 *  BillingUsagePage.tsx) - no charting library exists in this codebase. */
function CustomerPagesBarChart({ data }: { data: FinanceCustomerRow[] }) {
  const width = 560;
  const height = 220;
  const padding = { top: 10, right: 10, bottom: 40, left: 10 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const max = Math.max(1, ...data.map((d) => d.pages));
  const gap = 16;
  const barW = data.length > 0 ? Math.max(8, plotW / data.length - gap) : 0;

  if (data.length === 0) {
    return <div className="flex h-[220px] items-center justify-center text-sm text-slate-400">No customers yet.</div>;
  }

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Pages by customer">
      {data.map((d, i) => {
        const barH = (d.pages / max) * plotH;
        const x = padding.left + i * (plotW / data.length) + gap / 2;
        const y = padding.top + (plotH - barH);
        return (
          <g key={d.tenant_id}>
            <rect x={x} y={y} width={barW} height={Math.max(barH, 1)} fill={colorFor(i)} rx={4}>
              <title>{`${d.tenant_name}: ${d.pages.toLocaleString()} pages`}</title>
            </rect>
            <text x={x + barW / 2} y={padding.top + plotH - barH - 6} textAnchor="middle" fontSize={11} fontWeight={600} fill="currentColor" className="text-slate-600 dark:text-slate-300">
              {d.pages.toLocaleString()}
            </text>
            <text x={x + barW / 2} y={height - 14} textAnchor="middle" fontSize={10} fill="currentColor" className="text-slate-400">
              {d.tenant_name.length > 14 ? `${d.tenant_name.slice(0, 13)}…` : d.tenant_name}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** Platform-wide billable amount, trailing 12 months. */
function RevenueTrendChart({ data }: { data: FinanceRevenuePoint[] }) {
  const width = 560;
  const height = 220;
  const padding = { top: 10, right: 10, bottom: 28, left: 10 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const max = Math.max(1, ...data.map((d) => d.total_amount));

  if (data.length === 0) {
    return <div className="flex h-[220px] items-center justify-center text-sm text-slate-400">No revenue history yet.</div>;
  }

  const points = data.map((d, i) => {
    const x = padding.left + (data.length === 1 ? plotW / 2 : (i / (data.length - 1)) * plotW);
    const y = padding.top + plotH - (d.total_amount / max) * plotH;
    return { x, y, d };
  });
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Revenue trend">
      <path d={path} fill="none" stroke={colorFor(0)} strokeWidth={2.5} />
      {points.map((p) => (
        <g key={p.d.month}>
          <circle cx={p.x} cy={p.y} r={4} fill={colorFor(0)}>
            <title>{`${monthLabel(p.d.month)}: ${formatInr(p.d.total_amount)}`}</title>
          </circle>
          <text x={p.x} y={height - 10} textAnchor="middle" fontSize={9} fill="currentColor" className="text-slate-400">
            {p.d.month.slice(5)}
          </text>
        </g>
      ))}
    </svg>
  );
}

export function SaasFinanceDashboardPage() {
  const navigate = useNavigate();
  const [month, setMonth] = useState(currentMonthStr());
  const [dashboard, setDashboard] = useState<FinanceDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError(null);
    financeApi
      .getFinanceDashboard(month === currentMonthStr() ? undefined : month)
      .then(setDashboard)
      .catch((err) => {
        setDashboard(null);
        setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not load the dashboard." : "Could not load the dashboard.");
      })
      .finally(() => setLoading(false));
  }, [month]);

  async function handleExport() {
    setExporting(true);
    try {
      await financeApi.exportFinanceReport("customer-billing", "xlsx", month === currentMonthStr() ? undefined : month);
    } catch {
      setError("Could not export this month's figures.");
    } finally {
      setExporting(false);
    }
  }

  const columns: Column<FinanceCustomerRow>[] = [
    { header: "Customer", render: (r) => <span className="font-medium text-slate-900 dark:text-slate-100">{r.tenant_name}</span> },
    { header: "Jobs", render: (r) => r.jobs.toLocaleString() },
    { header: "Pages", render: (r) => r.pages.toLocaleString() },
    { header: "Platform Fee", render: (r) => formatInr(r.platform_fee) },
    { header: "Usage Charges", render: (r) => formatInr(r.usage_charges) },
    { header: "GST", render: (r) => formatInr(r.gst_amount) },
    { header: "Total Amount", render: (r) => <span className="font-medium">{formatInr(r.total_amount)}</span> },
    {
      header: "Invoice Status",
      render: (r) =>
        r.invoice_status === null ? (
          <span className="text-xs text-slate-400">Live</span>
        ) : (
          <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${r.invoice_status === "paid" ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300"}`}>
            {r.invoice_status === "paid" ? "Paid" : "Unpaid"}
          </span>
        ),
    },
  ];

  return (
    <AppShell
      title="Finance Dashboard"
      subtitle="Platform-wide usage, billing, and payments across every customer."
      actions={
        <Button variant="secondary" size="sm" isLoading={exporting} onClick={handleExport}>
          Export
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

      {!loading && dashboard && (
        <>
          <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
            <StatCard label="Total Customers" value={dashboard.total_customers} />
            <StatCard label="Total Jobs" value={dashboard.total_jobs.toLocaleString()} />
            <StatCard label="Total Pages" value={dashboard.total_pages.toLocaleString()} />
            <StatCard label="Usage Charges" value={formatInr(dashboard.total_usage_charges)} />
            <StatCard label="Total Billable" value={formatInr(dashboard.total_billable_amount)} />
            <StatCard label="Outstanding" value={formatInr(dashboard.outstanding_amount)} />
          </div>

          <div className="mb-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card className="p-5">
              <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Usage by Customer (Pages)</h2>
              <CustomerPagesBarChart data={dashboard.by_customer} />
            </Card>
            <Card className="p-5">
              <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Revenue Trend (Total Amount)</h2>
              <RevenueTrendChart data={dashboard.revenue_trend} />
            </Card>
          </div>

          <Card className="p-5">
            <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Customer Billing Overview</h2>
            <DataTable
              columns={columns}
              rows={dashboard.by_customer}
              keyFor={(r) => r.tenant_id}
              onRowClick={(r) => navigate(`/saas-finance/customers/${r.tenant_id}`)}
              emptyMessage="No customers yet."
            />
          </Card>
        </>
      )}
    </AppShell>
  );
}
