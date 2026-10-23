import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./useAuth";
import type { Role } from "../types/auth";
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
    return <Navigate to="/not-authorized" replace />;
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
