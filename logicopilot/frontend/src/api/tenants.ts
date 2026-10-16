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

/** Super Admin only - marks an invoice paid once payment is actually received outside the
 *  platform (there is no payment gateway integration yet). */
export async function markInvoicePaid(tenantId: string, invoiceId: string, paid = true): Promise<Invoice> {
  const response = await apiClient.post<Invoice>(
    `/tenants/${tenantId}/billing/invoices/${invoiceId}/mark-paid`,
    { paid },
  );
  return response.data;
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
