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
  // Where a template's marks and transformations are reviewed and approved - the work of
  // whoever built the template, not the customer's.
  { path: "/super-admin/data-transformation", navLabel: "Data Transformation", group: "Platform Configuration", order: 5 },
  // Pages removed from every document before classification (a supplier's terms and
  // conditions, a recurring cover sheet) - the page existed but had no menu entry.
  { path: "/super-admin/filter-pages", navLabel: "Filter Pages", group: "Platform Configuration", order: 5.5 },
  // Per template: start extraction automatically, hold it for a large zip, Excel by sheet and
  // the Excel files never used - how documents are processed, so Super Admin only.
  { path: "/super-admin/document-processing", navLabel: "Document Processing", group: "Platform Configuration", order: 5.6 },
  // Fields operators keep correcting on many different jobs - where an extraction prompt needs
  // work (app/core/field_edits.py).
  { path: "/super-admin/extraction-corrections", navLabel: "Extraction Corrections", group: "Platform Configuration", order: 5.7 },
  // Lay out a template document's job screen: sections, order, product card (Screen Designer).
  { path: "/super-admin/screen-designer", navLabel: "Screen Designer", group: "Platform Configuration", order: 5.8 },

  // Connect any Gmail/Zoho mailbox (independent of any operator user) and assign it a
  // Tenant + Template - replaces the old per-user mailbox card on the Tenant Admin's own
  // "New User" form.
  // Customer Onboarding (2026-11-24): the new step-by-step onboarding, built alongside the
  // existing wizard (which stays untouched until this replaces it). Step 1 - ERP fields.
  { path: "/super-admin/onboarding/start", navLabel: "Step 0 - Choose Template", group: "Customer Onboarding", order: 5.94 },
  { path: "/super-admin/onboarding/erp-fields", navLabel: "Step 1 - ERP Field Mapping", group: "Customer Onboarding", order: 5.95 },
  { path: "/super-admin/onboarding/document-mapping", navLabel: "Step 2 - Document Mapping", group: "Customer Onboarding", order: 5.96 },
  // Step 3 is the Screen Designer itself, reached from the onboarding flow.
  { path: "/super-admin/onboarding/screen-designer", navLabel: "Step 3 - Screen Designer", group: "Customer Onboarding", order: 5.97 },
  { path: "/super-admin/onboarding/field-config", navLabel: "Step 4 - Field Configuration", group: "Customer Onboarding", order: 5.98 },
  // Step 5: template-level settings on the live template (documents, rules, mail, ERP scripts,
  // reference data, Data Transformation & approval ...).
  { path: "/super-admin/onboarding/template-settings", navLabel: "Step 5 - Template Settings", group: "Customer Onboarding", order: 5.99 },

  { path: "/super-admin/email-puller", navLabel: "Email Puller", group: "Integrations & Automation", order: 6 },
  { path: "/super-admin/erp-scripts", navLabel: "ERP Script Management", group: "Integrations & Automation", order: 7 },
  { path: "/super-admin/entry-browser", navLabel: "Entry Browser", group: "Integrations & Automation", order: 8 },

  { path: "/jobs", navLabel: "Jobs", group: "Operations", order: 9 },

  { path: "/super-admin/billing", navLabel: "Billing & Usage", group: "Finance & System", order: 11 },
  // OpenAI's real daily cost next to the pages it paid for - collected nightly (app/core/api_cost.py).
  { path: "/super-admin/api-cost", navLabel: "API Cost & Usage", group: "Finance & System", order: 11.5 },
  // Live kill switches for AI-dependent work (email pull, extraction) - Super Admin only,
  // since it affects every tenant equally and nobody else should be able to stop the whole
  // platform's document processing.
  { path: "/super-admin/settings", navLabel: "Platform Settings", group: "Finance & System", order: 12 },
].sort((a, b) => a.order - b.order);

// The groups, in render order - every item's own `group` must be one of these.
export const SUPER_ADMIN_FEATURE_GROUPS = [
  "Customer Management",
  "Platform Configuration",
  "Customer Onboarding",
  "Integrations & Automation",
  "Operations",
  "Finance & System",
];
