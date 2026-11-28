// Talks to the Cargora backend with the Bearer token the server hands to apps
// (/auth/login with token_response=true). Tokens live in the phone's secure store.
// A 401 refreshes once (rotation: the new refresh token replaces the old) and retries.
import Constants from "expo-constants";
import * as SecureStore from "expo-secure-store";

const K_SERVER = "cargora.server";
const K_ACCESS = "cargora.access";
const K_REFRESH = "cargora.refresh";
const K_EMAIL = "cargora.email";
// "Remember me": the password, kept ONLY in the phone's secure storage (Android Keystore /
// iOS Keychain), so the app can sign in again by itself when the server's session ends. Cleared
// on Log out or when Remember me is unticked.
const K_PASS = "cargora.pass";

export const BUILD_LABEL: string =
  (Constants.expoConfig?.extra as { buildLabel?: string } | undefined)?.buildLabel ?? "dev";

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

export async function loadSession(): Promise<{ server: string; signedIn: boolean; email: string; remember: boolean }> {
  server = (await SecureStore.getItemAsync(K_SERVER)) || DEFAULT_SERVER;
  access = await SecureStore.getItemAsync(K_ACCESS);
  refresh = await SecureStore.getItemAsync(K_REFRESH);
  const email = (await SecureStore.getItemAsync(K_EMAIL)) || "";
  const remembered = !!(await SecureStore.getItemAsync(K_PASS));
  return { server, signedIn: !!refresh || remembered, email, remember: remembered || !email };
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

export async function login(serverUrl: string, email: string, password: string, remember = true): Promise<Me> {
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
  if (remember) await SecureStore.setItemAsync(K_PASS, password);
  else await SecureStore.deleteItemAsync(K_PASS);
  await saveTokens(body.tokens.access_token, body.tokens.refresh_token);
  return user;
}

/** The server's session ended (it lasts while the app is used): sign in again with the
 *  remembered email + password, if Remember me was on. */
async function reLogin(): Promise<boolean> {
  const email = await SecureStore.getItemAsync(K_EMAIL);
  const pass = await SecureStore.getItemAsync(K_PASS);
  if (!email || !pass) return false;
  try {
    await login(server, email, pass, true);
    return true;
  } catch (e) {
    // Wrong password now (changed on the website) - forget it and ask.
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) await SecureStore.deleteItemAsync(K_PASS);
    return false;
  }
}

let refreshing: Promise<boolean> | null = null;

async function doRefresh(): Promise<boolean> {
  if (!refresh) return reLogin();
  try {
    const res = await fetch(`${server}/api/v1/auth/refresh`, {
      method: "POST",
      headers: BASE_HEADERS,
      body: JSON.stringify({ refresh_token: refresh, token_response: true }),
    });
    if (!res.ok) return reLogin();
    const body = await res.json();
    await saveTokens(body.tokens.access_token, body.tokens.refresh_token);
    return true;
  } catch {
    return false;          // no network - keep the session, try again later
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

/** Called once at start: no session but Remember me is on - sign in by itself. */
export async function ensureSession(): Promise<boolean> {
  if (refresh) return true;
  return reLogin();
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
  await SecureStore.deleteItemAsync(K_PASS);
}
