import { apiClient } from "./client";

export interface EbCompany { id: string; name: string }
export interface EbTemplate { id: string; name: string; failed_count: number; job_count?: number }
export interface EbFailedJob { id: string; reference: string; status?: string; operator: string | null; updated_at: string | null }
export interface PlaybackSummary {
  steps_done: number;
  steps_total: number;
  stopped: boolean;
  reason?: string | null;
  failed_steps: string[];
  final_url?: string | null;
}

export interface PlaybackState {
  summary?: PlaybackSummary;
  screenshot: string | null;
  log: string[];
  done: boolean;
  status: string;
  result: { status?: string; reason?: string; error?: string } | null;
}

export async function ebCompanies(): Promise<EbCompany[]> {
  const { data } = await apiClient.get<EbCompany[]>("/entry-browser/companies");
  return data;
}

export async function ebTemplates(tenantId: string): Promise<EbTemplate[]> {
  const { data } = await apiClient.get<EbTemplate[]>(`/entry-browser/companies/${tenantId}/templates`);
  return data;
}

export async function ebFailedJobs(groupId: string): Promise<EbFailedJob[]> {
  const { data } = await apiClient.get<EbFailedJob[]>(`/entry-browser/templates/${groupId}/failed-jobs`);
  return data;
}

export async function ebRerunLive(jobId: string): Promise<{ session_id: string; erp_url: string }> {
  const { data } = await apiClient.post<{ session_id: string; erp_url: string }>(`/entry-browser/jobs/${jobId}/rerun-live`);
  return data;
}

/** Ask a running rerun to stop. It stops at the end of the step it is on. */
export async function ebPlaybackStop(sessionId: string, jobId?: string): Promise<{ stopping: boolean; note: string }> {
  const { data } = await apiClient.post<{ stopping: boolean; note: string }>(
    `/entry-browser/playback/${sessionId}/stop${jobId ? `?job_id=${jobId}` : ""}`,
  );
  return data;
}

export async function ebPlayback(sessionId: string): Promise<PlaybackState> {
  const { data } = await apiClient.get<PlaybackState>(`/entry-browser/playback/${sessionId}`);
  return data;
}

// --- step-by-step (manual) mode ---
export interface SteppedStart { session_id: string; erp_url: string; total: number; screenshot: string | null }
export interface StepResult {
  ok?: boolean; status?: string; note?: string; step_index?: number; idx: number; total: number;
  done: boolean; description?: string; value?: string; action?: string; screenshot: string | null;
  reason?: string; attempts?: number; verified?: boolean;
}

export async function ebSteppedStart(jobId: string): Promise<SteppedStart> {
  const { data } = await apiClient.post<SteppedStart>(`/entry-browser/jobs/${jobId}/stepped-start`);
  return data;
}
export async function ebSteppedNext(sid: string): Promise<StepResult> {
  const { data } = await apiClient.post<StepResult>(`/entry-browser/stepped/${sid}/next`);
  return data;
}
export interface StepElement {
  tag: string; selector: string; text?: string; input_type?: string;
  is_input?: boolean; is_select?: boolean; is_button?: boolean; label?: string; options?: string[] | null;
}

export async function ebSteppedInspect(sid: string, x: number, y: number): Promise<{ element: StepElement | null; screenshot: string | null }> {
  const { data } = await apiClient.post<{ element: StepElement | null; screenshot: string | null }>(`/entry-browser/stepped/${sid}/inspect`, { x, y });
  return data;
}

// Manual actions also return updated step counters (a manual entry counts as a step).
export interface ManualResult { screenshot: string | null; idx?: number; total?: number; done?: boolean; element?: StepElement | null }

export async function ebSteppedClick(sid: string, x: number, y: number): Promise<ManualResult> {
  const { data } = await apiClient.post<ManualResult>(`/entry-browser/stepped/${sid}/click`, { x, y });
  return data;
}

export async function ebSteppedSelect(sid: string, x: number, y: number, value: string): Promise<ManualResult> {
  const { data } = await apiClient.post<ManualResult>(`/entry-browser/stepped/${sid}/select`, { x, y, value });
  return data;
}
export async function ebSteppedType(sid: string, x: number, y: number, value: string): Promise<ManualResult> {
  const { data } = await apiClient.post<ManualResult>(`/entry-browser/stepped/${sid}/type`, { x, y, value });
  return data;
}
export async function ebSteppedStop(sid: string): Promise<void> {
  await apiClient.post(`/entry-browser/stepped/${sid}/stop`);
}
