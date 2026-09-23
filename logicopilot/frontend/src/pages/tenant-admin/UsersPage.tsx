import { useEffect, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Button } from "../../components/ui/Button";
import { Card, StatCard } from "../../components/ui/Card";
import { DataTable, type Column } from "../../components/ui/DataTable";
import { StatusBadge } from "../../components/ui/Badge";
import { CreateUserModal } from "./CreateUserModal";
import * as usersApi from "../../api/users";
import * as userTypesApi from "../../api/userTypes";
import { customTypeIdOf, type TypeSelection, type UserType } from "../../api/userTypes";
import type { User } from "../../types/auth";

type CreatableRole = "operator" | "gk2" | "manager";

const USER_TYPES: { value: CreatableRole; label: string }[] = [
  { value: "operator", label: "Gate Keeper 1 (Operator)" },
  { value: "gk2", label: "Gate Keeper 2" },
  { value: "manager", label: "Manager" },
];

/** Every Gate Keeper 1, Gate Keeper 2, Manager, and custom-type account — create, edit,
 *  disable, delete. A Gate Keeper 1's own form still has everything it always did (mailbox
 *  connect, template assignment); this page only moved WHERE that form lives, not what it
 *  does. Each account's actual permissions come from Masters, by its type — nothing here
 *  overrides that. A built-in type here means "this role, with no custom type" — a custom
 *  type based on the same role is a separate group of its own, listed under its own name. */
export function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [userTypes, setUserTypes] = useState<UserType[]>([]);
  const [selectedType, setSelectedType] = useState<TypeSelection>("operator");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editUser, setEditUserState] = useState<User | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  async function refresh() {
    setError(null);
    try {
      const [userList, types] = await Promise.all([usersApi.listUsers(), userTypesApi.listUserTypes()]);
      setUsers(userList.filter((u) => u.role === "operator" || u.role === "gk2" || u.role === "manager"));
      setUserTypes(types);
    } catch {
      setError("Failed to load users. Try refreshing the page.");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleDelete(target: User) {
    try {
      await usersApi.deleteUser(target.id);
      setConfirmDelete(null);
      refresh();
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not delete this account.");
    }
  }

  async function handleToggleActive(target: User) {
    try {
      await usersApi.setUserActive(target.id, !target.is_active);
      refresh();
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.detail ?? "Could not update that account.");
      }
    }
  }

  const customId = customTypeIdOf(selectedType);
  const selectedCustom = customId ? userTypes.find((t) => t.id === customId) : undefined;
  const baseRole: CreatableRole = selectedCustom ? selectedCustom.base_role : (selectedType as CreatableRole);
  const visibleUsers = users.filter((u) =>
    selectedCustom ? u.user_type_id === selectedCustom.id : u.role === selectedType && !u.user_type_id,
  );
  const typeLabel = selectedCustom ? selectedCustom.name : USER_TYPES.find((t) => t.value === selectedType)?.label ?? "";

  const columns: Column<User>[] = [
    { header: "Name", render: (u) => <span className="font-medium text-slate-900 dark:text-slate-100">{u.full_name}</span> },
    { header: "Email", render: (u) => u.email },
    ...(baseRole !== "manager"
      ? [{
          header: "Shipment Modes",
          render: (u: User) => (u.modes?.length ? u.modes.join(", ") : "—"),
        } as Column<User>]
      : []),
    { header: "Status", render: (u) => <StatusBadge isActive={u.is_active} /> },
    {
      header: "",
      className: "text-right",
      render: (u) =>
        confirmDelete === u.id ? (
          <div className="flex justify-end gap-1">
            <span className="self-center text-xs text-rose-600">Delete + jobs?</span>
            <Button variant="danger" size="sm" onClick={() => handleDelete(u)}>Yes</Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(null)}>No</Button>
          </div>
        ) : (
          <div className="flex justify-end gap-1">
            <Button variant="ghost" size="sm" onClick={() => { setEditUserState(u); setModalOpen(true); }}>
              Edit
            </Button>
            <Button variant="ghost" size="sm" onClick={() => handleToggleActive(u)}>
              {u.is_active ? "Disable" : "Enable"}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(u.id)}>Delete</Button>
          </div>
        ),
    },
  ];

  return (
    <AppShell title="Users" subtitle="Create and manage every user type's accounts, built-in or custom.">
      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}

      <Card className="mb-6 p-5">
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium text-slate-700 dark:text-slate-300">User type</label>
            <select
              value={selectedType}
              onChange={(e) => setSelectedType(e.target.value as TypeSelection)}
              className="rounded-lg border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
            >
              <optgroup label="Built-in">
                {USER_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </optgroup>
              {userTypes.length > 0 && (
                <optgroup label="Custom types">
                  {userTypes.map((t) => (
                    <option key={t.id} value={`custom:${t.id}`}>{t.name}</option>
                  ))}
                </optgroup>
              )}
            </select>
          </div>

          <Button size="sm" onClick={() => { setEditUserState(null); setModalOpen(true); }} className="ml-auto">
            + New Member
          </Button>
        </div>
      </Card>

      <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <StatCard label={typeLabel} value={visibleUsers.length} />
        <StatCard label="Active" value={visibleUsers.filter((u) => u.is_active).length} />
      </div>

      <Card>
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">{typeLabel}</h2>
        </div>
        {!isLoading && (
          <DataTable
            columns={columns}
            rows={visibleUsers}
            keyFor={(u) => u.id}
            emptyMessage={`No ${typeLabel.toLowerCase()} accounts yet — use "+ New Member" to create the first one.`}
          />
        )}
      </Card>

      <CreateUserModal
        open={modalOpen}
        onClose={() => {
          setModalOpen(false);
          setEditUserState(null);
        }}
        editUser={editUser}
        initialRole={selectedType}
        onCreated={() => {
          setModalOpen(false);
          setEditUserState(null);
          refresh();
        }}
      />
    </AppShell>
  );
}
