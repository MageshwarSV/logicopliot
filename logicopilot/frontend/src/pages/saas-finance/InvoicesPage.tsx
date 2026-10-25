import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import { DataTable, type Column } from "../../components/ui/DataTable";
import type { FinanceInvoiceRow } from "../../api/saasFinance";
import { useFinanceInvoices } from "./useFinanceInvoices";
import { formatInr, isOverdue, monthLabel } from "./financeFormat";

function InvoiceStatusBadge({ row }: { row: FinanceInvoiceRow }) {
  if (row.status === "paid") {
    return <span className="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">Paid</span>;
  }
  if (isOverdue(row.period_end, row.status)) {
    return <span className="inline-flex items-center rounded-full bg-rose-50 px-2 py-0.5 text-xs font-medium text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">Overdue</span>;
  }
  return <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-500/10 dark:text-amber-300">Unpaid</span>;
}

/** Cross-tenant invoice search. There is no separate "Generate" action to build - a past
 *  month's invoice is finalized lazily the moment it's viewed (see
 *  app/api/v1/saas_finance.py::list_finance_invoices), so simply loading this page finalizes
 *  every tenant's outstanding past months. */
export function SaasFinanceInvoicesPage() {
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState<"" | "unpaid" | "paid">("");
  const { rows, loading, error, markingId, markPaid } = useFinanceInvoices(
    statusFilter ? { status: statusFilter } : {},
  );

  const columns: Column<FinanceInvoiceRow>[] = [
    { header: "Customer", render: (r) => <span className="font-medium text-slate-900 dark:text-slate-100">{r.tenant_name}</span> },
    { header: "Invoice #", render: (r) => r.invoice_number },
    { header: "Period", render: (r) => monthLabel(r.period_start.slice(0, 7)) },
    { header: "Total Amount", render: (r) => <span className="font-medium">{formatInr(r.total_amount)}</span> },
    { header: "Status", render: (r) => <InvoiceStatusBadge row={r} /> },
    {
      header: "",
      render: (r) => (
        <div className="flex items-center justify-end gap-2" onClick={(e) => e.stopPropagation()}>
          <button
            type="button"
            onClick={() => navigate(`/saas-finance/invoices/${r.tenant_id}/${r.id}/print`)}
            className="text-sm font-medium text-indigo-600 hover:underline dark:text-indigo-400"
          >
            Preview / Download
          </button>
          {r.status === "unpaid" && (
            <Button variant="secondary" size="sm" isLoading={markingId === r.id} onClick={() => markPaid(r.id, r.tenant_id)}>
              Mark Paid
            </Button>
          )}
        </div>
      ),
    },
  ];

  return (
    <AppShell title="Invoices" subtitle="Every customer's invoice history, in one place.">
      <Card className="mb-4 flex items-center gap-3 p-4">
        <label className="text-sm font-medium text-slate-600 dark:text-slate-300">Status</label>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as "" | "unpaid" | "paid")}
          className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
        >
          <option value="">All statuses</option>
          <option value="unpaid">Unpaid</option>
          <option value="paid">Paid</option>
        </select>
      </Card>

      {error && <div className="mb-4"><Alert>{error}</Alert></div>}

      <Card className="p-5">
        {loading ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : (
          <DataTable columns={columns} rows={rows} keyFor={(r) => r.id} emptyMessage="No invoices raised yet." />
        )}
      </Card>
    </AppShell>
  );
}
