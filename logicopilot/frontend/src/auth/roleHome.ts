import type { Role } from "../types/auth";

// Every role with its own Dashboard page lands there after signing in - confirmed by request
// (2026-11-09), reversing operator's own earlier "straight to New Jobs" choice below. admin
// and gk2 have no dashboard page of their own (only the shared Jobs list and friends), so
// /jobs is still the only sensible landing page for them, unchanged.
//
// Shared between LoginPage.tsx (post-login redirect) and ProtectedRoute.tsx (bouncing a user
// off a page their OWN role cannot see) so there is only ever one place deciding where each
// role actually belongs.
export const ROLE_HOME: Record<Role, string> = {
  super_admin: "/super-admin",
  tenant_admin: "/tenant-admin",
  operator: "/operator",
  admin: "/jobs",
  gk2: "/jobs",
  manager: "/manager",
  saas_finance: "/saas-finance",
  // An app login has no home HERE - it exists for the Cargora Manager mobile app and
  // reaches no page in this portal (every route 403s for it server-side). LoginPage
  // turns it away with an explanation before this is ever used; it is listed only
  // because Record<Role, string> demands every role, and sending it back to the sign-in
  // screen is the one harmless answer.
  app_manager: "/login",
};
