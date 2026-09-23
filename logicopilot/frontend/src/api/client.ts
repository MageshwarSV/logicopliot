import axios, { type AxiosError, type InternalAxiosRequestConfig } from "axios";

const baseURL = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export const apiClient = axios.create({
  baseURL,
  withCredentials: true,
  headers: {
    // Cheap CSRF mitigation alongside SameSite=Lax cookies: a cross-site form POST
    // cannot set custom headers, so the backend can require this on mutating requests.
    "X-Requested-With": "XMLHttpRequest",
  },
});

interface RetryableConfig extends InternalAxiosRequestConfig {
  _retry?: boolean;
}

let refreshPromise: Promise<void> | null = null;

function isAuthEndpoint(url: string | undefined, path: string): boolean {
  return Boolean(url && url.includes(path));
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as RetryableConfig | undefined;

    if (
      error.response?.status !== 401 ||
      !originalRequest ||
      originalRequest._retry ||
      isAuthEndpoint(originalRequest.url, "/auth/login") ||
      isAuthEndpoint(originalRequest.url, "/auth/refresh")
    ) {
      return Promise.reject(error);
    }

    originalRequest._retry = true;

    try {
      if (!refreshPromise) {
        refreshPromise = apiClient.post("/auth/refresh").then(
          () => undefined,
          (refreshError) => {
            throw refreshError;
          },
        );
      }
      await refreshPromise;
      return apiClient(originalRequest);
    } catch (refreshError) {
      return Promise.reject(refreshError);
    } finally {
      refreshPromise = null;
    }
  },
);

export default apiClient;
