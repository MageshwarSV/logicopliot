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
  /** Gates this tenant's own IRN Pending link (see app/api/v1/public_irn.py) - null until a
   *  Super Admin presses "Generate Link" for them. Never rotates on its own. */
  irn_pending_access_key?: string | null;
}
