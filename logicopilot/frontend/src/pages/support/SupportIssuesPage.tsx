/** Production Support - every failed / stuck job across all customers, with Retry. */
import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Alert } from "../../components/ui/Alert";
import { ExclamationTriangleIcon } from "../../components/ui/icons";
import * as supportApi from "../../api/support";
import type { IssueTab, SupportIssues } from "../../api/support";
import { IssuesTable } from "./IssuesTable";
import { SectionCard } from "./supportUi";

export function SupportIssuesPage() {
  const [tab, setTab] = useState<IssueTab>("all");
  const [data, setData] = useState<SupportIssues | null>(null);
  const [showHandled, setShowHandled] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await supportApi.getIssues(tab, showHandled));
    } catch (err) {
      setError(axios.isAxiosError(err) ? (err.response?.data?.detail ?? "Could not load the list.") : "Could not load the list.");
    }
  }, [tab, showHandled]);

  useEffect(() => {
    load();
    const t = window.setInterval(load, 60000);
    return () => window.clearInterval(t);
  }, [load]);

  return (
    <AppShell title="Failed / Delayed Jobs" icon={<ExclamationTriangleIcon className="h-6 w-6" />}
      subtitle="Every customer. Open a job to investigate; retry where it is allowed.">
      <div className="space-y-4">
        {error && <Alert>{error}</Alert>}
        <SectionCard padded={false} title="Open problems"
          extra={
            <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
              <input type="checkbox" checked={showHandled} onChange={(e) => setShowHandled(e.target.checked)} />
              Show handled
            </label>
          }>
          <IssuesTable data={data} tab={tab} onTab={setTab} onChanged={load} />
        </SectionCard>
        <p className="text-xs text-slate-500">
          Retry ERP: only a failed ERP entry. Retry extraction: only an extraction that did not finish - a job already read is
          never re-read from here, so the operator's work is kept. Jobs from the last 60 days; refreshes every minute.
        </p>
      </div>
    </AppShell>
  );
}
