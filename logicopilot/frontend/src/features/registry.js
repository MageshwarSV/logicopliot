// ---------------------------------------------------------------------------
// SUPER ADMIN NAV — the wizard flow collapses the old 7 feature pages into
// just two sidebar options:
//   1. Customer/Tenant Creation      -> the dashboard (tenant + admin management)
//   2. Customer with Template Creation -> the step-by-step onboarding wizard
// Pure data only (no component imports) so importing this never creates a cycle
// with AppShell. Route components are wired directly in routes.tsx.
// ---------------------------------------------------------------------------

export const superAdminFeatures = [
  { path: "/super-admin", navLabel: "Customer/Tenant Creation", order: 1 },
  { path: "/super-admin/template-creation", navLabel: "Customer with Template Creation", order: 2 },
  { path: "/super-admin/template-list", navLabel: "Template List", order: 3 },
  // Moved here from the Tenant Admin's workspace. It is where a template's marks and
  // transformations are reviewed and approved, and approving is not the customer's job -
  // it is the work of whoever built the template.
  { path: "/super-admin/data-transformation", navLabel: "Data Transformation", order: 4 },
  { path: "/super-admin/erp-scripts", navLabel: "ERP Script Creation", order: 5 },
  { path: "/super-admin/entry-browser", navLabel: "Entry Browser", order: 6 },
  { path: "/jobs", navLabel: "Jobs", order: 7 },
  // Mail the auto-router could not place on its own — the operator side of "ask which
  // template this is" is manual template picking here, not a per-operator inbox: nobody
  // can say which TENANT an unmatched email even belongs to, only Super Admin sees across
  // all of them.
  { path: "/super-admin/pending-mail", navLabel: "Unidentified Mail", order: 8 },
  // Live kill switches for AI-dependent work (email pull, extraction) - Super Admin only,
  // since it affects every tenant equally and nobody else should be able to stop the whole
  // platform's document processing.
  { path: "/super-admin/settings", navLabel: "Settings", order: 9 },
  // Content-match filter: an admin uploads a recurring boilerplate page (a cover sheet, a
  // letterhead page) and any future email-pulled document containing a near-identical page
  // has it dropped automatically - pure text comparison, never AI.
  { path: "/super-admin/filter-pages", navLabel: "Page Filter", order: 10 },
].sort((a, b) => a.order - b.order);
