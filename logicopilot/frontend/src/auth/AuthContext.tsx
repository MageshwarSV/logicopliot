import { createContext, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import * as authApi from "../api/auth";
import type { User } from "../types/auth";

interface AuthContextValue {
  user: User | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
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

  useEffect(() => {
    // Tokens live only in httpOnly cookies, invisible to JS — this is the only way
    // the frontend can rehydrate identity after a page reload.
    authApi
      .fetchMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setIsLoading(false));
  }, []);

  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel(AUTH_CHANNEL_NAME);
    channelRef.current = channel;
    channel.onmessage = (event: MessageEvent<AuthBroadcast>) => {
      const msg = event.data;
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

  const login = useCallback(async (email: string, password: string) => {
    const loggedInUser = await authApi.login(email, password);
    setUser(loggedInUser);
    channelRef.current?.postMessage({ type: "login", userId: loggedInUser.id } satisfies AuthBroadcast);
    return loggedInUser;
  }, []);

  const logout = useCallback(async () => {
    await authApi.logout();
    setUser(null);
    channelRef.current?.postMessage({ type: "logout" } satisfies AuthBroadcast);
  }, []);

  return <AuthContext.Provider value={{ user, isLoading, login, logout }}>{children}</AuthContext.Provider>;
}
