import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import * as jobsApi from "../../api/jobs";
import * as tenantsApi from "../../api/tenants";
import type { Tenant } from "../../types/tenant";

/** How a template's status reads, and whether its operators can actually run it. */
const STATUS_LOOK: Record<string, { cls: string; label: string }> = {
  approved: {
    cls: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300",
    label: "Approved",
  },
  ready: {
    cls: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300",
    label: "Ready — needs approval",
  },
  changes_requested: {
    cls: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300",
    label: "Changes requested",
  },
};

export function DataTransformationPage() {
  const navigate = useNavigate();
  const [groups, setGroups] = useState<jobsApi.AvailableGroup[]>([]);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  // "" means every customer. A Super Admin sees them all, and on a platform with a dozen
  // customers the templates of the one being worked on are what matter.
  const [tenantId, setTenantId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([jobsApi.listAvailableGroups(), tenantsApi.listTenants()])
      .then(([g, t]) => {
        setGroups(g);
        setTenants(t);
      })
      .catch(() => setError("Failed to load templates. Is the backend running?"))
      .finally(() => setLoading(false));
  }, []);

  const nameOf = useMemo(
    () => new Map(tenants.map((t) => [t.id, t.name])),
    [tenants],
  );

  // Only customers that actually HAVE a template are offered. A dropdown listing companies
  // that can only ever show an empty page is a list of dead ends.
  const withTemplates = useMemo(() => {
    const ids = new Set(groups.map((g) => g.tenant_id));
    return tenants
      .filter((t) => ids.has(t.id))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [tenants, groups]);

  const shown = useMemo(
    () => (tenantId ? groups.filter((g) => g.tenant_id === tenantId) : groups),
    [groups, tenantId],
  );

  return (
    <AppShell
      title="Data Transformation"
      subtitle="Review each template's fields and transformations, then approve it for the operators."
    >
      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}

      {!loading && withTemplates.length > 0 && (
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <label
            htmlFor="tenant-filter"
            className="text-sm font-medium text-slate-700 dark:text-slate-300"
          >
            Customer
          </label>
          <select
            id="tenant-filter"
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
          >
            <option value="">All customers</option>
            {withTemplates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
          <span className="text-sm text-slate-500 dark:text-slate-400">
            {shown.length} template{shown.length === 1 ? "" : "s"}
          </span>
        </div>
      )}

      {loading && <p className="text-sm text-slate-400">Loading…</p>}

      {!loading && shown.length === 0 && (
        <Card className="p-8 text-center">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {tenantId
              ? "This customer has no templates yet."
              : "No templates yet. Finish a “Customer with Template Creation” first."}
          </p>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {shown.map((g) => {
          const look = STATUS_LOOK[g.status] ?? {
            cls: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
            label: g.status,
          };
          return (
            <Card key={g.id} className="flex flex-col justify-between p-5">
              <div>
                <p className="font-semibold text-slate-900 dark:text-slate-50">{g.name}</p>
                {/* Whose template it is. Without this, two customers with similarly named
                    templates are indistinguishable once "All customers" is selected. */}
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                  {nameOf.get(g.tenant_id) ?? "—"}
                </p>
                <span
                  className={`mt-2 inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium ${look.cls}`}
                >
                  {look.label}
                </span>
                <p className="mt-2 text-xs text-slate-400">
                  Configure how each field's value is formatted, then approve.
                </p>
              </div>
              <Button
                size="sm"
                className="mt-4"
                onClick={() => navigate(`/super-admin/data-transformation/${g.id}`)}
              >
                Open →
              </Button>
            </Card>
          );
        })}
      </div>
    </AppShell>
  );
}
