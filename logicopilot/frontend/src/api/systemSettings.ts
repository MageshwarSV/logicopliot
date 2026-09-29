import { apiClient } from "./client";

export interface SystemSettings {
  email_pull_paused: boolean;
  extraction_paused: boolean;
  /** Never any part of the key itself, not even masked - a partial value is still
   *  something "anyone can take". Changing it is a write-only action. */
  openai_api_key_set: boolean;
  /** A SEPARATE key from the one above (see setOpenAIAdminKey) - only used to read real
   *  spend for Spend Analytics, never to make calls. */
  openai_admin_key_set: boolean;
  email_poll_workers: number;
  /** What THIS server can actually sustain (one thread per CPU core) - a hard ceiling,
   *  not a suggestion; the backend refuses anything above it. */
  max_email_poll_workers: number;
  /** Manually entered (see setOpenAIBalance) - OpenAI has no API, not even the Admin key,
   *  that returns account credit balance or its expiry. null when never set. */
  openai_balance_usd: number | null;
  /** YYYY-MM-DD, or null if never set / no expiry entered. */
  openai_balance_expiry: string | null;
}

export async function getSystemSettings(): Promise<SystemSettings> {
  const { data } = await apiClient.get<SystemSettings>("/system-settings");
  return data;
}

/** Turning this ON also clears any message caught mid-read - it was opened but nothing
 *  completed for it, so the response reports how many were cleared back to retriable. */
export async function setEmailPullPaused(
  paused: boolean,
): Promise<SystemSettings & { cleared_in_progress: number }> {
  const { data } = await apiClient.post<SystemSettings & { cleared_in_progress: number }>(
    "/system-settings/email-pull",
    { paused },
  );
  return data;
}

export async function setExtractionPaused(paused: boolean): Promise<SystemSettings> {
  const { data } = await apiClient.post<SystemSettings>("/system-settings/extraction", { paused });
  return data;
}

export interface Mailbox {
  user_id: string;
  full_name: string;
  tenant_id: string | null;
  tenant_name: string | null;
  mail_provider: string | null;
  mail_email: string;
  is_active: boolean;
  /** Per-mailbox switch - independent of the whole-system email_pull_paused flag above.
   *  Stopping one operator's mailbox never touches anyone else's. */
  mail_paused: boolean;
}

/** Every operator mailbox connected anywhere in the system (Gmail, Zoho, ...), across every
 *  tenant - this screen is Super Admin/system-wide, not scoped to one tenant. The shared
 *  inbox (configured in .env) has no per-row identity and is not included here. */
export async function listMailboxes(): Promise<Mailbox[]> {
  const { data } = await apiClient.get<Mailbox[]>("/system-settings/mailboxes");
  return data;
}

export async function setMailboxPaused(
  userId: string,
  paused: boolean,
): Promise<{ user_id: string; mail_paused: boolean }> {
  const { data } = await apiClient.post<{ user_id: string; mail_paused: boolean }>(
    `/system-settings/mailboxes/${userId}/pause`,
    { paused },
  );
  return data;
}

/** Verified live against OpenAI before being saved - rejects with a 400 (message in
 *  err.response.data.detail) if the key itself is invalid, so nothing gets saved on a typo.
 *  Applied immediately: the very next AI call anywhere in the app uses it, no restart. */
export async function setOpenAIApiKey(apiKey: string): Promise<{ openai_api_key_set: boolean }> {
  const { data } = await apiClient.post<{ openai_api_key_set: boolean }>(
    "/system-settings/openai-key",
    { api_key: apiKey },
  );
  return data;
}

/** Refused (400, message in err.response.data.detail) rather than clamped if it is above
 *  what this server can sustain - see max_email_poll_workers on the settings response. */
export async function setEmailPollWorkers(count: number): Promise<{ email_poll_workers: number }> {
  const { data } = await apiClient.post<{ email_poll_workers: number }>(
    "/system-settings/workers",
    { count },
  );
  return data;
}

export interface SpendAnalyticsDay {
  date: string;
  jobs: number;
  documents: number;
  pages: number;
  characters: number;
  estimated_tokens: number;
  zero_result_jobs: number;
}

export interface SpendAnalyticsTotals {
  jobs: number;
  documents: number;
  pages: number;
  characters: number;
  estimated_tokens: number;
  zero_result_jobs: number;
}

export interface SpendAnalytics {
  start: string;
  end: string;
  totals: SpendAnalyticsTotals;
  days: SpendAnalyticsDay[];
}

/** Platform-wide (every tenant) jobs/documents/pages, and an ESTIMATED token count derived
 *  from the actual OCR text volume this system read (~4 characters/token) — not the real
 *  OpenAI bill, which this app never records anywhere. start/end are inclusive YYYY-MM-DD
 *  dates; omit both for the last 30 days ending today. */
export async function getSpendAnalytics(start?: string, end?: string): Promise<SpendAnalytics> {
  const { data } = await apiClient.get<SpendAnalytics>("/system-settings/spend-analytics", {
    params: { ...(start ? { start } : {}), ...(end ? { end } : {}) },
  });
  return data;
}

/** OpenAI's own "Admin API key" (Organization > Admin keys on platform.openai.com) - a
 *  SEPARATE credential from the regular API key above. That regular key is used to MAKE
 *  calls and has no permission to read what those calls cost; this one is the only key type
 *  OpenAI allows to read organization-level cost/usage data. Verified live before saving. */
export async function setOpenAIAdminKey(apiKey: string): Promise<{ openai_admin_key_set: boolean }> {
  const { data } = await apiClient.post<{ openai_admin_key_set: boolean }>(
    "/system-settings/openai-admin-key",
    { api_key: apiKey },
  );
  return data;
}

export async function disconnectOpenAIAdminKey(): Promise<{ openai_admin_key_set: boolean }> {
  const { data } = await apiClient.delete<{ openai_admin_key_set: boolean }>(
    "/system-settings/openai-admin-key",
  );
  return data;
}

export interface OpenAICostDay {
  date: string;
  usd: number;
  input_tokens: number;
  output_tokens: number;
}

export interface OpenAICosts {
  connected: boolean;
  start: string;
  end: string;
  total_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  days: OpenAICostDay[];
}

/** The REAL dollar figure and real token counts, straight from OpenAI - not spend-analytics's
 *  character-based estimate. connected=false (never a thrown error) when no Admin key has
 *  been saved yet. */
export async function getOpenAICosts(start?: string, end?: string): Promise<OpenAICosts> {
  const { data } = await apiClient.get<OpenAICosts>("/system-settings/openai-costs", {
    params: { ...(start ? { start } : {}), ...(end ? { end } : {}) },
  });
  return data;
}

/** Manually entered - OpenAI has no API (not even the Admin key) that returns account
 *  balance or its expiry, so a Super Admin types it in after checking platform.openai.com.
 *  Pass balanceUsd=null to clear it back to "not set". */
export async function setOpenAIBalance(
  balanceUsd: number | null,
  expiryDate: string | null,
): Promise<{ openai_balance_usd: number | null; openai_balance_expiry: string | null }> {
  const { data } = await apiClient.post<{ openai_balance_usd: number | null; openai_balance_expiry: string | null }>(
    "/system-settings/openai-balance",
    { balance_usd: balanceUsd, expiry_date: expiryDate },
  );
  return data;
}

export interface UsdInrRate {
  rate: number;
  as_of: string;
}

/** Live 1 USD -> INR rate (European Central Bank's own daily reference rate, via Frankfurter -
 *  see app/core/currency.py). Used to show the real OpenAI spend and the manually-entered
 *  balance in INR alongside USD. */
export async function getUsdInrRate(): Promise<UsdInrRate> {
  const { data } = await apiClient.get<UsdInrRate>("/system-settings/usd-inr-rate");
  return data;
}
