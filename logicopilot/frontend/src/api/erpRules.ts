import { apiClient } from "./client";

/** ERP Column Rules (backend: api/v1/erp_rules.py). */
export interface ErpRule {
  required?: boolean;
  length?: number;
  length_mode?: "exact" | "max";
  master?: "country" | "currency" | "unit";
  number?: boolean;
  pattern?: string;
  message?: string;
  uppercase?: boolean;
  off?: boolean;
}

export interface RuleTemplate { id: string; name: string; tenant: string; mode: string | null; rules_saved: number; suggestions: number }

export interface RuleColumn {
  key: string;
  sheet: string;
  header: string;
  field: string;
  fixed_value: string | null;
  builtin: (ErpRule & { builtin: true }) | null;
  builtin_text: string | null;
  rule: ErpRule | null;
  active_text: string | null;
}

export interface RuleCheckItem { display: string; where: string; sent: string[]; needs: string; fixable: boolean }

export async function listTemplates(): Promise<RuleTemplate[]> {
  const { data } = await apiClient.get<RuleTemplate[]>("/erp-rules/templates");
  return data;
}

export async function getColumns(groupId: string): Promise<{ id: string; name: string; columns: RuleColumn[]; suggestions: RuleSuggestion[]; masters: Record<string, string> }> {
  const { data } = await apiClient.get(`/erp-rules/${groupId}`);
  return data;
}

export async function saveRules(groupId: string, rules: Record<string, ErpRule>): Promise<{ saved: number }> {
  const { data } = await apiClient.put(`/erp-rules/${groupId}`, { rules });
  return data;
}

export async function copyFrom(groupId: string, otherId: string): Promise<{ copied: number; skipped: number }> {
  const { data } = await apiClient.post(`/erp-rules/${groupId}/copy-from/${otherId}`);
  return data;
}

export async function checkJob(groupId: string, reference: string): Promise<{ reference: string; items: RuleCheckItem[] }> {
  const { data } = await apiClient.get(`/erp-rules/${groupId}/check`, { params: { reference } });
  return data;
}

export interface RuleSuggestion {
  key: string; sheet: string; header: string; field: string;
  rule: ErpRule & { needs_list?: boolean };
  rule_text: string; erp_text: string | null; count: number; last_job: string | null; last_at: string | null;
}

export async function acceptSuggestion(groupId: string, key: string): Promise<void> {
  await apiClient.post(`/erp-rules/${groupId}/suggestions/accept`, { key });
}

export async function dismissSuggestion(groupId: string, key: string): Promise<void> {
  await apiClient.post(`/erp-rules/${groupId}/suggestions/dismiss`, { key });
}

/** P4 - what the ERP would refuse in the job's current values (GET /jobs/{id}/erp-check). */
export async function getJobErpCheck(jobId: string): Promise<{ checked: boolean; items: import("../types/jobs").ErpHelpItem[]; excel_edited: boolean }> {
  const { data } = await apiClient.get(`/jobs/${jobId}/erp-check`);
  return data;
}

/** The country / currency list for a code box's search dropdown - fetched once per list. */
export interface MasterEntry { code: string; name: string }
const masterCache: Record<string, Promise<MasterEntry[]>> = {};
export function getMasterList(kind: "country" | "currency"): Promise<MasterEntry[]> {
  if (!masterCache[kind]) {
    masterCache[kind] = apiClient.get<MasterEntry[]>(`/erp-rules/masters/${kind}`).then((r) => r.data)
      .catch((err) => { delete masterCache[kind]; throw err; });
  }
  return masterCache[kind];
}
