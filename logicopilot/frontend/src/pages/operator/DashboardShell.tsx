import { useState } from "react";
import { JobActivityChart } from "../../components/JobActivityChart";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/AppShell";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import * as emailApi from "../../api/email";

export function OperatorDashboard() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [checkingMail, setCheckingMail] = useState(false);
  const [mailMsg, setMailMsg] = useState<string | null>(null);

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

      <JobActivityChart />
    </AppShell>
  );
}
