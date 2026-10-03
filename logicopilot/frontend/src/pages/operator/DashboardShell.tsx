import { useEffect, useState } from "react";
import { JobsOverview } from "../../components/JobsOverview";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import * as jobsApi from "../../api/jobs";
import * as emailApi from "../../api/email";
import type { Job } from "../../types/jobs";

export function OperatorDashboard() {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [checkingMail, setCheckingMail] = useState(false);
  const [mailMsg, setMailMsg] = useState<string | null>(null);

  async function refresh() {
    try {
      setJobs(await jobsApi.listJobs());
    } catch {
      setJobs([]);
    }
  }

  useEffect(() => {
    refresh();
    // Auto-refresh so the boxes' own counts (a job moving to Completed, a fresh GK2 approval)
    // stay live without a manual reload. 30s, not 4s: this is the FULL, unpaginated job list,
    // fully enriched (stage/customer/mode/operator/...) per job purely to compute a handful of
    // count boxes from it client-side - a 4s interval meant every open dashboard tab repeated
    // that full, expensive fetch 15x/minute continuously, which is what exhausted the database
    // connection pool under any real concurrent use and took the whole Jobs list down for
    // everyone. 30s is still "live" for a status dashboard a human is glancing at, at a small
    // fraction of the load.
    const t = window.setInterval(() => { jobsApi.listJobs().then(setJobs).catch(() => {}); }, 30000);
    return () => window.clearInterval(t);
  }, []);

  async function checkEmail() {
    setCheckingMail(true);
    setMailMsg(null);
    setError(null);
    try {
      const res = await emailApi.pullEmail();
      if (!res.ok) setMailMsg(`Mailbox error: ${res.error ?? "unavailable"}`);
      else if (res.note) setMailMsg(res.note);
      else {
        const created = (res.processed ?? []).filter((m) => m.job_id);
        setMailMsg(
          created.length
            ? `Pulled ${created.length} new job${created.length === 1 ? "" : "s"} from email.`
            : "Checked — no new document emails for you.",
        );
      }
      await refresh();
    } catch {
      setMailMsg("Could not check email.");
    } finally {
      setCheckingMail(false);
    }
  }

  return (
    <AppShell
      title="User"
      subtitle="Process transactions: upload, verify, and submit ERP entries."
      actions={
        <div className="flex gap-2">
          <Button size="sm" variant="secondary" onClick={checkEmail} isLoading={checkingMail}>✉ Check Email</Button>
          <Button size="sm" onClick={() => navigate("/jobs")}>Go to Jobs</Button>
        </div>
      }
    >
      {error && <div className="mb-6"><Alert>{error}</Alert></div>}
      {mailMsg && (
        <div className="mb-6 rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm text-indigo-800 dark:border-indigo-500/20 dark:bg-indigo-500/10 dark:text-indigo-300">
          {mailMsg}
        </div>
      )}

      {/* Pressing any box navigates straight to the Jobs list, filtered to exactly what it
          counted — there is no separate "recent jobs" table here to keep in sync with it. */}
      <JobsOverview jobs={jobs} />
    </AppShell>
  );
}
