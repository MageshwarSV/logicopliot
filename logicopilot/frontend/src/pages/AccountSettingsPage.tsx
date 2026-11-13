import { useEffect, useRef, useState, type FormEvent } from "react";
import axios from "axios";
import { AppShell } from "../components/AppShell";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { Alert } from "../components/ui/Alert";
import { Avatar } from "../components/ui/Avatar";
import { RoleBadge } from "../components/ui/Badge";
import * as authApi from "../api/auth";
import { useAuth } from "../auth/useAuth";

const MAX_PHOTO_BYTES = 2 * 1024 * 1024;
const PHOTO_TYPES = ["image/png", "image/jpeg", "image/webp"];

function errorText(err: unknown, fallback: string): string {
  return axios.isAxiosError(err) ? err.response?.data?.detail ?? fallback : fallback;
}

/** Every web role's own account page ("Profile" in the sidebar's user menu): photo, name and
 * phone, all self-service. Email and role are shown read-only - they are what an admin issued
 * the account with. The password is changed from the user menu's own "Change Password" item,
 * not here. Super Admin's Settings page (SettingsPage.tsx) is unrelated system-wide config. */
export function AccountSettingsPage() {
  const { user, setUser } = useAuth();
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [phone, setPhone] = useState(user?.phone ?? "");
  const [saving, setSaving] = useState(false);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Pick up the saved values once the signed-in user arrives (or changes after a save).
  useEffect(() => {
    setFullName(user?.full_name ?? "");
    setPhone(user?.phone ?? "");
  }, [user?.full_name, user?.phone]);

  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), 3000);
    return () => window.clearTimeout(t);
  }, [notice]);

  const dirty =
    fullName.trim() !== (user?.full_name ?? "") || phone.trim() !== (user?.phone ?? "");

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!fullName.trim()) {
      setError("Name cannot be empty.");
      return;
    }
    setSaving(true);
    try {
      const updated = await authApi.updateProfile({ full_name: fullName.trim(), phone: phone.trim() });
      setUser(updated);
      setNotice("Profile updated.");
    } catch (err) {
      setError(errorText(err, "Could not save your profile."));
    } finally {
      setSaving(false);
    }
  }

  async function handlePhoto(file: File | undefined) {
    if (fileRef.current) fileRef.current.value = "";
    if (!file) return;
    setError(null);
    if (!PHOTO_TYPES.includes(file.type)) {
      setError("Choose a PNG, JPG or WEBP image.");
      return;
    }
    if (file.size > MAX_PHOTO_BYTES) {
      setError("The photo must be 2 MB or smaller.");
      return;
    }
    setPhotoBusy(true);
    try {
      setUser(await authApi.uploadAvatar(file));
      setNotice("Photo updated.");
    } catch (err) {
      setError(errorText(err, "Could not upload the photo."));
    } finally {
      setPhotoBusy(false);
    }
  }

  async function handleRemovePhoto() {
    setError(null);
    setPhotoBusy(true);
    try {
      setUser(await authApi.deleteAvatar());
      setNotice("Photo removed.");
    } catch (err) {
      setError(errorText(err, "Could not remove the photo."));
    } finally {
      setPhotoBusy(false);
    }
  }

  return (
    <AppShell title="Profile" subtitle="Manage your personal information and how you appear to your team.">
      <div className="max-w-3xl space-y-6">
        {error && <Alert>{error}</Alert>}
        {notice && (
          <div
            role="status"
            className="rounded-lg border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-sm text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300"
          >
            {notice}
          </div>
        )}

        <Card className="p-6">
          <div className="flex flex-col items-center gap-6 sm:flex-row sm:items-center">
            <div className="relative">
              <Avatar name={user?.full_name} url={user?.avatar_url} size="lg" />
              {photoBusy && (
                <div className="absolute inset-0 flex items-center justify-center rounded-full bg-white/70 text-xs font-medium text-slate-600 dark:bg-slate-900/70 dark:text-slate-300">
                  Saving…
                </div>
              )}
            </div>
            <div className="min-w-0 flex-1 text-center sm:text-left">
              <h2 className="truncate text-xl font-semibold text-slate-900 dark:text-slate-50">{user?.full_name}</h2>
              <p className="truncate text-sm text-slate-500 dark:text-slate-400">{user?.email}</p>
              {user && <div className="mt-2"><RoleBadge role={user.role} /></div>}
            </div>
            <div className="flex flex-col items-center gap-2 sm:items-end">
              <div className="flex gap-2">
                <Button size="sm" variant="secondary" disabled={photoBusy} onClick={() => fileRef.current?.click()}>
                  {user?.avatar_url ? "Change photo" : "Upload photo"}
                </Button>
                {user?.avatar_url && (
                  <Button size="sm" variant="ghost" disabled={photoBusy} onClick={handleRemovePhoto}>
                    Remove
                  </Button>
                )}
              </div>
              <p className="text-xs text-slate-400">PNG, JPG or WEBP · up to 2 MB</p>
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="hidden"
                onChange={(e) => handlePhoto(e.target.files?.[0])}
              />
            </div>
          </div>
        </Card>

        <Card>
          <form onSubmit={handleSave}>
            <div className="border-b border-slate-200 px-6 py-4 dark:border-slate-800">
              <h3 className="font-semibold text-slate-900 dark:text-slate-50">Personal information</h3>
              <p className="text-sm text-slate-500 dark:text-slate-400">Your name and phone number are visible to your team.</p>
            </div>
            <div className="grid grid-cols-1 gap-5 px-6 py-5 sm:grid-cols-2">
              <Input
                label="Full name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                autoComplete="name"
                maxLength={255}
                required
              />
              <Input
                label="Phone number"
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+91 98400 12345"
                autoComplete="tel"
                maxLength={30}
              />
              <div className="flex flex-col gap-1.5">
                <Input label="Email address" value={user?.email ?? ""} disabled readOnly />
                <p className="text-xs text-slate-400">Managed by your administrator.</p>
              </div>
              <div className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-slate-700 dark:text-slate-300">Role</span>
                <div className="flex h-[42px] items-center">{user && <RoleBadge role={user.role} />}</div>
              </div>
            </div>
            <div className="flex justify-end gap-3 border-t border-slate-200 px-6 py-4 dark:border-slate-800">
              <Button
                type="button"
                variant="ghost"
                disabled={!dirty || saving}
                onClick={() => {
                  setFullName(user?.full_name ?? "");
                  setPhone(user?.phone ?? "");
                  setError(null);
                }}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={!dirty} isLoading={saving}>
                Save changes
              </Button>
            </div>
          </form>
        </Card>
      </div>
    </AppShell>
  );
}
