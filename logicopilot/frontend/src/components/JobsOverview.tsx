import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Card } from "./ui/Card";
import type { Job } from "../types/jobs";

/** Dashboard overview: three groups of clickable counts (Job Pending / ETA / Pending
 *  Approval), each broken into Today / This Week / This Month / Overall. Pressing a box
 *  takes you straight to the Jobs list, filtered server-side to exactly what it counted (see
 *  the matching group/bucket logic in app/api/v1/jobs.py) — no chart, no separate breakdown
 *  cards, no inline table here to keep in sync with it.
 *
 *  Counts are always over EVERY job, unscoped — the Quick Filter (date range, customer)
 *  lives on the Jobs list itself now, not here, so what a box says and what pressing it
 *  shows never disagree.
 */
type Bucket = "today" | "week" | "month" | "all";
type Group = "pending" | "eta" | "approval" | "completed" | "missing";

// Exported so the Jobs list page (navigated to from a box's onClick, with ?group=&bucket=)
// can show the same labels in its own filter banner without redefining them.
export const BUCKETS: { key: Bucket; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "week", label: "This Week" },
  { key: "month", label: "This Month" },
  { key: "all", label: "Overall" },
];

export const GROUP_LABELS: Record<Group, string> = {
  pending: "Job Pending",
  eta: "ETA",
  approval: "Pending Approval",
  completed: "Completed",
  missing: "Missing Documents",
};

// A distinct accent per group so the three sections read apart from a glance, not just from
// their headings — Job Pending (brand indigo), ETA (teal, a "time" color), Pending Approval
// (amber, an "attention" color, same family the app already uses for warnings).
const GROUP_THEME: Record<Group, {
  heading: string; cardTop: string; boxBg: string; boxBorder: string; boxHover: string; number: string;
}> = {
  pending: {
    heading: "text-indigo-700 dark:text-indigo-300",
    cardTop: "border-t-4 border-t-indigo-400 dark:border-t-indigo-500",
    boxBg: "bg-indigo-50/70 dark:bg-indigo-500/10",
    boxBorder: "border-indigo-100 dark:border-indigo-500/20",
    boxHover: "hover:border-indigo-400 dark:hover:border-indigo-400",
    number: "text-indigo-900 dark:text-indigo-100",
  },
  eta: {
    heading: "text-teal-700 dark:text-teal-300",
    cardTop: "border-t-4 border-t-teal-400 dark:border-t-teal-500",
    boxBg: "bg-teal-50/70 dark:bg-teal-500/10",
    boxBorder: "border-teal-100 dark:border-teal-500/20",
    boxHover: "hover:border-teal-400 dark:hover:border-teal-400",
    number: "text-teal-900 dark:text-teal-100",
  },
  approval: {
    heading: "text-amber-700 dark:text-amber-300",
    cardTop: "border-t-4 border-t-amber-400 dark:border-t-amber-500",
    boxBg: "bg-amber-50/70 dark:bg-amber-500/10",
    boxBorder: "border-amber-100 dark:border-amber-500/20",
    boxHover: "hover:border-amber-400 dark:hover:border-amber-400",
    number: "text-amber-900 dark:text-amber-100",
  },
  // Same emerald family the Status column already uses for "AI - ERP Submitted"/"Completed"
  // badges (see stageBadge in JobsPage.tsx) - a finished job reads as "done" here the same
  // way it does there.
  completed: {
    heading: "text-emerald-700 dark:text-emerald-300",
    cardTop: "border-t-4 border-t-emerald-400 dark:border-t-emerald-500",
    boxBg: "bg-emerald-50/70 dark:bg-emerald-500/10",
    boxBorder: "border-emerald-100 dark:border-emerald-500/20",
    boxHover: "hover:border-emerald-400 dark:hover:border-emerald-400",
    number: "text-emerald-900 dark:text-emerald-100",
  },
  // Same amber "attention" family the Status column already uses for the Missing Documents
  // badge itself (see stageBadge in JobsPage.tsx).
  missing: {
    heading: "text-amber-700 dark:text-amber-300",
    cardTop: "border-t-4 border-t-amber-400 dark:border-t-amber-500",
    boxBg: "bg-amber-50/70 dark:bg-amber-500/10",
    boxBorder: "border-amber-100 dark:border-amber-500/20",
    boxHover: "hover:border-amber-400 dark:hover:border-amber-400",
    number: "text-amber-900 dark:text-amber-100",
  },
};

// Every customer and operator is in India - reckoned in IST explicitly, not the browser's own
// local timezone (which usually IS IST for these users, but isn't guaranteed - a misconfigured
// OS clock, a VPN, opening the dashboard from abroad) and not the server's UTC clock either
// (see IST in app/api/v1/jobs.py's own identical comment) - both sides have to agree on the
// same timezone or a box's count and what pressing it actually shows disagree. A fixed offset,
// not a zoneinfo lookup, since India itself has had no DST since 1945.
const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;

function startOfDay(d: Date): Date {
  // Shift the instant so ITS OWN UTC getters read as its IST wall-clock date, then build that
  // same calendar day's midnight back out in real UTC terms - this is a genuine instant,
  // directly comparable to any other Date, regardless of what timezone the runtime is in.
  const shifted = new Date(d.getTime() + IST_OFFSET_MS);
  const istMidnightAsUtc = Date.UTC(shifted.getUTCFullYear(), shifted.getUTCMonth(), shifted.getUTCDate());
  return new Date(istMidnightAsUtc - IST_OFFSET_MS);
}

// Once GK1 hands a job to GK2 (any of these outer_status words), it is no longer "pending" in
// GK1's own queue, even though its raw status is still "extracted" - without this a job
// counted here AND in Pending Approval at once, the moment it was submitted. "Failed" here
// only ever means a GK2 run that failed (job.status stays "extracted" throughout that flow,
// on purpose) - an ordinary GK1-only failure is already excluded by the status check below.
const GK2_IN_FLIGHT = new Set([
  "Pending GK2 Approval", "AI - Preparing for ERP", "ERP Entry Process Started",
  "AI - ERP Submitted", "Failed",
]);

// Exported so anything else counting the same two populations (the manager dashboard's
// per-user table, for one) uses the identical rule instead of a second copy that could drift
// out of sync with these boxes.
export function isJobPending(j: Job): boolean {
  return !["completed", "failed"].includes(j.status)
    && !GK2_IN_FLIGHT.has(j.outer_status ?? "")
    // Counted in its own dedicated Missing Documents box instead - same reasoning as
    // GK2_IN_FLIGHT above, so a job is never counted as "pending" in two boxes at once.
    && j.outer_status !== "Missing Documents";
}

// A job GK2 has fully approved and whose ERP entry finished submitting - the same condition
// _outer_status (app/api/v1/jobs.py) uses to produce "AI - ERP Submitted", and the same one
// the backend's group=completed filter checks (Job.gk2_status == "submitted").
export function isJobCompleted(j: Job): boolean {
  return j.outer_status === "AI - ERP Submitted";
}

/** Whether a date string (a full timestamp, or a plain "YYYY-MM-DD") falls in the given
 *  calendar bucket, reckoned from right now. "This Week" is Monday-to-Sunday, "This Month"
 *  the current calendar month — plain calendar windows, not "last N days". */
function inBucket(dateStr: string | null | undefined, bucket: Bucket): boolean {
  if (bucket === "all") return true;
  if (!dateStr) return false;
  const d = new Date(dateStr.length <= 10 ? `${dateStr}T00:00:00` : dateStr);
  if (Number.isNaN(d.getTime())) return false;
  const now = new Date();
  const today = startOfDay(now);
  const DAY_MS = 24 * 60 * 60 * 1000;
  if (bucket === "today") {
    const tomorrow = new Date(today.getTime() + DAY_MS);
    return d >= today && d < tomorrow;
  }
  if (bucket === "week") {
    // today is already IST midnight as a real instant - shifting it the same way once more
    // reads its own IST day-of-week, Monday = 0 ... Sunday = 6.
    const dow = (new Date(today.getTime() + IST_OFFSET_MS).getUTCDay() + 6) % 7;
    const monday = new Date(today.getTime() - dow * DAY_MS);
    const nextMonday = new Date(monday.getTime() + 7 * DAY_MS);
    return d >= monday && d < nextMonday;
  }
  // month - compared on each side's own IST calendar year/month, not the runtime's local one.
  const dIST = new Date(d.getTime() + IST_OFFSET_MS);
  const nowIST = new Date(now.getTime() + IST_OFFSET_MS);
  return dIST.getUTCFullYear() === nowIST.getUTCFullYear() && dIST.getUTCMonth() === nowIST.getUTCMonth();
}

function dateFieldFor(group: Group, j: Job): string | null | undefined {
  return group === "eta" ? j.eta_date : j.created_at;
}

function FilterSection({
  group,
  population,
  onSelect,
}: {
  group: Group;
  population: Job[];
  onSelect: (group: Group, bucket: Bucket) => void;
}) {
  const theme = GROUP_THEME[group];
  return (
    <Card className={`p-5 ${theme.cardTop}`}>
      <h2 className={`mb-3 font-semibold ${theme.heading}`}>{GROUP_LABELS[group]}</h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {BUCKETS.map((b) => {
          const count = population.filter((j) => inBucket(dateFieldFor(group, j), b.key)).length;
          return (
            <button
              key={b.key}
              type="button"
              onClick={() => onSelect(group, b.key)}
              className={`rounded-xl border p-4 text-left transition-colors ${theme.boxBg} ${theme.boxBorder} ${theme.boxHover}`}
            >
              <p className="text-sm text-slate-600 dark:text-slate-300">{b.label}</p>
              <p className={`text-2xl font-semibold tracking-tight ${theme.number}`}>{count}</p>
            </button>
          );
        })}
      </div>
    </Card>
  );
}

// Which boxes to show, and in what order — defaults to the operator's own four. Manager's
// dashboard passes just ["completed", "pending"] instead, reusing this exact component (and
// its live-count/click-through wiring) rather than a second copy of it.
const DEFAULT_GROUPS: Group[] = ["pending", "missing", "eta", "approval"];

export function JobsOverview({ jobs, groups = DEFAULT_GROUPS }: { jobs: Job[]; groups?: Group[] }) {
  const navigate = useNavigate();

  // The populations each group's four boxes count within — every job, no scoping.
  const pending = useMemo(() => jobs.filter(isJobPending), [jobs]);
  const withEta = useMemo(() => jobs.filter((j) => j.eta_date), [jobs]);
  const pendingApproval = useMemo(
    () => jobs.filter((j) => j.outer_status === "Pending GK2 Approval"),
    [jobs],
  );
  const completed = useMemo(() => jobs.filter(isJobCompleted), [jobs]);
  const missingDocuments = useMemo(
    () => jobs.filter((j) => j.outer_status === "Missing Documents"),
    [jobs],
  );
  const populationFor: Record<Group, Job[]> = {
    pending, eta: withEta, approval: pendingApproval, completed, missing: missingDocuments,
  };

  function select(group: Group, bucket: Bucket) {
    navigate(`/jobs?group=${group}&bucket=${bucket}`);
  }

  return (
    <div className="mb-6 flex flex-col gap-4">
      {groups.map((g) => (
        <FilterSection key={g} group={g} population={populationFor[g]} onSelect={select} />
      ))}
    </div>
  );
}
