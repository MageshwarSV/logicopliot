import { useState, type FormEvent } from "react";
import axios from "axios";
import { Modal } from "./ui/Modal";
import { Input } from "./ui/Input";
import { Button } from "./ui/Button";
import { Alert } from "./ui/Alert";
import * as authApi from "../api/auth";
import { useAuth } from "../auth/useAuth";

/** Shared by two callers: ProtectedRoute's forced popup (an admin-chosen password still on the
 * account - see User.must_change_password) and a voluntary change from Settings. Both are the
 * same action (prove you know the current password, then pick your own), so there is one form.
 *
 * `forced` controls only presentation (title/copy and whether the X/backdrop can dismiss it) -
 * the backend endpoint and success handling are identical either way. */
export function ChangePasswordModal({
  open,
  onClose,
  forced = false,
}: {
  open: boolean;
  onClose: () => void;
  forced?: boolean;
}) {
  const { setUser } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reset = () => {
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setError(null);
  };

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (newPassword.length < 8) {
      setError("New password must be at least 8 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("New password and confirmation do not match.");
      return;
    }
    setBusy(true);
    try {
      const updated = await authApi.changePassword(currentPassword, newPassword);
      setUser(updated);
      reset();
      onClose();
    } catch (err) {
      setError(
        axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not change password." : "Could not change password.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title={forced ? "Set a permanent password" : "Change password"}
      dismissible={!forced}
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {forced && (
          <p className="text-sm text-slate-600 dark:text-slate-300">
            The password on this account was set by whoever created it. Pick one only you know
            before continuing.
          </p>
        )}
        <Input
          label={forced ? "Temporary password" : "Current password"}
          type="password"
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
        <Input
          label="New password"
          type="password"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          placeholder="At least 8 characters"
          minLength={8}
          autoComplete="new-password"
          required
        />
        <Input
          label="Confirm new password"
          type="password"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          autoComplete="new-password"
          required
        />
        {error && <Alert>{error}</Alert>}
        <div className="flex items-center justify-end gap-3 pt-1">
          {!forced && (
            <button
              type="button"
              onClick={() => {
                reset();
                onClose();
              }}
              className="text-sm text-slate-500 hover:underline dark:text-slate-400"
            >
              Cancel
            </button>
          )}
          <Button type="submit" isLoading={busy}>
            Set password
          </Button>
        </div>
      </form>
    </Modal>
  );
}
