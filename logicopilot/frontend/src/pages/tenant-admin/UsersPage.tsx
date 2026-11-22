import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { formatDateTime } from "../../utils/dateTime";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Button } from "../../components/ui/Button";
import { Card, StatCard } from "../../components/ui/Card";
import { DataTable, type Column } from "../../components/ui/DataTable";
import { StatusBadge } from "../../components/ui/Badge";
import { Pagination } from "../../components/ui/Pagination";
import { FloatingPanel } from "../../components/ui/FloatingPanel";
import { SearchIcon, UsersIcon } from "../../components/ui/icons";
import { CreateUserModal } from "./CreateUserModal";
import * as usersApi from "../../api/users";
import * as userTypesApi from "../../api/userTypes";
import { customTypeIdOf, type TypeSelection, type UserType } from "../../api/userTypes";
import type { User } from "../../types/auth";

type CreatableRole = "operator" | "gk2" | "manager";
type StatusFilter = "all" | "active" | "disabled";

const USER_TYPES: { value: CreatableRole; label: string; tabLabel: string; description: string }[] = [
  { value: "operator", label: "Gate Keeper 1 (Operator)", tabLabel: "Gate Keeper 1", description: "Can review, validate and process jobs." },
  { value: "gk2", label: "Gate Keeper 2", tabLabel: "Gate Keeper 2", description: "Gives the final approval before a job goes to the ERP." },
  { value: "manager", label: "Manager", tabLabel: "Manager", description: "Read-only view of every job across the organization." },
];

const MODE_OPTIONS = ["Air Export", "Air Import", "Sea Export", "Sea Import"];
const PAGE_SIZES = [5, 10, 25, 50];

const AVATAR_TONES = [
  "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
  "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
  "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  "bg-orange-100 text-orange-700 dark:bg-orange-500/15 dark:text-orange-300",
  "bg-indigo-100 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300",
  "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
];

const SELECT_CLASS =
  "rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

function avatarTone(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return AVATAR_TONES[h % AVATAR_TONES.length];
}

function formatLastLogin(iso: string | null): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return formatDateTime(d, { year: true });
}

function PlaneIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
      <path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z" />
    </svg>
  );
}

function ShipIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
      <path d="M2 20c1.5 1 3 1 4.5 0s3-1 4.5 0 3 1 4.5 0 3-1 4.5 0" />
      <path d="M4 18 3 13h18l-2 5" />
      <path d="M12 3v10M7 13V8h10v5" />
    </svg>
  );
}

function CrownIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
      <path d="m3 7 4.5 4L12 5l4.5 6L21 7l-2 11H5z" />
      <path d="M5 21h14" />
    </svg>
  );
}

function ModeChip({ mode }: { mode: string }) {
  const isAir = mode.toLowerCase().startsWith("air");
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium ${
        isAir
          ? "bg-sky-50 text-sky-700 dark:bg-sky-500/10 dark:text-sky-300"
          : "bg-violet-50 text-violet-700 dark:bg-violet-500/10 dark:text-violet-300"
      }`}
    >
      {isAir ? <PlaneIcon /> : <ShipIcon />}
      {mode}
    </span>
  );
}

/** The "⋯" menu on each row. Rendered through FloatingPanel so the table Card's overflow-hidden
 *  can't clip it. */
function RowActions({
  user,
  onEdit,
  onToggleActive,
  onDelete,
}: {
  user: User;
  onEdit: () => void;
  onToggleActive: () => void;
  onDelete: () => void;
}) {
  const anchorRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      const t = e.target as HTMLElement;
      if (anchorRef.current?.contains(t) || t.closest("[data-floating-panel-root]")) return;
      setOpen(false);
      setConfirming(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && (setOpen(false), setConfirming(false));
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const item = "block w-full px-3.5 py-2 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-800";

  return (
    <div ref={anchorRef} className="flex justify-end">
      <button
        type="button"
        aria-label={`Actions for ${user.full_name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => { setOpen((o) => !o); setConfirming(false); }}
        className="rounded-lg px-2 py-1 text-lg leading-none text-slate-500 hover:bg-slate-100 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
      >
        ⋯
      </button>
      <FloatingPanel anchorRef={anchorRef} open={open} className="z-50 flex justify-end">
        <div role="menu" className="mt-1 w-48 shrink-0 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg shadow-slate-900/10 dark:border-slate-700 dark:bg-slate-900">
          {confirming ? (
            <div className="px-3.5 py-2">
              <p className="mb-2 text-xs text-rose-600 dark:text-rose-400">Delete this account and its jobs?</p>
              <div className="flex gap-1.5">
                <Button variant="danger" size="sm" onClick={() => { setOpen(false); onDelete(); }}>Delete</Button>
                <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>Cancel</Button>
              </div>
            </div>
          ) : (
            <>
              <button type="button" role="menuitem" className={`${item} text-slate-700 dark:text-slate-200`} onClick={() => { setOpen(false); onEdit(); }}>
                Edit
              </button>
              <button type="button" role="menuitem" className={`${item} text-slate-700 dark:text-slate-200`} onClick={() => { setOpen(false); onToggleActive(); }}>
                {user.is_active ? "Disable" : "Enable"}
              </button>
              <button type="button" role="menuitem" className={`${item} text-rose-600 dark:text-rose-400`} onClick={() => setConfirming(true)}>
                Delete
              </button>
            </>
          )}
        </div>
      </FloatingPanel>
    </div>
  );
}

function ActiveCaption({ count }: { count: number }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400">
      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
      {count} Active
    </span>
  );
}

/** Every Gate Keeper 1, Gate Keeper 2, Manager, and custom-type account: create, edit,
 *  disable, delete. A Gate Keeper 1's own form still has everything it always did (mailbox
 *  connect, template assignment); this page only moved WHERE that form lives, not what it
 *  does. Each account's actual permissions come from Masters, by its type, and nothing here
 *  overrides that. A built-in type here means "this role, with no custom type"; a custom
 *  type based on the same role is a separate group of its own, listed under its own name. */
export function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [userTypes, setUserTypes] = useState<UserType[]>([]);
  const [selectedType, setSelectedType] = useState<TypeSelection>("operator");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editUser, setEditUserState] = useState<User | null>(null);

  const [search, setSearch] = useState("");
  const [modeFilter, setModeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

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

  // Back to page 1 whenever what's being listed changes.
  useEffect(() => {
    setPage(1);
  }, [selectedType, search, modeFilter, statusFilter, pageSize]);

  async function handleDelete(target: User) {
    try {
      await usersApi.deleteUser(target.id);
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

  function selectType(t: TypeSelection) {
    setSelectedType(t);
    setModeFilter("");
  }

  const builtInUsers = (role: CreatableRole) => users.filter((u) => u.role === role && !u.user_type_id);

  const customId = customTypeIdOf(selectedType);
  const selectedCustom = customId ? userTypes.find((t) => t.id === customId) : undefined;
  const baseRole: CreatableRole = selectedCustom ? selectedCustom.base_role : (selectedType as CreatableRole);
  const builtIn = USER_TYPES.find((t) => t.value === selectedType);
  const typeLabel = selectedCustom ? selectedCustom.name : builtIn?.label ?? "";
  const typeDescription = selectedCustom
    ? `Custom type based on ${USER_TYPES.find((t) => t.value === selectedCustom.base_role)?.tabLabel ?? selectedCustom.base_role}.`
    : builtIn?.description ?? "";
  const showModes = baseRole !== "manager";

  const typeUsers = useMemo(
    () => users.filter((u) => (selectedCustom ? u.user_type_id === selectedCustom.id : u.role === selectedType && !u.user_type_id)),
    [users, selectedCustom, selectedType],
  );

  const filteredUsers = useMemo(() => {
    const q = search.trim().toLowerCase();
    return typeUsers.filter((u) => {
      if (q && !u.full_name.toLowerCase().includes(q) && !u.email.toLowerCase().includes(q)) return false;
      if (statusFilter === "active" && !u.is_active) return false;
      if (statusFilter === "disabled" && u.is_active) return false;
      if (showModes && modeFilter && !(u.modes ?? []).includes(modeFilter)) return false;
      return true;
    });
  }, [typeUsers, search, statusFilter, modeFilter, showModes]);

  const lastPage = Math.max(1, Math.ceil(filteredUsers.length / pageSize));
  const currentPage = Math.min(page, lastPage);
  const pageRows = filteredUsers.slice((currentPage - 1) * pageSize, currentPage * pageSize);

  const columns: Column<User>[] = [
    {
      header: "User",
      render: (u) => (
        <div className="flex items-center gap-3">
          <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${avatarTone(u.id)}`}>
            {(u.full_name.trim()[0] ?? "?").toUpperCase()}
          </span>
          <span className="font-medium text-slate-900 dark:text-slate-100">{u.full_name}</span>
        </div>
      ),
    },
    { header: "Email", render: (u) => u.email },
    ...(showModes
      ? [{
          header: "Shipment Access",
          render: (u: User) =>
            u.modes?.length ? (
              <div className="flex flex-wrap gap-1.5">
                {u.modes.map((m) => <ModeChip key={m} mode={m} />)}
              </div>
            ) : (
              <span className="text-slate-400">—</span>
            ),
        } as Column<User>]
      : []),
    { header: "Status", render: (u) => <StatusBadge isActive={u.is_active} /> },
    { header: "Last Login", render: (u) => <span className="text-slate-600 dark:text-slate-400">{formatLastLogin(u.last_login_at)}</span> },
    {
      header: "Actions",
      className: "text-right",
      render: (u) => (
        <RowActions
          user={u}
          onEdit={() => { setEditUserState(u); setModalOpen(true); }}
          onToggleActive={() => handleToggleActive(u)}
          onDelete={() => handleDelete(u)}
        />
      ),
    },
  ];

  const tabs: { value: TypeSelection; label: string; count: number }[] = [
    ...USER_TYPES.map((t) => ({ value: t.value as TypeSelection, label: t.tabLabel, count: builtInUsers(t.value).length })),
    ...userTypes.map((t) => ({
      value: `custom:${t.id}` as TypeSelection,
      label: t.name,
      count: users.filter((u) => u.user_type_id === t.id).length,
    })),
  ];

  const statCards: { value: CreatableRole; label: string; icon: ReactNode; tone: "blue" | "violet" | "amber" }[] = [
    { value: "operator", label: "Gate Keeper 1 (Operator)", icon: <UsersIcon />, tone: "blue" },
    { value: "gk2", label: "Gate Keeper 2", icon: <UsersIcon />, tone: "violet" },
    { value: "manager", label: "Managers", icon: <CrownIcon />, tone: "amber" },
  ];

  return (
    <AppShell title="Users & Access" subtitle="Manage team members, roles and shipment access for your organization.">
      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}

      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {statCards.map((c) => {
          const list = builtInUsers(c.value);
          return (
            <StatCard
              key={c.value}
              label={c.label}
              value={list.length}
              icon={c.icon}
              tone={c.tone}
              valueCaption={<ActiveCaption count={list.filter((u) => u.is_active).length} />}
              onClick={() => selectType(c.value)}
              active={selectedType === c.value}
            />
          );
        })}
        <StatCard label="Total Users" value={users.length} icon={<UsersIcon />} tone="blue" valueCaption="All Roles" />
      </div>

      <div className="mb-6 flex flex-wrap gap-2" role="tablist">
        {tabs.map((t) => {
          const active = selectedType === t.value;
          return (
            <button
              key={t.value}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => selectType(t.value)}
              className={`inline-flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium transition-colors ${
                active
                  ? "border-indigo-300 bg-indigo-50 text-indigo-700 dark:border-indigo-500/40 dark:bg-indigo-500/10 dark:text-indigo-300"
                  : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
              }`}
            >
              <UsersIcon className="h-4 w-4" />
              {t.label} ({t.count})
            </button>
          );
        })}
      </div>

      <Card>
        <div className="flex flex-col gap-4 border-b border-slate-200 px-5 py-4 lg:flex-row lg:items-center lg:justify-between dark:border-slate-800">
          <div>
            <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-50">{typeLabel}</h2>
            <p className="text-sm text-slate-500 dark:text-slate-400">
              {typeUsers.length} {typeUsers.length === 1 ? "user" : "users"} · {typeDescription}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by name or email..."
                className={`${SELECT_CLASS} w-64 pl-9`}
              />
            </div>
            {showModes && (
              <select value={modeFilter} onChange={(e) => setModeFilter(e.target.value)} className={SELECT_CLASS} aria-label="Shipment access">
                <option value="">All shipment access</option>
                {MODE_OPTIONS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            )}
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as StatusFilter)} className={SELECT_CLASS} aria-label="Status">
              <option value="all">All statuses</option>
              <option value="active">Active</option>
              <option value="disabled">Disabled</option>
            </select>
            <Button size="sm" onClick={() => { setEditUserState(null); setModalOpen(true); }}>
              + Add New Member
            </Button>
          </div>
        </div>

        {!isLoading && (
          <DataTable
            columns={columns}
            rows={pageRows}
            keyFor={(u) => u.id}
            emptyMessage={
              typeUsers.length === 0
                ? `No ${typeLabel.toLowerCase()} accounts yet. Use "+ Add New Member" to create the first one.`
                : "No users match these filters."
            }
          />
        )}

        {!isLoading && filteredUsers.length > 0 && (
          <div className="px-5 pb-4">
            <Pagination
              page={currentPage}
              pageSize={pageSize}
              total={filteredUsers.length}
              onPageChange={setPage}
              noun="users"
              extra={
                <label className="mr-3 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                  Rows per page
                  <select value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))} className={`${SELECT_CLASS} py-1`}>
                    {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </label>
              }
            />
          </div>
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
