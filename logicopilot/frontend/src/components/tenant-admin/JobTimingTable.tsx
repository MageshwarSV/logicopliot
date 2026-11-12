import { useNavigate } from "react-router-dom";
import { Card } from "../ui/Card";
import type { JobTiming } from "../../api/tenants";

/** Day + month in the viewer's local time, e.g. "11 Nov". Parsed as the full timestamp, not
 *  its first 10 characters: the backend sends UTC, so slicing the date part would show the UTC
 *  day, which is the previous day for anything created before 05:30 IST. */
function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
}

function fmtMinutes(m: number | null): string {
  if (m == null) return "—";
  if (m < 60) return `${Math.round(m * 10) / 10} mins`;
  const h = Math.floor(m / 60);
  const rem = Math.round(m % 60);
  return `${h}h ${rem}m`;
}

/** The "Jobs" drill-down table shared by Job Processing Time and Workforce Productivity - same
 *  three stages either page's aggregate cards average, but each row shows that ONE job's actual
 *  time (not an average), so a slow tenant-wide number can be traced to the job(s) behind it. */
export function JobTimingTable({ jobs, loading }: { jobs: JobTiming[]; loading: boolean }) {
  const navigate = useNavigate();

  return (
    <Card className="mt-4 p-5">
      <h2 className="mb-4 font-semibold text-slate-900 dark:text-slate-50">Jobs</h2>
      {loading ? (
        <p className="py-8 text-center text-sm text-slate-400">Loading…</p>
      ) : jobs.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate-400">No completed jobs in this range.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                <th className="pb-2 font-medium">Job</th>
                <th className="pb-2 font-medium">Shipment Type</th>
                <th className="pb-2 font-medium">Created</th>
                <th className="pb-2 font-medium">Completed</th>
                <th className="pb-2 font-medium">Extracted Time</th>
                <th className="pb-2 font-medium">Review Time</th>
                <th className="pb-2 font-medium">ERP Entry Time</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((row) => (
                <tr key={row.job_id} className="border-b border-slate-50 last:border-0 dark:border-slate-800/60">
                  <td className="py-2">
                    <button
                      type="button"
                      onClick={() => navigate(`/jobs/${row.job_id}`)}
                      className="font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                    >
                      {row.reference}
                    </button>
                  </td>
                  <td className="py-2 text-slate-500 dark:text-slate-400">{row.mode ?? "—"}</td>
                  <td className="py-2 text-slate-500 dark:text-slate-400">{fmtDate(row.created_at)}</td>
                  <td className="py-2 text-slate-500 dark:text-slate-400">
                    {fmtDate(row.completed_at)}
                  </td>
                  <td className="py-2 text-slate-700 dark:text-slate-200">{fmtMinutes(row.extraction_minutes)}</td>
                  <td className="py-2 text-slate-700 dark:text-slate-200">{fmtMinutes(row.review_minutes)}</td>
                  <td className="py-2 text-slate-700 dark:text-slate-200">{fmtMinutes(row.erp_submission_minutes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
