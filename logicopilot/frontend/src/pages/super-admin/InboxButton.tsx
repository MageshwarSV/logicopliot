import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Modal } from "../../components/ui/Modal";
import { Button } from "../../components/ui/Button";
import * as reviewsApi from "../../api/reviews";

/** Header button (top-right) that opens the Super Admin inbox as a popup. */
export function InboxButton() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<reviewsApi.InboxItem[]>([]);

  async function refresh() {
    try {
      setItems(await reviewsApi.listInbox());
    } catch {
      /* silent */
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 20000); // keep the badge fresh
    return () => clearInterval(id);
  }, []);

  async function dismiss(item: reviewsApi.InboxItem) {
    if (item.kind === "erp_access") {
      await reviewsApi.resolveErpAccess(item.id);
    } else if (item.kind === "job_failure") {
      await reviewsApi.resolveJobFailure(item.id);
    } else {
      await reviewsApi.resolveReview(item.id);
    }
    refresh();
  }

  return (
    <>
      <button
        type="button"
        onClick={() => { setOpen(true); refresh(); }}
        className="relative inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 10-12 0v3.2a2 2 0 01-.6 1.4L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
        </svg>
        Inbox
        {items.length > 0 && (
          <span className="absolute -right-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full bg-rose-500 px-1 text-xs font-semibold text-white">
            {items.length}
          </span>
        )}
      </button>

      <Modal open={open} onClose={() => setOpen(false)} title="Inbox — change requests">
        <div className="flex max-h-[60vh] flex-col gap-3 overflow-y-auto">
          {items.length === 0 && (
            <p className="text-sm text-slate-400">No messages. Tenant-admin change requests will appear here.</p>
          )}
          {items.map((it) =>
            it.kind === "job_failure" ? (
              <div key={it.id} className="rounded-lg border border-rose-200 bg-rose-50 p-3 dark:border-rose-500/20 dark:bg-rose-500/10">
                <p className="text-xs font-medium text-rose-800 dark:text-rose-300">⚠ {it.tenant_name} · Web-entry FAILED</p>
                <p className="mt-1 text-sm text-slate-700 dark:text-slate-200">{it.message}</p>
                {it.erp_url && <p className="mt-1 text-xs text-slate-400">ERP: {it.erp_url}</p>}
                <div className="mt-2 flex gap-2">
                  {it.job_id && (
                    <Button
                      size="sm"
                      onClick={() => { setOpen(false); navigate(`/super-admin/entry-browser?job=${it.job_id}`); }}
                    >
                      Open in Entry Browser
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => dismiss(it)}>Dismiss</Button>
                </div>
              </div>
            ) : it.kind === "erp_access" ? (
              <div key={it.id} className="rounded-lg border border-sky-200 bg-sky-50 p-3 dark:border-sky-500/20 dark:bg-sky-500/10">
                <p className="text-xs font-medium text-sky-800 dark:text-sky-300">{it.tenant_name} · ERP access request</p>
                <div className="mt-2 space-y-1 text-sm">
                  <p className="text-slate-700 dark:text-slate-200"><span className="text-slate-400">URL:</span> {it.erp_url}</p>
                  <p className="text-slate-700 dark:text-slate-200"><span className="text-slate-400">Username:</span> {it.erp_username}</p>
                  <p className="font-mono text-slate-700 dark:text-slate-200"><span className="font-sans text-slate-400">Password:</span> {it.erp_password}</p>
                </div>
                {it.raised_by && <p className="mt-1 text-xs text-slate-400">— {it.raised_by}</p>}
                <div className="mt-2">
                  <Button size="sm" variant="ghost" onClick={() => dismiss(it)}>Dismiss</Button>
                </div>
              </div>
            ) : (
              <div key={it.id} className="rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-500/20 dark:bg-amber-500/10">
                <p className="text-xs font-medium text-amber-800 dark:text-amber-300">
                  {it.tenant_name} · {it.group_name}
                </p>
                <p className="mt-1 text-sm text-slate-700 dark:text-slate-200">“{it.message}”</p>
                {it.raised_by && <p className="mt-1 text-xs text-slate-400">— {it.raised_by}</p>}
                <div className="mt-2 flex gap-2">
                  <Button
                    size="sm"
                    onClick={() => {
                      setOpen(false);
                      navigate(`/super-admin/template-creation?edit=${it.group_id}&review=${it.id}`);
                    }}
                  >
                    Check &amp; modify template
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => dismiss(it)}>Dismiss</Button>
                </div>
              </div>
            ),
          )}
        </div>
      </Modal>
    </>
  );
}
