import type { ReactNode } from "react";

export interface Column<T> {
  header: string;
  render: (row: T) => ReactNode;
  className?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  keyFor: (row: T) => string;
  emptyMessage?: string;
  /** Makes the whole row the target. A row carrying one obvious action does not need a button
   *  saying so — the row IS the button, which is how every list of things behaves. Controls
   *  inside a clickable row must stop the event, or Delete would also open the thing it
   *  just deleted. */
  onRowClick?: (row: T) => void;
}

export function DataTable<T>({
  columns,
  rows,
  keyFor,
  emptyMessage = "Nothing here yet.",
  onRowClick,
}: DataTableProps<T>) {
  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-1 py-16 text-center">
        <p className="text-sm text-slate-500 dark:text-slate-400">{emptyMessage}</p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 dark:border-slate-800">
            {columns.map((col) => (
              <th key={col.header} className="whitespace-nowrap px-5 py-3 font-medium text-slate-500 dark:text-slate-400">
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {rows.map((row) => (
            <tr
              key={keyFor(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={`transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/50 ${
                onRowClick ? "cursor-pointer" : ""
              }`}
            >
              {columns.map((col) => (
                <td key={col.header} className={`whitespace-nowrap px-5 py-3.5 text-slate-700 dark:text-slate-300 ${col.className ?? ""}`}>
                  {col.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
