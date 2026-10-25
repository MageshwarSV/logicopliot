import { apiClient } from "./client";

export interface FinanceCustomerRow {
  tenant_id: string;
  tenant_name: string;
  is_active: boolean;
  jobs: number;
  pages: number;
  platform_fee: number;
  usage_charges: number;
  gst_amount: number;
  total_amount: number;
  /** null means this month is still live, not yet an invoice. */
  invoice_status: "unpaid" | "paid" | null;
}

export interface FinanceRevenuePoint {
  month: string; // YYYY-MM
  total_amount: number;
}

export interface FinanceDashboard {
  month: string;
  total_customers: number;
  total_jobs: number;
  total_pages: number;
  total_usage_charges: number;
  total_billable_amount: number;
  /** A running balance across every tenant's unpaid invoices - independent of `month`. */
  outstanding_amount: number;
  by_customer: FinanceCustomerRow[];
  revenue_trend: FinanceRevenuePoint[];
}

/** Omit `month` for the current, live month. */
export async function getFinanceDashboard(month?: string): Promise<FinanceDashboard> {
  const { data } = await apiClient.get<FinanceDashboard>("/finance/dashboard", {
    params: month ? { month } : undefined,
  });
  return data;
}

export interface FinanceInvoiceRow {
  id: string;
  tenant_id: string;
  tenant_name: string;
  invoice_number: string;
  period_start: string;
  period_end: string;
  total_jobs: number;
  total_pages: number;
  platform_fee: number;
  usage_charges: number;
  gst_amount: number;
  total_amount: number;
  status: "unpaid" | "paid";
  paid_at: string | null;
}

export interface FinanceInvoiceFilters {
  tenant_id?: string;
  status?: "unpaid" | "paid";
  period_from?: string; // YYYY-MM
  period_to?: string; // YYYY-MM
}

export async function listFinanceInvoices(filters: FinanceInvoiceFilters = {}): Promise<FinanceInvoiceRow[]> {
  const { data } = await apiClient.get<FinanceInvoiceRow[]>("/finance/invoices", { params: filters });
  return data;
}

export type FinanceReportType =
  | "monthly-usage"
  | "customer-billing"
  | "revenue-summary"
  | "outstanding-payments"
  | "page-usage-charges";

export async function getFinanceReport(
  reportType: FinanceReportType,
  month?: string,
): Promise<Record<string, string | number>[]> {
  const { data } = await apiClient.get<Record<string, string | number>[]>(
    `/finance/reports/${reportType}`,
    { params: month ? { month } : undefined },
  );
  return data;
}

/** Triggers a browser download of the report as CSV or XLSX - there is nothing to "return",
 *  the file goes straight to the user's downloads. */
export async function exportFinanceReport(
  reportType: FinanceReportType,
  format: "csv" | "xlsx",
  month?: string,
): Promise<void> {
  const response = await apiClient.get(`/finance/reports/${reportType}/export`, {
    params: { format, ...(month ? { month } : {}) },
    responseType: "blob",
  });
  const url = window.URL.createObjectURL(new Blob([response.data]));
  const link = document.createElement("a");
  link.href = url;
  const disposition = response.headers["content-disposition"] as string | undefined;
  const match = disposition?.match(/filename="(.+)"/);
  link.download = match?.[1] ?? `${reportType}.${format}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}
