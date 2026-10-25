import { useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import * as financeApi from "../../api/saasFinance";
import type { FinanceReportType } from "../../api/saasFinance";
import { currentMonthStr } from "./financeFormat";

const REPORTS: { type: FinanceReportType; label: string }[] = [
  { type: "monthly-usage", label: "Monthly Usage" },
  { type: "customer-billing", label: "Customer-wise Billing" },
  { type: "revenue-summary", label: "Revenue & Invoice Summary" },
  { type: "outstanding-payments", label: "Outstanding Payments" },
  { type: "page-usage-charges", label: "Page Processing & Usage Charges" },
];

export function SaasFinanceReportsPage() {
  const [active, setActive] = useState<FinanceReportType>("monthly-usage");
  const [rows, setRows] = useState<Record<string, string | number>[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<"csv" | "xlsx" | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    financeApi
      .getFinanceReport(active)
      .then(setRows)
      .catch(() => setError("Could not load this report."))
      .finally(() => setLoading(false));
  }, [active]);

  async function handleExport(format: "csv" | "xlsx") {
    setExporting(format);
    try {
      await financeApi.exportFinanceReport(active, format);
    } catch {
      setError("Could not export this report.");
    } finally {
      setExporting(null);
    }
  }

  const headers = rows[0] ? Object.keys(rows[0]) : [];

  return (
    <AppShell title="Reports" subtitle={`Platform-wide reports for ${currentMonthStr()}.`}>
      <Card className="mb-4 flex flex-wrap items-center gap-2 p-4">
        {REPORTS.map((r) => (
          <button
            key={r.type}
            type="button"
            onClick={() => setActive(r.type)}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              active === r.type
                ? "bg-indigo-600 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
            }`}
          >
            {r.label}
          </button>
        ))}
      </Card>

      {error && <div className="mb-4"><Alert>{error}</Alert></div>}

      <Card className="p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">
            {REPORTS.find((r) => r.type === active)?.label}
          </h2>
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" isLoading={exporting === "csv"} onClick={() => handleExport("csv")}>
              Export CSV
            </Button>
            <Button variant="secondary" size="sm" isLoading={exporting === "xlsx"} onClick={() => handleExport("xlsx")}>
              Export Excel
            </Button>
          </div>
        </div>

        {loading ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-slate-400">No data for this report yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 dark:border-slate-800">
                  {headers.map((h) => (
                    <th key={h} className="whitespace-nowrap px-4 py-2 font-medium text-slate-500 dark:text-slate-400">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {rows.map((row, i) => (
                  <tr key={i}>
                    {headers.map((h) => (
                      <td key={h} className="whitespace-nowrap px-4 py-2.5 text-slate-700 dark:text-slate-300">
                        {row[h]}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </AppShell>
  );
}
