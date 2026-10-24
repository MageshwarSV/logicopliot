import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./useAuth";
import type { Role } from "../types/auth";
import { ROLE_HOME } from "./roleHome";
import { ChangePasswordModal } from "../components/ChangePasswordModal";

interface ProtectedRouteProps {
  allowedRoles: Role[];
  children: ReactNode;
}

export function ProtectedRoute({ allowedRoles, children }: ProtectedRouteProps) {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex min-h-svh items-center justify-center bg-slate-50 dark:bg-slate-950">
        <svg className="h-6 w-6 animate-spin text-indigo-600" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      </div>
    );
  }

  if (!user) {
    // Where they were headed, so a successful sign-in can send them straight back instead of
    // always landing on the role's own default home page.
    const from = location.pathname + location.search;
    return <Navigate to="/login" replace state={{ from }} />;
  }

  if (!allowedRoles.includes(user.role)) {
    // Found live: logging in as a DIFFERENT account right after someone else's left a
    // location.state.from behind (see AuthContext/LoginPage) sent the new account straight to
    // a page it has no business on - a dead-end "Not authorized" page helps nobody here; this
    // account's own real home does. Not shown for every possible reason a role could be
    // blocked (a stale `from` is only one of them), but it is always the more useful landing
    // spot than an error screen regardless of why this was reached.
    return <Navigate to={ROLE_HOME[user.role]} replace />;
  }

  // Blocks every role/route uniformly, regardless of which dashboard it lands on, rather than
  // duplicating this check into each one - see User.must_change_password. The real page renders
  // underneath (inert behind the modal's backdrop) so completing it reveals exactly the page the
  // user was headed to, with no extra redirect.
  return (
    <>
      {children}
      {user.must_change_password && (
        <ChangePasswordModal open forced onClose={() => {}} />
      )}
    </>
  );
}
