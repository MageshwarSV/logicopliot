import { apiClient } from "./client";

/** Online invoice payment through Razorpay (IDFC FIRST Bank's gateway). The amount is always
 *  worked out by the server; the browser only opens Razorpay's window for the order it gets. */

export interface PaymentOptions {
  online_enabled: boolean;
  /** Pay now is offered to this user (Tenant Admin of this organization, gateway set up). */
  can_pay: boolean;
  fee_percent: number;
  fee_gst_percent: number;
  bank_details: string | null;
}

export interface PayOrder {
  payment_id: string;
  key_id: string;
  order_id: string;
  amount_paise: number;
  currency: string;
  invoice_number: string;
  invoice_amount: number;
  fee_amount: number;
  fee_gst_amount: number;
  total_amount: number;
  business_name: string;
  prefill_name: string;
  prefill_email: string;
}

export interface VerifyResult {
  status: "unpaid" | "paid";
  paid_at: string | null;
  payment_reference: string | null;
}

export async function getPaymentOptions(tenantId: string): Promise<PaymentOptions> {
  const { data } = await apiClient.get<PaymentOptions>(`/tenants/${tenantId}/billing/payment-options`);
  return data;
}

export async function startPayment(tenantId: string, invoiceId: string): Promise<PayOrder> {
  const { data } = await apiClient.post<PayOrder>(`/tenants/${tenantId}/billing/invoices/${invoiceId}/pay`);
  return data;
}

export async function verifyPayment(
  tenantId: string,
  invoiceId: string,
  body: { razorpay_order_id: string; razorpay_payment_id: string; razorpay_signature: string },
): Promise<VerifyResult> {
  const { data } = await apiClient.post<VerifyResult>(
    `/tenants/${tenantId}/billing/invoices/${invoiceId}/pay/verify`,
    body,
  );
  return data;
}

/** Razorpay's own checkout script, loaded once, only when someone presses Pay now. */
const CHECKOUT_SRC = "https://checkout.razorpay.com/v1/checkout.js";
let loading: Promise<void> | null = null;

export function loadCheckout(): Promise<void> {
  if ((window as unknown as { Razorpay?: unknown }).Razorpay) return Promise.resolve();
  if (loading) return loading;
  loading = new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = CHECKOUT_SRC;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => {
      loading = null;
      reject(new Error("Could not load the payment window. Check your connection and try again."));
    };
    document.body.appendChild(s);
  });
  return loading;
}
