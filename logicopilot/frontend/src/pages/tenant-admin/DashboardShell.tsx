import { useEffect, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { StatCard } from "../../components/ui/Card";
import * as usersApi from "../../api/users";
import type { User } from "../../types/auth";

/** Tenant Admin's actual landing page — before this, "Dashboard" and "Masters" were the
 *  same screen, so there was nowhere that just said how many of each user type you have. */
export function TenantAdminDashboard() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    usersApi
      .listUsers()
      .then(setUsers)
      .catch(() => setUsers([]))
      .finally(() => setLoading(false));
  }, []);

  const gk1 = users.filter((u) => u.role === "operator").length;
  const gk2 = users.filter((u) => u.role === "gk2").length;
  const manager = users.filter((u) => u.role === "manager").length;

  return (
    <AppShell title="Dashboard" subtitle="Your organization's users, at a glance.">
      {!loading && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Total Users" value={gk1 + gk2 + manager} />
          <StatCard label="Gate Keeper 1" value={gk1} />
          <StatCard label="Gate Keeper 2" value={gk2} />
          <StatCard label="Manager" value={manager} />
        </div>
      )}
    </AppShell>
  );
}
