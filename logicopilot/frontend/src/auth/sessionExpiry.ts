/**
 * Bridges apiClient's response interceptor (outside React, see api/client.ts) to AuthProvider
 * (inside the Router, so it can actually navigate) - when the refresh-token call itself fails,
 * the session is genuinely gone, not just a single request's bad luck.
 *
 * `notified` guards against several requests failing from the same dead session at once: only
 * the FIRST one actually logs out/redirects, instead of firing the alert and the redirect once
 * per failed request. Cleared on the next successful login, so a FUTURE expiry can fire again.
 */
type Listener = () => void;

let listener: Listener | null = null;
let notified = false;

export function setSessionExpiredHandler(handler: Listener | null): void {
  listener = handler;
}

export function notifySessionExpired(): void {
  if (notified) return;
  notified = true;
  listener?.();
}

export function resetSessionExpiredNotice(): void {
  notified = false;
}

/**
 * Found live: logging out deliberately clears the auth cookie right away, but a background
 * request already in flight at that exact moment (AppShell's own 30s badge-count poll, for
 * one) can still land a 401 a moment later - its own refresh attempt then fails too (there is
 * nothing left to refresh), which used to read as a genuine session expiry and threw up "Your
 * session has expired" immediately after a perfectly ordinary, intentional logout. Call this
 * the instant a deliberate logout starts, so any such stray failure is swallowed exactly like
 * an already-notified one - cleared again by the next successful login (resetSessionExpiredNotice).
 */
export function suppressSessionExpiredNotice(): void {
  notified = true;
}
