import { useEffect, useMemo, useState, type FormEvent } from "react";
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
import { PlaneIcon, ShipIcon } from "../../components/ui/icons";

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

  // Shipment modes are no longer picked separately (2026-11-28): every template belongs to one
  // mode, so the modes are worked out from the templates picked below and saved exactly as
  // before - Gate Keeper 1: the Jobs-page tabs (none picked = every tab); Gate Keeper 2: the real
  // access gate (at least one). An existing user's saved modes preselect their templates.
  const [editModes, setEditModes] = useState<string[]>([]);

  // Gate Keeper 2 only: a second, independent narrowing — which operators' jobs this account
  // sees at all. Starts with every CURRENT operator ticked (both for a brand-new account and
  // for an existing one nobody has ever scoped, i.e. assigned_operator_ids is null) so the box
  // only actually narrows anything once the Tenant Admin unticks someone.
  const [operators, setOperators] = useState<User[]>([]);
  const [selectedOperatorIds, setSelectedOperatorIds] = useState<string[]>([]);

  function toggleOperator(id: string) {
    setSelectedOperatorIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  useEffect(() => {
    if (!open) return;
    jobsApi
      .listAvailableGroups()
      .then((gs) => setTemplates(gs.filter((g) => g.status === "approved")))
      .catch(() => setTemplates([]));
    userTypesApi.listUserTypes().then(setUserTypes).catch(() => setUserTypes([]));
    usersApi
      .listUsers()
      .then((users) => {
        const ops = users.filter((u) => u.role === "operator");
        setOperators(ops);
        // null/undefined (never scoped, or a brand-new account) means "every operator" -
        // resolved here, once the real operator list is in hand, rather than left as an
        // empty box that would misleadingly read as "nobody".
        setSelectedOperatorIds(editUser?.assigned_operator_ids ?? ops.map((o) => o.id));
      })
      .catch(() => setOperators([]));
    if (editUser) {
      setSelection(selectionFor(editUser.user_type_id, editUser.role));
      setFullName(editUser.full_name);
      setEmail(editUser.email);
      setPassword("");
      setEditModes(editUser.modes ?? []);
      if (editUser.role === "operator") {
        usersApi.getUserTemplates(editUser.id).then(setSelectedTemplates).catch(() => setSelectedTemplates([]));
      }
    } else {
      setSelection(initialRole);
      setFullName("");
      setEmail("");
      setPassword("");
      setSelectedTemplates([]);
      setEditModes([]);
    }
    setError(null);
  }, [open, editUser, initialRole]);

  // Gate Keeper 2: an existing account's modes tick every template of those modes.
  useEffect(() => {
    if (editUser?.role === "gk2" && templates.length) {
      setSelectedTemplates(templates.filter((t) => t.mode && editModes.includes(t.mode)).map((t) => t.id));
    }
  }, [editUser, editModes, templates]);

  const modeOf = (id: string) => templates.find((t) => t.id === id)?.mode ?? null;
  const modes = useMemo(
    () => Array.from(new Set(selectedTemplates.map(modeOf).filter((m): m is string => !!m))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedTemplates, templates],
  );

  function toggleTemplate(id: string) {
    setSelectedTemplates((prev) => {
      const on = !prev.includes(id);
      // Gate Keeper 2 reviews by MODE, so every template of that mode comes along - the screen
      // never shows less access than the account really has.
      const ids = role === "gk2" ? templates.filter((t) => t.mode && t.mode === modeOf(id)).map((t) => t.id) : [id];
      const ofMode = ids.length ? ids : [id];
      return on ? Array.from(new Set([...prev, ...ofMode])) : prev.filter((x) => !ofMode.includes(x));
    });
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);

    if (role === "gk2" && modes.length === 0) {
      setError("Pick at least one template for this user to review.");
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
          ...(role === "gk2" ? { assigned_operator_ids: selectedOperatorIds } : {}),
        });
      } else {
        await usersApi.createUser({
          email, password, full_name: fullName, role: role as Role,
          ...(selectedCustom ? { user_type_id: selectedCustom.id } : {}),
          ...(role === "operator" || role === "gk2" ? { modes } : {}),
          ...(role === "operator" ? { template_ids: selectedTemplates } : {}),
          ...(role === "gk2" ? { assigned_operator_ids: selectedOperatorIds } : {}),
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

        {(role === "operator" || role === "gk2") && (
          <section className="rounded-xl border border-slate-200 bg-slate-50/60 p-3 dark:border-slate-700 dark:bg-slate-800/40">
            <div className="mb-2 flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                  {role === "gk2" ? "Templates this user reviews" : "Templates this user works on"}
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {role === "gk2"
                    ? "Jobs of these templates, waiting for Gate Keeper 2 approval, show in this user's queue."
                    : "Leave all unticked to allow every template."}
                </p>
              </div>
              {templates.length > 1 && (
                <div className="flex shrink-0 gap-2 text-xs font-semibold">
                  <button type="button" onClick={() => setSelectedTemplates(templates.map((t) => t.id))} className="text-indigo-600 hover:underline dark:text-indigo-400">Select all</button>
                  <button type="button" onClick={() => setSelectedTemplates([])} className="text-slate-500 hover:underline">Clear</button>
                </div>
              )}
            </div>
            {templates.length === 0 ? (
              <p className="text-xs text-slate-400">No approved templates yet — approve one under Data Transformation first.</p>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {templates.map((t) => {
                  const on = selectedTemplates.includes(t.id);
                  const air = (t.mode ?? "").toLowerCase().startsWith("air");
                  const Icon = air ? PlaneIcon : ShipIcon;
                  return (
                    <button key={t.id} type="button" onClick={() => toggleTemplate(t.id)} aria-pressed={on}
                      className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition ${on
                        ? "border-indigo-500 bg-white ring-2 ring-indigo-500/20 dark:bg-slate-900"
                        : "border-slate-200 bg-white hover:border-slate-300 dark:border-slate-700 dark:bg-slate-900"}`}>
                      <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${air
                        ? "bg-sky-50 text-sky-600 dark:bg-sky-500/10 dark:text-sky-300"
                        : "bg-teal-50 text-teal-600 dark:bg-teal-500/10 dark:text-teal-300"}`}>
                        <Icon className="h-4 w-4" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-slate-900 dark:text-slate-100">{t.name}</span>
                        <span className="block text-xs text-slate-500 dark:text-slate-400">{t.mode || "No shipment mode"}</span>
                      </span>
                      <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md border text-[11px] font-bold ${on
                        ? "border-indigo-600 bg-indigo-600 text-white" : "border-slate-300 text-transparent dark:border-slate-600"}`}>✓</span>
                    </button>
                  );
                })}
              </div>
            )}
            <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
              {role === "gk2"
                ? (modes.length ? <>Reviews: <b className="text-slate-700 dark:text-slate-200">{modes.join(", ")}</b></> : "Pick at least one.")
                : (modes.length ? <>Jobs page tabs: <b className="text-slate-700 dark:text-slate-200">{modes.join(", ")}</b></> : "Jobs page: every tab, every template.")}
            </p>
          </section>
        )}

        {role === "gk2" && (
          <div>
            <p className="mb-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
              Operators whose jobs this user reviews
            </p>
            <p className="mb-2 text-xs text-slate-400 dark:text-slate-500">
              Starts with every operator ticked. Untick someone to stop their jobs showing in
              this user's queue — you can change this anytime.
            </p>
            {operators.length === 0 ? (
              <p className="text-xs text-slate-400">No Gate Keeper 1 (operator) accounts yet.</p>
            ) : (
              <div className="flex max-h-40 flex-col gap-1.5 overflow-y-auto rounded-lg border border-slate-200 p-2 dark:border-slate-700">
                {operators.map((op) => (
                  <label key={op.id} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                    <input
                      type="checkbox"
                      checked={selectedOperatorIds.includes(op.id)}
                      onChange={() => toggleOperator(op.id)}
                    />
                    {op.full_name} <span className="text-xs text-slate-400">({op.email})</span>
                  </label>
                ))}
              </div>
            )}
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
