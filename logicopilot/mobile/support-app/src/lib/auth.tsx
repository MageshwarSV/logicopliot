import { ReactNode, createContext, useCallback, useContext, useEffect, useState } from "react";

import * as Api from "./api";
import { registerForPush } from "./push";

type AuthState = {
  ready: boolean;
  signedIn: boolean;
  email: string;
  server: string;
  pushProblem: string | null;
  signIn: (server: string, email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [signedIn, setSignedIn] = useState(false);
  const [email, setEmail] = useState("");
  const [server, setServer] = useState(Api.DEFAULT_SERVER);
  const [pushToken, setPushToken] = useState<string | null>(null);
  const [pushProblem, setPushProblem] = useState<string | null>(null);

  const startPush = useCallback(async () => {
    const r = await registerForPush();
    setPushToken(r.token);
    setPushProblem(r.problem);
  }, []);

  useEffect(() => {
    Api.setSignedOutHandler(() => setSignedIn(false));
    Api.loadSession().then((s) => {
      setServer(s.server);
      setEmail(s.email);
      setSignedIn(s.signedIn);
      setReady(true);
      if (s.signedIn) startPush();
    });
    return () => Api.setSignedOutHandler(null);
  }, [startPush]);

  const signIn = useCallback(async (srv: string, em: string, pw: string) => {
    await Api.login(srv, em, pw);
    setServer(Api.getServer());
    setEmail(em.trim());
    setSignedIn(true);
    startPush();
  }, [startPush]);

  const signOut = useCallback(async () => {
    await Api.logout(pushToken);
    setPushToken(null);
    setSignedIn(false);
  }, [pushToken]);

  return (
    <Ctx.Provider value={{ ready, signedIn, email, server, pushProblem, signIn, signOut }}>{children}</Ctx.Provider>
  );
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
