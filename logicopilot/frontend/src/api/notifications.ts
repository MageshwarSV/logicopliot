import { apiClient } from "./client";

/** The two things that happen to a job after the operator has moved on from it. */
export interface AppNotification {
  /** "<kind>:<job id>" - there is no notification row, so this is what identifies one. */
  id: string;
  kind: "submitted" | "failed";
  job_id: string;
  job_reference: string;
  /** Why it failed, for kind "failed". */
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
