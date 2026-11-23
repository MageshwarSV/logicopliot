import apiClient from "./client";

/** What the operator chose or typed on the E-Docket page - saved on the job. */
export interface EDocketSaved {
  state: Record<string, unknown> | null;
  saved_at: string | null;
  saved_by: string | null;
}

export async function getEDocket(jobId: string): Promise<EDocketSaved> {
  const { data } = await apiClient.get<EDocketSaved>(`/jobs/${jobId}/edocket`);
  return data;
}

export async function saveEDocket(jobId: string, state: Record<string, unknown>): Promise<EDocketSaved> {
  const { data } = await apiClient.put<EDocketSaved>(`/jobs/${jobId}/edocket`, { state });
  return data;
}
