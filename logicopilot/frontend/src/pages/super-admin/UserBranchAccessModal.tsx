import { useEffect, useState } from "react";
import axios from "axios";
import { Modal } from "../../components/ui/Modal";
import { Button } from "../../components/ui/Button";
import * as api from "../../api/erpBranchSessions";
import type { User } from "../../types/auth";

/** Super Admin: which ERP branch logins this user may turn on and off. Ticked logins show up in
 *  the user's own top-bar "ERP Branch" button - see components/ErpBranchButton.tsx. */
export function UserBranchAccessModal({ user, onClose }: { user: User | null; onClose: () => void }) {
  const [access, setAccess] = useState<api.UserBranchAccess | null>(null);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!user) return;
    setAccess(null);
    setError(null);
    setSaved(false);
    setLoading(true);
    api.getUserBranchAccess(user.id)
      .then((a) => { setAccess(a); setTicked(new Set(a.branch_session_ids)); })
      .catch((err) => setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not load." : "Could not load."))
      .finally(() => setLoading(false));
  }, [user]);

  function toggle(id: string) {
    setSaved(false);
    setTicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function save() {
    if (!user) return;
    setSaving(true);
    setError(null);
    try {
      const a = await api.setUserBranchAccess(user.id, [...ticked]);
      setAccess(a);
      setTicked(new Set(a.branch_session_ids));
      setSaved(true);
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not save." : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={user !== null} onClose={onClose} title={user ? `ERP Branch — ${user.full_name}` : "ERP Branch"} maxWidth="max-w-md">
      <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">
        Tick the ERP branch logins this user may turn on and off. They get an <b>ERP Branch</b> button
        in their top bar with only these logins.
      </p>
      {error && (
        <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}
      {loading && <p className="text-sm text-slate-400">Loading…</p>}
      {access && access.branches.length === 0 && (
        <p className="text-sm text-slate-400">This company has no ERP branch logins yet (ERP Scripts → Branch logins).</p>
      )}
      {access && access.branches.length > 0 && (
        <div className="flex flex-col gap-2">
          {access.branches.map((b) => (
            <label
              key={b.id}
              className="flex cursor-pointer items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
            >
              <span className="flex items-center gap-3">
                <input type="checkbox" checked={ticked.has(b.id)} onChange={() => toggle(b.id)} className="h-4 w-4" />
                <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{b.branch}</span>
              </span>
              <span className={`text-xs ${b.enabled ? "text-emerald-600 dark:text-emerald-400" : "text-slate-400"}`}>
                {b.enabled ? (b.status === "live" ? "Live" : b.status) : "Off"}
              </span>
            </label>
          ))}
        </div>
      )}
      <div className="mt-4 flex items-center justify-end gap-2">
        {saved && <span className="mr-auto text-xs text-emerald-600 dark:text-emerald-400">Saved</span>}
        <Button variant="ghost" onClick={onClose}>Close</Button>
        <Button onClick={save} isLoading={saving} disabled={!access || loading}>Save</Button>
      </div>
    </Modal>
  );
}
