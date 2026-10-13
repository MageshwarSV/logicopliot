import { useEffect, useState, type FormEvent } from "react";
import axios from "axios";
import { Modal } from "../../components/ui/Modal";
import { Input } from "../../components/ui/Input";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import * as usersApi from "../../api/users";
import * as jobsApi from "../../api/jobs";
import * as userTypesApi from "../../api/userTypes";
import { customTypeIdOf, selectionFor, type TypeSelection, type UserType } from "../../api/userTypes";
import type { Role, User } from "../../types/auth";
import { MODES } from "../../types/onboarding";

type CreatableRole = "operator" | "gk2" | "manager";

const ROLE_OPTIONS: { value: CreatableRole; label: string }[] = [
  { value: "operator", label: "Gate Keeper 1 (Operator)" },
  { value: "gk2", label: "Gate Keeper 2" },
  { value: "manager", label: "Manager" },
];

/** One consolidated form for every user type a Tenant Admin creates — a type picker at the
 *  top swaps in the fields that type's underlying role actually needs, instead of three
 *  separate modals. The picker lists the 3 built-in roles plus any custom type this Tenant
 *  Admin created on Masters (e.g. "Supervisor") — picking one still shows exactly the same
 *  fields as its underlying role, since that's what actually drives access. */
export function CreateUserModal({
  open,
  onClose,
  onCreated,
  editUser = null,
  initialRole = "operator",
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
  editUser?: User | null;
  /** Which type to preselect for a NEW user — e.g. the Users page passes whichever type is
   *  currently selected there ("operator" or `custom:<id>`). Ignored once editUser is set
   *  (its own type always wins). */
  initialRole?: TypeSelection;
}) {
  const isEdit = !!editUser;
  const [userTypes, setUserTypes] = useState<UserType[]>([]);
  const [selection, setSelection] = useState<TypeSelection>(initialRole);
  const customId = customTypeIdOf(selection);
  const selectedCustom = customId ? userTypes.find((t) => t.id === customId) : undefined;
  const role: CreatableRole = selectedCustom ? selectedCustom.base_role : (selection as CreatableRole);
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Gate Keeper 1 (operator) only: real access control (templates) + an optional tab-filter
  // (modes). Mailbox connection lives on its own, standalone Email Puller screen now (Super
  // Admin > Settings > Email Puller) - connecting mail is no longer part of creating a user.
  const [templates, setTemplates] = useState<jobsApi.AvailableGroup[]>([]);
  const [selectedTemplates, setSelectedTemplates] = useState<string[]>([]);

  // Gate Keeper 1 (optional, tab-filter only) and Gate Keeper 2 (required, the real access
  // gate) both use this — Manager never does, it sees every job in the tenant unrestricted.
  const [modes, setModes] = useState<string[]>([]);

  function toggleMode(mode: string) {
    setModes((prev) => (prev.includes(mode) ? prev.filter((m) => m !== mode) : [...prev, mode]));
  }

  useEffect(() => {
    if (!open) return;
    jobsApi
      .listAvailableGroups()
      .then((gs) => setTemplates(gs.filter((g) => g.status === "approved")))
      .catch(() => setTemplates([]));
    userTypesApi.listUserTypes().then(setUserTypes).catch(() => setUserTypes([]));
    if (editUser) {
      setSelection(selectionFor(editUser.user_type_id, editUser.role));
      setFullName(editUser.full_name);
      setEmail(editUser.email);
      setPassword("");
      setModes(editUser.modes ?? []);
      if (editUser.role === "operator") {
        usersApi.getUserTemplates(editUser.id).then(setSelectedTemplates).catch(() => setSelectedTemplates([]));
      }
    } else {
      setSelection(initialRole);
      setFullName("");
      setEmail("");
      setPassword("");
      setSelectedTemplates([]);
      setModes([]);
    }
    setError(null);
  }, [open, editUser, initialRole]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);

    if (role === "gk2" && modes.length === 0) {
      setError("Pick at least one shipment mode for this user to review.");
      return;
    }

    setIsSubmitting(true);
    try {
      if (isEdit && editUser) {
        await usersApi.updateUser(editUser.id, {
          full_name: fullName,
          ...(email.trim() && email.trim() !== editUser.email ? { email: email.trim() } : {}),
          ...(password ? { password } : {}),
          ...(role === "operator" || role === "gk2" ? { modes } : {}),
          ...(role === "operator" ? { template_ids: selectedTemplates } : {}),
        });
      } else {
        await usersApi.createUser({
          email, password, full_name: fullName, role: role as Role,
          ...(selectedCustom ? { user_type_id: selectedCustom.id } : {}),
          ...(role === "operator" || role === "gk2" ? { modes } : {}),
          ...(role === "operator" ? { template_ids: selectedTemplates } : {}),
        });
      }
      onCreated();
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.detail ?? "Could not save this user.");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  const typeLabel = selectedCustom
    ? selectedCustom.name
    : ROLE_OPTIONS.find((r) => r.value === role)?.label ?? "User";
  const title = isEdit ? `Edit ${typeLabel}` : "New User";

  return (
    <Modal open={open} onClose={onClose} title={title}>
      <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
        <div>
          <p className="mb-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">User type</p>
          {isEdit ? (
            <p className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 dark:border-slate-700 dark:text-slate-300">
              {typeLabel}
              <span className="ml-2 text-xs text-slate-400">(cannot be changed after creation)</span>
            </p>
          ) : (
            <select
              value={selection}
              onChange={(e) => setSelection(e.target.value as TypeSelection)}
              className="w-full rounded-lg border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
            >
              <optgroup label="Built-in">
                {ROLE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
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
          )}
        </div>

        <Input label="Full name" value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Jane Doe" required />
        <Input label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="user@company.com" required />
        <Input
          label={isEdit ? "New password (leave blank to keep current)" : "Temporary password"}
          type="text"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={isEdit ? "Leave blank to keep" : "At least 8 characters"}
          minLength={isEdit ? undefined : 8}
          required={!isEdit}
        />

        {role === "operator" && (
          <div>
            <p className="mb-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">Templates this user can use</p>
            {templates.length === 0 ? (
              <p className="text-xs text-slate-400">No approved templates yet — approve one under Data Transformation first.</p>
            ) : (
              <div className="flex max-h-40 flex-col gap-1.5 overflow-y-auto rounded-lg border border-slate-200 p-2 dark:border-slate-700">
                {templates.map((t) => (
                  <label key={t.id} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                    <input
                      type="checkbox"
                      checked={selectedTemplates.includes(t.id)}
                      onChange={(e) => setSelectedTemplates((arr) => (e.target.checked ? [...arr, t.id] : arr.filter((x) => x !== t.id)))}
                    />
                    {t.name}
                  </label>
                ))}
              </div>
            )}
            <p className="mt-1 text-xs text-slate-400">Leave all unchecked to allow every approved template.</p>
          </div>
        )}

        {(role === "operator" || role === "gk2") && (
          <div>
            <p className="mb-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
              Shipment modes {role === "gk2" ? "this user reviews" : "shown as tabs for this user"}
            </p>
            <p className="mb-2 text-xs text-slate-400 dark:text-slate-500">
              {role === "gk2"
                ? "Only jobs on these modes, waiting on GK2 approval, will show in this user's queue."
                : "Purely a tab filter on the Jobs page — leave unchecked to show every job unfiltered."}
            </p>
            <div className="grid grid-cols-2 gap-2">
              {MODES.map((mode) => (
                <label
                  key={mode}
                  className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 dark:border-slate-700 dark:text-slate-300"
                >
                  <input
                    type="checkbox"
                    checked={modes.includes(mode)}
                    onChange={() => toggleMode(mode)}
                    className="h-4 w-4 rounded border-slate-300"
                  />
                  {mode}
                </label>
              ))}
            </div>
          </div>
        )}

        {role === "manager" && (
          <p className="text-xs text-slate-400">
            A Manager sees every job in your organization — no templates or shipment modes to
            assign, and no ability to approve, edit, or submit anything.
          </p>
        )}

        {error && <Alert>{error}</Alert>}
        <Button type="submit" isLoading={isSubmitting} className="mt-1">
          {isEdit ? "Save changes" : "Create user"}
        </Button>
      </form>
    </Modal>
  );
}
