/** Small formatting helpers shared across the SaaS Finance portal's pages - duplicated in
 *  spirit from (not imported from) BillingUsagePage.tsx's own identical helpers, since that
 *  file is deliberately left untouched by this whole feature (see the plan's own reasoning:
 *  the existing tenant-facing Billing & Usage page must never be put at risk of a regression
 *  for the sake of a few shared one-line functions). */

export function formatInr(n: number): string {
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

export function currentMonthStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-IN", { month: "long", year: "numeric" });
}

export function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** An unpaid invoice is shown as Overdue once its billing period ended 15+ days ago - a
 *  purely cosmetic badge (no due-date field exists anywhere in the data model). */
export function isOverdue(periodEnd: string, status: "unpaid" | "paid"): boolean {
  if (status === "paid") return false;
  const end = new Date(periodEnd);
  const daysSince = (Date.now() - end.getTime()) / (1000 * 60 * 60 * 24);
  return daysSince > 15;
}

const PALETTE = ["#6366f1", "#0ea5e9", "#14b8a6", "#f59e0b", "#ec4899", "#f43f5e", "#84cc16", "#a855f7"];

export function colorFor(index: number): string {
  return PALETTE[index % PALETTE.length];
}
