export type Role = "super_admin" | "tenant_admin" | "operator" | "admin" | "gk2" | "manager";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  tenant_id: string | null;
  is_active: boolean;
  last_login_at: string | null;
  mail_provider?: MailProvider | null;
  mail_email?: string | null;
  mail_connected?: boolean;
  // Which shipment modes this user is scoped to — the real access gate for "gk2"; a
  // tab-filter convenience only for "operator" (their real access stays template-based);
  // meaningless for every other role, including "manager" (sees every job, unrestricted).
  modes?: string[] | null;
  // GK2 only: which operators' jobs this account may see/act on — null/empty means every
  // operator (unrestricted); meaningless for every other role.
  assigned_operator_ids?: string[] | null;
  // Set only when this account was created under a Tenant Admin's own custom type (e.g.
  // "Supervisor") — role above still carries that type's underlying base role.
  user_type_id?: string | null;
  user_type_name?: string | null;
  // True until this account sets its OWN password - set whenever an admin creates the account
  // or resets its password. Drives the blocking "set a permanent password" modal in
  // ProtectedRoute until the user changes it themselves.
  must_change_password?: boolean;
}

export type MailProvider = "gmail" | "zoho";
