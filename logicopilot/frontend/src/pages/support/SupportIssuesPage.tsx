/** Production Support - every failed / stuck job across all customers, with Retry. */
import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import * as supportApi from "../../api/support";
import type { IssueTab, SupportIssues } from "../../api/support";
import { IssuesTable } from "./IssuesTable";

export function SupportIssuesPage() {
  const [tab, setTab] = useState<IssueTab>("all");
  const [data, setData] = useState<SupportIssues | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await supportApi.getIssues(tab));
    } catch (err) {
      setError(axios.isAxiosError(err) ? (err.response?.data?.detail ?? "Could not load the list.") : "Could not load the list.");
    }
  }, [tab]);

  useEffect(() => {
    load();
    const t = window.setInterval(load, 60000);
    return () => window.clearInterval(t);
  }, [load]);

  return (
    <AppShell title="Failed / Delayed Jobs" subtitle="Every customer. Open a job to investigate; retry where it is allowed.">
      {error && <Alert>{error}</Alert>}
      <Card className="p-5">
        <IssuesTable data={data} tab={tab} onTab={setTab} onChanged={load} />
        <p className="mt-3 text-xs text-slate-500">
          Retry ERP: only a failed ERP entry. Retry extraction: only an extraction that did not finish -
          a job already read is never re-read from here, so the operator's work is kept.
        </p>
      </Card>
    </AppShell>
  );
}
