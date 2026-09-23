export function NotAuthorizedPage() {
  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-2 bg-slate-50 px-6 text-center dark:bg-slate-950">
      <div className="mb-2 flex h-14 w-14 items-center justify-center rounded-2xl bg-rose-50 text-rose-500 dark:bg-rose-500/10 dark:text-rose-400">
        <svg className="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
        </svg>
      </div>
      <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-50">Not authorized</h1>
      <p className="max-w-sm text-sm text-slate-500 dark:text-slate-400">
        Your account doesn't have access to this page.
      </p>
    </div>
  );
}
