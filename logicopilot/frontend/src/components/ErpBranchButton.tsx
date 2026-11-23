import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { Modal } from "./ui/Modal";
import { Button } from "./ui/Button";
import * as api from "../api/erpBranchSessions";

/** Top-bar "ERP Branch" button for a user a Super Admin ticked ERP branch logins for
 *  (Tenants -> Users -> ERP Branch). Shown only when there is at least one such login.
 *  Turning a login off stops the always-on ERP session for that branch, so the ERP user can be
 *  used by hand; turning it on logs it back in. */
export function ErpBranchButton() {
  const [items, setItems] = useState<api.BranchSwitch[]>([]);
  const [open, setOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await api.myBranchSwitches());
    } catch {
      // No access / not available: the button simply does not show.
      setItems([]);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // While the popup is open, follow the login coming up / going down (the server picks a
  // change up within ~10 seconds).
  useEffect(() => {
    if (!open) return;
    const t = window.setInterval(() => { void load(); }, 5000);
    return () => window.clearInterval(t);
  }, [open, load]);

  async function setEnabled(b: api.BranchSwitch, enabled: boolean) {
    setBusyId(b.id);
    setError(null);
    try {
      const updated = await api.switchBranchLogin(b.id, enabled);
      setItems((prev) => prev.map((x) => (x.id === updated.id ? updated : x)));
      setConfirmId(null);
    } catch (err) {
      setError(axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not change it." : "Could not change it.");
    } finally {
      setBusyId(null);
    }
  }

  if (items.length === 0) return null;
  const anyOff = items.some((b) => !b.enabled);

  return (
    <>
      <button
        type="button"
        onClick={() => { setOpen(true); void load(); }}
        className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
        title={anyOff ? "An ERP branch login is turned off" : "ERP branch logins"}
      >
        <span className={`h-2 w-2 rounded-full ${anyOff ? "bg-amber-500" : "bg-emerald-500"}`} />
        ERP Branch
      </button>

      <Modal open={open} onClose={() => { setOpen(false); setConfirmId(null); }} title="ERP Branch logins" maxWidth="max-w-md">
        <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">
          Turn a branch login <b>off</b> before using that ERP user yourself, and <b>on</b> again when you are done.
        </p>
        {error && (
          <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
            {error}
          </div>
        )}
        <div className="flex flex-col gap-2">
          {items.map((b) => {
            const state = !b.enabled
              ? { label: "Off", cls: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400" }
              : b.status === "live"
                ? { label: "Live", cls: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" }
                : b.status === "error"
                  ? { label: "Error", cls: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300" }
                  : { label: "Logging in…", cls: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300" };
            return (
              <div key={b.id} className="rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-800">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-900 dark:text-slate-100">{b.branch}</span>
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${state.cls}`}>{state.label}</span>
                  </div>
                  {b.enabled ? (
                    confirmId === b.id ? (
                      <div className="flex items-center gap-2">
                        <Button size="sm" variant="danger" isLoading={busyId === b.id} onClick={() => setEnabled(b, false)}>Turn off</Button>
                        <Button size="sm" variant="ghost" onClick={() => setConfirmId(null)}>Cancel</Button>
                      </div>
                    ) : (
                      <Button size="sm" variant="secondary" onClick={() => setConfirmId(b.id)}>Turn off</Button>
                    )
                  ) : (
                    <Button size="sm" isLoading={busyId === b.id} onClick={() => setEnabled(b, true)}>Turn on</Button>
                  )}
                </div>
                {confirmId === b.id && (
                  <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
                    While {b.branch} is off, its ERP entries cannot run - new {b.branch} jobs sent to the ERP will fail
                    until it is turned on again.
                  </p>
                )}
                {b.enabled && b.status === "error" && b.status_note && (
                  <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{b.status_note}</p>
                )}
              </div>
            );
          })}
        </div>
      </Modal>
    </>
  );
}
