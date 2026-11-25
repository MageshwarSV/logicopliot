import { apiClient } from "./client";

/** The things that happen to a job after the operator has moved on from it. */
export interface AppNotification {
  /** "<kind>:<job id>" - there is no notification row, so this is what identifies one. */
  id: string;
  /**
   * "password" / "password_reminder": a job waiting for a zip password (see HeldPasswordPanel).
   * "renumbered": the job was re-run and the ERP filed it as a NEW entry under a new job
   * number - the old one is never reused. job_reference is already the new number; `detail`
   * carries "<old> -> <new>", which is what the operator needs to find the stale entry in
   * SOFTLINK and delete it.
   */
  kind: "submitted" | "failed" | "password" | "password_reminder" | "renumbered";
  job_id: string;
  job_reference: string;
  /** Why it failed (kind "failed"), or "<old number> -> <new number>" (kind "renumbered"). */
  detail?: string | null;
  at?: string | null;
  read: boolean;
}

export const notificationsApi = {
  async list(): Promise<AppNotification[]> {
    const { data } = await apiClient.get<AppNotification[]>("/notifications");
    return data;
  },
  /** No ids = mark every notification currently on the bell as read. */
  async markRead(ids?: string[]): Promise<void> {
    await apiClient.post("/notifications/read", ids?.length ? { ids } : {});
  },
};
