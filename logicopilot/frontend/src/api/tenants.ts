import { apiClient } from "./client";
import type { Tenant } from "../types/tenant";

export async function listTenants(): Promise<Tenant[]> {
  const response = await apiClient.get<Tenant[]>("/tenants");
  return response.data;
}

export async function getTenant(tenantId: string): Promise<Tenant> {
  const response = await apiClient.get<Tenant>(`/tenants/${tenantId}`);
  return response.data;
}

export async function deleteTenant(tenantId: string): Promise<void> {
  await apiClient.delete(`/tenants/${tenantId}`);
}

/** "Generate Link" on the Super Admin dashboard - creates the tenant's IRN Pending key the
 *  first time this is called for them, or just returns the existing one on every call after
 *  that (never rotates it). */
export async function generateIrnPendingKey(tenantId: string): Promise<Tenant> {
  const response = await apiClient.post<Tenant>(`/tenants/${tenantId}/irn-pending-key`);
  return response.data;
}

/** Tenant Admin manages their own tenant's allowed_modes (the "Shipment Type" page); Super
 *  Admin can use it for any tenant. Empty array clears the restriction (unrestricted). */
export async function updateTenant(
  tenantId: string,
  payload: {
    allowed_modes?: string[];
    role_write_enabled?: Record<string, boolean>;
    gk2_required_by_mode?: Record<string, boolean>;
  },
): Promise<Tenant> {
  const response = await apiClient.patch<Tenant>(`/tenants/${tenantId}`, payload);
  return response.data;
}

/** Pure reference data for the Super Admin's own records - what this tenant is billed for
 *  CARGORA itself (platform fee + two-tier AI page rate) and which TrackContainer.in plan
 *  they're on. Returns the standard published rates when nothing has been saved yet. */
export interface TenantBillingConfig {
  cargora_monthly_fee: number;
  cargora_tier1_rate_per_page: number;
  cargora_tier1_page_limit: number;
  cargora_tier2_rate_per_page: number;
  /** A page never matched to any document ("Not Required") is billed at this flat rate
   *  instead of the Tier 1/2 rate above. */
  not_required_page_rate: number;
  trackcontainer_plan: "starter" | "professional" | null;
  trackcontainer_starter_price: number;
  trackcontainer_starter_container_limit: number;
  trackcontainer_professional_price: number;
  trackcontainer_professional_container_limit: number;
  gst_percent: number;
  /** YYYY-MM-DD go-live date, or null for no proration. The calendar month this falls in has
   *  its platform fee pro-rated to the days actually live; every month after bills in full. */
  billing_start_date: string | null;
}

export async function getTenantBillingConfig(tenantId: string): Promise<TenantBillingConfig> {
  const response = await apiClient.get<TenantBillingConfig>(`/tenants/${tenantId}/billing-config`);
  return response.data;
}

/** Only the fields included are changed - leave the rest out to keep them as they are. */
export async function updateTenantBillingConfig(
  tenantId: string,
  payload: Partial<TenantBillingConfig>,
): Promise<TenantBillingConfig> {
  const response = await apiClient.put<TenantBillingConfig>(`/tenants/${tenantId}/billing-config`, payload);
  return response.data;
}

/** This tenant's own extraction pause + OpenAI key routing, independent of every other
 *  tenant and of the platform-wide switches on Settings. "shared" = use the global OpenAI
 *  key pool (today's only behavior); "own" = use only this tenant's own keys below. */
export interface TenantExtractionConfig {
  extraction_paused: boolean;
  openai_key_mode: "shared" | "own";
}

export async function getTenantExtractionConfig(tenantId: string): Promise<TenantExtractionConfig> {
  const response = await apiClient.get<TenantExtractionConfig>(`/tenants/${tenantId}/extraction-config`);
  return response.data;
}

export async function updateTenantExtractionConfig(
  tenantId: string,
  payload: Partial<TenantExtractionConfig>,
): Promise<TenantExtractionConfig> {
  const response = await apiClient.put<TenantExtractionConfig>(
    `/tenants/${tenantId}/extraction-config`,
    payload,
  );
  return response.data;
}

export interface TenantOpenAIKey {
  id: string;
  label: string;
  is_active: boolean;
  sort_order: number;
}

export async function listTenantOpenAIKeys(tenantId: string): Promise<TenantOpenAIKey[]> {
  const response = await apiClient.get<TenantOpenAIKey[]>(`/tenants/${tenantId}/openai-key-pool`);
  return response.data;
}

export async function addTenantOpenAIKey(
  tenantId: string,
  label: string,
  apiKey: string,
): Promise<TenantOpenAIKey> {
  const response = await apiClient.post<TenantOpenAIKey>(`/tenants/${tenantId}/openai-key-pool`, {
    label,
    api_key: apiKey,
  });
  return response.data;
}

export async function setTenantOpenAIKeyActive(
  tenantId: string,
  keyId: string,
  isActive: boolean,
): Promise<TenantOpenAIKey> {
  const response = await apiClient.patch<TenantOpenAIKey>(
    `/tenants/${tenantId}/openai-key-pool/${keyId}`,
    { is_active: isActive },
  );
  return response.data;
}

export async function deleteTenantOpenAIKey(tenantId: string, keyId: string): Promise<void> {
  await apiClient.delete(`/tenants/${tenantId}/openai-key-pool/${keyId}`);
}

export interface ShipmentTypeUsage {
  shipment_type: string;
  jobs: number;
  pages: number;
}

/** The Billing & Usage page's headline figure for one calendar month. The current month is
 *  always live (is_finalized: false, no invoice fields set); any earlier month is a permanent,
 *  already-raised invoice's own frozen numbers. */
export interface BillingUsage {
  month: string; // YYYY-MM
  is_finalized: boolean;
  invoice_id: string | null;
  invoice_number: string | null;
  status: "unpaid" | "paid" | null;
  total_jobs: number;
  total_pages: number;
  by_shipment_type: ShipmentTypeUsage[];
  /** Of total_pages: first-time processing vs an explicit reclassify-triggered re-run. null on
   *  a finalized (past-month, from a stored Invoice) response - older invoices never recorded
   *  this breakdown. */
  pages_original: number | null;
  pages_rerun: number | null;
  /** Read live from current job/document state, not the permanent ledger, so this can drift
   *  slightly from pages_original if a page was deleted afterward without a matching rerun.
   *  null on a finalized response, same reason as above. */
  final_classified_pages: number | null;
  final_unclassified_pages: number | null;
  /** Of the jobs billed THIS period (by event date, not job creation date - a different
   *  scoping from total_jobs above), how many still exist vs have been hard-deleted since.
   *  null on a finalized response, same reason as above. */
  active_jobs_billed: number | null;
  deleted_jobs_billed: number | null;
  /** Original Pages minus whatever is still classified/unclassified live - a page billed once
   *  and later removed (a duplicate/wrong upload deleted, with no rerun to replace it) stays
   *  billed forever, but isn't counted as classified or unclassified anywhere any more. null on
   *  a finalized response, same reason as above. */
  deleted_pages: number | null;
  platform_fee: number;
  /** platform_fee_billed_days < platform_fee_month_days means this month's fee was pro-rated
   *  (a go-live date fell partway through it). Equal means a normal, full-month charge. */
  platform_fee_billed_days: number;
  platform_fee_month_days: number;
  tier1_rate: number;
  tier1_pages: number;
  tier1_amount: number;
  tier2_rate: number;
  tier2_pages: number;
  tier2_amount: number;
  /** Pages never matched to any document ("Not Required"), billed at this flat rate instead
   *  of Tier 1/2 - not counted in tier1_pages/tier2_pages above. */
  not_required_rate: number;
  not_required_pages: number;
  not_required_amount: number;
  usage_charges: number;
  subtotal: number;
  gst_percent: number;
  gst_amount: number;
  total_amount: number;
}

export interface Invoice {
  id: string;
  invoice_number: string;
  period_start: string;
  period_end: string;
  platform_fee: number;
  platform_fee_billed_days: number;
  platform_fee_month_days: number;
  total_jobs: number;
  total_pages: number;
  tier1_rate: number;
  tier1_pages: number;
  tier1_amount: number;
  tier2_rate: number;
  tier2_pages: number;
  tier2_amount: number;
  not_required_rate: number;
  not_required_pages: number;
  not_required_amount: number;
  usage_charges: number;
  subtotal: number;
  gst_percent: number;
  gst_amount: number;
  total_amount: number;
  by_shipment_type: Record<string, { jobs: number; pages: number }>;
  status: "unpaid" | "paid";
  paid_at: string | null;
  created_at: string;
}

/** Omit `month` for the current, live month; pass "YYYY-MM" for an earlier, finalized one. */
export async function getBillingUsage(tenantId: string, month?: string): Promise<BillingUsage> {
  const response = await apiClient.get<BillingUsage>(`/tenants/${tenantId}/billing/usage`, {
    params: month ? { month } : undefined,
  });
  return response.data;
}

export async function listInvoices(tenantId: string): Promise<Invoice[]> {
  const response = await apiClient.get<Invoice[]>(`/tenants/${tenantId}/billing/invoices`);
  return response.data;
}

/** Super Admin or SaaS Finance - marks an invoice paid once payment is actually received
 *  outside the platform (there is no payment gateway integration yet). */
export async function markInvoicePaid(tenantId: string, invoiceId: string, paid = true): Promise<Invoice> {
  const response = await apiClient.post<Invoice>(
    `/tenants/${tenantId}/billing/invoices/${invoiceId}/mark-paid`,
    { paid },
  );
  return response.data;
}

export interface JobBillingRow {
  job_id: string;
  job_reference: string;
  shipment_type: string;
  /** A live Job.status value, or "deleted" if the job no longer exists. */
  status: string;
  started_at: string; // ISO 8601
  original_pages: number;
  rerun_pages: number;
  final_classified_pages: number;
  final_unclassified_pages: number;
  /** original_pages minus whatever is still classified/unclassified live - a page billed once
   *  (real OCR cost incurred) and later removed (a duplicate/wrong upload deleted, with no
   *  rerun to replace it) stays billed forever, but is no longer counted as classified or
   *  unclassified anywhere. Never negative. */
  deleted_pages: number;
  total_billed: number;
}

export interface JobBillingPage {
  items: JobBillingRow[];
  total: number;
  page: number;
  page_size: number;
}

/** Which StatCard was clicked on the Billing & Usage page - narrows the Job Details table to
 *  just the jobs that number is counting. Keep in sync with backend.billing.HIGHLIGHTS. */
export type JobBillingHighlight = "deleted_jobs" | "unclassified" | "deleted_pages" | "rerun";

export interface JobBillingFilters {
  month?: string;
  search?: string;
  shipmentType?: string;
  /** YYYY-MM-DD. Narrows within the selected month - omit for the full month. */
  dateFrom?: string;
  dateTo?: string;
  highlight?: JobBillingHighlight | null;
}

function billingJobsParams(filters: JobBillingFilters) {
  return {
    month: filters.month || undefined,
    search: filters.search || undefined,
    shipment_type: filters.shipmentType || undefined,
    date_from: filters.dateFrom || undefined,
    date_to: filters.dateTo || undefined,
    highlight: filters.highlight || undefined,
  };
}

/** The Billing & Usage page's own per-job table, server-paginated. */
export async function listBillingJobs(
  tenantId: string,
  filters: JobBillingFilters,
  page: number,
  pageSize: number,
): Promise<JobBillingPage> {
  const response = await apiClient.get<JobBillingPage>(`/tenants/${tenantId}/billing/jobs`, {
    params: { ...billingJobsParams(filters), page, page_size: pageSize },
  });
  return response.data;
}

/** Downloads the same rows listBillingJobs shows (same filters, no pagination) as a CSV blob -
 *  the caller turns this into an object-URL download. */
export async function exportBillingJobs(tenantId: string, filters: JobBillingFilters): Promise<Blob> {
  const response = await apiClient.get<Blob>(`/tenants/${tenantId}/billing/jobs/export`, {
    params: billingJobsParams(filters),
    responseType: "blob",
  });
  return response.data;
}

export interface DashboardFilters {
  /** YYYY-MM-DD, inclusive. Both default server-side to the current month so far. */
  dateFrom?: string;
  dateTo?: string;
  /** Comma-separated shipment modes (e.g. "Sea Import,Air Import"). Omit for every mode. */
  mode?: string;
}

function dashboardParams(filters: DashboardFilters) {
  return {
    date_from: filters.dateFrom || undefined,
    date_to: filters.dateTo || undefined,
    mode: filters.mode || undefined,
  };
}

export interface DocTypeAccuracy {
  document_name: string;
  documents: number;
  fields_extracted: number;
  fields_edited: number;
  accuracy_pct: number | null;
}

export interface DashboardTrendPoint {
  day: string;
  accuracy_pct?: number | null;
  avg_total_tat_minutes?: number | null;
}

export interface AccuracyDashboard {
  documents_processed: number;
  fields_extracted_by_ai: number;
  fields_manually_edited: number;
  accuracy_pct: number | null;
  by_document_type: DocTypeAccuracy[];
  trend: DashboardTrendPoint[];
}

export interface ProcessingTimeDashboard {
  total_jobs: number;
  /** How many of total_jobs have actually finished - avg_total_tat_minutes is computed from
   *  only this subset. */
  jobs_completed: number;
  avg_total_tat_minutes: number | null;
  avg_extraction_minutes: number | null;
  avg_review_minutes: number | null;
  avg_erp_submission_minutes: number | null;
  trend: DashboardTrendPoint[];
}

/** Executive Dashboard - Data Accuracy page. Scoped to completed jobs only (see
 *  app/core/dashboard_metrics.py's own module docstring for why). */
export async function getAccuracyDashboard(
  tenantId: string,
  filters: DashboardFilters,
): Promise<AccuracyDashboard> {
  const response = await apiClient.get<AccuracyDashboard>(`/tenants/${tenantId}/dashboard/accuracy`, {
    params: dashboardParams(filters),
  });
  return response.data;
}

/** Executive Dashboard - Job Processing Time page. */
export async function getProcessingTimeDashboard(
  tenantId: string,
  filters: DashboardFilters,
): Promise<ProcessingTimeDashboard> {
  const response = await apiClient.get<ProcessingTimeDashboard>(`/tenants/${tenantId}/dashboard/processing-time`, {
    params: dashboardParams(filters),
  });
  return response.data;
}

export interface EmployeeReviewRow {
  user_id: string;
  name: string;
  jobs_reviewed: number;
  review_hours: number;
  avg_minutes_per_job: number | null;
}

export interface WorkforceDashboard {
  employee_requirement_fte: number | null;
  avg_review_minutes_per_job: number | null;
  total_jobs: number;
  human_review_hours: number;
  jobs_with_recorded_review: number;
  by_employee: EmployeeReviewRow[];
}

/** Executive Dashboard - Workforce Productivity page. Only covers jobs with a real recorded
 *  "Approved and Proceed" timestamp - jobs_with_recorded_review says how many of total_jobs
 *  that is. */
export async function getWorkforceDashboard(
  tenantId: string,
  filters: DashboardFilters,
): Promise<WorkforceDashboard> {
  const response = await apiClient.get<WorkforceDashboard>(`/tenants/${tenantId}/dashboard/workforce`, {
    params: dashboardParams(filters),
  });
  return response.data;
}

export type TenantReportType = "monthly-usage" | "billing-summary" | "revenue-trend" | "outstanding-payments";

/** A tenant-scoped report row set, trailing 12 months (except outstanding-payments, which is
 *  every unpaid invoice regardless of month). Keys vary by report type - render generically
 *  off Object.keys(rows[0]), same pattern the SaaS Finance reports page already uses. */
export async function getTenantReport(
  tenantId: string,
  reportType: TenantReportType,
  month?: string,
): Promise<Record<string, string | number>[]> {
  const response = await apiClient.get<Record<string, string | number>[]>(
    `/tenants/${tenantId}/reports/${reportType}`,
    { params: { month: month || undefined } },
  );
  return response.data;
}

export async function exportTenantReport(
  tenantId: string,
  reportType: TenantReportType,
  format: "csv" | "xlsx",
  month?: string,
): Promise<void> {
  const response = await apiClient.get(`/tenants/${tenantId}/reports/${reportType}/export`, {
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

export async function createTenant(payload: {
  name: string;
  region?: string;
  currency?: string;
  /** Transport modes this client is licensed for. Omit/empty = unrestricted. */
  allowed_modes?: string[];
  admin_full_name?: string;
  admin_email?: string;
  admin_password?: string;
}): Promise<Tenant> {
  const response = await apiClient.post<Tenant>("/tenants", payload);
  return response.data;
}
