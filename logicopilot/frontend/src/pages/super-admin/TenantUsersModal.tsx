import { useState } from "react";
import axios from "axios";
import { Modal } from "../../components/ui/Modal";
import { Button } from "../../components/ui/Button";
import { RoleBadge, StatusBadge } from "../../components/ui/Badge";
import { CreateTenantAdminModal } from "./CreateTenantAdminModal";
import * as usersApi from "../../api/users";
import type { User } from "../../types/auth";
import type { Tenant } from "../../types/tenant";

export function TenantUsersModal({
  tenant,
  users,
  onClose,
  onChanged,
}: {
  tenant: Tenant | null;
  users: User[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Creating a tenant admin now happens from within the tenant it belongs to, rather than a
  // generic top-level button on the Super Admin dashboard unaffiliated with any one tenant.
  const [adminModalOpen, setAdminModalOpen] = useState(false);
  const [editAdmin, setEditAdmin] = useState<User | null>(null);

  const tenantUsers = tenant ? users.filter((u) => u.tenant_id === tenant.id) : [];

  async function remove(u: User) {
    setBusyId(u.id);
    setError(null);
    try {
      await usersApi.deleteUser(u.id);
      setConfirmId(null);
      onChanged();
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not delete.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Modal open={tenant !== null} onClose={onClose} title={tenant ? `Users — ${tenant.name}` : "Users"} maxWidth="max-w-lg">
      {error && (
        <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}
      <div className="mb-3 flex justify-end">
        <Button size="sm" onClick={() => { setEditAdmin(null); setAdminModalOpen(true); }}>
          + New Tenant Admin
        </Button>
      </div>
      <div className="flex max-h-[60vh] flex-col gap-2 overflow-y-auto">
        {tenantUsers.length === 0 && <p className="text-sm text-slate-400">No users under this tenant yet.</p>}
        {tenantUsers.map((u) => (
          <div key={u.id} className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-800">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">{u.full_name}</p>
              <p className="truncate text-xs text-slate-500">{u.email}</p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <RoleBadge role={u.role} />
              <StatusBadge isActive={u.is_active} />
              {u.role === "tenant_admin" && (
                <Button size="sm" variant="ghost" onClick={() => { setEditAdmin(u); setAdminModalOpen(true); }}>
                  Edit
                </Button>
              )}
              {confirmId === u.id ? (
                <>
                  <span className="text-xs text-rose-600">Delete + all their jobs?</span>
                  <Button size="sm" variant="danger" onClick={() => remove(u)} isLoading={busyId === u.id}>Yes</Button>
                  <Button size="sm" variant="ghost" onClick={() => setConfirmId(null)}>No</Button>
                </>
              ) : (
                <Button size="sm" variant="ghost" onClick={() => setConfirmId(u.id)}>Delete</Button>
              )}
            </div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs text-slate-400">
        Deleting a tenant admin also removes the operators they created and all related jobs.
      </p>

      {tenant && (
        <CreateTenantAdminModal
          open={adminModalOpen}
          onClose={() => {
            setAdminModalOpen(false);
            setEditAdmin(null);
          }}
          tenants={[tenant]}
          editUser={editAdmin}
          onCreated={() => {
            setAdminModalOpen(false);
            setEditAdmin(null);
            onChanged();
          }}
        />
      )}
    </Modal>
  );
}
