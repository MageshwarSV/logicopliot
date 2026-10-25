import { useEffect, useState, type FormEvent } from "react";
import axios from "axios";
import { Modal } from "../../components/ui/Modal";
import { Input } from "../../components/ui/Input";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import * as usersApi from "../../api/users";
import type { User } from "../../types/auth";

/** SaaS Finance, like Admin, is cross-tenant - no tenant selector and no template
 *  assignment, both meaningless for this role (same reasoning CreateTenantAdminModal.tsx's
 *  own fields exist for tenant_admin, inverted). */
export function CreateFinanceUserModal({
  open,
  onClose,
  onCreated,
  editUser = null,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
  editUser?: User | null;
}) {
  const isEdit = !!editUser;
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (editUser) {
      setFullName(editUser.full_name);
      setEmail(editUser.email);
      setPassword("");
    } else {
      setFullName("");
      setEmail("");
      setPassword("");
    }
    setError(null);
  }, [open, editUser]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      if (isEdit && editUser) {
        await usersApi.updateUser(editUser.id, {
          full_name: fullName,
          ...(email.trim() && email.trim() !== editUser.email ? { email: email.trim() } : {}),
          ...(password ? { password } : {}),
        });
      } else {
        await usersApi.createUser({
          email, password, full_name: fullName, role: "saas_finance",
        });
      }
      onCreated();
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.detail ?? "Could not save this Finance user.");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={isEdit ? "Edit Finance User" : "New Finance User"}>
      <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
        <Input label="Full name" value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Jane Doe" required />
        <Input label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="finance@company.com" required />
        <Input
          label={isEdit ? "New password (leave blank to keep current)" : "Temporary password"}
          type="text"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={isEdit ? "Leave blank to keep" : "At least 8 characters"}
          minLength={isEdit ? undefined : 8}
          required={!isEdit}
        />
        {error && <Alert>{error}</Alert>}
        <Button type="submit" isLoading={isSubmitting} className="mt-1">
          {isEdit ? "Save changes" : "Create Finance user"}
        </Button>
      </form>
    </Modal>
  );
}
