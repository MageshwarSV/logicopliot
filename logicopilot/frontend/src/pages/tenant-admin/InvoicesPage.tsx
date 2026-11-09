import { useEffect, useState } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { useAuth } from "../../auth/useAuth";
import * as tenantsApi from "../../api/tenants";
import type { Invoice } from "../../api/tenants";

function formatInr(n: number): string {
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-IN", { month: "long", year: "numeric" });
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

/** Invoices, split out of Billing & Usage into its own sidebar destination - same data
 *  (tenantsApi.listInvoices), same table, just reachable on its own rather than only as the
 *  bottom card of the combined Usage & Consumption page (still there too, unchanged - this
 *  is a second front door, not a replacement). "View" jumps to the Usage page scoped to that
 *  invoice's month, since the actual usage breakdown still lives there. */
export function InvoicesPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user?.tenant_id) return;
    setLoading(true);
    tenantsApi
      .listInvoices(user.tenant_id)
      .then(setInvoices)
      .catch((err) =>
        setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not load invoices." : "Could not load invoices."),
      )
      .finally(() => setLoading(false));
  }, [user?.tenant_id]);

  return (
    <AppShell title="Invoices" subtitle="Every invoice raised for your organization, most recent first.">
      {error && <p className="mb-4 text-sm text-rose-600 dark:text-rose-400">{error}</p>}
      <Card className="p-5">
        {loading ? (
          <p className="py-10 text-center text-sm text-slate-400">Loading…</p>
        ) : invoices.length === 0 ? (
          <p className="py-10 text-center text-sm text-slate-400">No invoices raised yet.</p>
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
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => navigate(`/tenant-admin/billing?month=${inv.period_start.slice(0, 7)}`)}
                    >
                      View
                    </Button>
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
