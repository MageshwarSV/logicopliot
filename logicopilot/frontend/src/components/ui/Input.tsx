import { forwardRef, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

interface FieldProps {
  label: string;
  error?: string;
  /** Rendered inside the input's own box, pinned to the right - e.g. a show/hide password
   *  toggle. Positioned against the input itself (a dedicated wrapper around just that
   *  element), not the label above it, so it stays correctly placed whether or not an error
   *  message is showing below. */
  trailingAdornment?: ReactNode;
}

export const Input = forwardRef<HTMLInputElement, FieldProps & InputHTMLAttributes<HTMLInputElement>>(
  ({ label, error, trailingAdornment, className = "", id, ...rest }, ref) => {
    const inputId = id ?? label.toLowerCase().replace(/\s+/g, "-");
    return (
      <div className="flex flex-col gap-1.5">
        <label htmlFor={inputId} className="text-sm font-medium text-slate-700 dark:text-slate-300">
          {label}
        </label>
        <div className="relative w-full">
          <input
            ref={ref}
            id={inputId}
            className={`w-full rounded-lg border px-3.5 py-2.5 text-sm text-slate-900 placeholder:text-slate-400 outline-none transition-shadow focus:ring-2 focus:ring-indigo-500/40 dark:text-slate-100 dark:placeholder:text-slate-500 ${
              error
                ? "border-rose-400 focus:border-rose-500"
                : "border-slate-200 bg-white focus:border-indigo-500 dark:border-slate-700 dark:bg-slate-900"
            } ${trailingAdornment ? "pr-10" : ""} ${className}`}
            {...rest}
          />
          {trailingAdornment && (
            <div className="absolute inset-y-0 right-2 flex items-center">{trailingAdornment}</div>
          )}
        </div>
        {error && <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}
      </div>
    );
  },
);
Input.displayName = "Input";

interface SelectFieldProps extends FieldProps, SelectHTMLAttributes<HTMLSelectElement> {}

export const Select = forwardRef<HTMLSelectElement, SelectFieldProps>(
  ({ label, error, className = "", id, children, ...rest }, ref) => {
    const selectId = id ?? label.toLowerCase().replace(/\s+/g, "-");
    return (
      <div className="flex flex-col gap-1.5">
        <label htmlFor={selectId} className="text-sm font-medium text-slate-700 dark:text-slate-300">
          {label}
        </label>
        <select
          ref={ref}
          id={selectId}
          className={`rounded-lg border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none transition-shadow focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 ${className}`}
          {...rest}
        >
          {children}
        </select>
        {error && <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}
      </div>
    );
  },
);
Select.displayName = "Select";
