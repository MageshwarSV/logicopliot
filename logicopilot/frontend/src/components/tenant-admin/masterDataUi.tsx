import { SortIcon } from "../ui/icons";

/** Shared by the ERP Master Data pages (Importer, Quotation) so both look and behave alike. */

export const FIELD_CLASS =
  "rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

export const OUTLINE_BTN =
  "inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800";

export function SortHeader({ label, active, dir, onClick }: { label: string; active: boolean; dir: "asc" | "desc"; onClick: () => void }) {
  return (
    <th className="px-4 py-3 font-semibold" aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" onClick={onClick} className="inline-flex items-center gap-1.5 hover:text-indigo-600 dark:hover:text-indigo-400">
        {label}
        <SortIcon className={`h-4 w-4 ${active ? "text-indigo-600 dark:text-indigo-400" : "text-slate-400"}`} direction={active ? dir : undefined} />
      </button>
    </th>
  );
}

/** Active / Inactive switch. Master-data records are never deleted - only switched off. */
export function StatusToggle({
  active, busy, label, onChange,
}: {
  active: boolean;
  busy: boolean;
  label: string;
  onChange: (next: boolean) => void;
}) {
  return (
    <label className={`inline-flex items-center gap-2.5 ${busy ? "cursor-wait opacity-60" : "cursor-pointer"}`}>
      <button
        type="button"
        role="switch"
        aria-checked={active}
        aria-label={`${active ? "Make inactive" : "Make active"}: ${label}`}
        disabled={busy}
        onClick={() => onChange(!active)}
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 ${
          active ? "bg-emerald-500" : "bg-slate-300 dark:bg-slate-600"
        }`}
      >
        <span className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${active ? "translate-x-5" : "translate-x-0.5"}`} />
      </button>
      <span className={`text-sm font-medium ${active ? "text-emerald-700 dark:text-emerald-400" : "text-slate-500 dark:text-slate-400"}`}>
        {active ? "Active" : "Inactive"}
      </span>
    </label>
  );
}
