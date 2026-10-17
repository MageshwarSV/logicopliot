import { apiClient } from "./client";
import type { User } from "../types/auth";

export async function login(email: string, password: string): Promise<User> {
  const response = await apiClient.post<{ user: User }>("/auth/login", { email, password });
  return response.data.user;
}

export async function logout(): Promise<void> {
  await apiClient.post("/auth/logout");
}

export async function fetchMe(): Promise<User> {
  const response = await apiClient.get<User>("/auth/me");
  return response.data;
}

/** Self-service only - the logged-in user changing their OWN password, whether completing the
 * forced first-login popup or a voluntary change from Settings. Throws (axios error) on a
 * wrong current password - the caller shows that inline rather than a generic failure. */
export async function changePassword(currentPassword: string, newPassword: string): Promise<User> {
  const response = await apiClient.post<User>("/auth/change-password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
  return response.data;
}
