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

// ---- Tax invoice (proper GST invoice) ------------------------------------------------------

/** Axiora's own details printed on every invoice (SaaS Finance -> Invoice settings). */
export interface InvoiceSettings {
  seller_name?: string | null;
  address?: string | null;
  state_name?: string | null;
  state_code?: string | null;
  country?: string | null;
  gstin?: string | null;
  email?: string | null;
  phone?: string | null;
  sac_platform?: string | null;
  sac_usage?: string | null;
  terms?: string | null;
  due_days?: number | null;
  notes?: string | null;
  bank_details?: string | null;
  number_prefix?: string | null;
}

export async function getInvoiceSettings(): Promise<InvoiceSettings> {
  const { data } = await apiClient.get<InvoiceSettings>("/finance/invoice-settings");
  return data;
}

export async function saveInvoiceSettings(body: InvoiceSettings): Promise<InvoiceSettings> {
  const { data } = await apiClient.put<InvoiceSettings>("/finance/invoice-settings", body);
  return data;
}

/** A customer's details on their tax invoice (Bill To / Ship To / place of supply). */
export interface BillingDetails {
  billing_legal_name?: string | null;
  billing_gstin?: string | null;
  billing_address?: string | null;
  billing_state_name?: string | null;
  billing_state_code?: string | null;
  billing_email?: string | null;
  billing_ship_to?: string | null;
}

export async function getBillingDetails(tenantId: string): Promise<BillingDetails> {
  const { data } = await apiClient.get<BillingDetails>(`/tenants/${tenantId}/billing-details`);
  return data;
}

export async function saveBillingDetails(tenantId: string, body: BillingDetails): Promise<BillingDetails> {
  const { data } = await apiClient.put<BillingDetails>(`/tenants/${tenantId}/billing-details`, body);
  return data;
}

export interface TaxInvoiceLine {
  description: string; sac: string; qty: number; rate: number; taxable: number;
  cgst_percent: number; cgst: number; sgst_percent: number; sgst: number;
  igst_percent: number; igst: number; amount: number;
}

export interface TaxInvoiceParty {
  name: string; address: string; state_name: string; state_code: string; gstin: string;
  email?: string; phone?: string; country?: string; ship_to?: string;
}

export interface TaxInvoice {
  invoice_number: string; invoice_date: string; due_date: string; terms: string; period: string;
  place_of_supply: string; seller: TaxInvoiceParty; buyer: TaxInvoiceParty;
  tax_mode: "cgst_sgst" | "igst"; lines: TaxInvoiceLine[];
  subtotal: number; gst_percent: number; cgst: number; sgst: number; igst: number;
  total: number; balance_due: number; amount_in_words: string; notes: string; bank_details: string;
  status: string; paid_at?: string | null;
}

export async function getTaxInvoice(tenantId: string, invoiceId: string): Promise<TaxInvoice> {
  const { data } = await apiClient.get<TaxInvoice>(`/tenants/${tenantId}/billing/invoices/${invoiceId}/print`);
  return data;
}

/** GST state codes - first two digits of a GSTIN. */
export const GST_STATES: [string, string][] = [
  ["01", "Jammu and Kashmir"], ["02", "Himachal Pradesh"], ["03", "Punjab"], ["04", "Chandigarh"],
  ["05", "Uttarakhand"], ["06", "Haryana"], ["07", "Delhi"], ["08", "Rajasthan"], ["09", "Uttar Pradesh"],
  ["10", "Bihar"], ["11", "Sikkim"], ["12", "Arunachal Pradesh"], ["13", "Nagaland"], ["14", "Manipur"],
  ["15", "Mizoram"], ["16", "Tripura"], ["17", "Meghalaya"], ["18", "Assam"], ["19", "West Bengal"],
  ["20", "Jharkhand"], ["21", "Odisha"], ["22", "Chhattisgarh"], ["23", "Madhya Pradesh"], ["24", "Gujarat"],
  ["26", "Dadra and Nagar Haveli and Daman and Diu"], ["27", "Maharashtra"], ["29", "Karnataka"],
  ["30", "Goa"], ["31", "Lakshadweep"], ["32", "Kerala"], ["33", "Tamil Nadu"], ["34", "Puducherry"],
  ["35", "Andaman and Nicobar Islands"], ["36", "Telangana"], ["37", "Andhra Pradesh"], ["38", "Ladakh"],
  ["97", "Other Territory"],
];

/** Manual (one-off) invoices - backend app/api/v1/manual_invoices.py. */
export interface ManualInvoiceLine { description: string; sac: string; qty: number; rate: number; amount?: number }
export interface ManualInvoice {
  id: string; tenant_id: string; tenant_name: string;
  status: "draft" | "issued" | "paid" | "cancelled";
  invoice_number: string | null; invoice_date: string; due_date: string; period_label: string | null;
  lines: ManualInvoiceLine[]; gst_percent: number; subtotal: number; gst_amount: number; total_amount: number;
  notes: string | null; issued_at: string | null; paid_at: string | null; cancelled_at: string | null;
  cancel_reason: string | null; created_at: string | null;
}
export interface ManualInvoiceDraft {
  tenant_id: string; invoice_date: string; due_date: string; period_label: string;
  lines: ManualInvoiceLine[]; gst_percent: number; notes: string;
}

export async function listManualInvoices(): Promise<ManualInvoice[]> {
  const { data } = await apiClient.get<ManualInvoice[]>("/finance/manual-invoices");
  return data;
}
export async function createManualInvoice(body: ManualInvoiceDraft): Promise<ManualInvoice> {
  const { data } = await apiClient.post<ManualInvoice>("/finance/manual-invoices", body);
  return data;
}
export async function updateManualInvoice(id: string, body: ManualInvoiceDraft): Promise<ManualInvoice> {
  const { data } = await apiClient.put<ManualInvoice>(`/finance/manual-invoices/${id}`, body);
  return data;
}
export async function deleteManualInvoice(id: string): Promise<void> {
  await apiClient.delete(`/finance/manual-invoices/${id}`);
}
export async function issueManualInvoice(id: string): Promise<ManualInvoice> {
  const { data } = await apiClient.post<ManualInvoice>(`/finance/manual-invoices/${id}/issue`);
  return data;
}
export async function markManualInvoicePaid(id: string, paid: boolean): Promise<ManualInvoice> {
  const { data } = await apiClient.post<ManualInvoice>(`/finance/manual-invoices/${id}/mark-paid`, { paid });
  return data;
}
export async function cancelManualInvoice(id: string, reason: string): Promise<ManualInvoice> {
  const { data } = await apiClient.post<ManualInvoice>(`/finance/manual-invoices/${id}/cancel`, { reason });
  return data;
}
export async function getManualTaxInvoice(id: string): Promise<TaxInvoice> {
  const { data } = await apiClient.get<TaxInvoice>(`/finance/manual-invoices/${id}/print`);
  return data;
}
