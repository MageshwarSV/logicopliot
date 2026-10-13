import { useEffect, useMemo, useState, type FormEvent } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Modal } from "../../components/ui/Modal";
import { Alert } from "../../components/ui/Alert";
import { Toggle } from "../../components/ui/Toggle";
import { DataTable, type Column } from "../../components/ui/DataTable";
import * as emailPullerApi from "../../api/emailPullerMailboxes";
import * as jobsApi from "../../api/jobs";
import * as tenantsApi from "../../api/tenants";
import type { MailProvider } from "../../types/auth";
import type { Tenant } from "../../types/tenant";

const PROVIDER_LABEL: Record<string, string> = { gmail: "Google", zoho: "Zoho" };

/** Connect any mailbox (Gmail/Zoho), independent of any operator user - it lands
 *  Unassigned until a Tenant and Template are picked here, each one independently and
 *  whenever ready. This is now the ONLY place a mailbox gets connected - the Tenant Admin's
 *  own "New User" form no longer has a mailbox card. */
export function EmailPullerPage() {
  const [mailboxes, setMailboxes] = useState<emailPullerApi.ConnectedMailbox[]>([]);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [groups, setGroups] = useState<jobsApi.AvailableGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyRowId, setBusyRowId] = useState<string | null>(null);

  const [connectOpen, setConnectOpen] = useState(false);
  const [connectEmail, setConnectEmail] = useState("");
  const [connectPassword, setConnectPassword] = useState("");
  const [connectProvider, setConnectProvider] = useState<MailProvider | "">("");
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);

  const [disconnectTarget, setDisconnectTarget] = useState<emailPullerApi.ConnectedMailbox | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);

  async function load() {
    try {
      const [m, t, g] = await Promise.all([
        emailPullerApi.listMailboxes(),
        tenantsApi.listTenants(),
        jobsApi.listAvailableGroups(),
      ]);
      setMailboxes(m);
      setTenants(t);
      setGroups(g);
    } catch {
      setError("Failed to load mailboxes. Is the backend running?");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const groupsByTenant = useMemo(() => {
    const map = new Map<string, jobsApi.AvailableGroup[]>();
    for (const g of groups) {
      const arr = map.get(g.tenant_id) ?? [];
      arr.push(g);
      map.set(g.tenant_id, arr);
    }
    return map;
  }, [groups]);

  async function handleConnect(event: FormEvent) {
    event.preventDefault();
    setConnectError(null);
    setConnecting(true);
    try {
      const row = await emailPullerApi.connectMailbox({
        email: connectEmail.trim(),
        app_password: connectPassword,
        ...(connectProvider ? { provider: connectProvider } : {}),
      });
      setMailboxes((prev) => [...prev, row]);
      setConnectOpen(false);
      setConnectEmail("");
      setConnectPassword("");
      setConnectProvider("");
    } catch (err) {
      setConnectError(
        axios.isAxiosError(err) ? err.response?.data?.detail ?? "Could not connect this mailbox." : "Could not connect this mailbox.",
      );
    } finally {
      setConnecting(false);
    }
  }

  async function setTenant(row: emailPullerApi.ConnectedMailbox, tenantId: string) {
    setBusyRowId(row.id);
    setError(null);
    try {
      const updated = await emailPullerApi.updateMailbox(row.id, { tenant_id: tenantId || null });
      setMailboxes((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
    } catch {
      setError(`Could not update the tenant for ${row.email}.`);
    } finally {
      setBusyRowId(null);
    }
  }

  async function setGroup(row: emailPullerApi.ConnectedMailbox, groupId: string) {
    setBusyRowId(row.id);
    setError(null);
    try {
      const updated = await emailPullerApi.updateMailbox(row.id, { group_id: groupId || null });
      setMailboxes((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
    } catch {
      setError(`Could not update the template for ${row.email}.`);
    } finally {
      setBusyRowId(null);
    }
  }

  async function togglePaused(row: emailPullerApi.ConnectedMailbox) {
    setBusyRowId(row.id);
    setError(null);
    try {
      const updated = await emailPullerApi.updateMailbox(row.id, { paused: !row.paused });
      setMailboxes((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
    } catch {
      setError(`Could not update ${row.email}.`);
    } finally {
      setBusyRowId(null);
    }
  }

  async function confirmDisconnect() {
    if (!disconnectTarget) return;
    setDisconnecting(true);
    try {
      await emailPullerApi.disconnectMailbox(disconnectTarget.id);
      setMailboxes((prev) => prev.filter((m) => m.id !== disconnectTarget.id));
      setDisconnectTarget(null);
    } catch {
      setError(`Could not disconnect ${disconnectTarget.email}.`);
    } finally {
      setDisconnecting(false);
    }
  }

  const columns: Column<emailPullerApi.ConnectedMailbox>[] = [
    {
      header: "Mailbox",
      render: (row) => (
        <div className="min-w-0">
          <p className="truncate text-slate-900 dark:text-slate-100">{row.email}</p>
          <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-600 dark:bg-slate-700 dark:text-slate-300">
            {PROVIDER_LABEL[row.provider] ?? row.provider}
          </span>
        </div>
      ),
    },
    {
      header: "Tenant",
      render: (row) => (
        <select
          value={row.tenant_id ?? ""}
          disabled={busyRowId === row.id}
          onChange={(e) => setTenant(row, e.target.value)}
          className="w-44 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-900 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
        >
          <option value="">Unassigned</option>
          {tenants.map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>
      ),
    },
    {
      header: "Template",
      render: (row) => {
        const options = row.tenant_id ? groupsByTenant.get(row.tenant_id) ?? [] : [];
        return (
          <select
            value={row.group_id ?? ""}
            disabled={busyRowId === row.id || !row.tenant_id}
            onChange={(e) => setGroup(row, e.target.value)}
            className="w-44 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-900 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/40 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
          >
            <option value="">{row.tenant_id ? "Any (needs content to identify)" : "Pick a tenant first"}</option>
            {options.map((g) => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </select>
        );
      },
    },
    {
      header: "Status",
      render: (row) =>
        row.tenant_id ? (
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500 dark:text-slate-400">{row.paused ? "Off" : "On"}</span>
            <Toggle
              checked={!row.paused}
              disabled={busyRowId === row.id}
              onChange={() => togglePaused(row)}
              label={`Poll ${row.email}`}
            />
          </div>
        ) : (
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
            Unassigned
          </span>
        ),
    },
    {
      header: "",
      render: (row) => (
        <Button size="sm" variant="ghost" onClick={() => setDisconnectTarget(row)}>
          Disconnect
        </Button>
      ),
    },
  ];

  return (
    <AppShell
      title="Email Puller"
      subtitle="Connect any mailbox, then assign it a Tenant and a Template — independently of any operator."
      actions={<Button onClick={() => setConnectOpen(true)}>+ Connect mailbox</Button>}
    >
      <Card>
        {loading && <p className="px-5 py-6 text-sm text-slate-500 dark:text-slate-400">Loading…</p>}
        {!loading && (
          <DataTable
            columns={columns}
            rows={mailboxes}
            keyFor={(row) => row.id}
            emptyMessage="No mailbox connected yet — press + Connect mailbox to add one."
          />
        )}
        {error && (
          <p className="mx-5 mb-4 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
            {error}
          </p>
        )}
      </Card>

      <Modal open={connectOpen} onClose={() => setConnectOpen(false)} title="Connect mailbox">
        <form className="flex flex-col gap-4" onSubmit={handleConnect}>
          <Input
            label="Mailbox email"
            type="email"
            value={connectEmail}
            onChange={(e) => setConnectEmail(e.target.value)}
            placeholder="accounts@company.com"
            required
          />
          <Input
            label="App password"
            type="text"
            value={connectPassword}
            onChange={(e) => setConnectPassword(e.target.value)}
            placeholder="16-character app password"
            required
          />
          <p className="-mt-2 text-xs text-slate-400">
            Not the account password — a Google or Zoho app password, generated in that mailbox's own security settings.
          </p>
          <div className="flex gap-3 text-sm">
            <label className="flex items-center gap-1.5 text-slate-700 dark:text-slate-300">
              <input type="radio" name="connect_provider" checked={connectProvider === ""} onChange={() => setConnectProvider("")} />
              Auto-detect
            </label>
            {(["gmail", "zoho"] as const).map((p) => (
              <label key={p} className="flex items-center gap-1.5 text-slate-700 dark:text-slate-300">
                <input type="radio" name="connect_provider" checked={connectProvider === p} onChange={() => setConnectProvider(p)} />
                {p === "gmail" ? "Google" : "Zoho"}
              </label>
            ))}
          </div>
          {connectError && <Alert>{connectError}</Alert>}
          <Button type="submit" isLoading={connecting} className="mt-1">
            Connect
          </Button>
        </form>
      </Modal>

      <Modal open={!!disconnectTarget} onClose={() => setDisconnectTarget(null)} title="Disconnect mailbox?">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            {disconnectTarget?.email} will stop being polled, and any Tenant/Template
            assignment on it is lost. This does not affect jobs already created.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDisconnectTarget(null)} disabled={disconnecting}>
              Cancel
            </Button>
            <Button variant="danger" onClick={confirmDisconnect} isLoading={disconnecting}>
              Disconnect
            </Button>
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}
