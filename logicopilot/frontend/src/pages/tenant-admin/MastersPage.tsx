import { useEffect, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { useAuth } from "../../auth/useAuth";
import * as tenantsApi from "../../api/tenants";
import * as userTypesApi from "../../api/userTypes";
import { customTypeIdOf, type TypeSelection, type UserType } from "../../api/userTypes";

type CreatableRole = "operator" | "gk2" | "manager";

const USER_TYPES: { value: CreatableRole; label: string }[] = [
  { value: "operator", label: "Gate Keeper 1 (Operator)" },
  { value: "gk2", label: "Gate Keeper 2" },
  { value: "manager", label: "Manager" },
];

/** Masters: the three built-in user types plus whatever custom types this Tenant Admin has
 *  created (e.g. "Supervisor"), and the rules each one follows — nothing about individual
 *  accounts lives here (see the separate "Users" page for that). Every account of a type
 *  follows that type's rules; there is no per-account override. A custom type always borrows
 *  one of the three built-ins' underlying job access (own-job for Gate Keeper 1, the
 *  approval queue for Gate Keeper 2, tenant-wide read-only for Manager) — it only carries its
 *  own name and its own Write rule on top. */
export function MastersPage() {
  const { user } = useAuth();
  const [roleWriteEnabled, setRoleWriteEnabled] = useState<Record<string, boolean>>({});
  const [userTypes, setUserTypes] = useState<UserType[]>([]);
  const [selectedType, setSelectedType] = useState<TypeSelection>("operator");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [showNewType, setShowNewType] = useState(false);
  const [newName, setNewName] = useState("");
  const [newBase, setNewBase] = useState<CreatableRole>("operator");
  const [newWrite, setNewWrite] = useState(true);
  const [creating, setCreating] = useState(false);

  async function refresh() {
    setError(null);
    try {
      const [tenant, types] = await Promise.all([
        user?.tenant_id ? tenantsApi.getTenant(user.tenant_id) : Promise.resolve(null),
        userTypesApi.listUserTypes(),
      ]);
      setRoleWriteEnabled(tenant?.role_write_enabled ?? {});
      setUserTypes(types);
    } catch {
      setError("Failed to load data. Try refreshing the page.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const customId = customTypeIdOf(selectedType);
  const selectedCustom = customId ? userTypes.find((t) => t.id === customId) : undefined;
  const baseRole: CreatableRole = selectedCustom ? selectedCustom.base_role : (selectedType as CreatableRole);
  const isManager = baseRole === "manager";
  // Read is unconditional for every type - there is no "cannot even view" account here, so
  // that checkbox is always ticked and never editable. Write is the one real switch.
  const isWriteEnabled = selectedCustom
    ? selectedCustom.write_enabled
    : !isManager && (roleWriteEnabled[selectedType] ?? true);
  const typeLabel = selectedCustom ? selectedCustom.name : USER_TYPES.find((t) => t.value === selectedType)?.label ?? "";

  async function setWrite(write: boolean) {
    if (isManager) return;
    setSaving(true);
    setError(null);
    try {
      if (selectedCustom) {
        const updated = await userTypesApi.updateUserType(selectedCustom.id, write);
        setUserTypes((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
      } else if (user?.tenant_id) {
        const tenant = await tenantsApi.updateTenant(user.tenant_id, {
          role_write_enabled: { [selectedType]: write },
        });
        setRoleWriteEnabled(tenant.role_write_enabled ?? {});
      }
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not save this rule.");
    } finally {
      setSaving(false);
    }
  }

  async function createType() {
    if (!newName.trim()) {
      setError("Give the new type a name.");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const created = await userTypesApi.createUserType({
        name: newName.trim(),
        base_role: newBase,
        write_enabled: newBase === "manager" ? false : newWrite,
      });
      setUserTypes((prev) => [...prev, created]);
      setSelectedType(`custom:${created.id}`);
      setShowNewType(false);
      setNewName("");
      setNewBase("operator");
      setNewWrite(true);
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not create this type.");
    } finally {
      setCreating(false);
    }
  }

  async function deleteType(id: string) {
    setError(null);
    try {
      await userTypesApi.deleteUserType(id);
      setUserTypes((prev) => prev.filter((t) => t.id !== id));
      if (customTypeIdOf(selectedType) === id) setSelectedType("operator");
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not delete this type.");
    }
  }

  const checkboxRow = "flex items-center gap-2.5 rounded-lg border px-3.5 py-3 text-sm";

  return (
    <AppShell title="Masters" subtitle="Pick a user type, then set the rules every account of that type follows.">
      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}

      <Card className="max-w-lg p-5">
        <div className="mb-6 flex flex-col gap-1.5">
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
                  <option key={t.id} value={`custom:${t.id}`}>
                    {t.name} ({USER_TYPES.find((b) => b.value === t.base_role)?.label})
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </div>

        {!loading && (
          <>
            <p className="mb-2 text-sm font-medium text-slate-700 dark:text-slate-300">
              Rules for {typeLabel}
            </p>
            <div className="flex flex-col gap-2">
              <label className={`${checkboxRow} border-slate-200 text-slate-500 dark:border-slate-700 dark:text-slate-400`}>
                <input type="checkbox" checked readOnly disabled className="h-4 w-4 rounded border-slate-300" />
                <span>
                  <span className="font-medium text-slate-700 dark:text-slate-300">Read</span> — can view every job and its data
                </span>
              </label>
              <label
                className={`${checkboxRow} ${
                  isManager
                    ? "border-slate-200 text-slate-400 dark:border-slate-700 dark:text-slate-500"
                    : "border-slate-200 text-slate-700 dark:border-slate-700 dark:text-slate-300"
                }`}
              >
                <input
                  type="checkbox"
                  checked={isWriteEnabled}
                  disabled={isManager || saving}
                  onChange={(e) => setWrite(e.target.checked)}
                  className="h-4 w-4 rounded border-slate-300 disabled:opacity-60"
                />
                <span>
                  <span className="font-medium text-slate-700 dark:text-slate-300">Write</span> — can approve, upload, and submit
                </span>
              </label>
            </div>
            <p className="mt-3 text-xs text-slate-400">
              {isManager
                ? "This type is always Read only, by design — it can never approve, edit, upload, or submit anything, and this cannot be changed here."
                : isWriteEnabled
                  ? `${typeLabel} accounts can view and act on jobs normally.`
                  : `${typeLabel} accounts are locked to Read only in this tenant — every approve/upload/submit action is blocked, same as Manager.`}
            </p>
            {selectedCustom && (
              <div className="mt-4 flex justify-end">
                <Button variant="ghost" size="sm" onClick={() => deleteType(selectedCustom.id)}>
                  Delete this custom type
                </Button>
              </div>
            )}
          </>
        )}
      </Card>

      <Card className="mt-6 max-w-lg p-5">
        {!showNewType ? (
          <Button variant="ghost" size="sm" onClick={() => setShowNewType(true)}>
            + New custom type
          </Button>
        ) : (
          <div className="flex flex-col gap-3">
            <p className="text-sm font-medium text-slate-700 dark:text-slate-300">New custom type</p>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-slate-500 dark:text-slate-400">Name</label>
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="e.g. Supervisor"
                className="rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-900 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-slate-500 dark:text-slate-400">Based on</label>
              <select
                value={newBase}
                onChange={(e) => setNewBase(e.target.value as CreatableRole)}
                className="rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-900 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              >
                {USER_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
              <p className="text-xs text-slate-400">
                Decides how this type's accounts actually access jobs — the name is just a label on top.
              </p>
            </div>
            <label className={`${checkboxRow} ${newBase === "manager" ? "border-slate-200 text-slate-400 dark:border-slate-700 dark:text-slate-500" : "border-slate-200 text-slate-700 dark:border-slate-700 dark:text-slate-300"}`}>
              <input
                type="checkbox"
                checked={newBase === "manager" ? false : newWrite}
                disabled={newBase === "manager"}
                onChange={(e) => setNewWrite(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300 disabled:opacity-60"
              />
              <span><span className="font-medium text-slate-700 dark:text-slate-300">Write</span> — can approve, upload, and submit</span>
            </label>
            <div className="flex gap-2">
              <Button size="sm" isLoading={creating} onClick={createType}>Create type</Button>
              <Button variant="ghost" size="sm" onClick={() => setShowNewType(false)}>Cancel</Button>
            </div>
          </div>
        )}
      </Card>
    </AppShell>
  );
}
