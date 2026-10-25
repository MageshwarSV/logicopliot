import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import { DataTable, type Column } from "../../components/ui/DataTable";
import * as financeApi from "../../api/saasFinance";
import type { FinanceCustomerRow } from "../../api/saasFinance";
import { currentMonthStr, formatInr } from "./financeFormat";

/** No create/edit/delete action anywhere on this page, by design - Finance only ever VIEWS
 *  customers (the backend also still blocks it: saas_finance was never added to
 *  create_tenant/update_tenant/delete_tenant's role lists in app/api/v1/tenants.py). */
export function SaasFinanceCustomersPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<FinanceCustomerRow[]>([]);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "inactive">("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    financeApi
      .getFinanceDashboard()
      .then((d) => setRows(d.by_customer))
      .catch((err) => {
        setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not load customers." : "Could not load customers.");
      })
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (statusFilter === "active" && !r.is_active) return false;
      if (statusFilter === "inactive" && r.is_active) return false;
      if (search && !r.tenant_name.toLowerCase().includes(search.toLowerCase())) return false;
      return true;
    });
  }, [rows, search, statusFilter]);

  const columns: Column<FinanceCustomerRow>[] = [
    {
      header: "Customer",
      render: (r) => (
        <span className="font-medium text-slate-900 dark:text-slate-100">
          {r.tenant_name}
          {!r.is_active && (
            <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
              Inactive
            </span>
          )}
        </span>
      ),
    },
    { header: "Jobs", render: (r) => r.jobs.toLocaleString() },
    { header: "Pages", render: (r) => r.pages.toLocaleString() },
    { header: "Platform Fee", render: (r) => formatInr(r.platform_fee) },
    { header: "Usage Charges", render: (r) => formatInr(r.usage_charges) },
    { header: "GST", render: (r) => formatInr(r.gst_amount) },
    { header: "Total Billable", render: (r) => <span className="font-medium">{formatInr(r.total_amount)}</span> },
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
    {
      header: "",
      render: (r) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            navigate(`/saas-finance/customers/${r.tenant_id}`);
          }}
          className="text-sm font-medium text-indigo-600 hover:underline dark:text-indigo-400"
        >
          View
        </button>
      ),
    },
  ];

  return (
    <AppShell title="Customers" subtitle={`Every customer's billing summary for ${currentMonthStr()}.`}>
      <Card className="mb-4 flex flex-wrap items-center gap-3 p-4">
        <input
          type="text"
          placeholder="Search customer…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-64 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as "all" | "active" | "inactive")}
          className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
        >
          <option value="all">All statuses</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </select>
      </Card>

      {error && <div className="mb-4"><Alert>{error}</Alert></div>}

      <Card className="p-5">
        {loading ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : (
          <DataTable
            columns={columns}
            rows={filtered}
            keyFor={(r) => r.tenant_id}
            onRowClick={(r) => navigate(`/saas-finance/customers/${r.tenant_id}`)}
            emptyMessage="No customers match."
          />
        )}
      </Card>
    </AppShell>
  );
}
