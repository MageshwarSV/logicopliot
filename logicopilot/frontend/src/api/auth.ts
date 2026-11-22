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

/** Self-service profile: the logged-in user's OWN name and phone (email and role stay admin-set). */
export async function updateProfile(changes: { full_name?: string; phone?: string }): Promise<User> {
  const response = await apiClient.patch<User>("/auth/me", changes);
  return response.data;
}

/** PNG, JPG or WEBP, 2 MB at most - the server checks the file's own bytes, not its name. */
export async function uploadAvatar(file: File): Promise<User> {
  const form = new FormData();
  form.append("file", file);
  const response = await apiClient.post<User>("/auth/me/avatar", form);
  return response.data;
}

export async function deleteAvatar(): Promise<User> {
  const response = await apiClient.delete<User>("/auth/me/avatar");
  return response.data;
}
