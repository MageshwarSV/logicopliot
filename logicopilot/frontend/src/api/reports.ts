import { apiClient } from "./client";

/** Reports -> User Job Summary (backend: GET /jobs/user-summary in api/v1/jobs.py). */
export interface UserJobSummaryRow {
  user_id: string | null;
  name: string;
  avatar_url: string | null;
  total: number;
  completed: number;
  pending: number;
}

export interface UserJobSummary {
  modes: string[];
  mode: string | null;
  date_from: string;
  date_to: string;
  users: UserJobSummaryRow[];
  totals: { total: number; completed: number; pending: number };
}

export interface UserJobSummaryParams {
  mode?: string;
  date_from: string;
  date_to: string;
}

export async function getUserJobSummary(params: UserJobSummaryParams): Promise<UserJobSummary> {
  const { data } = await apiClient.get<UserJobSummary>("/jobs/user-summary", { params });
  return data;
}

export async function downloadUserJobSummary(params: UserJobSummaryParams): Promise<void> {
  const { data, headers } = await apiClient.get<Blob>("/jobs/user-summary/export", { params, responseType: "blob" });
  const match = /filename="?([^"]+)"?/.exec(String(headers["content-disposition"] ?? ""));
  const url = URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url;
  a.download = match?.[1] ?? "User_Job_Summary.xlsx";
  a.click();
  URL.revokeObjectURL(url);
}
