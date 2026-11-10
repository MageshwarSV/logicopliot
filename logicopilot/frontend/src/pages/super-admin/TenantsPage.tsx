import { useEffect, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { DataTable, type Column } from "../../components/ui/DataTable";
import { Modal } from "../../components/ui/Modal";
import { StatusBadge } from "../../components/ui/Badge";
import { CreateTenantModal } from "./CreateTenantModal";
import { TenantUsersModal } from "./TenantUsersModal";
import * as tenantsApi from "../../api/tenants";
import * as usersApi from "../../api/users";
import type { Tenant } from "../../types/tenant";
import type { User } from "../../types/auth";

/** The full, ready-to-paste IRN Pending link for a tenant that already has its key. */
function irnPendingLink(tenant: Tenant): string {
  return `${window.location.origin}/irn-pending?key=${encodeURIComponent(tenant.irn_pending_access_key ?? "")}`;
}

/** The "Tenants" screen, split out from the combined Dashboard page (2026-11-10, by explicit
 *  request, matching a reference sidebar design) - same data and the same tenant-management
 *  actions the Dashboard's own Tenants table already had, just on its own sidebar link. Purely
 *  a frontend split: no backend change, nothing here that the Dashboard page didn't already
 *  do. */
export function TenantsPage() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tenantModalOpen, setTenantModalOpen] = useState(false);
  const [usersOfTenant, setUsersOfTenant] = useState<Tenant | null>(null);
  const [irnLinkTenant, setIrnLinkTenant] = useState<Tenant | null>(null);
  const [irnLinkBusy, setIrnLinkBusy] = useState(false);
  const [irnLinkCopied, setIrnLinkCopied] = useState(false);

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

  /** Generates the tenant's key on first press (a no-op on every press after that - it never
   *  rotates), then opens the modal to show/copy the link. */
  async function handleShowIrnLink(t: Tenant) {
    setIrnLinkCopied(false);
    if (t.irn_pending_access_key) {
      setIrnLinkTenant(t);
      return;
    }
    setIrnLinkBusy(true);
    try {
      const updated = await tenantsApi.generateIrnPendingKey(t.id);
      setTenants((prev) => prev.map((row) => (row.id === updated.id ? updated : row)));
      setIrnLinkTenant(updated);
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.detail ?? "Could not generate the IRN link.");
      }
    } finally {
      setIrnLinkBusy(false);
    }
  }

  async function handleCopyIrnLink() {
    if (!irnLinkTenant) return;
    try {
      await navigator.clipboard.writeText(irnPendingLink(irnLinkTenant));
      setIrnLinkCopied(true);
    } catch {
      /* clipboard permission denied - the link is still selectable in the field below */
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
            <Button
              variant="ghost"
              size="sm"
              disabled={irnLinkBusy}
              onClick={() => handleShowIrnLink(t)}
              title="A standalone, no-login link showing only this tenant's IRN Document Process jobs"
            >
              {t.irn_pending_access_key ? "IRN Link" : "Generate IRN Link"}
            </Button>
            <Button variant="ghost" size="sm" className="text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-500/10" onClick={() => handleDeleteTenant(t)}>
              Delete
            </Button>
          </div>
        );
      },
    },
  ];

  return (
    <AppShell title="Tenants" subtitle="Create and manage the customers on this platform.">
      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}

      <Card>
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

      <Modal open={irnLinkTenant !== null} onClose={() => setIrnLinkTenant(null)} title="IRN Pending link">
        {irnLinkTenant && (
          <div className="space-y-3">
            <p className="text-sm text-slate-600 dark:text-slate-300">
              Shows only <span className="font-medium text-slate-900 dark:text-slate-100">{irnLinkTenant.name}</span>'s
              jobs in IRN Document Process — no login required. Anyone with this link can open it, so share it only
              with whoever handles DSC signing for this tenant.
            </p>
            <div className="flex items-center gap-2">
              <input
                type="text"
                readOnly
                value={irnPendingLink(irnLinkTenant)}
                onFocus={(e) => e.currentTarget.select()}
                className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700 outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              />
              <Button size="sm" onClick={handleCopyIrnLink}>
                {irnLinkCopied ? "Copied" : "Copy"}
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </AppShell>
  );
}
