import { useCallback, useMemo, useState } from "react";
import { JobActivityChart } from "../../components/JobActivityChart";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import * as emailApi from "../../api/email";

/** First/last calendar day of a "YYYY-MM" month, as the "YYYY-MM-DD" strings
 *  JobCategoryCountFilters already expects (see JobsPage.tsx's own quickRange usage). */
function monthBounds(month: string): { dateFrom: string; dateTo: string } {
  const [y, m] = month.split("-").map(Number);
  const lastDay = new Date(y, m, 0).getDate(); // day 0 of next month = this month's last day
  return { dateFrom: `${month}-01`, dateTo: `${month}-${String(lastDay).padStart(2, "0")}` };
}

export function OperatorDashboard() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [checkingMail, setCheckingMail] = useState(false);
  const [mailMsg, setMailMsg] = useState<string | null>(null);
  // Keeps the sidebar's New/Missing/Failed/Submitted/Verified badges scoped to whatever month
  // (and operator) the Job Activity chart below is currently showing - see JobActivityChart's
  // own onViewChange docstring for why this exists. null until the chart's first mount-time
  // callback arrives, during which the sidebar briefly shows its old unscoped (all-time) count.
  const [chartView, setChartView] = useState<{ month: string; assignedTo: string } | null>(null);
  const handleChartViewChange = useCallback(
    (view: { month: string; assignedTo: string }) => setChartView(view),
    [],
  );
  const categoryCountFilters = useMemo(() => {
    if (!chartView) return undefined;
    return { ...monthBounds(chartView.month), assignedTo: chartView.assignedTo || undefined };
  }, [chartView]);

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
      categoryCountFilters={categoryCountFilters}
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

      <JobActivityChart onViewChange={handleChartViewChange} />
    </AppShell>
  );
}
