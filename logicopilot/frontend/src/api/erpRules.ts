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

export interface RuleTemplate { id: string; name: string; tenant: string; mode: string | null; rules_saved: number }

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

export async function getColumns(groupId: string): Promise<{ id: string; name: string; columns: RuleColumn[]; masters: Record<string, string> }> {
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
