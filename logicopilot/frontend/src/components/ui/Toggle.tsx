interface ToggleProps {
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
  /** Read by screen readers only - the switch itself carries no visible label. */
  label: string;
}

/** A standard on/off switch, not a checkbox - used wherever ONE row among many needs its own
 *  independent on/off state (a single mailbox's poll switch, say) rather than a page-level
 *  action button. */
export function Toggle({ checked, onChange, disabled, label }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50 ${
        checked ? "bg-emerald-500" : "bg-slate-300 dark:bg-slate-700"
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
          checked ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );
}
