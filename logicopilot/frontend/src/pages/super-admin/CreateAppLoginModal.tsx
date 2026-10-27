import { useEffect, useMemo, useState, type FormEvent } from "react";
import axios from "axios";
import { Modal } from "../../components/ui/Modal";
import { Input } from "../../components/ui/Input";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import * as usersApi from "../../api/users";
import { MODES } from "../../types/onboarding";
import type { Tenant } from "../../types/tenant";
import type { User } from "../../types/auth";

/**
 * Issues a login for the Cargora Manager mobile app.
 *
 * Super Admin only, and created from inside the company it belongs to (the tenant's own
 * "Users" view) rather than from a generic top-level button - the same place and the same
 * reasoning as "+ New Tenant Admin" next to it.
 *
 * Deliberately NOT part of the Tenant Admin's own operator/GK2/manager screens: these are
 * issued per customer as part of selling the app, not by a customer to their own staff.
 *
 * The shipment types picked here are the login's HARD limit, enforced server-side on every
 * request. The phone can narrow them further from its own settings; it can never widen them.
 * That is why one company takes several of these - one for Sea Import, one for Air Import -
 * rather than one login that sees everything.
 */
export function CreateAppLoginModal({
  open,
  onClose,
  onCreated,
  tenant,
  editUser = null,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
  tenant: Tenant;
  editUser?: User | null;
}) {
  const isEdit = !!editUser;
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [modes, setModes] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  /** Only the shipment types this COMPANY is licensed for. Offering a mode the tenant does
   *  not have would create a login that is allowed to watch something that can never exist,
   *  which looks to the manager like an app showing permanent zeroes. A tenant with no
   *  restriction set (null/empty means unrestricted, unlike a user's own modes) gets the
   *  full list. */
  const available = useMemo(() => {
    const licensed = tenant.allowed_modes;
    if (!licensed || licensed.length === 0) return [...MODES] as string[];
    return (MODES as readonly string[]).filter((m) => licensed.includes(m));
  }, [tenant]);

  useEffect(() => {
    if (!open) return;
    if (editUser) {
      setFullName(editUser.full_name);
      setEmail(editUser.email);
      setPassword("");
      // Their current modes, dropping any the tenant has since lost its licence for.
      setModes((editUser.modes ?? []).filter((m) => available.includes(m)));
    } else {
      setFullName("");
      setEmail("");
      setPassword("");
      // Nothing pre-ticked: which shipments this login watches is the whole point of the
      // screen, and a sensible-looking default is how every login ends up seeing everything.
      setModes([]);
    }
    setError(null);
  }, [open, editUser, available]);

  function toggleMode(mode: string) {
    setModes((current) =>
      current.includes(mode) ? current.filter((m) => m !== mode) : [...current, mode],
    );
  }

  const allSelected = modes.length === available.length && available.length > 0;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);

    // Checked here as well as server-side so the Super Admin is told before the round trip.
    // An app login with no modes can sign in and then see nothing at all, with no hint why.
    if (modes.length === 0) {
      setError("Pick at least one shipment type for this login to watch.");
      return;
    }

    setIsSubmitting(true);
    try {
      if (isEdit && editUser) {
        await usersApi.updateUser(editUser.id, {
          full_name: fullName,
          modes,
          ...(email.trim() && email.trim() !== editUser.email ? { email: email.trim() } : {}),
          ...(password ? { password } : {}),
        });
      } else {
        await usersApi.createUser({
          email: email.trim(),
          password,
          full_name: fullName,
          role: "app_manager",
          tenant_id: tenant.id,
          modes,
        });
      }
      onCreated();
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.detail ?? "Could not save this app login.");
      } else {
        setError("Could not save this app login.");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? `Edit app login — ${tenant.name}` : `New app login — ${tenant.name}`}
      maxWidth="max-w-lg"
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          A sign-in for the Cargora Manager mobile app. It is view-only — it cannot approve,
          edit or submit anything — and it never signs in to this website.
        </p>

        {error && <Alert>{error}</Alert>}

        <Input
          label="Name"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          placeholder="Sea Import Manager"
          required
        />
        <Input
          label="Email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="seaimport.manager@company.com"
          required
          autoComplete="off"
        />
        <Input
          label={isEdit ? "New password (leave blank to keep the current one)" : "Password"}
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required={!isEdit}
          autoComplete="new-password"
          minLength={isEdit ? undefined : 8}
        />

        <div>
          <div className="mb-2 flex items-center justify-between">
            <label className="text-sm font-medium text-slate-700 dark:text-slate-200">
              Shipment types this login may watch
            </label>
            {available.length > 1 && (
              <button
                type="button"
                onClick={() => setModes(allSelected ? [] : [...available])}
                className="text-xs font-medium text-indigo-600 hover:text-indigo-700 dark:text-indigo-400"
              >
                {allSelected ? "Clear all" : "Select all"}
              </button>
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            {available.map((mode) => {
              const on = modes.includes(mode);
              return (
                <button
                  key={mode}
                  type="button"
                  onClick={() => toggleMode(mode)}
                  className={`rounded-full border px-3 py-1.5 text-sm font-medium transition ${
                    on
                      ? "border-indigo-600 bg-indigo-600 text-white"
                      : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                  }`}
                >
                  {on && <span className="mr-1">✓</span>}
                  {mode}
                </button>
              );
            })}
          </div>

          {available.length === 0 && (
            <p className="mt-2 text-sm text-amber-700 dark:text-amber-400">
              This company has no shipment types licensed yet, so there is nothing for an app
              login to watch. Set its shipment types first, on the tenant itself.
            </p>
          )}

          <p className="mt-2 text-xs text-slate-400">
            This is a hard limit, checked on every request. The person holding the phone can
            narrow it further in the app, but never widen it — so give one company a separate
            login per desk (Sea Import, Air Import) rather than one that sees everything.
          </p>
        </div>

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" isLoading={isSubmitting} disabled={available.length === 0}>
            {isEdit ? "Save changes" : "Create app login"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
