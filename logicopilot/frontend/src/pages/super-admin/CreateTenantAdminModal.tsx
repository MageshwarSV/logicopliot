import { useEffect, useState, type FormEvent } from "react";
import axios from "axios";
import { Modal } from "../../components/ui/Modal";
import { Input, Select } from "../../components/ui/Input";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import * as usersApi from "../../api/users";
import * as onboardingApi from "../../api/onboarding";
import type { Tenant } from "../../types/tenant";
import type { User } from "../../types/auth";
import type { TemplateGroup } from "../../types/onboarding";

export function CreateTenantAdminModal({
  open,
  onClose,
  onCreated,
  tenants,
  editUser = null,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
  tenants: Tenant[];
  editUser?: User | null;
}) {
  const isEdit = !!editUser;
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenantId, setTenantId] = useState(tenants[0]?.id ?? "");
  const [templates, setTemplates] = useState<TemplateGroup[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Prime fields when (re)opening.
  useEffect(() => {
    if (!open) return;
    if (editUser) {
      setFullName(editUser.full_name);
      setEmail(editUser.email);
      setTenantId(editUser.tenant_id ?? "");
      setPassword("");
      usersApi.getUserTemplates(editUser.id).then(setSelected).catch(() => setSelected([]));
    } else {
      setFullName("");
      setEmail("");
      setPassword("");
      setTenantId(tenants[0]?.id ?? "");
      setSelected([]);
    }
    setError(null);
  }, [open, editUser, tenants]);

  // Load the chosen tenant's templates for assignment.
  useEffect(() => {
    if (!open || !tenantId) return;
    onboardingApi.listGroups(tenantId).then(setTemplates).catch(() => setTemplates([]));
  }, [open, tenantId]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      if (isEdit && editUser) {
        await usersApi.updateUser(editUser.id, {
          full_name: fullName,
          template_ids: selected,
          ...(email.trim() && email.trim() !== editUser.email ? { email: email.trim() } : {}),
          ...(password ? { password } : {}),
        });
      } else {
        await usersApi.createUser({
          email,
          password,
          full_name: fullName,
          role: "tenant_admin",
          tenant_id: tenantId,
          template_ids: selected,
        });
      }
      onCreated();
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.detail ?? "Could not save tenant admin.");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={isEdit ? "Edit Tenant Admin" : "New Tenant Admin"}>
      <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
        <Select label="Tenant" value={tenantId} onChange={(e) => setTenantId(e.target.value)} disabled={isEdit} required>
          {tenants.map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </Select>
        <Input label="Full name" value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Jane Doe" required />
        <Input label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="admin@company.com" required />
        <Input
          label={isEdit ? "New password (leave blank to keep current)" : "Temporary password"}
          type="text"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={isEdit ? "Leave blank to keep" : "At least 8 characters"}
          minLength={isEdit ? undefined : 8}
          required={!isEdit}
        />

        <div>
          <p className="mb-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">Assign templates</p>
          {templates.length === 0 ? (
            <p className="text-xs text-slate-400">No templates for this tenant yet.</p>
          ) : (
            <div className="flex max-h-40 flex-col gap-1.5 overflow-y-auto rounded-lg border border-slate-200 p-2 dark:border-slate-700">
              {templates.map((t) => (
                <label key={t.id} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                  <input
                    type="checkbox"
                    checked={selected.includes(t.id)}
                    onChange={(e) => setSelected((arr) => (e.target.checked ? [...arr, t.id] : arr.filter((x) => x !== t.id)))}
                  />
                  {t.name} <span className="text-xs text-slate-400">({t.status})</span>
                </label>
              ))}
            </div>
          )}
          <p className="mt-1 text-xs text-slate-400">Leave all unchecked to give access to every template in this tenant.</p>
        </div>

        {error && <Alert>{error}</Alert>}
        <Button type="submit" isLoading={isSubmitting} className="mt-1">
          {isEdit ? "Save changes" : "Create tenant admin"}
        </Button>
      </form>
    </Modal>
  );
}
