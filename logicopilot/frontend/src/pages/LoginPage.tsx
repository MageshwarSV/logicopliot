import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";
import axios from "axios";
import { useAuth } from "../auth/useAuth";
import type { Role } from "../types/auth";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { Alert } from "../components/ui/Alert";

// The only thing ever remembered is the email address itself - never the password, and only
// when the user explicitly ticks the box. Wrapped in try/catch everywhere it's touched, same
// convention as sessionStorage elsewhere (JobsPage.tsx) - a private window or blocked storage
// must never break the form, just silently not remember anything.
const REMEMBERED_EMAIL_KEY = "cargora-remembered-email";

function readRememberedEmail(): string {
  try {
    return window.localStorage.getItem(REMEMBERED_EMAIL_KEY) ?? "";
  } catch {
    return "";
  }
}

// Operator lands straight on New Jobs (not the Dashboard), pre-filtered to their own work from
// the last 7 days - the dashboard and every other page stay one click away, this just changes
// what the FIRST screen after signing in shows. assignedTo=me is resolved to the operator's own
// full_name once the user profile loads (see JobsPage.tsx) - their own display name isn't known
// yet at this exact moment, only their role is.
const ROLE_HOME: Record<Role, string> = {
  super_admin: "/super-admin",
  tenant_admin: "/tenant-admin",
  operator: "/jobs?category=new&quick=7&assignedTo=me",
  admin: "/jobs",
  gk2: "/jobs",
  manager: "/manager",
};

const FEATURES = [
  { title: "AI document processing", body: "Extract shipment data automatically." },
  { title: "Automated clearance workflow", body: "Reduce repetitive manual operations." },
  { title: "Faster customs submission", body: "Improve speed, accuracy, and operational productivity." },
];

export function LoginPage() {
  const { user, login } = useAuth();
  const location = useLocation();
  const rememberedEmail = readRememberedEmail();
  const [email, setEmail] = useState(rememberedEmail);
  const [password, setPassword] = useState("");
  // Unchecked unless a previously-remembered email exists - never checked by default.
  const [rememberMe, setRememberMe] = useState(rememberedEmail !== "");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const sessionExpired = (location.state as { expired?: boolean } | null)?.expired === true;

  if (user) {
    const redirectTo = (location.state as { from?: string } | null)?.from ?? ROLE_HOME[user.role];
    return <Navigate to={redirectTo} replace />;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await login(email, password);
      // Only the email is ever persisted, and only on an explicit opt-in - never the
      // password, never saved automatically.
      try {
        if (rememberMe) window.localStorage.setItem(REMEMBERED_EMAIL_KEY, email);
        else window.localStorage.removeItem(REMEMBERED_EMAIL_KEY);
      } catch {
        // Private window or blocked storage - the sign-in itself already succeeded, so this
        // is a convenience that just silently doesn't persist, never a reason to fail here.
      }
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 401) {
        setError(err.response.data?.detail ?? "Invalid email or password");
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-svh">
      {/* Branding panel */}
      <div className="relative hidden w-1/2 flex-col justify-between overflow-hidden bg-gradient-to-br from-indigo-700 via-indigo-800 to-violet-950 p-12 text-white lg:flex">
        <div
          className="pointer-events-none absolute inset-0 opacity-20"
          style={{
            backgroundImage:
              "radial-gradient(circle at 20% 20%, white 0, transparent 40%), radial-gradient(circle at 80% 70%, white 0, transparent 35%)",
          }}
        />
        <div className="relative flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-white/15 text-sm font-bold backdrop-blur-sm">C</div>
          <span className="text-lg font-semibold tracking-tight">Cargora</span>
          <span className="text-sm font-normal text-indigo-200/70">for Customs Clearing</span>
        </div>

        <div className="relative">
          <h1 className="max-w-md text-4xl font-semibold leading-tight tracking-tight">
            Stop Doing Clearance Operations Manually.
          </h1>
          <p className="mt-4 max-w-md text-indigo-100/80">
            With Cargora, every shipment is AI-assisted — from document collection to final
            submission.
          </p>

          <div className="mt-10 flex flex-col gap-5">
            {FEATURES.map((f) => (
              <div key={f.title} className="flex gap-3">
                <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/15">
                  <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <div>
                  <p className="font-medium">{f.title}</p>
                  <p className="text-sm text-indigo-100/70">{f.body}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <p className="relative text-sm text-indigo-100/50">© {new Date().getFullYear() || "2026"} Cargora</p>
      </div>

      {/* Login form */}
      <div className="flex w-full flex-1 items-center justify-center bg-slate-50 p-6 dark:bg-slate-950 lg:w-1/2">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 text-sm font-bold text-white">
              C
            </div>
            <span className="text-lg font-semibold tracking-tight text-slate-900 dark:text-slate-50">Cargora</span>
          </div>

          <h2 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-50">Welcome back</h2>
          <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
            Sign in with the account issued to you by your administrator.
          </p>

          <form className="mt-8 flex flex-col gap-4" onSubmit={handleSubmit}>
            <Input
              label="Email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              placeholder="you@company.com"
              required
            />
            <Input
              label="Password"
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              placeholder="••••••••"
              required
              trailingAdornment={
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  tabIndex={-1}
                  title={showPassword ? "Hide password" : "Show password"}
                  className="text-base leading-none text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300"
                >
                  {showPassword ? "🙈" : "👁️"}
                </button>
              }
            />
            <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
              <input
                type="checkbox"
                checked={rememberMe}
                onChange={(e) => setRememberMe(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-2 focus:ring-indigo-500/40 dark:border-slate-600 dark:bg-slate-800"
              />
              Remember me
            </label>
            {sessionExpired && (
              <Alert>Your session has expired. Please log in again to continue.</Alert>
            )}
            {error && <Alert>{error}</Alert>}
            <Button type="submit" isLoading={isSubmitting} className="mt-2 w-full">
              {isSubmitting ? "Signing in..." : "Sign in"}
            </Button>
          </form>

          <p className="mt-8 text-center text-xs text-slate-400 dark:text-slate-500">
            Don't have an account? Ask your Super Admin or Tenant Admin to create one for you.
          </p>
        </div>
      </div>
    </div>
  );
}
