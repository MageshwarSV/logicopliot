export interface Tenant {
  id: string;
  name: string;
  region: string | null;
  currency: string | null;
  is_active: boolean;
  /** Transport modes this client is licensed for (see MODES in types/onboarding.ts).
   *  Empty/null = unrestricted — every mode is offered when building a template for them. */
  allowed_modes?: string[] | null;
  /** Set on the "Masters" page: {"operator": bool, "gk2": bool} — false locks that role's
   *  users in this tenant to read-only. A missing key or null column means read-and-write. */
  role_write_enabled?: Record<string, boolean> | null;
  /** Set on the Tenant Admin's "Masters" page: {"Sea Import": bool, ...}, keyed by MODES.
   *  A missing key or null column means GK2 review IS required (unchanged default behavior).
   *  False for a mode: a job of that shipment type skips GK2 entirely once GK1 approves it. */
  gk2_required_by_mode?: Record<string, boolean> | null;
  /** Gates this tenant's own IRN Pending link (see app/api/v1/public_irn.py) - null until a
   *  Super Admin presses "Generate Link" for them. Never rotates on its own. */
  irn_pending_access_key?: string | null;
}
