import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import { DataTable, type Column } from "../../components/ui/DataTable";
import type { FinanceInvoiceRow } from "../../api/saasFinance";
import { useFinanceInvoices } from "./useFinanceInvoices";
import { formatInr, isOverdue, monthLabel } from "./financeFormat";

/** Same rows as InvoicesPage.tsx (see useFinanceInvoices) - this view emphasizes payment
 *  status instead of invoice identity, but there is no separate Payment record anywhere in
 *  this codebase to show: a "payment" IS an invoice's own status/paid_at flag, flipped
 *  manually, since there is no payment gateway integration. */
export function SaasFinancePaymentsPage() {
  const { rows, loading, error, markingId, markPaid } = useFinanceInvoices({});

  const columns: Column<FinanceInvoiceRow>[] = [
    { header: "Customer", render: (r) => <span className="font-medium text-slate-900 dark:text-slate-100">{r.tenant_name}</span> },
    { header: "Period", render: (r) => monthLabel(r.period_start.slice(0, 7)) },
    { header: "Invoice Amount", render: (r) => formatInr(r.total_amount) },
    { header: "Outstanding", render: (r) => (r.status === "unpaid" ? formatInr(r.total_amount) : formatInr(0)) },
    { header: "Payment Date", render: (r) => (r.paid_at ? new Date(r.paid_at).toLocaleDateString("en-IN") : "—") },
    {
      header: "Status",
      render: (r) =>
        r.status === "paid" ? (
          <span className="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">Paid</span>
        ) : isOverdue(r.period_end, r.status) ? (
          <span className="inline-flex items-center rounded-full bg-rose-50 px-2 py-0.5 text-xs font-medium text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">Overdue</span>
        ) : (
          <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-500/10 dark:text-amber-300">Unpaid</span>
        ),
    },
    {
      header: "",
      render: (r) =>
        r.status === "unpaid" ? (
          <Button variant="secondary" size="sm" isLoading={markingId === r.id} onClick={() => markPaid(r.id, r.tenant_id)}>
            Mark Paid
          </Button>
        ) : null,
    },
  ];

  return (
    <AppShell title="Payments" subtitle="Paid, unpaid, and overdue invoices across every customer.">
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
