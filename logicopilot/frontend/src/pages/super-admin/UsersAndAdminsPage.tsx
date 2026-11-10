import { useEffect, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { DataTable, type Column } from "../../components/ui/DataTable";
import { RoleBadge, StatusBadge } from "../../components/ui/Badge";
import { CreateTenantAdminModal } from "./CreateTenantAdminModal";
import { CreateFinanceUserModal } from "./CreateFinanceUserModal";
import * as tenantsApi from "../../api/tenants";
import * as usersApi from "../../api/users";
import type { Tenant } from "../../types/tenant";
import type { User } from "../../types/auth";

/** The "Users & Tenant Admins" screen, split out from the combined Dashboard page
 *  (2026-11-10, by explicit request, matching a reference sidebar design) - same data and the
 *  same user-management actions the Dashboard's own Tenant Admins/Finance Users tables already
 *  had, just on its own sidebar link. Purely a frontend split: no backend change, nothing here
 *  that the Dashboard page didn't already do. */
export function UsersAndAdminsPage() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adminModalOpen, setAdminModalOpen] = useState(false);
  const [editAdmin, setEditAdmin] = useState<User | null>(null);
  const [financeModalOpen, setFinanceModalOpen] = useState(false);
  const [editFinance, setEditFinance] = useState<User | null>(null);

  async function refresh() {
    setError(null);
    try {
      const [tenantList, userList] = await Promise.all([tenantsApi.listTenants(), usersApi.listUsers()]);
      setTenants(tenantList);
      setUsers(userList);
    } catch {
      setError("Failed to load data. Try refreshing the page.");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const tenantAdmins = users.filter((u) => u.role === "tenant_admin");
  const financeUsers = users.filter((u) => u.role === "saas_finance");
  const tenantNameById = new Map(tenants.map((t) => [t.id, t.name]));

  async function handleToggleActive(user: User) {
    try {
      await usersApi.setUserActive(user.id, !user.is_active);
      refresh();
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.detail ?? "Could not update that account.");
      }
    }
  }

  const adminColumns: Column<User>[] = [
    { header: "Name", render: (u) => <span className="font-medium text-slate-900 dark:text-slate-100">{u.full_name}</span> },
    { header: "Email", render: (u) => u.email },
    { header: "Tenant", render: (u) => (u.tenant_id ? tenantNameById.get(u.tenant_id) ?? "—" : "—") },
    { header: "Role", render: (u) => <RoleBadge role={u.role} /> },
    { header: "Status", render: (u) => <StatusBadge isActive={u.is_active} /> },
    {
      header: "",
      className: "text-right",
      render: (u) => (
        <div className="flex justify-end gap-1">
          <Button variant="ghost" size="sm" onClick={() => { setEditAdmin(u); setAdminModalOpen(true); }}>
            Edit
          </Button>
          <Button variant="ghost" size="sm" onClick={() => handleToggleActive(u)}>
            {u.is_active ? "Disable" : "Enable"}
          </Button>
        </div>
      ),
    },
  ];

  const financeColumns: Column<User>[] = [
    { header: "Name", render: (u) => <span className="font-medium text-slate-900 dark:text-slate-100">{u.full_name}</span> },
    { header: "Email", render: (u) => u.email },
    { header: "Role", render: (u) => <RoleBadge role={u.role} /> },
    { header: "Status", render: (u) => <StatusBadge isActive={u.is_active} /> },
    {
      header: "",
      className: "text-right",
      render: (u) => (
        <div className="flex justify-end gap-1">
          <Button variant="ghost" size="sm" onClick={() => { setEditFinance(u); setFinanceModalOpen(true); }}>
            Edit
          </Button>
          <Button variant="ghost" size="sm" onClick={() => handleToggleActive(u)}>
            {u.is_active ? "Disable" : "Enable"}
          </Button>
        </div>
      ),
    },
  ];

  return (
    <AppShell title="Users & Tenant Admins" subtitle="Everyone with cross-tenant or tenant-admin access on this platform.">
      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}

      <Card className="mb-8">
        <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">Tenant Admins</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Created from within a tenant's own "Users" view, not from here — open that tenant
            on the Tenants page and use "+ New Tenant Admin" there.
          </p>
        </div>
        {!isLoading && (
          <DataTable
            columns={adminColumns}
            rows={tenantAdmins}
            keyFor={(u) => u.id}
            emptyMessage="No tenant admins yet — create a tenant first, then issue its admin a login here."
          />
        )}
      </Card>

      <Card>
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <div>
            <h2 className="font-semibold text-slate-900 dark:text-slate-50">Finance Users</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              Cross-tenant, read-only access to usage/billing/invoices for the finance team.
            </p>
          </div>
          <Button size="sm" onClick={() => setFinanceModalOpen(true)}>
            + New Finance User
          </Button>
        </div>
        {!isLoading && (
          <DataTable
            columns={financeColumns}
            rows={financeUsers}
            keyFor={(u) => u.id}
            emptyMessage="No Finance users yet."
          />
        )}
      </Card>

      <CreateTenantAdminModal
        open={adminModalOpen}
        onClose={() => {
          setAdminModalOpen(false);
          setEditAdmin(null);
        }}
        tenants={tenants}
        editUser={editAdmin}
        onCreated={() => {
          setAdminModalOpen(false);
          setEditAdmin(null);
          refresh();
        }}
      />

      <CreateFinanceUserModal
        open={financeModalOpen}
        onClose={() => {
          setFinanceModalOpen(false);
          setEditFinance(null);
        }}
        editUser={editFinance}
        onCreated={() => {
          setFinanceModalOpen(false);
          setEditFinance(null);
          refresh();
        }}
      />
    </AppShell>
  );
}
