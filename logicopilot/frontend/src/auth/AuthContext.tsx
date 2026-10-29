import { createContext, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import * as authApi from "../api/auth";
import type { User } from "../types/auth";
import { resetSessionExpiredNotice, setSessionExpiredHandler, suppressSessionExpiredNotice } from "./sessionExpiry";

interface AuthContextValue {
  user: User | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  /** Updates the cached user in place - for a change that doesn't need a full re-login, e.g.
   * clearing must_change_password once the forced/self-service change-password form succeeds. */
  setUser: (user: User) => void;
}

export const AuthContext = createContext<AuthContextValue | undefined>(undefined);

// Every tab of the same browser shares the same httpOnly auth cookie. Logging out and back in
// as someone else in one tab silently swaps that cookie out from under every other tab still
// open on the old session — their next request just starts running as the new identity instead
// of failing, which is what made a stale tab visibly break (partial data, 403s on admin-only
// calls) instead of cleanly dropping back to the login page. This channel tells every other
// tab the instant that happens, so they drop their own session before that can happen.
type AuthBroadcast = { type: "login"; userId: string } | { type: "logout" };
const AUTH_CHANNEL_NAME = "logicopilot-auth";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const channelRef = useRef<BroadcastChannel | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    // Tokens live only in httpOnly cookies, invisible to JS — this is the only way
    // the frontend can rehydrate identity after a page reload.
    //
    // Suppressed FIRST, for the duration of this one probe. Whether a session exists is a
    // question only the server can answer, so simply not being logged in makes this 401 -
    // and the interceptor then tries /auth/refresh, which also 401s, which reads as a
    // genuine expiry and threw up "Your session has expired" on EVERY load of the login
    // page by someone who was merely signed out. The refresh attempt itself must stay (that
    // is what silently restores a session whose access token died while the tab was closed);
    // only the announcement is wrong here. A real expiry still announces itself through the
    // in-app 401 path, which is the only place it actually tells the user anything they did
    // not already know from being sat on the login screen.
    suppressSessionExpiredNotice();
    authApi
      .fetchMe()
      .then((me) => {
        setUser(me);
        // There IS a live session, so a later failure is a real expiry worth announcing.
        resetSessionExpiredNotice();
      })
      .catch(() => setUser(null))
      .finally(() => setIsLoading(false));
  }, []);

  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel(AUTH_CHANNEL_NAME);
    channelRef.current = channel;
    channel.onmessage = (event: MessageEvent<AuthBroadcast>) => {
      const msg = event.data;
      // This tab's own cookie just died too (it's shared across every tab) - its own in-flight
      // request can race into the same false "expired" read logout() already guards against
      // locally (see sessionExpiry.ts).
      if (msg.type === "logout") suppressSessionExpiredNotice();
      setUser((current) => {
        if (!current) return current;
        if (msg.type === "logout") return null;
        // A different identity logged in elsewhere — this tab's cookie now belongs to them,
        // not the user this tab thinks is signed in. Drop this tab's session rather than let
        // it keep rendering under an identity it no longer holds.
        if (msg.type === "login" && msg.userId !== current.id) return null;
        return current;
      });
    };
    return () => channel.close();
  }, []);

  // The session genuinely expired (apiClient's refresh call itself failed, see
  // api/client.ts) - not a normal "never logged in" visit to a protected URL, which
  // ProtectedRoute already handles on its own. Drops this tab's session, tells every other
  // tab to do the same (same broadcast logout already uses), and sends the user back to
  // Login with where they were, so a successful sign-in can return them there.
  useEffect(() => {
    setSessionExpiredHandler(() => {
      setUser(null);
      channelRef.current?.postMessage({ type: "logout" } satisfies AuthBroadcast);
      const from = window.location.pathname + window.location.search;
      navigate("/login", { replace: true, state: { from, expired: true } });
    });
    return () => setSessionExpiredHandler(null);
  }, [navigate]);

  const login = useCallback(async (email: string, password: string) => {
    const loggedInUser = await authApi.login(email, password);
    setUser(loggedInUser);
    channelRef.current?.postMessage({ type: "login", userId: loggedInUser.id } satisfies AuthBroadcast);
    // A fresh, successful sign-in means any later failure is a NEW expiry, not an echo of
    // this one - let it notify again next time.
    resetSessionExpiredNotice();
    return loggedInUser;
  }, []);

  const logout = useCallback(async () => {
    // Before the cookie is even cleared - a request already in flight at this exact moment
    // can still land a 401 a moment later and read as a surprise expiry otherwise (see
    // sessionExpiry.ts's own docstring on this).
    suppressSessionExpiredNotice();
    await authApi.logout();
    setUser(null);
    channelRef.current?.postMessage({ type: "logout" } satisfies AuthBroadcast);
  }, []);

  return (
    <AuthContext.Provider value={{ user, isLoading, login, logout, setUser }}>{children}</AuthContext.Provider>
  );
}
