import { useState } from "react";
import { AppShell } from "../components/AppShell";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { ChangePasswordModal } from "../components/ChangePasswordModal";
import { useAuth } from "../auth/useAuth";

/** Shared across Tenant Admin, Gate Keeper 1 (operator), Gate Keeper 2, and SaaS Finance -
 * the one thing every one of these roles can do for their own account today is change their
 * own password. Super Admin's own Settings page (SettingsPage.tsx) is unrelated system-wide
 * config, not an account page, and manager has no page here yet - add one if asked. */
export function AccountSettingsPage() {
  const { user } = useAuth();
  const [changePasswordOpen, setChangePasswordOpen] = useState(false);

  return (
    <AppShell title="Settings" subtitle="Your own account">
      <div className="max-w-xl space-y-4">
        <Card className="p-5">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">Account</h2>
          <dl className="mt-3 space-y-1 text-sm">
            <div className="flex justify-between gap-4">
              <dt className="text-slate-500 dark:text-slate-400">Name</dt>
              <dd className="text-slate-900 dark:text-slate-100">{user?.full_name}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-slate-500 dark:text-slate-400">Email</dt>
              <dd className="text-slate-900 dark:text-slate-100">{user?.email}</dd>
            </div>
          </dl>
        </Card>

        <Card className="p-5">
          <h2 className="font-semibold text-slate-900 dark:text-slate-50">Password</h2>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Set a new password for your own account at any time.
          </p>
          <Button className="mt-3" variant="secondary" onClick={() => setChangePasswordOpen(true)}>
            Change password
          </Button>
        </Card>
      </div>

      <ChangePasswordModal open={changePasswordOpen} onClose={() => setChangePasswordOpen(false)} />
    </AppShell>
  );
}
