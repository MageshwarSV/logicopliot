import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { JobsOverview, isJobPending, isJobCompleted } from "../../components/JobsOverview";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { ModeIcon } from "../../components/ModeIcon";
import * as jobsApi from "../../api/jobs";
import * as usersApi from "../../api/users";
import type { Job } from "../../types/jobs";
import type { User } from "../../types/auth";

const ALL = "All";

/** Manager's own dashboard - read-only, tenant-wide, and deliberately just two boxes
 *  (Completed / Pending) rather than the operator's three: a manager is checking on
 *  throughput, not working a personal queue, so ETA and GK2's own approval queue aren't
 *  theirs to track here. Below the boxes, the same two counts broken down per operator, so
 *  a manager can see who is carrying how much without opening the Jobs list and counting by
 *  hand. */
export function ManagerDashboard() {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [operators, setOperators] = useState<User[]>([]);
  // Shipment-type tabs — every mode with at least one job, worked out from the jobs
  // themselves rather than a fixed list, so a mode nobody has shipped yet doesn't clutter
  // the row. "All" (the default) is everything combined, same as before these existed.
  const [modeTab, setModeTab] = useState<string>(ALL);
  const modes = useMemo(() => {
    const s = new Set<string>();
    for (const j of jobs) if (j.mode) s.add(j.mode);
    return [...s].sort();
  }, [jobs]);
  const scopedJobs = useMemo(
    () => (modeTab === ALL ? jobs : jobs.filter((j) => j.mode === modeTab)),
    [jobs, modeTab],
  );

  useEffect(() => {
    async function refresh() {
      try {
        setJobs(await jobsApi.listJobs());
      } catch {
        setJobs([]);
      }
    }
    refresh();
    // Auto-refresh so the boxes' own counts stay live without a manual reload. 30s, not 4s -
    // see the operator dashboard's identical change for why (this is the full, unpaginated,
    // fully-enriched job list, fetched purely to compute a few count boxes from it).
    const t = window.setInterval(refresh, 30000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    usersApi.listUsers().then((all) => setOperators(all.filter((u) => u.role === "operator"))).catch(() => {});
  }, []);

  // One row per operator, in the same Pending/Completed shape as the boxes above - reusing
  // isJobPending/isJobCompleted so a job can never count differently down here than it does
  // in its own box. A job with nobody assigned falls into its own "Unassigned" row rather
  // than being silently dropped, since every new job starts Unassigned until someone picks
  // it up from the Jobs list' own dropdown.
  //
  // "All" lists every operator in the tenant - the full roster. A specific mode tab lists
  // that mode's OWN TEAM instead - operators whose own profile is scoped to it (User.modes,
  // the same field the assign dropdown itself filters candidates by) - always, 0 or not, so
  // a manager can see who is responsible for Sea Import even before anyone's touched one.
  // An operator from a different team never appears here, whatever jobs happen to be
  // assigned to them.
  const rows = useMemo(() => {
    const roster = modeTab === ALL ? operators : operators.filter((op) => (op.modes ?? []).includes(modeTab));
    const byOperator = roster.map((op) => {
      const own = scopedJobs.filter((j) => j.assigned_operator_id === op.id);
      return { id: op.id, name: op.full_name, pending: own.filter(isJobPending).length, completed: own.filter(isJobCompleted).length };
    });
    const unassigned = scopedJobs.filter((j) => !j.assigned_operator_id);
    byOperator.push({
      id: "unassigned",
      name: "Unassigned",
      pending: unassigned.filter(isJobPending).length,
      completed: unassigned.filter(isJobCompleted).length,
    });
    return byOperator;
  }, [scopedJobs, operators, modeTab]);

  return (
    <AppShell
      title="Manager"
      subtitle="A read-only view of every job in the tenant."
      actions={<Button size="sm" onClick={() => navigate("/jobs")}>Go to Jobs</Button>}
    >
      {/* Shipment-type tabs, no heading of their own - "All" plus one per mode actually
          shipped. Everything below (the boxes and the By User table) scopes to whichever
          is selected. */}
      {modes.length > 0 && (
        <div className="mb-4 flex gap-1.5 overflow-x-auto">
          {[ALL, ...modes].map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setModeTab(mode)}
              className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                modeTab === mode
                  ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
                  : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
              }`}
            >
              {mode !== ALL && <ModeIcon mode={mode} />}
              {mode}
            </button>
          ))}
        </div>
      )}

      {/* Pressing a box navigates straight to the Jobs list, filtered server-side to exactly
          what it counted - see the matching group/bucket logic in app/api/v1/jobs.py. */}
      <JobsOverview jobs={scopedJobs} groups={["completed", "pending"]} />

      <Card className="p-5">
        <h2 className="mb-3 font-semibold text-slate-700 dark:text-slate-200">By User</h2>
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs font-medium uppercase tracking-wide text-slate-500 dark:border-slate-700 dark:text-slate-400">
              <th className="py-2 pr-4">User</th>
              <th className="py-2 pr-4">Pending</th>
              <th className="py-2 pr-4">Completed</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-slate-100 last:border-0 dark:border-slate-800">
                <td className="py-2 pr-4 text-slate-800 dark:text-slate-200">{r.name}</td>
                <td className="py-2 pr-4 font-medium text-indigo-700 dark:text-indigo-300">{r.pending}</td>
                <td className="py-2 pr-4 font-medium text-emerald-700 dark:text-emerald-300">{r.completed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </AppShell>
  );
}
