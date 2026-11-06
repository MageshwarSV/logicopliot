import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { Card } from "../ui/Card";
import { Button } from "../ui/Button";
import { DataTable, type Column } from "../ui/DataTable";
import { Pagination } from "../ui/Pagination";
import { SearchIcon, DownloadIcon } from "../ui/icons";
import * as tenantsApi from "../../api/tenants";
import type { JobBillingRow, ShipmentTypeUsage } from "../../api/tenants";

const PAGE_SIZE = 10;

function monthBounds(month: string): { from: string; to: string } {
  const [y, m] = month.split("-").map(Number);
  const from = `${y}-${String(m).padStart(2, "0")}-01`;
  const lastDay = new Date(y, m, 0).getDate();
  const to = `${y}-${String(m).padStart(2, "0")}-${String(lastDay).padStart(2, "0")}`;
  return { from, to };
}

const STATUS_TONE: Record<string, string> = {
  completed: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300",
  submitted: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300",
  extracted: "bg-sky-50 text-sky-700 dark:bg-sky-500/10 dark:text-sky-300",
  processing: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300",
  extracting: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300",
  pending: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300",
  preparing_erp: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300",
  entering_erp: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300",
  draft: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
  open: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
  failed: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300",
  error: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300",
  stopped: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300",
  deleted: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300",
  possible_duplicate: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300",
};

function humanizeStatus(status: string): string {
  return status
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function StatusChip({ status }: { status: string }) {
  const tone = STATUS_TONE[status] ?? "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400";
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${tone}`}>
      {humanizeStatus(status)}
    </span>
  );
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/** The reference design's "Job Details" card - per-job Original/Final Classified/Final
 *  Unclassified/Rerun/Total breakdown, searchable by job number, filterable by shipment type
 *  and date range, server-paginated (job counts can be large), with CSV export. Shared by both
 *  BillingUsagePage.tsx and UsageBillingPage.tsx so the two stay visually identical. */
export function JobBillingTable({
  tenantId,
  month,
  shipmentTypes,
  onJobClick,
}: {
  tenantId: string;
  month: string;
  /** Options for the Shipment Type filter - reuse whatever this month's usage already named. */
  shipmentTypes: ShipmentTypeUsage[];
  onJobClick?: (jobId: string) => void;
}) {
  const defaultBounds = useMemo(() => monthBounds(month), [month]);

  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [shipmentType, setShipmentType] = useState("");
  const [dateFrom, setDateFrom] = useState(defaultBounds.from);
  const [dateTo, setDateTo] = useState(defaultBounds.to);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ items: JobBillingRow[]; total: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  // Reset the date range to the newly-selected month whenever the parent's own month changes.
  useEffect(() => {
    setDateFrom(defaultBounds.from);
    setDateTo(defaultBounds.to);
    setPage(1);
  }, [defaultBounds]);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 350);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, shipmentType, dateFrom, dateTo]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    tenantsApi
      .listBillingJobs(
        tenantId,
        { month, search: debouncedSearch, shipmentType, dateFrom, dateTo },
        page,
        PAGE_SIZE,
      )
      .then((res) => {
        if (!cancelled) setData({ items: res.items, total: res.total });
      })
      .catch((err) => {
        if (cancelled) return;
        setData(null);
        setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not load jobs." : "Could not load jobs.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tenantId, month, debouncedSearch, shipmentType, dateFrom, dateTo, page]);

  async function handleExport() {
    setExporting(true);
    try {
      const blob = await tenantsApi.exportBillingJobs(tenantId, {
        month, search: debouncedSearch, shipmentType, dateFrom, dateTo,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `billing-jobs-${month}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      setError("Could not export jobs.");
    } finally {
      setExporting(false);
    }
  }

  const columns: Column<JobBillingRow>[] = [
    {
      header: "#",
      render: (r) => (page - 1) * PAGE_SIZE + (data?.items.indexOf(r) ?? 0) + 1,
    },
    {
      header: "Job Number",
      render: (r) => (
        <span className="font-medium text-indigo-600 dark:text-indigo-400">{r.job_reference}</span>
      ),
    },
    { header: "Shipment Type", render: (r) => r.shipment_type },
    { header: "Job Start Time", render: (r) => formatDateTime(r.started_at) },
    { header: "Status", render: (r) => <StatusChip status={r.status} /> },
    { header: "Original Pages", render: (r) => r.original_pages.toLocaleString() },
    { header: "Final Classified", render: (r) => r.final_classified_pages.toLocaleString() },
    { header: "Final Unclassified", render: (r) => r.final_unclassified_pages.toLocaleString() },
    { header: "Rerun Pages", render: (r) => r.rerun_pages.toLocaleString() },
    {
      header: "Total (Billed)",
      render: (r) => <span className="font-semibold text-slate-900 dark:text-slate-100">{r.total_billed.toLocaleString()}</span>,
    },
  ];

  return (
    <Card className="p-5">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="font-semibold text-slate-900 dark:text-slate-50">Job Details</h2>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by Job Number…"
              className="w-48 rounded-lg border border-slate-200 bg-white py-1.5 pl-8 pr-3 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
            />
          </div>
          <select
            value={shipmentType}
            onChange={(e) => setShipmentType(e.target.value)}
            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
          >
            <option value="">All Shipment Types</option>
            {shipmentTypes.map((s) => (
              <option key={s.shipment_type} value={s.shipment_type}>
                {s.shipment_type}
              </option>
            ))}
          </select>
          <div className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2 py-1 dark:border-slate-700">
            <input
              type="date"
              value={dateFrom}
              min={defaultBounds.from}
              max={defaultBounds.to}
              onChange={(e) => setDateFrom(e.target.value)}
              className="bg-transparent text-sm text-slate-700 dark:text-slate-200"
            />
            <span className="text-slate-400">–</span>
            <input
              type="date"
              value={dateTo}
              min={defaultBounds.from}
              max={defaultBounds.to}
              onChange={(e) => setDateTo(e.target.value)}
              className="bg-transparent text-sm text-slate-700 dark:text-slate-200"
            />
          </div>
          <Button variant="secondary" size="sm" isLoading={exporting} onClick={handleExport}>
            <DownloadIcon className="h-4 w-4" /> Export
          </Button>
        </div>
      </div>

      {error && <p className="mb-3 text-sm text-rose-600 dark:text-rose-400">{error}</p>}

      {loading && !data ? (
        <p className="py-10 text-center text-sm text-slate-400">Loading…</p>
      ) : (
        <>
          <DataTable
            columns={columns}
            rows={data?.items ?? []}
            keyFor={(r) => r.job_id}
            emptyMessage="No jobs match these filters."
            onRowClick={onJobClick ? (r) => onJobClick(r.job_id) : undefined}
          />
          {data && (
            <div className="mt-4">
              <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPageChange={setPage} />
            </div>
          )}
        </>
      )}
    </Card>
  );
}
