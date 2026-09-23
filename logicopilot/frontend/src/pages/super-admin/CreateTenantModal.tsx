import { useState, type FormEvent } from "react";
import axios from "axios";
import { Modal } from "../../components/ui/Modal";
import { Input } from "../../components/ui/Input";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import * as tenantsApi from "../../api/tenants";
import { MODES } from "../../types/onboarding";

export function CreateTenantModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [region, setRegion] = useState("");
  const [currency, setCurrency] = useState("");
  const [adminName, setAdminName] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [adminPassword, setAdminPassword] = useState("");
  const [allowedModes, setAllowedModes] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  function toggleMode(mode: string) {
    setAllowedModes((prev) => (prev.includes(mode) ? prev.filter((m) => m !== mode) : [...prev, mode]));
  }

  function reset() {
    setName("");
    setRegion("");
    setCurrency("");
    setAdminName("");
    setAdminEmail("");
    setAdminPassword("");
    setAllowedModes([]);
    setError(null);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await tenantsApi.createTenant({
        name,
        region: region || undefined,
        currency: currency || undefined,
        allowed_modes: allowedModes.length ? allowedModes : undefined,
        admin_full_name: adminName,
        admin_email: adminEmail,
        admin_password: adminPassword,
      });
      reset();
      onCreated();
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.detail ?? "Could not create tenant.");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title="New Tenant"
    >
      <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Company</p>
          <div className="flex flex-col gap-3">
            <Input label="Company name" value={name} onChange={(e) => setName(e.target.value)} placeholder="4S Logistics" required />
            <div className="grid grid-cols-2 gap-3">
              <Input label="Region" value={region} onChange={(e) => setRegion(e.target.value)} placeholder="IN" />
              <Input label="Currency" value={currency} onChange={(e) => setCurrency(e.target.value)} placeholder="INR" maxLength={3} />
            </div>
          </div>
        </div>

        <div className="border-t border-slate-200 pt-4 dark:border-slate-800">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
            Modes this client does
          </p>
          <p className="mb-2 text-xs text-slate-400 dark:text-slate-500">
            Leave all unchecked for no restriction. Once any are checked, template creation for
            this client only offers these.
          </p>
          <div className="grid grid-cols-2 gap-2">
            {MODES.map((mode) => (
              <label
                key={mode}
                className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 dark:border-slate-700 dark:text-slate-300"
              >
                <input
                  type="checkbox"
                  checked={allowedModes.includes(mode)}
                  onChange={() => toggleMode(mode)}
                  className="h-4 w-4 rounded border-slate-300"
                />
                {mode}
              </label>
            ))}
          </div>
        </div>

        <div className="border-t border-slate-200 pt-4 dark:border-slate-800">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Tenant Admin login</p>
          <div className="flex flex-col gap-3">
            <Input label="Admin full name" value={adminName} onChange={(e) => setAdminName(e.target.value)} placeholder="Jane Doe" required />
            <Input
              label="Admin email (username)"
              type="email"
              value={adminEmail}
              onChange={(e) => setAdminEmail(e.target.value)}
              placeholder="admin@4slogistics.com"
              required
            />
            <Input
              label="Temporary password"
              type="text"
              value={adminPassword}
              onChange={(e) => setAdminPassword(e.target.value)}
              placeholder="At least 8 characters"
              minLength={8}
              required
            />
          </div>
          <p className="mt-2 text-xs text-slate-400 dark:text-slate-500">
            Share this with the tenant admin directly — they'll log in with the email + password.
          </p>
        </div>

        {error && <Alert>{error}</Alert>}
        <Button type="submit" isLoading={isSubmitting} className="mt-1">
          Create tenant &amp; admin
        </Button>
      </form>
    </Modal>
  );
}
