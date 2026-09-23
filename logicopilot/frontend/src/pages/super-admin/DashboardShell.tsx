import { useEffect, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Button } from "../../components/ui/Button";
import { Card, StatCard } from "../../components/ui/Card";
import { DataTable, type Column } from "../../components/ui/DataTable";
import { RoleBadge, StatusBadge } from "../../components/ui/Badge";
import { CreateTenantModal } from "./CreateTenantModal";
import { CreateTenantAdminModal } from "./CreateTenantAdminModal";
import { InboxButton } from "./InboxButton";
import { TenantUsersModal } from "./TenantUsersModal";
import * as tenantsApi from "../../api/tenants";
import * as usersApi from "../../api/users";
import type { Tenant } from "../../types/tenant";
import type { User } from "../../types/auth";

export function SuperAdminDashboard() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tenantModalOpen, setTenantModalOpen] = useState(false);
  const [adminModalOpen, setAdminModalOpen] = useState(false);
  const [editAdmin, setEditAdmin] = useState<User | null>(null);
  const [usersOfTenant, setUsersOfTenant] = useState<Tenant | null>(null);

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
  const operators = users.filter((u) => u.role === "operator");
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

  async function handleDeleteTenant(t: Tenant) {
    const count = users.filter((u) => u.tenant_id === t.id).length;
    const ok = window.confirm(
      `Delete "${t.name}"?\n\nThis permanently removes the tenant and everything scoped to it: ` +
        `${count} user(s), all templates, ERP scripts, and jobs. This cannot be undone.`,
    );
    if (!ok) return;
    try {
      await tenantsApi.deleteTenant(t.id);
      refresh();
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.detail ?? "Could not delete that tenant.");
      }
    }
  }

  const tenantColumns: Column<Tenant>[] = [
    { header: "Name", render: (t) => <span className="font-medium text-slate-900 dark:text-slate-100">{t.name}</span> },
    { header: "Region", render: (t) => t.region ?? "—" },
    { header: "Currency", render: (t) => t.currency ?? "—" },
    { header: "Status", render: (t) => <StatusBadge isActive={t.is_active} /> },
    {
      header: "",
      className: "text-right",
      render: (t) => {
        const count = users.filter((u) => u.tenant_id === t.id).length;
        return (
          <div className="flex justify-end gap-1">
            <Button variant="ghost" size="sm" onClick={() => setUsersOfTenant(t)}>
              Users ({count})
            </Button>
            <Button variant="ghost" size="sm" className="text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-500/10" onClick={() => handleDeleteTenant(t)}>
              Delete
            </Button>
          </div>
        );
      },
    },
  ];

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

  return (
    <AppShell title="Super Admin" subtitle="Manage tenants and the admins who run them." actions={<InboxButton />}>
      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}

      <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Tenants" value={tenants.length} />
        <StatCard label="Tenant Admins" value={tenantAdmins.length} />
        <StatCard label="Operators" value={operators.length} />
        <StatCard label="All Users" value={users.length} />
      </div>

      <Card className="mb-8">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">Tenants</h2>
          <Button size="sm" onClick={() => setTenantModalOpen(true)}>
            + New Tenant
          </Button>
        </div>
        {!isLoading && (
          <DataTable
            columns={tenantColumns}
            rows={tenants}
            keyFor={(t) => t.id}
            emptyMessage="No tenants yet — create your first customer to get started."
          />
        )}
      </Card>

      <Card>
        <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">Tenant Admins</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Created from within a tenant's own "Users" view, not from here — open a tenant
            above and use "+ New Tenant Admin" there.
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

      <CreateTenantModal
        open={tenantModalOpen}
        onClose={() => setTenantModalOpen(false)}
        onCreated={() => {
          setTenantModalOpen(false);
          refresh();
        }}
      />
      <TenantUsersModal
        tenant={usersOfTenant}
        users={users}
        onClose={() => setUsersOfTenant(null)}
        onChanged={refresh}
      />

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
    </AppShell>
  );
}
