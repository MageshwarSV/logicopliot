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
