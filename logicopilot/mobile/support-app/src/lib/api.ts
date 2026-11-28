// Talks to the Cargora backend with the Bearer token the server hands to apps
// (/auth/login with token_response=true). Tokens live in the phone's secure store.
// A 401 refreshes once (rotation: the new refresh token replaces the old) and retries.
import Constants from "expo-constants";
import * as SecureStore from "expo-secure-store";

const K_SERVER = "cargora.server";
const K_ACCESS = "cargora.access";
const K_REFRESH = "cargora.refresh";
const K_EMAIL = "cargora.email";

export const DEFAULT_SERVER: string =
  (Constants.expoConfig?.extra as { defaultServer?: string } | undefined)?.defaultServer ??
  "https://cargora.axioralabs.ai";

// The backend refuses a POST/PUT/DELETE without this header (its CSRF check - app/main.py),
// exactly as the website's own client sends it.
const BASE_HEADERS = { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" };

let server = DEFAULT_SERVER;
let access: string | null = null;
let refresh: string | null = null;
let onSignedOut: (() => void) | null = null;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function setSignedOutHandler(fn: (() => void) | null) {
  onSignedOut = fn;
}

export function getServer() {
  return server;
}

export async function loadSession(): Promise<{ server: string; signedIn: boolean; email: string }> {
  server = (await SecureStore.getItemAsync(K_SERVER)) || DEFAULT_SERVER;
  access = await SecureStore.getItemAsync(K_ACCESS);
  refresh = await SecureStore.getItemAsync(K_REFRESH);
  const email = (await SecureStore.getItemAsync(K_EMAIL)) || "";
  return { server, signedIn: !!refresh, email };
}

async function saveTokens(a: string | null, r: string | null) {
  access = a;
  refresh = r;
  if (a) await SecureStore.setItemAsync(K_ACCESS, a);
  else await SecureStore.deleteItemAsync(K_ACCESS);
  if (r) await SecureStore.setItemAsync(K_REFRESH, r);
  else await SecureStore.deleteItemAsync(K_REFRESH);
}

function cleanServer(s: string) {
  let v = s.trim().replace(/\/+$/, "");
  if (v && !/^https?:\/\//i.test(v)) v = `https://${v}`;
  return v || DEFAULT_SERVER;
}

async function detail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail)) return body.detail.map((d: { msg?: string }) => d.msg).join("; ");
  } catch {
    // not JSON
  }
  return `Request failed (${res.status})`;
}

export type Me = { id: string; email: string; full_name: string | null; role: string };

export async function login(serverUrl: string, email: string, password: string): Promise<Me> {
  const s = cleanServer(serverUrl);
  const res = await fetch(`${s}/api/v1/auth/login`, {
    method: "POST",
    headers: BASE_HEADERS,
    body: JSON.stringify({ email: email.trim(), password, token_response: true }),
  });
  if (!res.ok) throw new ApiError(res.status, await detail(res));
  const body = await res.json();
  const user: Me = body.user;
  if (user.role !== "support" && user.role !== "super_admin") {
    throw new ApiError(403, "This app is for the Production Support login only.");
  }
  server = s;
  await SecureStore.setItemAsync(K_SERVER, s);
  await SecureStore.setItemAsync(K_EMAIL, email.trim());
  await saveTokens(body.tokens.access_token, body.tokens.refresh_token);
  return user;
}

let refreshing: Promise<boolean> | null = null;

async function doRefresh(): Promise<boolean> {
  if (!refresh) return false;
  try {
    const res = await fetch(`${server}/api/v1/auth/refresh`, {
      method: "POST",
      headers: BASE_HEADERS,
      body: JSON.stringify({ refresh_token: refresh, token_response: true }),
    });
    if (!res.ok) return false;
    const body = await res.json();
    await saveTokens(body.tokens.access_token, body.tokens.refresh_token);
    return true;
  } catch {
    return false;
  }
}

function refreshOnce(): Promise<boolean> {
  if (!refreshing) refreshing = doRefresh().finally(() => (refreshing = null));
  return refreshing;
}

export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const call = () =>
    fetch(`${server}/api/v1${path}`, {
      method: init.method ?? "GET",
      headers: {
        ...BASE_HEADERS,
        ...(access ? { Authorization: `Bearer ${access}` } : {}),
      },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
    });
  let res = await call();
  if (res.status === 401 && (await refreshOnce())) res = await call();
  if (res.status === 401) {
    await saveTokens(null, null);
    onSignedOut?.();
    throw new ApiError(401, "Signed out - please log in again.");
  }
  if (!res.ok) throw new ApiError(res.status, await detail(res));
  return (await res.json()) as T;
}

export async function logout(pushToken: string | null) {
  try {
    if (pushToken) await api("/support/app/devices/remove", { method: "POST", body: { token: pushToken } });
    if (refresh) {
      await fetch(`${server}/api/v1/auth/logout`, {
        method: "POST",
        headers: BASE_HEADERS,
        body: JSON.stringify({ refresh_token: refresh }),
      });
    }
  } catch {
    // signing out locally is what matters
  }
  await saveTokens(null, null);
}
