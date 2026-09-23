import { useEffect, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import * as pendingMailApi from "../../api/pendingMail";
import type { PendingEmail } from "../../api/pendingMail";
import * as jobsApi from "../../api/jobs";
import type { AvailableGroup } from "../../api/jobs";
import * as tenantsApi from "../../api/tenants";
import type { Tenant } from "../../types/tenant";

export function PendingMailPage() {
  const [rows, setRows] = useState<PendingEmail[]>([]);
  const [groups, setGroups] = useState<AvailableGroup[]>([]);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);

  async function refresh() {
    setError(null);
    try {
      const [mail, availGroups, tenantList] = await Promise.all([
        pendingMailApi.listPendingEmails(),
        jobsApi.listAvailableGroups(),
        tenantsApi.listTenants(),
      ]);
      setRows(mail);
      setGroups(availGroups);
      setTenants(tenantList);
    } catch {
      setError("Could not load pending mail.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const tenantName = (id: string) => tenants.find((t) => t.id === id)?.name ?? id;

  async function handleResolve(row: PendingEmail) {
    const groupId = picked[row.id];
    if (!groupId) return;
    setBusyId(row.id);
    setError(null);
    try {
      await pendingMailApi.resolvePendingEmail(row.id, groupId);
      await refresh();
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not assign that template.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleDismiss(row: PendingEmail) {
    if (!window.confirm(`Dismiss this mail from "${row.sender ?? "unknown sender"}"? Its attachments will be deleted.`)) return;
    setBusyId(row.id);
    setError(null);
    try {
      await pendingMailApi.dismissPendingEmail(row.id);
      await refresh();
    } catch {
      setError("Could not dismiss that mail.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <AppShell
      title="Unidentified Mail"
      subtitle="Documents the auto-router could not place on their own — pick which template each one belongs to."
    >
      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}

      {!loading && rows.length === 0 && (
        <Card className="p-8 text-center">
          <p className="text-sm text-slate-500">
            Nothing waiting. Mail the router can't place on its own — no customer matched, or
            more than one did — shows up here.
          </p>
        </Card>
      )}

      <div className="flex flex-col gap-4">
        {rows.map((row) => (
          <Card key={row.id} className="p-5">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="font-medium text-slate-900 dark:text-slate-100">
                  {row.subject || "(no subject)"}
                </p>
                <p className="text-xs text-slate-500">From {row.sender || "unknown"}</p>
                <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">{row.reason}</p>
                {row.evidence && (
                  <p className="mt-1 text-xs text-slate-400">{row.evidence}</p>
                )}
              </div>
              <span className="shrink-0 text-xs text-slate-400">
                {new Date(row.created_at).toLocaleString(undefined, {
                  day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
                })}
              </span>
            </div>

            {row.attachments.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {row.attachments.map((a) => (
                  <a
                    key={a.path}
                    href={pendingMailApi.pendingAttachmentUrl(row.id, a.path)}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:border-indigo-300 hover:text-indigo-700 dark:border-slate-700 dark:text-slate-300"
                  >
                    {a.name}
                  </a>
                ))}
              </div>
            )}

            <div className="mt-4 flex items-center gap-2 border-t border-slate-200 pt-4 dark:border-slate-800">
              <select
                value={picked[row.id] ?? ""}
                onChange={(e) => setPicked((p) => ({ ...p, [row.id]: e.target.value }))}
                className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900"
              >
                <option value="">Select the template this belongs to…</option>
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {tenantName(g.tenant_id)} — {g.name}
                  </option>
                ))}
              </select>
              <Button
                onClick={() => handleResolve(row)}
                disabled={!picked[row.id]}
                isLoading={busyId === row.id}
              >
                Assign &amp; create job
              </Button>
              <Button variant="secondary" onClick={() => handleDismiss(row)} isLoading={busyId === row.id}>
                Dismiss
              </Button>
            </div>
          </Card>
        ))}
      </div>
    </AppShell>
  );
}
