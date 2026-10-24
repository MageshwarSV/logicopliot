import type { Role } from "../types/auth";

// Operator lands straight on New Jobs (not the Dashboard), pre-filtered to their own work from
// the last 7 days - the dashboard and every other page stay one click away, this just changes
// what the FIRST screen after signing in shows. assignedTo=me is resolved to the operator's own
// full_name once the user profile loads (see JobsPage.tsx) - their own display name isn't known
// yet at this exact moment, only their role is.
//
// Shared between LoginPage.tsx (post-login redirect) and ProtectedRoute.tsx (bouncing a user
// off a page their OWN role cannot see) so there is only ever one place deciding where each
// role actually belongs.
export const ROLE_HOME: Record<Role, string> = {
  super_admin: "/super-admin",
  tenant_admin: "/tenant-admin",
  operator: "/jobs?category=new&quick=7&assignedTo=me",
  admin: "/jobs",
  gk2: "/jobs",
  manager: "/manager",
};
