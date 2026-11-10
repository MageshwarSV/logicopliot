// ---------------------------------------------------------------------------
// SUPER ADMIN NAV - grouped into labelled sections (2026-11-10, by explicit request,
// replacing the old single flat "Platform Features" list). "/super-admin" itself (the
// existing tenant+admin management page, unchanged) is NOT listed here - the universal
// "Dashboard" link above every role's own sidebar (AppShell's own dashboardPath) already
// points there, so listing it again here would render it twice. Pure data only (no component
// imports) so importing this never creates a cycle with AppShell. Route components are wired
// directly in routes.tsx.
// ---------------------------------------------------------------------------

export const superAdminFeatures = [
  { path: "/super-admin/tenants", navLabel: "Tenants", group: "Customer Management", order: 1.1 },
  { path: "/super-admin/users", navLabel: "Users & Tenant Admins", group: "Customer Management", order: 1.2 },

  { path: "/super-admin/template-list", navLabel: "Document Templates", group: "Platform Configuration", order: 2 },
  // The step-by-step onboarding wizard - pairs a new customer with the template that reads
  // their documents.
  { path: "/super-admin/template-creation", navLabel: "Customer Template Mapping", group: "Platform Configuration", order: 3 },
  // Content-match filter: an admin uploads a recurring boilerplate page (a cover sheet, a
  // letterhead page) and any future email-pulled document containing a near-identical page
  // has it dropped automatically - pure text comparison, never AI.
  { path: "/super-admin/filter-pages", navLabel: "Page Filters", group: "Platform Configuration", order: 4 },
  // Where a template's marks and transformations are reviewed and approved - the work of
  // whoever built the template, not the customer's.
  { path: "/super-admin/data-transformation", navLabel: "Data Transformation", group: "Platform Configuration", order: 5 },

  // Connect any Gmail/Zoho mailbox (independent of any operator user) and assign it a
  // Tenant + Template - replaces the old per-user mailbox card on the Tenant Admin's own
  // "New User" form.
  { path: "/super-admin/email-puller", navLabel: "Email Puller", group: "Integrations & Automation", order: 6 },
  { path: "/super-admin/erp-scripts", navLabel: "ERP Script Management", group: "Integrations & Automation", order: 7 },
  { path: "/super-admin/entry-browser", navLabel: "Entry Browser", group: "Integrations & Automation", order: 8 },

  { path: "/jobs", navLabel: "Jobs", group: "Operations", order: 9 },
  // Mail the auto-router could not place on its own - the operator side of "ask which
  // template this is" is manual template picking here, not a per-operator inbox: nobody
  // can say which TENANT an unmatched email even belongs to, only Super Admin sees across
  // all of them.
  { path: "/super-admin/pending-mail", navLabel: "Unidentified Emails", group: "Operations", order: 10 },

  { path: "/super-admin/billing", navLabel: "Billing & Usage", group: "Finance & System", order: 11 },
  // Live kill switches for AI-dependent work (email pull, extraction) - Super Admin only,
  // since it affects every tenant equally and nobody else should be able to stop the whole
  // platform's document processing.
  { path: "/super-admin/settings", navLabel: "Platform Settings", group: "Finance & System", order: 12 },
].sort((a, b) => a.order - b.order);

// The groups, in render order - every item's own `group` must be one of these.
export const SUPER_ADMIN_FEATURE_GROUPS = [
  "Customer Management",
  "Platform Configuration",
  "Integrations & Automation",
  "Operations",
  "Finance & System",
];
