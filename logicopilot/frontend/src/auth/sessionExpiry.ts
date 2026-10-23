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
