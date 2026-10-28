import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ModeIcon } from "../../components/ModeIcon";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import axios from "axios";
import { AppShell } from "../../components/AppShell";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import { Input } from "../../components/ui/Input";
import { Modal } from "../../components/ui/Modal";
import * as jobsApi from "../../api/jobs";
import { CombinedDescriptionConfigModal } from "./CombinedDescriptionConfigModal";
import { useAuth } from "../../auth/useAuth";
import type { JobDetail, JobDocument, JobEvent, JobFieldValue } from "../../types/jobs";

// The operator's stages, as the customs desk actually works through a job. The backend still
// tracks four (Documents / Verification / ERP Entry / Completed) — these seven are how the
// work is DIVIDED on screen, and several of them share one backend stage. Keeping the two
// apart means the job list, the history and the submit gate are untouched by this.
// "dump" and "manual" used to be two more steps here (Dump Data, Manual Data Entry) — their
// content now renders directly on "extraction" (Required Details Review), below the
// extracted values, instead of on their own screens. See DumpDataView and the Manual Data
// Entry section rendered under the "extraction" tab further down.
type TabKey =
  | "capture"
  | "extraction"
  | "validation"
  | "irn"
  | "submission";

// `label` is the internal stage word - matched against the backend's _job_stage() answer
// above, and sent verbatim to recordStage() (see goNext() below), which stores it in
// job_events.stage. It must never change: existing jobs already have these exact strings in
// their history, and changing it would both break matching for them and start writing a
// different value going forward. `displayLabel` is the ONLY thing shown on screen - the rail
// button, the blocker text, the "Next: X" prompts - so the two can read differently (e.g.
// "Data Extraction" internally, "Required Details Review" on screen) without touching what
// is actually stored or compared.
const STEPS: { key: TabKey; label: string; displayLabel: string; built: boolean }[] = [
  { key: "capture", label: "Document Capture", displayLabel: "Document Capture", built: true },
  { key: "extraction", label: "Data Extraction", displayLabel: "Required Details Review", built: true },
  { key: "validation", label: "Data Validation", displayLabel: "Cross Docs Verification", built: true },
  { key: "irn", label: "IRN Processing", displayLabel: "IRN Processing", built: true },
  { key: "submission", label: "ERP Submission", displayLabel: "ERP Submission", built: true },
];

// The sidebar's document checklist under "Required Details Review" gets one extra row after
// every real document, opening the fields the system asks a person for (internally still the
// Manual Data Entry fields — nothing about how a value is asked or saved changed) instead of a
// document's extracted values. A plain string, not a document id, so it can never collide with
// a real uploaded file's UUID.
const ADDITIONAL_DETAILS_ID = "additional-details";

// Display-only relocation, requested for 4S Logistics' Sea Import template: these "worked
// out for this job" custom fields read more naturally next to the document they actually
// describe than lumped under Additional Details. Keyed by the owning document's doc_type
// (BL / Invoice) - matched by label_name since these are custom fields, not marks, so there
// is no mark_id to key off. Purely where they render; nothing about extraction, ask_operator,
// or the underlying data changes.
const RELOCATED_FIELD_LABELS_BY_DOC_TYPE: Record<string, string[]> = {
  BL: [
    "HBL Date", "Consignment Type", "TransportModeCode",
    "CustomsHouseCode", "Shipment Country", "Shipment City", "Shipment Port Code", "Package Unit",
  ],
  Invoice: [
    "Currency_type", "Product Value", "Consignee", "Shipper Address",
    "ERP Consignee Country", "ERP Consignee Name", "ERP Importer Address / Branch Code",
  ],
  Custom: ["Frt_Currency", "Misc_Charge_Currency", "insurance_percentage", "insurance_amount"],
};
const ALL_RELOCATED_FIELD_LABELS = new Set(
  Object.values(RELOCATED_FIELD_LABELS_BY_DOC_TYPE).flat(),
);
// Of every relocated field above, only these two were reported as showing a second time under
// Additional Details' "Other" section (both now live on the Invoice tab's own Party Details) -
// removed by request there, scoped to exactly these two labels. Every other relocated field
// (insurance_percentage included) is meant to keep showing in Additional Details too.
const DUPLICATED_ADDITIONAL_DETAILS_LABELS = new Set([
  "ERP Importer Address / Branch Code", "ERP Consignee Name",
]);
// Additional Details custom fields hidden outright, not relocated - "Cont Type" is a
// separate, older field from "Consignment Type" (both read the same BL/Freight documents);
// now that "Consignment Type" shows directly on the Bill of Lading tab, this duplicate has
// no reason to still show under Additional Details. "consignee_full_name" is a word-for-word
// duplicate of "Consignee" (identical ai_prompt) - only "Consignee" is shown anywhere now.
// "Supplier City"/"Supplier Country" (shown as Shipper City/Country) are removed by request -
// Supplier Country's own Excel export mapping (INVOICES!AH, Supplier_Country_Code) was
// cleared at the same time, so nothing downstream still reads it either. Display only - the
// fields themselves, ask_operator, and extraction are unchanged.
const HIDDEN_ADDITIONAL_DETAILS_LABELS = new Set([
  "Cont Type", "consignee_full_name", "Supplier City", "Supplier Country",
  // Removed by request - no longer mapped into the Excel export either (see excel_config.
  // GENERAL!F, repointed to send nothing). Display only - the field/extraction is unchanged.
  "ad_code",
  // Removed by request - Consignee Fields Update (the ERP's own consignee name/branch are the
  // ones that matter for the ERP entry; the document-read address was dropped entirely rather
  // than relocated anywhere). Display only - the field/extraction is unchanged.
  "Consignee Address",
]);
// A document tab's own field hidden outright (not relocated anywhere) - "mode_of_transport"
// ("Transport Mode") is redundant with "TransportModeCode" ("Transport Mode Code") already
// shown on the same Bill of Lading tab. Display only - the mark itself and its data are
// unchanged; only this field no longer renders. "PortOfShipmentCode" (the AI-read mark) is
// replaced on screen by "Shipment Port Code" (the new country+city reference-sheet lookup,
// fuzzy-matched against a 72k-row world port table) - its own Excel mapping (GENERAL!I) was
// repointed to the new field at the same time, so the export reads what the operator now sees.
const HIDDEN_DOCUMENT_FIELD_LABELS_BY_DOC_TYPE: Record<string, Set<string>> = {
  BL: new Set(["mode_of_transport", "PortOfShipmentCode"]),
};
// Display label only - label_name in the database is unchanged, so template config, ERP
// mapping, and every other screen that reads this field by name are unaffected. Keyed by
// the ACTIVE document's doc_type - "Supplier Address"/"Supplier Name" are real marks that
// exist on BOTH the Bill of Lading and the Fright Certificate under the same label_name, and
// only the Bill of Lading's copy should read "Shipper ...".
const FIELD_LABEL_OVERRIDES_BY_DOC_TYPE: Record<string, Record<string, string>> = {
  BL: {
    "Supplier Address": "Shipper Address",
    "Supplier Name": "Shipper Name",
    "HBL No": "HBL Number",
    vessel_name: "Vessel Name",
    voyage_number: "Voyage Number",
    "Consignment Type": "Shipment Type",
    port_of_entry: "Port of Entry",
    CountryOfShipmentCode: "Shipment Country Code",
    TransportModeCode: "Transport Mode Code",
    package_count: "Package Count",
    "Gross Wt": "Gross Weight",
    container_number: "Container Number",
    container_type: "Container Type",
    ContainerTypeCode: "CTR ISO CODE",
  },
  Invoice: {
    "Invoice No": "Invoice Number",
    Currency_type: "Currency",
    "Product Value": "Total Invoice Value",
    total_quantity: "Total Quantity",
    unit: "Quantity Unit",
    shipper: "Shipper Name",
    Consignee: "Document Consignee Name",
    "ERP Importer Address / Branch Code": "ERP Consignee Branch",
    CountryOfOriginCode: "Country of Origin Code",
    TOI: "Terms of Invoice",
    product_description: "Product Description",
    item_material_code: "Item Material Code",
    item_quantity: "Item Quantity",
    item_unit_price: "Unit Price",
    item_po_no: "Purchase Order Number",
    product_amount: "Product Amount",
    "product quantity type": "Product Quantity Unit",
    "Customer Part code": "Customer Part Code",
    // Covers a job extracted BEFORE the underlying field was renamed from "RITC No." to "CTH"
    // (see app/api/v1/field_marks.py's edit_custom_field - a rename only applies to jobs
    // extracted after it, a job's own stored field keeps whatever label it was extracted
    // under). Mapping the old label to the same "CTH" text here means an old job and a new
    // one read identically on screen either way, with no data backfill needed.
    "RITC No.": "CTH",
    "Combined Description": "Combined Product Description",
  },
  PackingList: {
    package_count: "Package Count",
    total_quantity: "Total Quantity",
  },
  Custom: {
    insurance_percentage: "Insurance Rate (%)",
    insurance_amount: "Insurance Amount",
  },
};
// A static, informational caption under a field - background on a business rule applied to
// it, not tied to this job's own data (see ExtractedField's own `hint` prop).
const FIELD_HINTS: Record<string, string> = {
  TOI: "EXW, EX-WORK and FCA are all taken as FOB for this declaration.",
};
// A fixed, short set of valid values reads better as a dropdown than free text. `label` is
// what the operator sees; `value` is what actually gets saved as the field's corrected value
// (and so what the ERP export reads) - e.g. picking "Transaction" saves "T", not the word.
type FieldSelectOption = { value: string; label: string };
const FIELD_SELECT_OPTIONS: Record<string, FieldSelectOption[]> = {
  TransportModeCode: [
    { value: "S", label: "S" },
    { value: "A", label: "A" },
  ],
  "Gross Weight Unit": [
    { value: "KGS", label: "KGS" },
    { value: "MTS", label: "MTS" },
  ],
  "Payment Method": [
    { value: "T", label: "Transaction" },
    { value: "D", label: "Deferred" },
  ],
  AdvancePriorNormal: [
    { value: "A", label: "Advance" },
    { value: "P", label: "Prior" },
    { value: "N", label: "Normal" },
  ],
  BETypeCode: [
    { value: "H", label: "Home" },
    { value: "I", label: "In-Bond" },
    { value: "X", label: "Ex-Bond" },
    { value: "SEZ-Z", label: "SEZ-Z | SEZ Bill of Entry" },
    { value: "SEZ-M", label: "SEZ-M | DTA Sales-Manufacturing" },
    { value: "SEZ-T", label: "SEZ-T | DTA Sales-Trading" },
    { value: "SEZ-V", label: "SEZ-V | Movement of Goods" },
    { value: "SEZ-S", label: "SEZ-S | DTA Clearance of Goods" },
    { value: "SEZ-W", label: "SEZ-W | SEZ to Customs Bonded Warehouse" },
  ],
  // ISO 6346 size/type codes - the same closed list app.core.llm's classify_container_type_code
  // constrains its own AI fallback to, so a wrong read is always pickable from here instead of
  // retyped freehand. Only ever applies to an FCL shipment - see run_extraction's own
  // container-field blanking pass for every other shipment type.
  ContainerTypeCode: [
    { value: "22B0", label: "20 Bulk (22B0)" },
    { value: "22G0", label: "20 Standard Dry (22G0)" },
    { value: "22G1", label: "20 Ft. Dry Container (22G1)" },
    { value: "22P1", label: "20 Flat Rack (22P1)" },
    { value: "22P3", label: "20 Collapsible Flat Rack (22P3)" },
    { value: "22R1", label: "20 Reefer (22R1)" },
    { value: "22U1", label: "20 Open Top (22U1)" },
    { value: "22UP", label: "20 Hard Top (22UP)" },
    { value: "29P0", label: "20 Platform (29P0)" },
    { value: "42G0", label: "40 Standard Dry (42G0)" },
    { value: "42G1", label: "40 Ft. Dry Container (42G1)" },
    { value: "42P1", label: "40 Flat Rack (42P1)" },
    { value: "42P3", label: "40 Collapsible Flat Rack (42P3)" },
    { value: "42R1", label: "40 Reefer (42R1)" },
    { value: "42U1", label: "40 Open Top (42U1)" },
    { value: "42UP", label: "40 Hard Top (42UP)" },
    { value: "45G0", label: "40 High Cube (45G0)" },
    { value: "45R1", label: "40 Reefer High Cube (45R1)" },
    { value: "49P0", label: "40 Platform (49P0)" },
    { value: "L5G1", label: "45 High Cube (L5G1)" },
    { value: "L5R1", label: "45 Reefer High Cube (L5R1)" },
  ],
};
// Fields whose dropdown options come from the server instead of the fixed list above - a
// reference-backed (is_target_value) custom field's own table (e.g. every real Customs House
// Code), fetched per-job via listCustomFieldReferenceOptions rather than hardcoded, so
// updating the reference sheet changes the dropdown without a frontend redeploy. Each one is
// narrowed to the rows relevant to THIS job by matching the table's own FIRST match column
// against the current value of the other job field named here - a Chennai shipment only ever
// needs to choose between Chennai's own codes (Sea vs Air), not every city's. The automatic
// lookup still uses the SECOND key (transport mode) too, to pick the exact one by default -
// only the dropdown's own option list is narrowed by just the first key, so the operator can
// still correct the mode by picking the other code for the same city.
const DYNAMIC_REFERENCE_DROPDOWN_FILTER_BY: Record<string, string> = {
  CustomsHouseCode: "port_of_entry",
  // Same shape as CustomsHouseCode: a forwarder's own branch list only ever matters once you
  // know WHICH forwarder (ERP Quotation Name / Erp Entry Name) - AIT's own branches, not
  // Kuehne+Nagel's. Reference table seeded from the Super Admin's own "Billing customers"
  // sheet (Organization -> branch code, one row per branch).
  branch_name: "Erp Entry Name",
};
// Fields that offer every previously-typed value as a dropdown (same server-side reference
// table as DYNAMIC_REFERENCE_DROPDOWN_FILTER_BY, fetched the same way) while still taking a
// brand-new typed value - unlike that one, this never replaces free text with a closed
// <select>: the operator can pick a known name or type one that's not there yet, and either
// way the save path (correct_field_value) remembers it so it shows up next time too.
const COMBOBOX_REFERENCE_FIELD_LABELS = new Set(["Erp Entry Name", "insurance_percentage"]);
// "ERP Quotation Address" - 4S Logistics only, UI-only (no JobFieldValue, no export mapping,
// no ERP script reads it - see check_jfv_exists.py). A read-only wide box shown right after
// "ERP Quotation Branch" in the "ERP Reference Mapping" card, resolved two-key (ERP Quotation
// Name + ERP Quotation Branch's own current values) against the quotation_address CustomField's
// reference table - fetched by LABEL (listCustomFieldReferenceOptionsByLabel), since there is
// no JobFieldValue to read a custom_field_id off of the usual way.
const QUOTATION_ADDRESS_LABEL = "quotation_address";
// A reference table backing a combobox/dropdown is a LEARNED (raw text -> corrected value)
// mapping, not a plain list of unique names - the same resolved value can legitimately be
// re-learned from several different raw texts (operator corrections on different jobs), so the
// rows themselves are never deduped. The dropdown built FROM them must dedupe its own option
// list, or a value learned twice shows twice.
const dedupe = (values: string[]): string[] => [...new Set(values)];
// A combobox field whose starting value should be SUGGESTED (not forced) from another field on
// the same job, fuzzy-matched against this field's own reference table - see
// suggestCustomFieldValue. Different customers often carry a fixed, previously-quoted premium
// rate, so a Nokia shipment should start pre-filled with Nokia's own known rate rather than a
// blank box or yesterday's customer's rate.
const SUGGESTED_VALUE_KEYED_ON_LABEL: Record<string, string> = {
  insurance_percentage: "Consignee",
};
// A full postal address reads better as a few short lines than as one long line the operator
// has to scroll sideways through to check - a taller, multi-line box shows it all at a glance.
const MULTILINE_FIELD_LABELS = new Set([
  "Supplier Address", "Shipper Address", "Combined Description",
]);
// A label too long to read fully under the shared truncate style wraps onto a second line
// instead of being cut off mid-word.
const WRAP_LABEL_FIELD_LABELS = new Set(["ERP Importer Address / Branch Code"]);
// A reference-backed field too large to ever offer as a plain dropdown (Shipment Port Code's
// ~72,000-row world port table) - when the automatic lookup (exact, then AI-assisted on the
// backend) leaves it blank, a manual search-and-pick box shows up right under the normal box
// so the operator can find the right row themselves by inspecting the document.
// "ERP Consignee Name" is the same scale of problem (~25,000 consignee organizations) - same
// treatment. Its paired branch field ("ERP Importer Address / Branch Code") is NOT listed
// here: it is narrowed to one consignee's own branches instead (see consigneeBranchOptions
// below), which a flat, ungated search box can't do.
const SEARCHABLE_REFERENCE_FIELD_LABELS = new Set(["Shipment Port Code", "ERP Consignee Name"]);
// Consignee / ERP Consignee Name / ERP Consignee Branch sat two-per-row in the shared grid,
// which split each box's width in half - the document's own long company names (e.g.
// "FLEXTRONICS INTERNATIONAL MANAGEMENT SERVICES LIMITED") were getting cut off mid-word.
// Each of these three now spans the full row on its own, by request.
const FULL_WIDTH_FIELD_LABELS = new Set([
  "Consignee", "ERP Consignee Name", "ERP Importer Address / Branch Code",
]);
// ReferenceSearchPicker's own placeholder, per field - found live: it was hardcoded to the
// Shipment Port Code wording ("Search by city or country...") even while showing up under ERP
// Consignee Name, which searches company names, not places. A label added to
// SEARCHABLE_REFERENCE_FIELD_LABELS without an entry here still gets a sensible generic prompt
// instead of silently inheriting the wrong one.
const REFERENCE_SEARCH_PLACEHOLDERS: Record<string, string> = {
  "Shipment Port Code": "Search by city or country to find or correct the code…",
  "ERP Consignee Name": "Search by consignee to find or correct the match…",
};
// Shipment Country Code: 2-3 LETTERS only ("IN", "IND") - no digits, no symbols. Deliberately
// format-only for now (confirmed with the user) - there is no country master data source in
// the system yet to also check the code is a REAL country, only that it LOOKS like one.
const COUNTRY_CODE_FORMAT_FIELD_LABELS = new Set(["CountryOfShipmentCode"]);
function countryCodeFormatError(raw: string): string | null {
  const v = raw.trim();
  if (!v) return null; // blank is a MISSING-value concern, not a format one - see approveActive
  return /^[A-Za-z]{2,3}$/.test(v)
    ? null
    : "Shipment Country Code must be 2-3 letters only (e.g. IN, IND) - no numbers, symbols, or longer codes.";
}
// Display-only grouping, requested for 4S Logistics' Sea Import template: the flat field grid
// read better as named sections, grouped the way an operator actually thinks about a
// shipment's paperwork rather than in whatever order marks happen to be stored. Keyed by
// doc_type; a doc_type with no entry here (any other template) keeps the plain ungrouped
// grid exactly as before. Within a doc_type, any field not listed in any group still renders,
// under a final "Other" section, so nothing silently disappears if a field is added later.
const FIELD_GROUPS_BY_DOC_TYPE: Record<string, { heading: string; labels: string[] }[]> = {
  BL: [
    {
      heading: "HBL & Shipment Details",
      labels: ["HBL No", "HBL Date", "vessel_name", "voyage_number", "Consignment Type"],
    },
    {
      heading: "Port & Transport Details",
      labels: [
        "TransportModeCode", "Shipment Country", "CountryOfShipmentCode", "Shipment City",
        "Shipment Port Code", "port_of_entry", "CustomsHouseCode",
      ],
    },
    {
      heading: "Package & Weight Details",
      labels: ["package_count", "Gross Wt", "Gross Weight Unit", "Package Unit"],
    },
    {
      heading: "Shipper Details",
      labels: ["Supplier Name", "Supplier Address"],
    },
  ],
  Invoice: [
    { heading: "Invoice Details", labels: ["Invoice No", "Invoice Date", "HBL No"] },
    {
      heading: "Value & Quantity Details",
      labels: ["Currency_type", "Product Value", "total_quantity", "unit", "Gross Wt"],
    },
    {
      heading: "Party Details",
      labels: [
        "shipper", "Shipper Address", "Consignee",
        "ERP Consignee Country", "ERP Consignee Name", "ERP Importer Address / Branch Code",
      ],
    },
    { heading: "Origin Details", labels: ["CountryOfOriginCode", "TOI"] },
  ],
  PackingList: [
    { heading: "Shipment Reference", labels: ["Invoice No", "Invoice Date", "HBL No"] },
    { heading: "Package & Weight Details", labels: ["package_count", "total_quantity", "Gross Wt"] },
  ],
  Custom: [
    { heading: "Shipment Reference", labels: ["HBL No", "package_count", "Gross Wt"] },
    {
      heading: "Charges",
      labels: [
        "Freight Amount", "Frt_Currency", "Misc Charge Amount", "Misc_Charge_Currency",
        "insurance_percentage", "insurance_amount",
      ],
    },
    { heading: "Shipper Details", labels: ["Supplier Name", "Supplier Address"] },
  ],
};
// Same grouping idea, for Additional Details - computed ("Worked out for this job") and
// manual-ask ("For the whole job") fields used to render as two separate, origin-based cards;
// these named sections mix both kinds together the way an operator actually thinks about them
// (e.g. "ERP Reference Mapping" holds both a computed field and a manual one), replacing that
// origin split entirely. A field not listed in any group still renders, under a final "Other"
// card, so nothing silently disappears.
const ADDITIONAL_DETAILS_GROUPS: { heading: string; labels: string[] }[] = [
  {
    heading: "ERP Reference Mapping",
    labels: ["Quotation Name", "Erp Entry Name", "branch_name"],
  },
  // "ERP Quotation Address" (below) is NOT listed here - unlike every other field in this
  // group, it has no JobFieldValue at all (a brand-new CustomField created after jobs were
  // already extracted - see check_jfv_exists.py). It is looked up purely client-side (two-key:
  // ERP Quotation Name + ERP Quotation Branch's own current values) and rendered directly in
  // the "ERP Reference Mapping" card below, right after branch_name.
  { heading: "Duty Details", labels: ["Payment Method"] },
  {
    heading: "Bill of Entry & Manifest Details",
    labels: ["BETypeCode", "AdvancePriorNormal", "IGM No", "IGM Date", "MBL No", "MBL Date"],
  },
];
// Display label only - label_name in the database is unchanged.
const ADDITIONAL_DETAILS_LABEL_OVERRIDES: Record<string, string> = {
  "Quotation Name": "Document Quotation Name",
  "Erp Entry Name": "ERP Quotation Name",
  branch_name: "ERP Quotation Branch",
  "Payment Method": "Duty Payment Method",
  AdvancePriorNormal: "Bill of Entry Type",
  BETypeCode: "BE Type",
  "IGM No": "IGM Number",
  "MBL No": "Master Bill of Lading Number",
  "MBL Date": "Master Bill of Lading Date",
  insurance_percentage: "Insurance Rate (%)",
  insurance_amount: "Insurance Amount",
};
// These dropdowns' own options already cover every real possibility - no blank "not found"
// choice to accidentally leave selected.
const NO_BLANK_DROPDOWN_OPTION_LABELS = new Set(["Payment Method", "AdvancePriorNormal", "BETypeCode"]);

/** Which of the four steps this job is on.
 *
 *  The SERVER already works this out - it is the badge in the jobs list - so use its answer
 *  rather than a second rule that disagrees with it. The old rule here only knew "completed"
 *  and "extracted": every other status fell through to 1, so a job whose ERP entry FAILED
 *  opened on the Documents tab, as if nothing had happened, instead of on ERP Entry where the
 *  failure and the fields to correct actually are. `status` is only a fallback for the moment
 *  before the job has loaded its stage.
 */
/** Which stage to open a job on, from the stage the server reports.
 *
 * The server now speaks the same seven words the rail does, so most stages map straight
 * across. The rest are outcomes rather than places — a failed or running job belongs on ERP
 * Submission, which is where the outcome is shown.
 */
function tabFromStage(job: JobDetail, isGk2: boolean): TabKey {
  const { stage, status, documents } = job;
  // A mandatory document is missing RIGHT NOW (outer_status "Missing Documents") - checked
  // first, ahead of everything else below, since this can be true even for a job whose own
  // `stage` says it progressed further already (a document removed or swapped out AFTER a
  // job was extracted leaves job.status/stage exactly where they were - see _outer_status's
  // own identical precedence on the backend). Always opens on Document Capture so there is
  // somewhere to actually fix it, regardless of what tab the stale stage would otherwise pick.
  if (job.missing_documents && job.missing_documents.length > 0) return "capture";
  // AI processing just finished, but nobody has looked at a single document yet — the
  // backend's own stage computation defaults straight to "Data Validation" the moment there
  // is no blocking cross-check, well before anything on Data Extraction has actually been
  // approved. Trust the real per-document gate here instead of that coarser label, so a
  // freshly-extracted job opens where there is still something to do, not past it. GK2
  // checks their OWN gk2_approved column — GK1 having approved everything already does not
  // mean GK2 has looked at a single document yet.
  if (
    status === "extracted" &&
    documents.filter((d) => d.is_uploaded).some((d) => !(isGk2 ? d.gk2_approved : d.approved))
  ) {
    return "extraction";
  }
  const direct = STEPS.find((s) => s.label === stage);
  if (direct) return direct.key;
  switch (stage) {
    case "Completed":
    case "Duplicate":
    case "Failed":
    case "Running":
      return "submission";
    // A job whose LAST recorded move (before this merge) was into one of these two now-gone
    // stages re-opens on "extraction" — that is exactly where their content lives today.
    case "Dump Data":
    case "Manual Data Entry":
      return "extraction";
  }
  if (status === "completed" || status === "failed") return "submission";
  if (status === "extracted" || status === "processing") return "validation";
  return "capture";
}

/** "90s" -> "1m 30s", so the OCR progress bar's ETA reads like a duration a person would say,
 *  not a raw second count. */
function formatEtaSeconds(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

export function JobRunPage() {
  const { jobId = "" } = useParams();
  const navigate = useNavigate();
  // Opened in its own tab (window.open, not client-side routing - see JobsPage.tsx's
  // onRowClick), so there is no real browser history to go "back" to: this tab's own history
  // has only ever had this one URL in it. JobsPage carries the screen it was opened FROM as
  // ?from=new|submitted|completed when it opens this tab, so the "back to Jobs" links here
  // can return to that SAME screen instead of landing on the plain, uncategorized /jobs list
  // (which still exists but is no longer any real nav link's own destination, so it read as
  // an "old" page to land on by surprise). Missing/unrecognised -> plain /jobs, same as
  // before this existed - a job opened any other way (a dashboard box, a direct link).
  const [searchParams] = useSearchParams();
  const fromCategory = searchParams.get("from");
  const backToJobsPath = fromCategory ? `/jobs?category=${fromCategory}` : "/jobs";
  const { user } = useAuth();
  const readOnly = user?.role === "tenant_admin" || user?.role === "super_admin" || user?.role === "manager";
  // Correcting a value is separated from the rest of readOnly. An admin watching a job should
  // not upload documents, accept its cross-checks or submit its entry — those belong to the
  // operator the job is assigned to. But a Super Admin who can rewrite the whole template was
  // barred from fixing one misread value, which is the smallest and most obvious repair there
  // is, and the person most likely to spot it. A Tenant Admin stays observing. Manager is
  // strictly read-only, no exception — unlike Super Admin, never gets canEditValues.
  const canEditValues = (!readOnly || user?.role === "super_admin") && user?.role !== "manager";
  // The step-by-step replay log, the ERP's internal URL and the progress bar are diagnostics
  // for whoever RECORDED the script — step numbers, selector names and timings mean nothing
  // to an operator, and the final-page URL exposes the ERP's internals. The operator is told
  // the outcome and shown the screenshot; the machinery behind it belongs to the Super Admin.
  const showsMachinery = user?.role === "super_admin";

  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("capture");
  // Why Next was refused. Shown as a popup rather than an inline banner, because the
  // operator has just pressed a button and expects an answer to THAT press.
  // The job's own history, for the header: when it last moved and to what. Read from the
  // recorded events rather than updated_at, so the header can say WHAT happened and not
  // merely that something did.
  const [history, setHistory] = useState<JobEvent[]>([]);
  const [blocker, setBlocker] = useState<
    { key: TabKey; label: string; displayLabel: string; why: string } | null
  >(null);
  // Asked fields that already carry a value nobody has confirmed — the duty notifications
  // arrive pre-filled, so "unanswered" and "empty" are no longer the same thing.
  const [approveOpen, setApproveOpen] = useState(false);
  const [approving, setApproving] = useState(false);
  const [validationApproving, setValidationApproving] = useState(false);
  // "Hold" dismisses the popup for THIS visit only — the job stays possible_duplicate, so
  // leaving and reopening it shows the popup again until someone actually decides.
  const [dupDismissed, setDupDismissed] = useState(false);
  const [dupBusy, setDupBusy] = useState(false);
  const [dupError, setDupError] = useState<string | null>(null);
  const [uploadingId, setUploadingId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [gk2Busy, setGk2Busy] = useState(false);
  // GK2's own "Approve & Proceed" press on IRN Processing - a dummy stand-in gate, like Data
  // Validation's "Approved and Proceed", since nothing real runs here yet for GK2. Kept
  // client-side (not persisted): this step has no real backend state of its own for GK2, and
  // it resets on a fresh page load, which is fine for a confirmation step rather than a fact
  // about data. GK1's own IRN stage (IRN Documents Upload) is now real, persisted state - see
  // job.irn_documents_done below.
  const [irnApproved, setIrnApproved] = useState(false);
  const [supportingDocs, setSupportingDocs] = useState<jobsApi.SupportingDocument[]>([]);
  const [prealert, setPrealert] = useState<jobsApi.Prealert | null>(null);
  const [irnLabel, setIrnLabel] = useState("");
  const [irnFiles, setIrnFiles] = useState<File[]>([]);
  const [irnUploading, setIrnUploading] = useState(false);
  const [irnError, setIrnError] = useState<string | null>(null);
  const [irnSkipping, setIrnSkipping] = useState(false);
  const [irnApproving, setIrnApproving] = useState(false);
  const [excelBusy, setExcelBusy] = useState(false);
  const [excelError, setExcelError] = useState<string | null>(null);
  const [smartBusy, setSmartBusy] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  // Set when Submit is pressed with something still empty. The button used to be disabled
  // instead, which says "you cannot" without ever saying WHAT is missing.
  // The first empty box, so Submit can scroll straight to it instead of leaving the operator
  // to hunt down a long invoice for whichever one it means.
  // What the ERP entry is doing RIGHT NOW. play_steps photographs the page as it goes; a job
  // run never asked for those frames, so a running entry was invisible until it finished.
  const [live, setLive] = useState<jobsApi.LiveRun | null>(null);
  // How far the entry got. Kept apart from `live`, which is deliberately null unless the job
  // is running - the progress has to outlive the run, or a job opened tomorrow says nothing
  // about how far it reached. The server works it out (from the live frame while running, from
  // the saved log afterwards) so the rule lives in one place, not two.
  const [progress, setProgress] = useState<jobsApi.RunProgress | null>(null);
  // Page-by-page OCR progress for a job a single-template mailbox just created, before any
  // document has landed in a slot - see jobPullProgress. null once there is nothing left to
  // report (classification has started, or the job already has a real file in some slot).
  const [pullProgress, setPullProgress] = useState<jobsApi.PullProgress | null>(null);
  // Pressing Submit with something unfilled used to paint a red banner naming the fields and
  // leave the operator to find them. On a fourteen-line invoice that is a hunt. Instead the
  // fields are asked for one at a time, in a popup, with a count of how many are left.
  const [fixOpen, setFixOpen] = useState(false);
  const [fixAt, setFixAt] = useState(0);
  const [fixDraft, setFixDraft] = useState("");
  const [fixSaving, setFixSaving] = useState(false);
  const [fixError, setFixError] = useState<string | null>(null);
  const [ruling, setRuling] = useState<jobsApi.RulingResult | null>(null);
  const [rulingBusy, setRulingBusy] = useState(false);
  const [rulingAnswer, setRulingAnswer] = useState("");
  const [smartResult, setSmartResult] = useState<{ filename: string; matched: string[] | null; error?: string }[] | null>(null);
  // Pages a smart-upload could not confidently place - shown as draggable cards next to the
  // document slots so an operator resolves them by hand instead of them being silently gone.
  const [unclassifiedPages, setUnclassifiedPages] = useState<jobsApi.UnclassifiedPage[]>([]);
  const [draggedPageId, setDraggedPageId] = useState<string | null>(null);
  const [assigningPageId, setAssigningPageId] = useState<string | null>(null);
  // An already-placed document being dragged onto a DIFFERENT slot - "Move to" by drag,
  // sharing the same copy-then-remove-the-source recipe the right-click menu uses. Distinct
  // from draggedPageId (an Unclassified Pages card), which assigns rather than moves.
  const [draggedDocId, setDraggedDocId] = useState<string | null>(null);
  const [draggedDocFromTdocId, setDraggedDocFromTdocId] = useState<string | null>(null);
  const [docActionBusyId, setDocActionBusyId] = useState<string | null>(null);
  // An already-placed document being dragged directly OVER another specific already-placed
  // document (not just its slot's general area) - that drop target means "merge into THIS
  // one", separate from dragOverId's slot-level "land as its own, separate document" highlight.
  const [dragOverDocId, setDragOverDocId] = useState<string | null>(null);
  // Menu on an already-placed document (whole document, or one of its own pages when
  // `pageNumber` is set): "Copy and send to X" / "Move to X". mode null = the two top-level
  // choices; "copy"/"move" = the list of other document-type labels to send it to. Once a slot
  // is picked, `chosenTdocId` advances to a THIRD step ONLY when that slot already holds one or
  // more documents - merge into one of them (same as dropping directly onto it) or keep this
  // as its own, separate document. A slot with nothing in it yet skips straight to acting, same
  // as before this step existed.
  const [docMenu, setDocMenu] = useState<
    {
      docId: string; fromTdocId: string; x: number; y: number;
      mode: "copy" | "move" | null; pageNumber?: number; chosenTdocId?: string;
    } | null
  >(null);
  // Which unclassified page the zoom viewer is open on - an index into unclassifiedPages, not
  // a copy of it, so Prev/Next just move this and the viewer re-fetches that page's image.
  const [viewingPageIndex, setViewingPageIndex] = useState<number | null>(null);
  const didInit = useRef(false);

  async function refreshUnclassifiedPages() {
    try {
      setUnclassifiedPages(await jobsApi.getUnclassifiedPages(jobId));
    } catch {
      /* non-critical - the slots themselves still load and work */
    }
  }

  async function handleAssignUnclassifiedPage(pageId: string, templateDocumentId: string) {
    setAssigningPageId(pageId);
    setError(null);
    try {
      setJob(await jobsApi.assignUnclassifiedPage(jobId, pageId, templateDocumentId));
      setUnclassifiedPages((prev) => prev.filter((p) => p.id !== pageId));
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not assign that page.");
    } finally {
      setAssigningPageId(null);
      setDraggedPageId(null);
    }
  }

  async function load() {
    try {
      const j = await jobsApi.getJob(jobId);
      setJob(j);
      if (!didInit.current) {
        didInit.current = true;
        setActiveTab(tabFromStage(j, user?.role === "gk2"));
      }
    } catch {
      setError("Failed to load the job.");
    }
  }

  useEffect(() => {
    load();
    jobsApi
      .jobHistory(jobId)
      .then(setHistory)
      .catch(() => {
        /* the header falls back to the job's own timestamp */
      });
    refreshUnclassifiedPages();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // Loaded only once the IRN tab is actually opened - a job's supporting documents and its
  // supportingDocs are not needed anywhere else on the page.
  useEffect(() => {
    if (activeTab !== "irn") return;
    jobsApi
      .listSupportingDocuments(jobId)
      .then(setSupportingDocs)
      .catch(() => setSupportingDocs([]));
  }, [activeTab, jobId]);

  // Prealert is loaded once, unconditionally - shown on Document Capture (the job's first
  // stage, so an operator sees what actually arrived before touching anything) AND on IRN
  // Documents Upload, not gated to either tab specifically.
  useEffect(() => {
    jobsApi
      .getPrealert(jobId)
      .then(setPrealert)
      .catch(() => setPrealert({ available: false }));
  }, [jobId]);

  async function handleIrnUpload() {
    if (!irnLabel.trim() || irnFiles.length === 0) return;
    setIrnUploading(true);
    setIrnError(null);
    try {
      const doc = await jobsApi.uploadSupportingDocuments(jobId, irnLabel.trim(), irnFiles);
      setSupportingDocs((prev) => [...prev, doc]);
      setIrnLabel("");
      setIrnFiles([]);
      const j = await jobsApi.getJob(jobId);
      setJob(j);
    } catch {
      setIrnError("Could not upload that document. Try again.");
    } finally {
      setIrnUploading(false);
    }
  }

  async function handleIrnDelete(docId: string) {
    try {
      await jobsApi.deleteSupportingDocument(jobId, docId);
      setSupportingDocs((prev) => prev.filter((d) => d.id !== docId));
    } catch {
      setIrnError("Could not remove that document. Try again.");
    }
  }

  async function handleIrnSkip() {
    setIrnSkipping(true);
    setIrnError(null);
    try {
      await jobsApi.skipIrnDocuments(jobId);
      const j = await jobsApi.getJob(jobId);
      setJob(j);
    } catch {
      setIrnError("Could not mark this stage complete. Try again.");
    } finally {
      setIrnSkipping(false);
    }
  }

  async function handleIrnApprove() {
    setIrnApproving(true);
    setIrnError(null);
    try {
      await jobsApi.approveIrnDocuments(jobId);
      const j = await jobsApi.getJob(jobId);
      setJob(j);
    } catch {
      setIrnError("Could not mark this stage complete. Try again.");
    } finally {
      setIrnApproving(false);
    }
  }

  async function openSupportingDocumentFile(docId: string, storedAs: string, name: string) {
    try {
      const url = await jobsApi.supportingDocumentFileUrl(jobId, docId, storedAs);
      window.open(url, "_blank", "noopener");
    } catch {
      setIrnError(`Could not open ${name}.`);
    }
  }

  async function openPrealertOriginal() {
    try {
      const url = await jobsApi.prealertOriginalUrl(jobId);
      window.open(url, "_blank", "noopener");
    } catch {
      setIrnError("Could not open the original email.");
    }
  }

  async function openPrealertAttachment(storedAs: string, name: string) {
    try {
      const url = await jobsApi.prealertAttachmentUrl(jobId, storedAs);
      window.open(url, "_blank", "noopener");
    } catch {
      setIrnError(`Could not open ${name}.`);
    }
  }

  // Poll only while it is actually running, and stop the moment it is not: an idle job page
  // must not sit there asking the server for a screenshot every second forever.
  useEffect(() => {
    // NO JOB YET. This runs on the very first render, before the job has loaded - so there is
    // nothing to ask about and, more to the point, nothing to take an id from. Asking anyway
    // threw on `job.id` and took the whole page down to a blank screen. TypeScript did not
    // catch it because the call said `job!.id`: the `!` promises the compiler that job is not
    // null, inside a branch entered precisely because it might be.
    if (!job) {
      setLive(null);
      return;
    }
    if (job.status !== "processing") {
      setLive(null);
      // Not running: ask ONCE for how far it got, and do not poll. The answer comes from the
      // log the run saved, so it is as true tomorrow as it is now.
      let dropped = false;
      jobsApi
        .jobLive(job.id)
        .then((f) => {
          if (!dropped) setProgress(f.progress ?? null);
        })
        .catch(() => {
          /* no progress to show is not an error worth a banner */
        });
      return () => {
        dropped = true;
      };
    }
    // Taken once, here, where the guards above have already proved there IS a job. Nothing
    // below needs a `!` to promise the compiler something the code has not established - which
    // is exactly the promise that put a blank page in front of the operator.
    const runningJobId = job.id;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        const f = await jobsApi.jobLive(runningJobId);
        if (stop) return;
        setLive(f);
        setProgress(f.progress ?? null);
        if (!f.live) {
          // the run has finished - pull the job again so its outcome replaces the live view
          load();
          return;
        }
      } catch {
        /* a missed frame is not worth showing an error for; the next one will do */
      }
      if (!stop) timer = setTimeout(tick, 1500);
    }
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [job?.id, job?.status]);

  // Page-by-page OCR progress for a job a single-template mailbox just created, before any
  // document has a real file in it yet - see jobsApi.jobPullProgress. Stops the moment any
  // slot gets filled (classification has resolved it one way or another) or the job reaches
  // a settled status - from there on the job's own stage/status already says what's happening.
  const hasAnyUploadedDoc = job?.documents.some((d) => d.is_uploaded) ?? false;
  useEffect(() => {
    if (!job) {
      setPullProgress(null);
      return;
    }
    const settled = job.status === "completed" || job.status === "failed"
      || job.status === "possible_duplicate";
    if (settled || hasAnyUploadedDoc) {
      setPullProgress(null);
      return;
    }
    const watchedJobId = job.id;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        const p = await jobsApi.jobPullProgress(watchedJobId);
        if (stop) return;
        setPullProgress(p.active ? p : null);
      } catch {
        /* a missed frame is not worth showing an error for; the next one will do */
      }
      if (!stop) timer = setTimeout(tick, 2000);
    }
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [job?.id, job?.status, hasAnyUploadedDoc]);

  // Extraction now starts on its own — the moment every slot is filled, or on a manual
  // (Re-run) press — and runs in the background, so there is nothing here to watch a live
  // feed for. Just keep reloading the job until its status moves off "extracting" and the
  // "AI Processing" badge gives way to whatever the extraction actually decided.
  useEffect(() => {
    if (!job || job.status !== "extracting") return;
    const jobId2 = job.id;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        const j = await jobsApi.getJob(jobId2);
        if (stop) return;
        setJob(j);
        if (j.status === "extracting") timer = setTimeout(tick, 1500);
      } catch {
        if (!stop) timer = setTimeout(tick, 1500);
      }
    }
    timer = setTimeout(tick, 1500);
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [job?.id, job?.status]);

  /**
   * Upload one or more files into a slot. A shipment covered by three invoices is three
   * files in the Invoice slot, so the picker takes several at once and they are sent one
   * after another — each response carries the whole job back, and sending them in parallel
   * would let the last reply win and hide the others.
   */
  async function handleUpload(
    templateDocumentId: string,
    picked: File | null | undefined | FileList,
  ) {
    const files = !picked ? [] : picked instanceof File ? [picked] : Array.from(picked);
    if (!files.length) return;
    // Word/Excel are accepted here too (converted to PDF server-side - see office_convert) -
    // a multi-sheet Excel workbook still gets rejected, but only once the server tries to
    // convert it and finds more than one sheet, since there's no way to tell sheet count from
    // the file alone on this side.
    if (files.some((f) => !/\.(pdf|png|jpe?g|docx?|xlsx?)$/i.test(f.name))) {
      setError("Only PDF, PNG, JPG, Word, or Excel files are allowed.");
      return;
    }
    setError(null);
    setUploadingId(templateDocumentId);
    try {
      for (const f of files) {
        setJob(await jobsApi.uploadJobDocument(jobId, templateDocumentId, f));
      }
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Upload failed.");
    } finally {
      setUploadingId(null);
    }
  }

  async function handleRemoveFile(jobDocumentId: string) {
    setError(null);
    setUploadingId(jobDocumentId);
    try {
      setJob(await jobsApi.deleteJobDocumentFile(jobId, jobDocumentId));
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not remove that file.");
    } finally {
      setUploadingId(null);
    }
  }

  /** "Copy and send to X" — the document stays where it is AND a fresh, independent copy is
   *  extracted into the other slot too. For a page that genuinely is two documents at once
   *  (a combined BL/Freight sheet, say). */
  async function handleCopyDocumentToSlot(jobDocumentId: string, targetTemplateDocumentId: string) {
    setError(null);
    setDocActionBusyId(jobDocumentId);
    try {
      setJob(await jobsApi.copyJobDocumentToSlot(jobId, jobDocumentId, targetTemplateDocumentId));
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not copy that document.");
    } finally {
      setDocActionBusyId(null);
    }
  }

  /** "Move to X" — the same copy, immediately followed by removing the original from its old
   *  slot (the existing Remove button's own call). Also what dragging a placed document onto a
   *  different slot does. */
  async function handleMoveDocumentToSlot(jobDocumentId: string, targetTemplateDocumentId: string) {
    setError(null);
    setDocActionBusyId(jobDocumentId);
    try {
      await jobsApi.copyJobDocumentToSlot(jobId, jobDocumentId, targetTemplateDocumentId);
      setJob(await jobsApi.deleteJobDocumentFile(jobId, jobDocumentId));
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not move that document.");
    } finally {
      setDocActionBusyId(null);
    }
  }

  /** ONE page of a multi-page document, copied into another slot as its own single-page
   *  document — the source document (every page of it) is left exactly as it was. */
  async function handleCopyDocumentPageToSlot(
    jobDocumentId: string,
    pageNumber: number,
    targetTemplateDocumentId: string,
  ) {
    setError(null);
    setDocActionBusyId(jobDocumentId);
    try {
      setJob(await jobsApi.copyJobDocumentPageToSlot(jobId, jobDocumentId, pageNumber, targetTemplateDocumentId));
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not copy that page.");
    } finally {
      setDocActionBusyId(null);
    }
  }

  /** "Move page N to X" — the same per-page copy, immediately followed by cutting that page
   *  out of the source document (rebuilt from its remaining pages, or removed outright if it
   *  was the only one left). */
  async function handleMoveDocumentPageToSlot(
    jobDocumentId: string,
    pageNumber: number,
    targetTemplateDocumentId: string,
  ) {
    setError(null);
    setDocActionBusyId(jobDocumentId);
    try {
      await jobsApi.copyJobDocumentPageToSlot(jobId, jobDocumentId, pageNumber, targetTemplateDocumentId);
      setJob(await jobsApi.deleteJobDocumentPage(jobId, jobDocumentId, pageNumber));
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not move that page.");
    } finally {
      setDocActionBusyId(null);
    }
  }

  /** Dropping a whole document directly ONTO an already-placed document (as opposed to onto
   *  the slot's general area) means "this belongs WITH that one" - merges its pages onto the
   *  end of the target, then (since dragging is always a move, same as handleMoveDocumentToSlot)
   *  removes the source outright. */
  async function handleMergeDocumentIntoDocument(sourceJobDocumentId: string, targetJobDocumentId: string) {
    setError(null);
    setDocActionBusyId(sourceJobDocumentId);
    try {
      await jobsApi.mergeJobDocumentIntoDocument(jobId, targetJobDocumentId, sourceJobDocumentId);
      setJob(await jobsApi.deleteJobDocumentFile(jobId, sourceJobDocumentId));
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not merge that document.");
    } finally {
      setDocActionBusyId(null);
    }
  }

  /** The "..." menu's own "merge into X" choice (as opposed to dragging) - keeps the source
   *  untouched (a copy-style merge) when `isMove` is false, or removes it afterward when true. */
  async function handleMergeDocumentIntoDocumentFromMenu(
    sourceJobDocumentId: string,
    targetJobDocumentId: string,
    isMove: boolean,
  ) {
    setError(null);
    setDocActionBusyId(sourceJobDocumentId);
    try {
      const updated = await jobsApi.mergeJobDocumentIntoDocument(jobId, targetJobDocumentId, sourceJobDocumentId);
      setJob(isMove ? await jobsApi.deleteJobDocumentFile(jobId, sourceJobDocumentId) : updated);
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not merge that document.");
    } finally {
      setDocActionBusyId(null);
    }
  }

  /** The single-page version of handleMergeDocumentIntoDocumentFromMenu - merges ONE page into
   *  an already-placed document instead of sending it in as its own new entry. */
  async function handleMergeDocumentPageIntoDocument(
    sourceJobDocumentId: string,
    pageNumber: number,
    targetJobDocumentId: string,
    isMove: boolean,
  ) {
    setError(null);
    setDocActionBusyId(sourceJobDocumentId);
    try {
      const updated = await jobsApi.mergeJobDocumentPageIntoDocument(
        jobId, targetJobDocumentId, sourceJobDocumentId, pageNumber,
      );
      setJob(isMove ? await jobsApi.deleteJobDocumentPage(jobId, sourceJobDocumentId, pageNumber) : updated);
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not merge that page.");
    } finally {
      setDocActionBusyId(null);
    }
  }

  async function handleSmartUpload(fileList: FileList | null | undefined) {
    if (!fileList || fileList.length === 0) return;
    setSmartBusy(true);
    setError(null);
    setSmartResult(null);
    try {
      const res = await jobsApi.smartUpload(jobId, Array.from(fileList));
      setJob(res.detail);
      setSmartResult(res.results);
      await refreshUnclassifiedPages();
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Smart upload failed.");
    } finally {
      setSmartBusy(false);
    }
  }

  async function runExtract() {
    // The request now returns as soon as extraction has STARTED, not once it is done — the job
    // comes back with status "extracting", and the poll effect above takes it from there. Move
    // to Data Extraction so the operator lands where the "AI Processing" progress actually
    // shows, rather than staying on Document Capture wondering if the press did anything.
    setExtracting(true);
    setError(null);
    try {
      const j = await jobsApi.extractJob(jobId);
      setJob(j);
      setActiveTab("extraction");
      // A fresh read means a fresh review of everything downstream too. The backend already
      // clears the document/validation approvals this depends on; IRN Processing's own
      // "Approved" state lives only here in the browser, so nothing else would ever clear it
      // — without this, a job re-run after already reaching IRN Processing kept showing it as
      // approved, letting the operator skip straight to Final Submit without pressing it again.
      setIrnApproved(false);
      setActiveFileId(null);
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Extraction failed.");
    } finally {
      setExtracting(false);
    }
  }

  async function decideVerification(linkId: string, accept: boolean) {
    setError(null);
    try {
      setJob(await jobsApi.setVerificationDecision(jobId, linkId, accept));
    } catch {
      setError("Could not update that check.");
    }
  }

  async function checkRuling(answer?: string) {
    setRulingBusy(true);
    setError(null);
    try {
      const res = await jobsApi.evaluateRuling(jobId, answer);
      setRuling(res);
      if (!res.needs_input) setRulingAnswer("");
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not evaluate the ruling.");
    } finally {
      setRulingBusy(false);
    }
  }

  /** Clear this job's failed run and stand it ready to enter again. Nothing is lost.
   *
   *  This used to restart from the first stage - deleting every extracted value and every
   *  verification approval - so correcting one rejected field and pressing it threw away all
   *  the other work. No confirmation is asked for any more, because there is nothing to lose:
   *  the values stay exactly as they are and the job simply becomes runnable again. It lands
   *  on ERP Entry, which is where the operator was working.
   */
  async function rerunJob() {
    setRerunning(true);
    setError(null);
    try {
      const j = await jobsApi.rerunJob(jobId);
      setJob(j);
      setActiveTab("submission");
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not make the job ready to run.");
    } finally {
      setRerunning(false);
    }
  }

  async function submitEntry() {
    setSubmitting(true);
    setError(null);
    try {
      const j = await jobsApi.completeJob(jobId);
      setJob(j);
      // Only advance to Completed if the ERP entry actually went through.
      if (j.status === "completed") setActiveTab("submission");
      else if (j.erp_status === "error" || j.erp_status === "failed")
        // Both retries keep the extracted data now: "Run failed job again" submits straight
        // away, "Rerun job" just clears the failure and stands it ready.
        setError(
          j.erp_failed_field
            ? `The ERP rejected “${j.erp_failed_field}”. It's outlined in red below — correct it, press Save, then Run failed job again.`
            : "The ERP entry failed. Check the details below, correct anything wrong, then Run failed job again.",
        );
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not submit the entry.");
    } finally {
      setSubmitting(false);
    }
  }

  /** GK1 (operator) presses "Final Submit for Approval" — normally hands the job to GK2's own
   *  queue (gk2_status "pending"). When this tenant has turned GK2 review off for this job's
   *  shipment type, the server instead runs the real ERP submission right away (gk2_skipped);
   *  poll it through to a final outcome exactly as GK2's own "Final Approve & Proceed" does,
   *  since nobody else is going to press a button to move it along. */
  async function submitForGk2Approval() {
    setGk2Busy(true);
    setError(null);
    try {
      let fresh = await jobsApi.submitForGk2Approval(jobId);
      setJob(fresh);
      if (fresh.gk2_skipped) {
        for (let i = 0; i < 60; i++) {
          if (fresh.gk2_status !== "preparing_erp" && fresh.gk2_status !== "entering_erp") break;
          await new Promise((r) => setTimeout(r, 3000));
          fresh = await jobsApi.getJob(jobId);
          setJob(fresh);
        }
      }
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not submit this for GK2 approval.");
    } finally {
      setGk2Busy(false);
    }
  }

  /** GK2 presses "Final Approve & Proceed" — the server moves the job to "preparing_erp"
   *  ("AI - Preparing for ERP") right away, builds the import workbook, then to "entering_erp"
   *  ("ERP Entry Process Started") once it actually starts replaying the ERP script for real -
   *  a real browser run (login, ~30 recorded steps, upload, submit) that can take well over a
   *  minute, nothing like the old placeholder's fixed few seconds. Poll through BOTH of those
   *  non-final states; stop only once gk2_status lands on "submitted" or "failed". */
  async function gk2ApproveAndProceed() {
    setGk2Busy(true);
    setError(null);
    try {
      setJob(await jobsApi.gk2Approve(jobId));
      // Up to 60 tries, 3s apart (3 minutes) — comfortably past a real script run.
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const fresh = await jobsApi.getJob(jobId);
        setJob(fresh);
        if (fresh.gk2_status !== "preparing_erp" && fresh.gk2_status !== "entering_erp") break;
      }
    } catch (err) {
      if (axios.isAxiosError(err)) setError(err.response?.data?.detail ?? "Could not record the GK2 approval.");
    } finally {
      setGk2Busy(false);
    }
  }

  /** "Download as Excel" — GK1's Final Submit for Approval and GK2's Final Approve & Proceed
   *  both offer this for an Excel-entry job: the same workbook the real submission builds,
   *  rebuilt fresh from the job's current data every time it's pressed. */
  async function downloadExcel() {
    setExcelBusy(true);
    setExcelError(null);
    try {
      const { url, filename } = await jobsApi.downloadJobExcelUrl(jobId);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setExcelError(err.response?.data?.detail ?? "Could not build the Excel file for this job.");
      }
    } finally {
      setExcelBusy(false);
    }
  }

  /** Beside every document header on the IRN tab, once GK1 has chosen Approval for IRN (not
   *  Skip) - a placeholder for a feature still being designed ("later I will tell the
   *  concept"), so it is deliberately inert for now: no click handler, no backend call. Shown
   *  to GK1 (as soon as they choose Approval) and GK2 alike, since both read the same
   *  persisted job.irn_approval_requested rather than anything role-specific. When GK1 chose
   *  Skip instead, this same spot just says so - nothing to place a placeholder beside. */
  function renderIrnPlaceholder() {
    if (!job) return null;
    if (job.irn_approval_requested) {
      return (
        <div className="mt-1 flex items-center gap-2">
          <span className="text-xs text-slate-400">IRN: —</span>
          <Button variant="secondary" disabled className="!px-2 !py-1 !text-xs">
            Get DSC + IRN Number
          </Button>
        </div>
      );
    }
    if (job.irn_documents_done) {
      return <p className="mt-1 text-xs text-slate-400">Skipped</p>;
    }
    return null;
  }

  /** The original email this job was created from, if it was created from one at all - shown
   *  on both Document Capture (so an operator sees what actually arrived, before touching
   *  anything) and IRN Documents Upload. A job started any other way (a manual upload, an
   *  Excel entry) has no email to show automatically, so the IRN tab instead lists whatever
   *  was captured on Document Capture - same header either way, so this card is never a hidden
   *  inconsistency between what GK1 saw and what GK2 sees. */
  function renderPrealert() {
    return (
      <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-700">
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
          Prealert
        </p>
        {prealert === null ? (
          <p className="mt-2 text-sm text-slate-400">Loading…</p>
        ) : !prealert.available ? (
          <div className="mt-2 space-y-3">
            <p className="text-sm text-slate-400">
              No original email is on file for this job.
            </p>
            {/* The documents already captured on Document Capture - for a job started by hand
                instead of arriving by email, these ARE the pre-alert documents, just uploaded
                directly rather than attached to a mail. Shown here too so GK1 and GK2 see them
                on the IRN tab without switching back to Document Capture to check. */}
            {docSlots.some((s) => s.files.some((f) => f.is_uploaded)) && (
              <div className="grid gap-3 sm:grid-cols-2">
                {docSlots.map(({ tdocId, name, doc_type, files }) => {
                  const uploaded = files.filter((f) => f.is_uploaded);
                  if (uploaded.length === 0) return null;
                  return (
                    <div key={tdocId}>
                      <p className="text-xs font-medium text-slate-600 dark:text-slate-300">
                        {name} <span className="text-slate-400">({doc_type})</span>
                      </p>
                      {uploaded.map((d) => (
                        <JobDocPreview key={d.id} jobId={jobId} docId={d.id} pages={d.page_count} updatedAt={d.updated_at} />
                      ))}
                      {renderIrnPlaceholder()}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ) : (
          <div className="mt-2 space-y-2">
            <p className="text-sm text-slate-700 dark:text-slate-300">
              <span className="font-medium">{prealert.subject}</span>
              {prealert.sender ? ` — from ${prealert.sender}` : ""}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              {prealert.has_original_eml && (
                <button
                  type="button"
                  onClick={openPrealertOriginal}
                  className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                >
                  Download original email
                </button>
              )}
              {(prealert.attachments ?? []).map((a) => (
                <button
                  key={a.stored_as}
                  type="button"
                  onClick={() => openPrealertAttachment(a.stored_as, a.name)}
                  className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                >
                  {/* Labelled by its own filename, the same way a document slot elsewhere on
                      this page shows "name (doc_type)" - an attachment carries no document
                      TYPE of its own (it hasn't been classified into a slot), so its original
                      filename is the label. */}
                  {a.name}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  /** The last ERP entry failed and the job is still submittable — so the operator can correct
   *  the rejected value and re-run just the entry, without re-extracting the documents. */
  const entryFailed =
    !!job && job.status === "extracted" &&
    (job.erp_status === "failed" || job.erp_status === "error");
  /** "Completed" but the ERP's own job number was never actually captured - its capture step
   *  may simply have failed against a differently-laid-out page (see persist_run_outcome /
   *  _outer_status's "AI - ERP Submitted"). Not trustworthy as a real success, so - unlike a
   *  genuinely completed entry - still shown as retryable (backend enforces the same rule:
   *  see _put_back_on_entry). */
  const erpJobNoCapture = job?.erp_captured?.["ERP_Job_No"];
  const erpJobNo = (typeof erpJobNoCapture === "object" ? erpJobNoCapture?.value : "")?.trim();
  const completedWithoutJobNo = !!job && job.status === "completed" && !erpJobNo;
  /**
   * The document slots, each with every file uploaded into it. A slot holds as many files as
   * the shipment has — three invoices is three files under one "Invoice" heading, not three
   * headings — so the list is grouped rather than rendered flat.
   */
  const docSlots = useMemo(() => {
    const order: string[] = [];
    const by: Record<string, { tdocId: string; name: string; doc_type: string; required: boolean; files: JobDocument[] }> = {};
    for (const d of job?.documents ?? []) {
      if (!by[d.template_document_id]) {
        by[d.template_document_id] = {
          tdocId: d.template_document_id, name: d.name, doc_type: d.doc_type,
          required: d.is_required !== false, files: [],
        };
        order.push(d.template_document_id);
      }
      by[d.template_document_id].files.push(d);
    }
    for (const k of order) by[k].files.sort((a, b) => a.file_index - b.file_index);
    return order.map((k) => by[k]);
  }, [job?.documents]);
  // Every uploaded FILE, flat (a slot with three invoices is three of these) — the rail's own
  // checklist under "Required Details Review" needs one row per file, in the template's own
  // order, same as ExtractionReview shows them. Lifted up here (rather than left inside that
  // component) so the sidebar and the content pane share ONE "which file is open" state
  // instead of the sidebar being unable to drive what the content pane shows.
  const reviewFiles = useMemo(
    () => (job?.documents ?? []).filter((d) => d.is_uploaded),
    [job?.documents],
  );
  const [activeFileId, setActiveFileId] = useState<string | null>(null);
  // The sidebar's own last row, sat below every document — same list, same click-to-open
  // pattern, just showing the fields the system asks a person for (never called "Manual Data
  // Entry" out loud; that name is internal only, this is what the operator sees) instead of
  // one document's extracted values.
  const isAdditionalDetailsActive = activeFileId === ADDITIONAL_DETAILS_ID;
  const activeFile = isAdditionalDetailsActive
    ? undefined
    : reviewFiles.find((f) => f.id === activeFileId) ?? reviewFiles[0];
  // GK2 re-reviews every document independently, on its own column — never GK1's.
  const isFileApproved = (d: JobDocument) => (user?.role === "gk2" ? d.gk2_approved : d.approved);
  // Ready when every MANDATORY slot has at least one file — an optional one is never waited
  // on. Counting files instead of slots would keep the job waiting forever once an extra
  // invoice added a row of its own.
  const allUploaded =
    docSlots.length > 0 && docSlots.every((s) => !s.required || s.files.some((f) => f.is_uploaded));
  // Fields the Super Admin ticked "ask the operator": each must be explicitly confirmed
  // (a saved corrected value) before the ERP entry may run.
  const askFields = job?.field_values.filter((fv) => fv.ask_operator) ?? [];
  // A field the system worked out itself (a custom field, computed rather than read verbatim
  // off one document) used to render duplicated on whichever document tab happened to list it
  // in its own (often default-to-every-document) source_document_ids, framed as "worked out
  // from THIS document" even when it was not read from that document at all. It belongs once,
  // here under Additional Details, common to the whole job - see the same exclusion
  // ExtractionReview's own isSelfVerifyingLabel used to apply for these fields.
  const isSelfVerifyingLabel = (label: string) => {
    const l = label.toLowerCase();
    return l.includes("verification") || l.trim().endsWith("(calculated)");
  };
  const commonComputedFields =
    job?.field_values.filter(
      (fv) =>
        fv.origin === "computed" &&
        fv.row_index == null &&
        !isSelfVerifyingLabel(fv.label_name) &&
        !ALL_RELOCATED_FIELD_LABELS.has(fv.label_name) &&
        !HIDDEN_ADDITIONAL_DETAILS_LABELS.has(fv.label_name),
    ) ?? [];
  // What Manual Data Entry actually asks for. Reference-sheet fields are asked too — the CTH
  // and RITC are marked "ask the operator" so a part missing from the sheet gets typed — but
  // they have their own screen, Dump Data, which shows them and refuses to pass while one is
  // blank. Listing them here as well put the same box on two screens, and two editable copies
  // of one value is how an edit gets typed into the one nobody is reading.
  const manualFields = askFields.filter((fv) => fv.self_filled !== true);
  // Dropdown options for an Additional Details combobox field (COMBOBOX_REFERENCE_FIELD_LABELS)
  // - every value previously saved for this custom field, fetched once per custom_field_id.
  // Separate from ExtractionReview's own copy of this same idea (a different component further
  // down this file, for the Data Extraction tab's relocated fields) - this page has no access
  // to that one's state.
  const [comboReferenceOptions, setComboReferenceOptions] =
    useState<Record<string, jobsApi.CustomFieldReferenceOption[]>>({});
  // `job` is nullable at the top of this component - read once here (where it's already been
  // null-checked for everything above) rather than inside the effect below, where a closure
  // loses that narrowing and tsc rightly refuses to compile `job.field_values`.
  const jobFieldValues = job?.field_values ?? [];
  useEffect(() => {
    const toFetch = jobFieldValues.filter(
      (fv) =>
        (COMBOBOX_REFERENCE_FIELD_LABELS.has(fv.label_name) ||
          fv.label_name in DYNAMIC_REFERENCE_DROPDOWN_FILTER_BY) &&
        fv.custom_field_id &&
        !(fv.custom_field_id in comboReferenceOptions),
    );
    if (!toFetch.length) return;
    let cancelled = false;
    toFetch.forEach((fv) => {
      jobsApi
        .listCustomFieldReferenceOptions(jobId, fv.custom_field_id!)
        .then((options) => {
          if (cancelled) return;
          setComboReferenceOptions((prev) => ({ ...prev, [fv.custom_field_id!]: options }));
        })
        .catch(() => {
          if (!cancelled) setComboReferenceOptions((prev) => ({ ...prev, [fv.custom_field_id!]: [] }));
        });
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobFieldValues, jobId]);
  // ERP Quotation Address's own reference rows (see QUOTATION_ADDRESS_LABEL) - fetched once by
  // label rather than custom_field_id, and empty (not an error) on any template other than 4S
  // Logistics' Sea Import. Only fetched once branch_name actually appears on this job, so this
  // never fires an extra request on every other template.
  const hasBranchField = jobFieldValues.some((fv) => fv.label_name === "branch_name");
  const [quotationAddressOptions, setQuotationAddressOptions] =
    useState<jobsApi.CustomFieldReferenceOption[] | null>(null);
  useEffect(() => {
    if (!hasBranchField || quotationAddressOptions !== null) return;
    let cancelled = false;
    jobsApi.listCustomFieldReferenceOptionsByLabel(jobId, QUOTATION_ADDRESS_LABEL).then((options) => {
      if (!cancelled) setQuotationAddressOptions(options);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasBranchField, jobId, quotationAddressOptions]);
  // ERP Quotation Address previews off whatever is currently PICKED in ERP Quotation Name /
  // ERP Quotation Branch, before either is saved - the operator wants to see the address while
  // still choosing, not only after committing a value (ExtractedField's own saved
  // corrected_value only updates post-save). ExtractedField's onDraftChange reports every
  // keystroke/pick; these re-sync to the saved value whenever IT changes (mirrors
  // ExtractedField's own draft/current resync), so a different field's save elsewhere on the
  // job can never clobber an in-progress, not-yet-saved pick here.
  const savedErpEntryName = (
    jobFieldValues.find((fv) => fv.row_index == null && fv.label_name === "Erp Entry Name")
      ?.corrected_value ??
    jobFieldValues.find((fv) => fv.row_index == null && fv.label_name === "Erp Entry Name")
      ?.extracted_value ??
    ""
  ).trim();
  const savedBranchName = (
    jobFieldValues.find((fv) => fv.row_index == null && fv.label_name === "branch_name")
      ?.corrected_value ??
    jobFieldValues.find((fv) => fv.row_index == null && fv.label_name === "branch_name")
      ?.extracted_value ??
    ""
  ).trim();
  const [liveErpEntryName, setLiveErpEntryName] = useState(savedErpEntryName);
  const [liveBranchName, setLiveBranchName] = useState(savedBranchName);
  useEffect(() => setLiveErpEntryName(savedErpEntryName), [savedErpEntryName]);
  useEffect(() => setLiveBranchName(savedBranchName), [savedBranchName]);
  // insurance_percentage and insurance_amount are mutually exclusive - a checkbox picks which
  // ONE the operator fills in; the ERP import reads whichever one actually holds a value (each
  // maps to its own Excel column already), so the one being hidden is cleared the moment the
  // operator switches, rather than silently keeping a stale number alongside the one they
  // actually meant. Shared between the Additional Details card below and ExtractionReview's own
  // copy on the Custom document tab (same field, shown in both places already, same as
  // insurance_percentage alone always has been) - lifted up here so the two stay in sync rather
  // than drifting into two independent checkboxes.
  const insurancePercentageFv = jobFieldValues.find(
    (fv) => fv.row_index == null && fv.label_name === "insurance_percentage",
  );
  const insuranceAmountFv = jobFieldValues.find(
    (fv) => fv.row_index == null && fv.label_name === "insurance_amount",
  );
  // Tracks which of the two is active WITHOUT ever touching either field's own stored value -
  // an earlier version cleared the hidden field's corrected_value on every toggle instead, which
  // permanently destroyed insurance_percentage's hardcoded tenant default the first time anyone
  // switched to amount mode and back (found live on MAA/SI/03328/26-27 and JOB-470880: the
  // field read blank forever after, with no way for an operator to get the default back short
  // of retyping it from memory). A hidden_from_capture custom field (never shown, never part of
  // any Excel column) is the one thing both ExtractedField copies and the Excel/ERP export
  // (_category_condition's own sibling, entry_values_and_rows in jobs.py) can read the SAME
  // decision from - the backend blanks whichever Excel column is inactive at read time instead.
  const insuranceModeFv = jobFieldValues.find(
    (fv) => fv.row_index == null && fv.label_name === "insurance_mode",
  );
  // null = neither box checked yet. Starts unselected on purpose - the operator must actively
  // pick one rather than silently inheriting a pre-checked default they could approve without
  // ever having looked at (the insurance_mode field's own hardcoded_value is blank for exactly
  // this reason). Both fields stay read-only until a choice is made - see InsuranceModeField.
  const [insuranceMode, setInsuranceModeState] = useState<"percentage" | "amount" | null>(null);
  useEffect(() => {
    if (insuranceModeFv) {
      const mode = (insuranceModeFv.corrected_value ?? insuranceModeFv.extracted_value ?? "").trim();
      setInsuranceModeState(mode === "amount" ? "amount" : mode === "percentage" ? "percentage" : null);
      return;
    }
    // No insurance_mode field on this template (not yet backfilled, or an older template this
    // feature was never added to) - fall back to the old heuristic: a non-blank amount can only
    // mean it was deliberately set on an earlier visit, since amount is never pre-filled.
    const amt = (insuranceAmountFv?.corrected_value ?? insuranceAmountFv?.extracted_value ?? "").trim();
    if (amt) setInsuranceModeState("amount");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [insuranceModeFv?.corrected_value, insuranceModeFv?.extracted_value,
      insuranceAmountFv?.corrected_value, insuranceAmountFv?.extracted_value]);
  async function switchInsuranceMode(toAmount: boolean) {
    const mode = toAmount ? "amount" : "percentage";
    setInsuranceModeState(mode);
    try {
      if (insuranceModeFv) {
        await jobsApi.correctFieldValue(insuranceModeFv.id, mode);
      }
    } finally {
      await load();
    }
  }
  // Suggested starting value for a SUGGESTED_VALUE_KEYED_ON_LABEL field (e.g. Insurance Rate
  // (%), suggested off this job's own Consignee) - only ever a default, never forced; the
  // operator can still pick a different option or type a new one. Only fetched while the field
  // is still blank (current ?? "" empty) - once it has a real value, a later Consignee
  // correction must never silently overwrite what was already saved or confirmed.
  const [suggestedValues, setSuggestedValues] = useState<Record<string, string | null>>({});
  useEffect(() => {
    const toFetch = jobFieldValues.filter((fv) => {
      const keyedOn = SUGGESTED_VALUE_KEYED_ON_LABEL[fv.label_name];
      const current = fv.corrected_value ?? fv.extracted_value ?? "";
      return keyedOn && fv.custom_field_id && !current.trim() && !(fv.custom_field_id in suggestedValues);
    });
    if (!toFetch.length) return;
    let cancelled = false;
    toFetch.forEach((fv) => {
      jobsApi
        .suggestCustomFieldValue(jobId, fv.custom_field_id!, SUGGESTED_VALUE_KEYED_ON_LABEL[fv.label_name])
        .then((value) => {
          if (cancelled) return;
          setSuggestedValues((prev) => ({ ...prev, [fv.custom_field_id!]: value }));
        })
        .catch(() => {
          if (!cancelled) setSuggestedValues((prev) => ({ ...prev, [fv.custom_field_id!]: null }));
        });
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobFieldValues, jobId]);
  // Named sections for Additional Details - see ADDITIONAL_DETAILS_GROUPS. Computed and
  // manual-ask fields are pooled together here (an operator doesn't care which one worked a
  // value out); a field not listed in any group still renders, under a final "Other" section.
  const groupedAdditionalDetailsFields = (() => {
    // "ERP Importer Address / Branch Code" and "ERP Consignee Name" both relocated onto the
    // Invoice tab's own Party Details (see RELOCATED_FIELD_LABELS_BY_DOC_TYPE) but still
    // rendered a second time here, under "Other" - removed by request, scoped to exactly these
    // two labels. Every OTHER relocated manual-ask field (insurance_percentage included) keeps
    // showing here too, same as before - only these two were ever asked to be dropped from
    // this screen.
    const pool = [
      ...commonComputedFields,
      ...manualFields.filter(
        (fv) => fv.row_index == null && !DUPLICATED_ADDITIONAL_DETAILS_LABELS.has(fv.label_name),
      ),
    ];
    const byLabel = new Map(pool.map((fv) => [fv.label_name, fv] as const));
    const used = new Set<string>();
    const sections = ADDITIONAL_DETAILS_GROUPS.map(({ heading, labels }) => {
      const items = labels
        .map((label) => byLabel.get(label))
        .filter((fv): fv is JobFieldValue => !!fv);
      items.forEach((fv) => used.add(fv.label_name));
      return { heading, items };
    }).filter((section) => section.items.length > 0);
    const leftover = pool.filter((fv) => !used.has(fv.label_name));
    if (leftover.length) sections.push({ heading: "Other", items: leftover });
    return sections;
  })();
  // Only mandatory ones hold up Submit Entry. Optional ones are still asked for and still
  // highlighted, but a value the operator does not have yet must not block the entry.
  // A field with a value the system worked out itself needs no confirming. The CTH comes
  // from the customer's own reference sheet, which is more authoritative than anything an
  // operator would retype - and requiring a "correction" on it blocked Submit while the
  // screen plainly showed the value. Same rule as the server's gate, or the button and the
  // server disagree about whether the job can go.
  // A field is answered once the operator has saved a value on it. A self-filling field —
  // the CTH and RITC, looked up in the customer's own reference sheet — counts as answered
  // on its own value, because that sheet is more authoritative than anything retyped.
  //
  // Anything else keeps needing a confirmation even when it arrives pre-filled. That matters
  // for a standing value that changes: the duty notification numbers start filled in, and the
  // operator confirming them is the step that catches the year one of them rolls over. This
  // is the server's rule exactly — the two used to differ, and a pre-filled field would have
  // shown Submit as ready while the server refused it. (An OPTIONAL field — ask_operator_
  // required false — is exempt from needing an answer at all; see the two filters below. A
  // MANDATORY per-row field genuinely does require real content, not just a saved blank — the
  // backend's own per-row gate agrees, unlike its job-level one, because "line 7's duty notn.
  // is blank" cannot become "line 7's duty notn. is confirmed blank" the way a shipment-level
  // field can.)
  const answered = (fv: JobFieldValue) =>
    (fv.corrected_value ?? "").trim() !== "" ||
    (fv.self_filled === true && (fv.value ?? "").trim() !== "");
  const askPending = askFields.filter(
    (fv) => !answered(fv) && fv.ask_operator_required !== false,
  );
  const manualPending = manualFields.filter(
    (fv) => !answered(fv) && fv.ask_operator_required !== false,
  );
  // Fields asked once per LINE ITEM (the CTH on each product line). They must be shown
  // numbered and next to the product they belong to, or the operator cannot tell which
  // value goes with which row.
  const perRowAsks = useMemo(() => {
    const rows = new Map<number, { fv: JobFieldValue; context: string }[]>();
    (job?.field_values ?? []).forEach((fv) => {
      // Same exclusion as manualFields: the CTH and RITC live on Dump Data, and showing
      // them here as well is the same value in two places.
      if (!fv.ask_operator || fv.row_index == null || fv.self_filled === true) return;
      const arr = rows.get(fv.row_index) ?? [];
      arr.push({ fv, context: "" });
      rows.set(fv.row_index, arr);
    });
    // Label each line with something the operator recognises — the product description of
    // that same row, falling back to any other extracted value on it.
    const describe = (n: number) => {
      const onRow = (job?.field_values ?? []).filter((f) => f.row_index === n && !f.ask_operator);
      const desc = onRow.find((f) => /descri/i.test(f.label_name) && (f.value ?? "").trim());
      return (desc ?? onRow.find((f) => (f.value ?? "").trim()))?.value ?? "";
    };
    return [...rows.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([n, items]) => ({
        line: n,
        // Always the same order on every line. Unsorted, the six boxes appeared in whatever
        // order the values happened to come back in - a different order on each row, so the
        // eye had to re-read every label instead of running straight down a column.
        items: items
          .map((i) => i.fv)
          .sort((a, b) => a.label_name.localeCompare(b.label_name)),
        context: describe(n),
      }));
  }, [job]);
  // Everything still unanswered, as ONE list. Job-level fields first - they describe the whole
  // shipment - then the line items in row order, each carrying the product it belongs to so the
  // popup can say WHICH line it is asking about.
  const fixQueue = useMemo(() => {
    const out: { fv: JobFieldValue; where: string }[] = [];
    askFields.forEach((fv) => {
      if (!answered(fv) && fv.ask_operator_required !== false) {
        out.push({ fv, where: "This shipment" });
      }
    });
    perRowAsks.forEach((row) => {
      row.items.forEach((fv) => {
        if (!answered(fv) && fv.ask_operator_required !== false) {
          out.push({
            fv,
            where: `Line ${row.line}${row.context ? ` — ${row.context}` : ""}`,
          });
        }
      });
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.field_values]);

  const unresolved = job?.verifications.filter((v) => v.status !== "match" && !v.accepted).length ?? 0;

  if (!job) {
    return (
      <AppShell title="Job">{error ? <Alert>{error}</Alert> : <p className="text-sm text-slate-400">Loading…</p>}</AppShell>
    );
  }


  // Why this stage is not finished yet — null when it is. The wording is what the operator
  // is shown, so it names the actual thing to do rather than "incomplete".
  const blockedBecause = (key: TabKey): string | null => {
    switch (key) {
      case "capture": {
        const missing = docSlots.filter((s) => s.required && !s.files.some((f) => f.is_uploaded));
        if (missing.length) {
          return `Every mandatory document has to be uploaded first. Still missing: ${missing
            .map((s) => s.name)
            .join(", ")}.`;
        }
        return null;
      }
      case "extraction": {
        if (job.status === "draft" || job.status === "extracting") {
          return "The documents have not been read yet. Press Extract on Document Capture.";
        }
        const uploaded = job.documents.filter((d) => d.is_uploaded);
        // GK2 re-reviews every document independently — their own gk2_approved column,
        // never GK1's approved, so GK2 must press through this screen themselves too.
        const isGk2Tab = user?.role === "gk2";
        const unapproved = uploaded.filter((d) => !(isGk2Tab ? d.gk2_approved : d.approved));
        if (unapproved.length) {
          return `${unapproved.length} document${unapproved.length === 1 ? "" : "s"} still need${
            unapproved.length === 1 ? "s" : ""
          } "${isGk2Tab ? "Approve & Proceed" : "Submit for Approval"}": ${unapproved.map((d) => d.name).join(", ")}.`;
        }
        // Formerly Dump Data's own gate: a part the reference sheet does not carry is left
        // blank on purpose rather than guessed — and a Bill of Entry cannot go without a CTH.
        // Blank is the one thing that holds this up; the rest of that section is a view.
        //
        // hidden_from_capture excluded: that field never renders anywhere on this screen (see
        // lookedUpByRow/DumpDataView's own identical exclusion), so blocking on it here would
        // ask for something the operator has no box to type into at all - a stage that can
        // never finish. It still computes and writes a value on every row exactly as normal;
        // it just never needs a human to confirm it.
        //
        // ask_operator_required === false excluded too: a self-filled field is mandatory by
        // default (a Bill of Entry cannot go without a CTH), but not every one genuinely is -
        // ContainerTypeCode only ever applies to an FCL shipment (blank on purpose for every
        // other shipment type, see run_extraction's own container-field blanking pass) and
        // must never hold up a job that has nothing to put there at all.
        const blank = job.field_values.filter(
          (fv) =>
            fv.self_filled === true &&
            !fv.hidden_from_capture &&
            fv.ask_operator_required !== false &&
            !(fv.value ?? "").trim(),
        );
        if (blank.length) {
          return `${blank.length} value(s) were not found on the reference sheet and need typing: ${[
            ...new Set(blank.map((f) => f.label_name)),
          ].join(", ")}.`;
        }
        // Formerly Manual Data Entry's own gate.
        if (manualPending.length) {
          return `${manualPending.length} field(s) still need an answer: ${manualPending
            .map((f) => f.label_name)
            .join(", ")}.`;
        }
        return null;
      }
      case "validation":
        // A single gate: the reviewer's own "Approved and Proceed" press — GK1's and GK2's
        // are two separate columns (validation_approved / gk2_validation_approved), so GK2
        // reviewing a job GK1 already approved still has to press it themselves. That press
        // now accepts the data as-is even over an open Mismatch/Review flag — the reviewer is
        // the one deciding it's fine to move on, not the system silently deciding for them -
        // so nothing here re-checks the cross-checks themselves, only whether that press
        // has happened yet.
        return (user?.role === "gk2" ? job.gk2_validation_approved : job.validation_approved)
          ? null
          : "Press \"Approved and Proceed\" once the data has been reviewed.";
      // GK1's IRN Documents Upload is real, persisted state - done once a supporting document
      // has been uploaded, or the stage explicitly skipped (it is optional). GK2's IRN
      // Processing still has nothing real behind it - its own dummy "Approve" press is the
      // one thing that finishes it.
      case "irn":
        if (user?.role === "gk2") {
          return irnApproved ? null : "Press \"Approve & Proceed\" to mark it complete.";
        }
        return job.irn_documents_done
          ? null
          : "Upload IRN documents, or skip, to mark this stage complete.";
      case "submission":
        return job.status === "completed" ? null : "The entry has not been submitted yet.";
    }
    return null;
  };

  // A stage counts as finished only when IT and everything BEFORE it is finished.
  //
  // Checking a stage alone made a brand-new job show Data Validation, Dump Data and Manual
  // Data Entry as complete before a single document had been uploaded: each asks "is anything
  // outstanding?", and on an empty job the answer is no — there are no cross-checks to fail,
  // no looked-up values to be blank and no questions to answer. An empty list of problems is
  // not the same as no problems; it means nothing has happened yet. The chain is what tells
  // them apart.
  const finishedThrough = (key: TabKey): boolean => {
    for (const step of STEPS) {
      if (blockedBecause(step.key)) return false;
      if (step.key === key) return true;
    }
    return true;
  };

  // When the job last actually moved. The newest history row whose stage matches where the
  // job is now; failing that, the newest row at all.
  const lastMoved = (() => {
    const rows = [...history].sort((a, b) => (a.at < b.at ? 1 : -1));
    const hit = rows.find((e) => e.stage && e.stage === job.stage) ?? rows[0];
    if (!hit) return null;
    const d = new Date(hit.at);
    return Number.isNaN(d.getTime())
      ? null
      : d.toLocaleString(undefined, {
          day: "2-digit",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
        });
  })();

  const stageIndex = STEPS.findIndex((s) => s.key === activeTab);
  const nextStage = stageIndex >= 0 ? STEPS[stageIndex + 1] : undefined;

  // The "irn" step reads differently per role everywhere it is named on screen (the rail,
  // "Next: X", the blocker banner) - GK1's IRN Documents Upload vs GK2's unchanged IRN
  // Processing. One place for that instead of repeating the same ternary at each call site.
  const displayLabelFor = (key: TabKey): string =>
    key === "irn"
      ? user?.role === "gk2"
        ? "IRN Processing"
        : "IRN Documents Upload"
      : STEPS.find((s) => s.key === key)?.displayLabel ?? key;

  // Pre-filled but unconfirmed. Refusing to move on would be right if these were blank, but
  // they are not — the operator has read them and changed nothing, which IS an answer. So ask
  // for it once, rather than making them retype six values on every line to say "yes, fine".
  const unconfirmedButFilled =
    askPending.length > 0 && askPending.every((fv) => (fv.value ?? "").trim());

  const approveDuplicateNow = async () => {
    setDupBusy(true);
    setDupError(null);
    try {
      await jobsApi.approveDuplicate(jobId);
      await load();
    } catch {
      setDupError("Could not record the decision — try again.");
    } finally {
      setDupBusy(false);
    }
  };

  const deleteDuplicateNow = async () => {
    setDupBusy(true);
    setDupError(null);
    try {
      await jobsApi.deleteJob(jobId);
      navigate("/jobs");
    } catch {
      setDupError("Could not delete the job — try again.");
      setDupBusy(false);
    }
  };

  const approveValidationNow = async () => {
    setValidationApproving(true);
    try {
      await jobsApi.approveValidation(jobId);
      await load();
    } catch {
      setError("Could not record the approval — a cross-check may still be open.");
    } finally {
      setValidationApproving(false);
    }
  };

  const approveUnchanged = async () => {
    setApproving(true);
    try {
      // Confirming is saving the value that is already there — the same corrected_value the
      // submit gate looks for, so nothing downstream needs to know this happened in bulk.
      for (const fv of askPending) {
        await jobsApi.correctFieldValue(fv.id, (fv.value ?? "").trim());
      }
      await load();
      setApproveOpen(false);
      if (nextStage) setActiveTab(nextStage.key);
    } catch {
      setError("Could not record the approval.");
    } finally {
      setApproving(false);
    }
  };

  // The FIRST unfinished stage at or before the one on screen. Jumping ahead in the rail is
  // allowed — looking is not gated — but moving forward from there was refusing with the
  // wrong reason: it complained about the stage you were on while the real blocker was one
  // you had skipped past, and never said which.
  const firstUnfinished = (): { key: TabKey; label: string; displayLabel: string; why: string } | null => {
    for (const step of STEPS) {
      const why = blockedBecause(step.key);
      if (why) return { key: step.key, label: step.label, displayLabel: step.displayLabel, why };
      if (step.key === activeTab) break;
    }
    return null;
  };

  const goNext = () => {
    const blocker = firstUnfinished();
    if (blocker) {
      // Formerly Manual Data Entry's own shortcut: a pre-filled-but-unconfirmed ask field
      // doesn't need retyping, just a bulk confirm — but only when THAT is actually what is
      // blocking "extraction" now, not an unapproved document or a still-blank reference-
      // sheet lookup, which need their own real fix rather than a rubber stamp.
      const uploaded = job.documents.filter((d) => d.is_uploaded);
      const isGk2Tab = user?.role === "gk2";
      const docsApproved = uploaded.every((d) => (isGk2Tab ? d.gk2_approved : d.approved));
      const noSelfFilledBlanks = !job.field_values.some(
        (fv) =>
          fv.self_filled === true &&
          !fv.hidden_from_capture &&
          fv.ask_operator_required !== false &&
          !(fv.value ?? "").trim(),
      );
      if (blocker.key === "extraction" && docsApproved && noSelfFilledBlanks && unconfirmedButFilled) {
        setActiveTab("extraction");
        setApproveOpen(true);
        return;
      }
      setBlocker(blocker);
      return;
    }
    if (nextStage) {
      setActiveTab(nextStage.key);
      // Leave a trace in the history. Fire-and-forget: a job must never fail to advance
      // because the note about advancing could not be written.
      jobsApi
        .recordStage(jobId, nextStage.label)
        .then(() => jobsApi.jobHistory(jobId).then(setHistory))
        .catch(() => {
          /* the stage still moved */
        });
    }
  };

  // The job's stages belong in the app's REAL left rail, under Dashboard and Jobs — not in a
  // card floating in the content area beside it, which read as two competing navigations.
  const stageNav = (
    <div>
      {/* No top margin: this is now the whole rail, not a section under the app links. */}
      <h3 className="px-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
        Job Progress
      </h3>
      <ul className="mt-2 space-y-1">
        {STEPS.map((step, i) => {
          const n = i + 1;
          // Green ONLY when the stage is genuinely finished — itself AND everything before
          // it — and never for one with nothing behind it. Checked exactly the way Next
          // checks it, so the tick and the button cannot disagree.
          const done = step.built && finishedThrough(step.key);
          const current = activeTab === step.key;
          return (
            <li key={step.key}>
              <button
                type="button"
                aria-current={current ? "step" : undefined}
                onClick={() => setActiveTab(step.key)}
                className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-medium transition-colors ${
                  current
                    ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
                    : "text-slate-700 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-slate-50"
                }`}
              >
                <span
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px] font-semibold ${
                    done
                      ? "border-emerald-500 bg-emerald-500 text-white"
                      : current
                        ? "border-indigo-600 bg-indigo-600 text-white"
                        : "border-slate-300 bg-transparent text-slate-400 dark:border-slate-600"
                  }`}
                >
                  {done ? "✓" : n}
                </span>
                <span className="min-w-0 flex-1 truncate">
                  {step.key === "submission"
                    ? user?.role === "gk2"
                      ? "Final Approve & Proceed"
                      : job.gk2_status == null && job.gk2_required === false
                        ? "Final Submit"
                        : "Final Submit for Approval"
                    : displayLabelFor(step.key)}
                </span>
              </button>
              {/* Every uploaded document, one line each, nested under Required Details
                  Review instead of a row of tabs above the extracted values — pressing one
                  opens it on the right, and a document already approved gets its own tick
                  the moment it is, the same way the stage above it does. Shown only while
                  this step is the one open, so the rail stays short everywhere else. */}
              {step.key === "extraction" && current && (
                <ul className="ml-7 mt-1 space-y-0.5 border-l border-slate-200 pl-3 dark:border-slate-700">
                  {reviewFiles.map((f) => {
                    const approved = isFileApproved(f);
                    const isOpen = activeFile?.id === f.id;
                    return (
                      <li key={f.id}>
                        <button
                          type="button"
                          onClick={() => setActiveFileId(f.id)}
                          className={`flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-xs transition-colors ${
                            isOpen
                              ? "bg-indigo-50 font-medium text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
                              : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-50"
                          }`}
                        >
                          <span
                            className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border text-[8px] ${
                              approved
                                ? "border-emerald-500 bg-emerald-500 text-white"
                                : "border-slate-300 bg-transparent dark:border-slate-600"
                            }`}
                          >
                            {approved ? "✓" : ""}
                          </span>
                          <span className="min-w-0 flex-1 truncate">
                            {f.name}
                            {f.original_name ? <span className="opacity-60"> · {f.original_name}</span> : null}
                            {f.set_index ? <span className="opacity-60"> · set {f.set_index}</span> : null}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                  <li>
                    <button
                      type="button"
                      onClick={() => setActiveFileId(ADDITIONAL_DETAILS_ID)}
                      className={`flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-xs transition-colors ${
                        isAdditionalDetailsActive
                          ? "bg-indigo-50 font-medium text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
                          : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-50"
                      }`}
                    >
                      <span
                        className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border text-[8px] ${
                          manualPending.length === 0
                            ? "border-emerald-500 bg-emerald-500 text-white"
                            : "border-slate-300 bg-transparent dark:border-slate-600"
                        }`}
                      >
                        {manualPending.length === 0 ? "✓" : ""}
                      </span>
                      <span className="min-w-0 flex-1 truncate">Additional Details</span>
                    </button>
                  </li>
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );

  return (
    <AppShell
      title={`Job · ${job.reference}`}
      subtitle={
        <span className="flex items-center gap-2">
          {job.group_name}
          {job.mode && (
            <span className="inline-flex items-center gap-1 text-slate-500 dark:text-slate-400">
              <span aria-hidden="true">·</span>
              <ModeIcon mode={job.mode} />
              {job.mode}
            </span>
          )}
        </span>
      }
      sidebar={stageNav}
      actions={
        <div className="flex items-center gap-3">
          <div className="text-right">
            {(() => {
              const outer = job.outer_status ?? job.stage ?? job.status;
              const busy =
                job.status === "processing" || job.status === "extracting" ||
                outer === "AI - Processing" || outer === "AI - Preparing for ERP" ||
                outer === "ERP Entry Process Started";
              const cls =
                job.status === "completed" || outer === "AI - ERP Submitted"
                  ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"
                  : job.status === "failed" || job.gk2_status === "failed"
                    ? "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"
                    : busy
                      ? "bg-sky-50 text-sky-700 dark:bg-sky-500/10 dark:text-sky-300"
                      : outer === "Pending GK2 Approval"
                        ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
                        : outer === "GK1 Review" || outer === "GK1 Reviewing"
                          ? "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300"
                          : outer === "Missing Documents"
                            ? "bg-amber-50 text-amber-800 ring-1 ring-amber-300 dark:bg-amber-500/10 dark:text-amber-300"
                            : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300";
              return (
                <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${cls}`}>
                  {busy && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-500" />}
                  {outer}
                </span>
              );
            })()}
            {/* Named right here too, not only on the Document Capture tab below - an operator
                scanning the header should see WHICH document without switching tabs. */}
            {job.missing_documents && job.missing_documents.length > 0 && (
              <p className="mt-0.5 max-w-[220px] text-[11px] text-amber-700 dark:text-amber-400">
                Missing: {job.missing_documents.join(", ")}
              </p>
            )}
            {/* WHEN it reached this stage, from the job's own history. "Last updated" alone
                says something moved without saying what, and on a job parked for two days
                that is the question being asked. */}
            {lastMoved && (
              <p className="mt-0.5 text-[11px] text-slate-400">since {lastMoved}</p>
            )}
          </div>
          <Link
            to={backToJobsPath}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Back to dashboard
          </Link>
        </div>
      }
    >
      {/* A single-template mailbox's job exists from the moment OCR starts, long before any
          document lands in a slot - this is the one place that says what's actually happening
          in that gap, instead of Document Capture just sitting there looking frozen. */}
      {pullProgress?.active && (
        <div className="mb-4 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 dark:border-sky-500/30 dark:bg-sky-500/10">
          <div className="flex items-center justify-between gap-3 text-sm font-medium text-sky-800 dark:text-sky-200">
            <span className="flex items-center gap-2">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-500" />
              Reading the attached documents — {pullProgress.done ?? 0} of {pullProgress.total ?? 0} page(s)
            </span>
            {pullProgress.eta_seconds != null && (
              <span className="shrink-0 text-xs text-sky-600 dark:text-sky-300">
                ~{formatEtaSeconds(pullProgress.eta_seconds)} remaining
              </span>
            )}
          </div>
          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-sky-100 dark:bg-sky-500/20">
            <div
              className="h-full rounded-full bg-sky-500 transition-all"
              style={{
                width: `${Math.round(
                  ((pullProgress.done ?? 0) / Math.max(pullProgress.total ?? 1, 1)) * 100,
                )}%`,
              }}
            />
          </div>
        </div>
      )}
      <div className="mb-4 flex items-center justify-between">
        <Link to={backToJobsPath} className="inline-block text-sm text-indigo-600 hover:underline">← All jobs</Link>
        {(job.status === "failed" || completedWithoutJobNo) && (
          <Button
            size="sm"
            variant="secondary"
            onClick={rerunJob}
            isLoading={rerunning}
            title={
              completedWithoutJobNo
                ? "This run reported success but never captured the ERP's own job number - there is no proof it actually went in. Clears it and makes this job ready to enter again. Everything you have filled in is kept."
                : "Clears the last failed run and makes this job ready to enter again. Everything you have filled in is kept."
            }
          >
            ↻ Rerun job
          </Button>
        )}
      </div>
      {error && <div className="mb-6"><Alert>{error}</Alert></div>}

      {/* One of this job's documents was removed after it was already extracted (an
          operator deleting the wrong file, or the custom-filter-page sweep stripping one
          that turned out to be junk-reference content) - its custom fields were cleared
          along with it, and this is the only thing that says so. Shown above every tab,
          not just Document Capture, since whoever opens the job next could land anywhere. */}
      {job.needs_reextraction && job.status !== "extracting" && (
        <div className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300">
          <span>⚠️ One of this job's documents changed since it was last extracted — press Extract to refresh its data.</span>
          {!readOnly && (
            <Button size="sm" variant="secondary" onClick={runExtract} isLoading={extracting}>
              Extract now
            </Button>
          )}
        </div>
      )}

      {/* Extraction is running RIGHT NOW - shown above every tab, same as the banner above,
          since the Document Capture tab's own "AI Processing…" pill is only visible from
          there. Found live: an operator deleted a wrong document, re-extraction kicked off,
          and looking at Required Details Review while it ran showed only blank CTH/RITC
          fields with nothing anywhere saying why - indistinguishable from the data simply
          being gone for good. */}
      {job.status === "extracting" && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800 dark:border-sky-500/20 dark:bg-sky-500/10 dark:text-sky-300">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-500" />
          <span>AI is reading this job's documents right now — fields will fill in as soon as it finishes. This can take a minute or two.</span>
        </div>
      )}

      {/* ---------------- Document Capture ---------------- */}
      {activeTab === "capture" && (
        <Card className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold text-slate-900 dark:text-slate-50">Upload documents</h2>
            {!readOnly && (
              <div className="flex items-center gap-2">
                <label className={`inline-flex cursor-pointer items-center rounded-lg border border-indigo-200 px-3 py-1.5 text-sm font-medium text-indigo-700 hover:bg-indigo-50 dark:border-indigo-500/30 dark:text-indigo-300 dark:hover:bg-indigo-500/10 ${smartBusy ? "opacity-60" : ""}`}>
                  {smartBusy ? "Classifying…" : "Smart upload (auto-classify)"}
                  <input
                    type="file"
                    multiple
                    accept=".pdf,.png,.jpg,.jpeg,.zip,.doc,.docx,.xls,.xlsx"
                    className="hidden"
                    disabled={smartBusy}
                    onChange={(e) => {
                      handleSmartUpload(e.target.files);
                      // Selecting the SAME file(s) again later must still fire onChange.
                      e.target.value = "";
                    }}
                  />
                </label>
                {job.status === "extracting" ? (
                  // Extraction now starts on its own once every slot is filled — nothing left
                  // to press here, just something to show is actually happening.
                  <span className="inline-flex items-center gap-1.5 rounded-lg border border-sky-200 bg-sky-50 px-3 py-1.5 text-sm font-medium text-sky-700 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-300">
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-500" />
                    AI Processing…
                  </span>
                ) : (
                  <Button size="sm" onClick={runExtract} isLoading={extracting} disabled={!allUploaded}>
                    {job.status === "draft" ? "Run extraction" : "Re-run extraction"}
                  </Button>
                )}
              </div>
            )}
          </div>
          {/* What actually arrived for this job, before anything is touched - shown here only
              once loaded and only when the job really was created from an email, so a job
              started any other way (a manual upload) sees no empty "Prealert" box. */}
          {prealert?.available && <div className="mb-4">{renderPrealert()}</div>}
          {smartResult && (
            <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm dark:border-slate-800 dark:bg-slate-900">
              <p className="mb-1 font-medium text-slate-700 dark:text-slate-200">Auto-classification result</p>
              {smartResult.map((r, i) => (
                <div key={i} className="text-xs text-slate-600 dark:text-slate-400">
                  <span className="font-mono">{r.filename}</span>{" → "}
                  {r.error ? <span className="text-rose-500">{r.error}</span>
                    : r.matched && r.matched.length ? <span className="text-emerald-600">{r.matched.join(", ")}</span>
                    : <span className="text-amber-600">no match (couldn't classify)</span>}
                </div>
              ))}
            </div>
          )}
          <p className="mb-3 text-xs text-slate-400">Upload a PDF/image per slot below, or use <b>Smart upload</b> to pick several files (or a ZIP) at once and auto-route every doc — a combined file fills every type it contains, and several invoices in one PDF each become their own row.</p>

          {/* Custom ruling — which documents are required for this job */}
          {!readOnly && (
            <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50/50 p-3 text-sm dark:border-amber-500/20 dark:bg-amber-500/5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-slate-600 dark:text-slate-300">⚖️ This template may require only some documents depending on the data.</span>
                <Button size="sm" variant="secondary" onClick={() => checkRuling()} isLoading={rulingBusy}>Check required documents</Button>
              </div>
              {ruling && ruling.has_ruling === false && (
                <p className="mt-2 text-xs text-slate-400">No custom ruling on this template — all declared documents apply.</p>
              )}
              {ruling && ruling.has_ruling && (
                <div className="mt-2 text-xs">
                  {ruling.decision && <p className="text-slate-600 dark:text-slate-300">Decision: <b>{ruling.decision}</b></p>}
                  {ruling.required_documents && ruling.required_documents.length > 0 && (
                    <p className="mt-0.5 text-emerald-700 dark:text-emerald-400">Required: <b>{ruling.required_documents.join(", ")}</b></p>
                  )}
                  {ruling.reason && <p className="mt-0.5 text-slate-400">{ruling.reason}</p>}
                  {ruling.missing_documents && ruling.missing_documents.length > 0 && (
                    <p className="mt-0.5 text-amber-700 dark:text-amber-400">
                      Still to upload: <b>{ruling.missing_documents.join(", ")}</b>
                    </p>
                  )}
                  {ruling.needs_input && (
                    <div className="mt-2 rounded-md border border-amber-300 bg-white p-2 dark:border-amber-500/30 dark:bg-slate-900">
                      <p className="mb-1 font-medium text-amber-700 dark:text-amber-300">
                        ⚠ {ruling.question || "The AI needs your input to decide which documents are required."}
                      </p>
                      <div className="flex gap-2">
                        {ruling.options && ruling.options.length > 0 ? (
                          <select
                            value={rulingAnswer}
                            onChange={(e) => setRulingAnswer(e.target.value)}
                            className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                          >
                            <option value="">— select —</option>
                            {ruling.options.map((o) => (
                              <option key={o} value={o}>{o}</option>
                            ))}
                          </select>
                        ) : (
                          <input
                            value={rulingAnswer}
                            onChange={(e) => setRulingAnswer(e.target.value)}
                            placeholder="Type your answer (e.g. the incoterm / document type)"
                            className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                          />
                        )}
                        <Button size="sm" onClick={() => checkRuling(rulingAnswer)} isLoading={rulingBusy} disabled={!rulingAnswer.trim()}>Submit</Button>
                      </div>
                      <p className="mt-1 text-[11px] text-slate-400">
                        The ERP entry is blocked until this is answered.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
          <div className="grid gap-3 sm:grid-cols-2">
            {docSlots.map(({ tdocId, name, doc_type, required, files }) => {
              const isUploading = uploadingId === tdocId;
              const uploaded = files.filter((f) => f.is_uploaded);
              const isDropTarget = draggedPageId !== null
                || (draggedDocId !== null && draggedDocFromTdocId !== tdocId);
              return (
                <div
                  key={tdocId}
                  onDragOver={(e) => {
                    if (!isDropTarget) return;
                    e.preventDefault();
                    setDragOverId(tdocId);
                  }}
                  onDragLeave={() => { if (draggedPageId || draggedDocId) setDragOverId(null); }}
                  onDrop={(e) => {
                    e.preventDefault();
                    setDragOverId(null);
                    if (draggedPageId) {
                      handleAssignUnclassifiedPage(draggedPageId, tdocId);
                    } else if (draggedDocId && draggedDocFromTdocId !== tdocId) {
                      handleMoveDocumentToSlot(draggedDocId, tdocId);
                    }
                    setDraggedDocId(null);
                    setDraggedDocFromTdocId(null);
                  }}
                  className={isDropTarget
                    ? `rounded-lg p-1.5 transition-colors ${dragOverId === tdocId ? "bg-teal-50 ring-2 ring-teal-400 dark:bg-teal-500/10" : "ring-1 ring-dashed ring-teal-300 dark:ring-teal-500/30"}`
                    : undefined}
                >
                  <p className="mb-1 text-sm font-medium text-slate-900 dark:text-slate-100">
                    {name} <span className="text-xs text-slate-400">({doc_type})</span>
                    {!required && (
                      <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                        optional
                      </span>
                    )}
                    {uploaded.length > 1 && (
                      <span className="ml-1 text-xs font-normal text-slate-400">
                        — {uploaded.length} files
                      </span>
                    )}
                  </p>
                  {uploaded.map((d) => (
                    <div key={d.id} className="mb-2">
                      <div
                        draggable={!readOnly && docActionBusyId !== d.id}
                        onDragStart={(e) => {
                          // Firefox refuses to start a drag at all without setData (Chrome is
                          // lenient about this, which is how this went unnoticed at first) -
                          // same call the Unclassified Pages cards already make.
                          e.dataTransfer.setData("text/plain", d.id);
                          e.dataTransfer.effectAllowed = "move";
                          setDraggedDocId(d.id);
                          setDraggedDocFromTdocId(tdocId);
                        }}
                        onDragEnd={() => { setDraggedDocId(null); setDraggedDocFromTdocId(null); setDragOverId(null); setDragOverDocId(null); }}
                        // Dropping directly ON this specific document (rather than on the
                        // slot's general area below) means "merge into THIS one" - caught and
                        // stopped here before it can also bubble to the slot's own onDrop,
                        // which would otherwise ALSO fire and land it as a second, separate
                        // document instead.
                        onDragOver={(e) => {
                          if (!draggedDocId || draggedDocId === d.id) return;
                          e.preventDefault();
                          e.stopPropagation();
                          setDragOverDocId(d.id);
                        }}
                        onDragLeave={() => { if (draggedDocId) setDragOverDocId(null); }}
                        onDrop={(e) => {
                          if (!draggedDocId || draggedDocId === d.id) return;
                          e.preventDefault();
                          e.stopPropagation();
                          setDragOverDocId(null);
                          handleMergeDocumentIntoDocument(draggedDocId, d.id);
                          setDraggedDocId(null);
                          setDraggedDocFromTdocId(null);
                        }}
                        title={readOnly ? undefined : "Drag onto another document type to move it there, or onto another document to merge into it"}
                        className={`flex items-center justify-between rounded-lg border px-3 py-2 text-sm transition-colors ${
                          dragOverDocId === d.id
                            ? "border-indigo-400 bg-indigo-50 text-indigo-800 ring-2 ring-indigo-400 dark:border-indigo-500/40 dark:bg-indigo-500/10 dark:text-indigo-200"
                            : "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300"
                        } ${!readOnly ? "cursor-grab active:cursor-grabbing" : ""} ${docActionBusyId === d.id ? "opacity-50" : ""}`}
                      >
                        <span className="min-w-0 truncate">
                          ✓ {d.original_name ?? `${d.page_count} page${d.page_count === 1 ? "" : "s"}`}
                          <span className="ml-1 text-xs opacity-70">
                            ({d.page_count} page{d.page_count === 1 ? "" : "s"}
                            {/* Which invoice this file was paired into — by invoice number,
                                not by the order it was uploaded. */}
                            {uploaded.length > 1 && d.set_index ? `, invoice ${d.set_index}` : ""}
                            {uploaded.length > 1 && !d.set_index ? ", whole job" : ""})
                          </span>
                        </span>
                        {!readOnly && (
                          <div className="ml-2 flex shrink-0 items-center gap-2">
                            {/* A dedicated small button for Copy/Move, separate from the row's
                                own drag handle - a click anywhere on the row used to open this
                                same menu, which fought with starting a drag from that same
                                spot (a touchpad tap-then-slide in particular reads as a click,
                                not a drag, so the menu kept winning). */}
                            <button
                              type="button"
                              draggable={false}
                              onClick={(e) => {
                                e.stopPropagation();
                                setDocMenu({ docId: d.id, fromTdocId: tdocId, x: e.clientX, y: e.clientY, mode: null });
                              }}
                              disabled={docActionBusyId === d.id}
                              title="Copy or move this document to another document type"
                              className="rounded p-1 leading-none text-emerald-700 hover:bg-emerald-100 disabled:opacity-50 dark:text-emerald-300 dark:hover:bg-emerald-500/20"
                            >
                              ⋯
                            </button>
                            <button
                              type="button"
                              onClick={(e) => { e.stopPropagation(); handleRemoveFile(d.id); }}
                              disabled={uploadingId === d.id || docActionBusyId === d.id}
                              className="text-xs font-medium text-rose-600 hover:underline disabled:opacity-50"
                            >
                              {uploadingId === d.id ? "Removing…" : "Remove"}
                            </button>
                          </div>
                        )}
                      </div>
                      <JobDocPreview
                        jobId={jobId}
                        docId={d.id}
                        pages={d.page_count}
                        updatedAt={d.updated_at}
                        readOnly={readOnly}
                        onPageMenu={(pageNumber, x, y) =>
                          setDocMenu({ docId: d.id, fromTdocId: tdocId, x, y, mode: null, pageNumber })
                        }
                      />
                    </div>
                  ))}
                  {readOnly ? (
                    uploaded.length === 0 && (
                      <div className="rounded-lg border border-dashed border-slate-300 px-3 py-4 text-center text-sm text-slate-400 dark:border-slate-700">Not uploaded</div>
                    )
                  ) : (
                    <label
                      onDragOver={(e) => { e.preventDefault(); setDragOverId(tdocId); }}
                      onDragLeave={() => setDragOverId(null)}
                      onDrop={(e) => { e.preventDefault(); setDragOverId(null); handleUpload(tdocId, e.dataTransfer.files); }}
                      className={`flex cursor-pointer items-center justify-center rounded-lg border-2 border-dashed px-3 py-3 text-center text-sm transition-colors ${
                        dragOverId === tdocId ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-500/10" : "border-slate-300 bg-slate-50 hover:border-indigo-400 dark:border-slate-700 dark:bg-slate-900"
                      }`}
                    >
                      {isUploading ? (
                        <span className="text-indigo-600">Uploading…</span>
                      ) : (
                        <span className="text-slate-500">
                          {uploaded.length === 0
                            ? "Click or drag a file (PDF, PNG, JPG, Word, Excel)"
                            : `Add another ${name.toLowerCase()}`}
                        </span>
                      )}
                      {/* `multiple`: three invoices are picked in one go, not one at a time. */}
                      <input type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.doc,.docx,.xls,.xlsx" className="hidden" disabled={isUploading} onChange={(e) => handleUpload(tdocId, e.target.files)} />
                    </label>
                  )}
                </div>
              );
            })}
          </div>
          {unclassifiedPages.length > 0 && (
            <div className="lg:sticky lg:top-4 lg:self-start">
              <p className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-500">
                Unclassified pages · {unclassifiedPages.length}
              </p>
              <p className="mb-2 text-xs text-slate-400">
                Couldn't be auto-routed — drag one onto the right document type on the left.
              </p>
              {/* Its own scroll, separate from the page's - so a long list never pushes the
                  document-type slots on the left out of reach while you're mid-drag. */}
              <div className="flex max-h-[70vh] flex-col gap-2 overflow-y-auto pr-1">
                {unclassifiedPages.map((p, i) => (
                  <UnclassifiedPageCard
                    key={p.id}
                    jobId={jobId}
                    page={p}
                    busy={assigningPageId === p.id}
                    readOnly={readOnly}
                    onDragStart={() => setDraggedPageId(p.id)}
                    onDragEnd={() => { setDraggedPageId(null); setDragOverId(null); }}
                    onView={() => setViewingPageIndex(i)}
                  />
                ))}
              </div>
            </div>
          )}
          {viewingPageIndex !== null && unclassifiedPages[viewingPageIndex] && (
            <UnclassifiedPageViewer
              jobId={jobId}
              pages={unclassifiedPages}
              index={viewingPageIndex}
              onIndexChange={setViewingPageIndex}
              onClose={() => setViewingPageIndex(null)}
            />
          )}
          {/* The "..." menu on an already-placed document (whole document) or on one of its own
              page thumbnails (that one page only) - for a sheet that genuinely IS two documents
              at once (e.g. one sheet that is both the BL and the Freight Certificate). "Copy and
              send to X" keeps the original and extracts a fresh, independent copy into X too;
              "Move to X" does the same copy and then removes the original (the whole document
              via handleMoveDocumentToSlot/dragging the document card, or just the one page via
              handleMoveDocumentPageToSlot). */}
          {docMenu && (
            <>
              <div
                className="fixed inset-0 z-40"
                onClick={() => setDocMenu(null)}
                onContextMenu={(e) => { e.preventDefault(); setDocMenu(null); }}
              />
              <div
                className="fixed z-50 w-60 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 text-sm shadow-lg dark:border-slate-700 dark:bg-slate-900"
                style={{ left: docMenu.x, top: docMenu.y }}
              >
                {docMenu.mode === null ? (
                  <>
                    <button
                      type="button"
                      onClick={() => setDocMenu({ ...docMenu, mode: "copy" })}
                      className="flex w-full items-center justify-between px-3 py-2 text-left text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800"
                    >
                      {docMenu.pageNumber != null ? `Copy page ${docMenu.pageNumber} to` : "Copy and send to"}{" "}
                      <span className="text-slate-400">▸</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setDocMenu({ ...docMenu, mode: "move" })}
                      className="flex w-full items-center justify-between px-3 py-2 text-left text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800"
                    >
                      {docMenu.pageNumber != null ? `Move page ${docMenu.pageNumber} to` : "Move to"}{" "}
                      <span className="text-slate-400">▸</span>
                    </button>
                  </>
                ) : docMenu.chosenTdocId === undefined ? (
                  <>
                    <button
                      type="button"
                      onClick={() => setDocMenu({ ...docMenu, mode: null })}
                      className="flex w-full items-center gap-1 px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800"
                    >
                      ‹ {docMenu.pageNumber != null
                        ? `${docMenu.mode === "copy" ? "Copy" : "Move"} page ${docMenu.pageNumber} to`
                        : docMenu.mode === "copy" ? "Copy and send to" : "Move to"}
                    </button>
                    <div className="my-1 border-t border-slate-100 dark:border-slate-800" />
                    {docSlots.filter((s) => s.tdocId !== docMenu.fromTdocId).length === 0 ? (
                      <p className="px-3 py-2 text-xs text-slate-400">No other document type on this template.</p>
                    ) : (
                      docSlots.filter((s) => s.tdocId !== docMenu.fromTdocId).map((s) => {
                        const existing = s.files.filter((f) => f.is_uploaded);
                        return (
                          <button
                            key={s.tdocId}
                            type="button"
                            onClick={() => {
                              if (existing.length === 0) {
                                // Nothing there yet - land as its own document immediately,
                                // same as before this merge step existed.
                                if (docMenu.pageNumber != null) {
                                  if (docMenu.mode === "copy") {
                                    handleCopyDocumentPageToSlot(docMenu.docId, docMenu.pageNumber, s.tdocId);
                                  } else {
                                    handleMoveDocumentPageToSlot(docMenu.docId, docMenu.pageNumber, s.tdocId);
                                  }
                                } else if (docMenu.mode === "copy") {
                                  handleCopyDocumentToSlot(docMenu.docId, s.tdocId);
                                } else {
                                  handleMoveDocumentToSlot(docMenu.docId, s.tdocId);
                                }
                                setDocMenu(null);
                              } else {
                                // Already has one or more documents - ask whether this belongs
                                // WITH one of them (merge) or stays its own, separate entry.
                                setDocMenu({ ...docMenu, chosenTdocId: s.tdocId });
                              }
                            }}
                            className="flex w-full items-center justify-between px-3 py-2 text-left text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800"
                          >
                            <span className="truncate">{s.name}</span>
                            {existing.length > 0 && <span className="shrink-0 text-slate-400">▸</span>}
                          </button>
                        );
                      })
                    )}
                  </>
                ) : (
                  (() => {
                    const chosenSlot = docSlots.find((s) => s.tdocId === docMenu.chosenTdocId);
                    const existing = chosenSlot?.files.filter((f) => f.is_uploaded) ?? [];
                    const isMove = docMenu.mode === "move";
                    const keepSeparate = () => {
                      if (docMenu.pageNumber != null) {
                        if (docMenu.mode === "copy") {
                          handleCopyDocumentPageToSlot(docMenu.docId, docMenu.pageNumber, docMenu.chosenTdocId!);
                        } else {
                          handleMoveDocumentPageToSlot(docMenu.docId, docMenu.pageNumber, docMenu.chosenTdocId!);
                        }
                      } else if (docMenu.mode === "copy") {
                        handleCopyDocumentToSlot(docMenu.docId, docMenu.chosenTdocId!);
                      } else {
                        handleMoveDocumentToSlot(docMenu.docId, docMenu.chosenTdocId!);
                      }
                      setDocMenu(null);
                    };
                    return (
                      <>
                        <button
                          type="button"
                          onClick={() => setDocMenu({ ...docMenu, chosenTdocId: undefined })}
                          className="flex w-full items-center gap-1 px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800"
                        >
                          ‹ {chosenSlot?.name}
                        </button>
                        <div className="my-1 border-t border-slate-100 dark:border-slate-800" />
                        {existing.map((f) => (
                          <button
                            key={f.id}
                            type="button"
                            onClick={() => {
                              if (docMenu.pageNumber != null) {
                                handleMergeDocumentPageIntoDocument(docMenu.docId, docMenu.pageNumber, f.id, isMove);
                              } else {
                                handleMergeDocumentIntoDocumentFromMenu(docMenu.docId, f.id, isMove);
                              }
                              setDocMenu(null);
                            }}
                            className="block w-full truncate px-3 py-2 text-left text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800"
                          >
                            Merge into {f.original_name ?? "this document"}
                          </button>
                        ))}
                        <div className="my-1 border-t border-slate-100 dark:border-slate-800" />
                        <button
                          type="button"
                          onClick={keepSeparate}
                          className="block w-full truncate px-3 py-2 text-left text-slate-500 hover:bg-slate-50 dark:text-slate-400 dark:hover:bg-slate-800"
                        >
                          Keep as a separate, new document
                        </button>
                      </>
                    );
                  })()
                )}
              </div>
            </>
          )}
          </div>
          {!allUploaded && !readOnly && <p className="mt-3 text-xs text-slate-400">Upload every document — extraction starts on its own once all of them are in.</p>}
        </Card>
      )}

      {/* ---------------- Data Validation ---------------- */}
      {activeTab === "validation" && (
        <Card>
          <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 dark:border-slate-800">
            <div>
              <h2 className="font-semibold text-slate-900 dark:text-slate-50">Document Cross-Verification</h2>
              <p className="text-xs text-slate-500">The same field compared across documents.</p>
            </div>
            {job.verifications.length > 0 && (
              <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${job.all_checks_passed ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"}`}>
                {job.all_checks_passed ? "✓ All checks passed" : `${unresolved} to review`}
              </span>
            )}
          </div>
          {job.verifications.length === 0 ? (
            <p className="px-5 py-4 text-sm text-slate-400">No cross-document checks configured for this template.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wider text-slate-500">
                  <tr className="border-b border-slate-200 dark:border-slate-800">
                    <th className="px-5 py-3">Field</th><th className="px-5 py-3">Source 1</th><th className="px-5 py-3">Source 2</th><th className="px-5 py-3 text-right">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {job.verifications.map((v) => {
                    const rowTint =
                      v.accepted || v.status === "match" ? ""
                      : v.status === "mismatch" ? "bg-rose-50/40 dark:bg-rose-500/5"
                      : v.status === "review" ? "bg-amber-50/40 dark:bg-amber-500/5"
                      : "bg-slate-50/60 dark:bg-slate-800/30"; // missing
                    const badge =
                      v.accepted ? { c: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300", t: "✓ Accepted" }
                      : v.status === "match" ? { c: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300", t: "✓ Match" }
                      : v.status === "mismatch" ? { c: "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300", t: "✗ Mismatch" }
                      : v.status === "review" ? { c: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300", t: "≈ Review" }
                      : { c: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400", t: "— Missing" };
                    const val = (x: string | null) =>
                      x ? <span className="whitespace-pre-line text-slate-900 dark:text-slate-100">{x}</span> : <span className="italic text-slate-400">not found</span>;
                    // A field whose own prompt already reconciled several documents itself
                    // (see _self_verifying_findings on the backend) has no second document to
                    // name - target_document comes back "" for that row.
                    const selfVerifying = v.target_document === "";
                    const canAct = !readOnly && !v.accepted && (v.status === "review" || v.status === "mismatch");
                    return (
                      <tr key={v.link_id} className={`border-b border-slate-100 dark:border-slate-800/60 ${rowTint}`}>
                        <td className="px-5 py-3">
                          <div className="font-medium text-slate-900 dark:text-slate-100">{v.field_label}</div>
                          <div className="text-xs text-slate-400">
                            {selfVerifying ? "Self-verifying" : `${v.source_document} vs ${v.target_document}`}
                          </div>
                        </td>
                        <td className="px-5 py-3"><div className="text-xs text-slate-400">{v.source_document}</div>{val(v.source_value)}</td>
                        <td className="px-5 py-3">
                          {selfVerifying ? <span className="text-slate-300 dark:text-slate-600">—</span> : (
                            <>
                              <div className="text-xs text-slate-400">{v.target_document}</div>
                              {val(v.target_value)}
                            </>
                          )}
                        </td>
                        <td className="px-5 py-3 text-right">
                          <div className="flex items-center justify-end gap-2">
                            <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${badge.c}`}>{badge.t}</span>
                            {canAct && (
                              <Button size="sm" variant="secondary" onClick={() => decideVerification(v.link_id, true)}>Accept</Button>
                            )}
                            {!readOnly && v.accepted && (
                              <button onClick={() => decideVerification(v.link_id, false)} className="text-xs text-slate-400 hover:underline">undo</button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {!readOnly && (
            <div className="flex items-center justify-end gap-3 border-t border-slate-200 px-5 py-4 dark:border-slate-800">
              {(user?.role === "gk2" ? job.gk2_validation_approved : job.validation_approved) ? (
                <>
                  <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
                    ✓ Approved
                  </span>
                  <Button variant="secondary" onClick={() => setActiveTab("submission")}>
                    Continue to ERP Submission →
                  </Button>
                </>
              ) : (
                <>
                  {unresolved > 0 && (
                    <span className="text-xs text-slate-400">
                      {unresolved} cross-check{unresolved === 1 ? "" : "s"} still flagged above —
                      pressing Approved and Proceed accepts the data as-is anyway.
                    </span>
                  )}
                  <Button onClick={approveValidationNow} isLoading={validationApproving}>
                    Approved and Proceed
                  </Button>
                </>
              )}
            </div>
          )}
        </Card>
      )}

      {/* ---------------- ERP Submission ---------------- */}
      {activeTab === "submission" && (
        <Card className="p-5">
          {(() => {
            // The raw field, not outer_status - "Failed" reads identically whether it came
            // from GK2's own run or an ordinary GK1 entry that never went near GK2, and only
            // gk2_status (null once no GK2 hand-off has happened) tells those apart.
            const gk2Started = job.gk2_status != null;
            const isGk2 = user?.role === "gk2";
            const gk2CanAct = job.gk2_status === "pending" || job.gk2_status === "failed";
            const gk2Skipped = job.gk2_skipped === true;
            const erpRunning = job.gk2_status === "preparing_erp" || job.gk2_status === "entering_erp";
            // Predictive (job.gk2_required, known before Final Submit is pressed) rather than
            // gk2_skipped (only ever set AFTER submission) - lets GK1 see up front whether this
            // job is headed to GK2 or straight to the real ERP, not just after the fact.
            const gk2NotNeeded = !gk2Started && job.gk2_required === false;
            return (
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="font-semibold text-slate-900 dark:text-slate-50">
                    {isGk2
                      ? "Final Approve & Proceed"
                      : !isGk2 && job.gk2_status === "failed"
                        ? "Retry & Proceed"
                        : gk2NotNeeded
                          ? "Final Submit"
                          : "Final Submit for Approval"}
                  </h2>
                  <p className="text-xs text-slate-500">
                    {isGk2
                      ? "Give this job its final approval, or send it back for GK1 to fix."
                      : job.gk2_status === "failed"
                        ? "The last ERP entry failed — fix what's wrong, then resubmit."
                        : gk2NotNeeded || gk2Skipped
                          ? "GK2 review isn't required for this shipment type — this goes straight to the real ERP entry."
                          : gk2Started
                            ? "Waiting on GK2's final approval."
                            : "Hand this job over to GK2 for a final approval and the real ERP entry."}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {job.excel_entry && (
                    <Button variant="secondary" onClick={downloadExcel} isLoading={excelBusy}>
                      Download as Excel
                    </Button>
                  )}
                  {!readOnly && isGk2 && (
                    <Button
                      onClick={gk2ApproveAndProceed}
                      isLoading={gk2Busy}
                      disabled={!gk2CanAct}
                    >
                      {job.gk2_status === "failed" ? "Retry & Proceed" : "Final Approve & Proceed"}
                    </Button>
                  )}
                  {!readOnly && !isGk2 && !gk2Started && (
                    <Button
                      onClick={() => {
                        if (fixQueue.length > 0) {
                          // Ask for them one at a time instead of naming them in a banner and
                          // leaving the operator to find them on the page.
                          setFixAt(0);
                          setFixDraft(fixQueue[0].fv.corrected_value ?? fixQueue[0].fv.value ?? "");
                          setFixError(null);
                          setFixOpen(true);
                          return;
                        }
                        submitForGk2Approval();
                      }}
                      isLoading={gk2Busy}
                      disabled={job.status !== "extracted" || !job.validation_approved}
                    >
                      {gk2NotNeeded ? "Final Submit" : "Final Submit for Approval"}
                    </Button>
                  )}
                  {/* GK1's own correct-and-resubmit, scoped to a FAILED ERP run only - the
                      original "pending" sign-off stays GK2's own (see gk2_approve's own
                      backend guard). Every other gk2Started state (mid-run, skipped,
                      submitted) still shows as plain status text below, unchanged. */}
                  {!readOnly && !isGk2 && gk2Started && job.gk2_status === "failed" && (
                    <Button onClick={gk2ApproveAndProceed} isLoading={gk2Busy}>
                      Retry & Proceed
                    </Button>
                  )}
                  {!readOnly && !isGk2 && gk2Started && job.gk2_status !== "failed" && (
                    <span className="text-xs font-medium text-indigo-600 dark:text-indigo-400">
                      {gk2Skipped
                        ? erpRunning
                          ? "Running the ERP entry…"
                          : "✓ Submitted — GK2 review not required for this shipment type"
                        : "✓ Submitted for GK2 approval"}
                    </span>
                  )}
                </div>
              </div>
            );
          })()}
          {excelError && (
            <div className="mb-4">
              <Alert>{excelError}</Alert>
            </div>
          )}

          {/* GK1 chose Approval for IRN, and GK2 has pressed Final Approve & Proceed - the
              real ERP submission does not run for this job, so there is no progress to show
              here (no spinner, no "preparing"/"entering" banner - job.erp_status is never
              touched on this path). Just says where the job actually is. */}
          {job.gk2_status === "irn_document_process" && (
            <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
              This job is in IRN Document Process.
            </div>
          )}

          {/* The last entry failed. Say what was rejected, point at the red field, and make the
              retry obvious — it re-runs the ERP script with the corrected values, WITHOUT
              re-extracting the documents, so a fixed value is all that changes. */}
          {!readOnly && entryFailed && (
            <div className="mb-4 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm dark:border-rose-500/30 dark:bg-rose-500/10">
              <p className="font-semibold text-rose-800 dark:text-rose-300">
                ⚠ The last ERP entry failed
                {job.erp_failed_field ? <> — the ERP rejected <b>{job.erp_failed_field}</b></> : null}
              </p>
              {job.erp_diagnosis && (
                <p className="mt-1 text-xs text-rose-800 dark:text-rose-200">{job.erp_diagnosis}</p>
              )}
              {!job.erp_diagnosis && job.erp_reason && (
                <p className="mt-1 text-xs text-rose-700 dark:text-rose-300">{job.erp_reason}</p>
              )}
              <p className="mt-2 text-xs text-rose-700 dark:text-rose-400">
                {job.erp_failed_field
                  ? "Correct the value outlined in red below, press Save on it, then Run failed job again."
                  : "Correct anything wrong below, press Save, then Run failed job again."}
              </p>
            </div>
          )}
          {/* These fields have their own section on Required Details Review now. Pointing at
              it rather than repeating the boxes here — two editable copies of one field is
              how a value gets typed in the place that is not the one being read. Pressing
              Submit still walks through whatever is unanswered, one at a time, so the fast
              path is unchanged. */}
          {!readOnly && askPending.length > 0 && (
            <div className="mb-4 rounded-lg border border-sky-300 bg-sky-50 px-4 py-3 text-sm dark:border-sky-500/30 dark:bg-sky-500/10">
              <p className="font-semibold text-sky-800 dark:text-sky-300">
                ✎ {askPending.length} field{askPending.length === 1 ? "" : "s"} still need an answer before this entry can be submitted
              </p>
              <p className="mt-1 text-xs text-sky-700 dark:text-sky-400">
                {askPending.map((f) => f.label_name).join(", ")}
              </p>
              <button
                type="button"
                onClick={() => setActiveTab("extraction")}
                className="mt-2 text-xs font-medium text-sky-800 underline hover:no-underline dark:text-sky-300"
              >
                Fill them on Required Details Review →
              </button>
            </div>
          )}
          {/* The per-line boxes and the whole extracted-value listing used to sit here.
              They belong to Required Details Review now — this stage is about the run: press
              Submit, watch it go, and see how it ended. Keeping editable copies here meant
              the same value had two homes, and the one being read was not always the one
              being typed into. */}
          {/* ERP replay result — shown after Submit Entry */}
          {!submitting && job.erp_status && (
            <div
              className={`mb-4 rounded-lg border px-4 py-3 text-sm ${
                job.erp_status === "ok"
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300"
                  : job.erp_status === "no_script" || job.erp_status === "partial"
                    ? "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300"
                    : "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300"
              }`}
            >
              <p className="font-semibold">
                {job.erp_status === "ok"
                  ? "✓ Entered into the ERP and submitted."
                  : job.erp_status === "partial"
                    ? /* Submitted, but not cleanly — the entry may be missing a date, a
                         document or a captured value. Not a re-run: that would duplicate it. */
                      `⚠ Entered and submitted, but some steps failed — check the entry in the ERP before relying on it.${job.erp_reason ? " " + job.erp_reason : ""}`
                  : job.erp_status === "no_script"
                      ? "No ERP script configured for this template — nothing was entered."
                      : `✗ ERP entry failed — reported to the Super Admin inbox.${job.erp_reason ? " " + job.erp_reason : ""}`}
              </p>
              {showsMachinery && job.erp_final_url && (
                <p className="mt-0.5 text-xs opacity-80">Final page: {job.erp_final_url}</p>
              )}
              {showsMachinery && job.erp_log && job.erp_log.length > 0 && (
                <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/5 p-2 font-mono text-[11px] dark:bg-white/5">
                  {job.erp_log.join("\n")}
                </pre>
              )}
              {/* HOW FAR THROUGH. Shown whenever the length of the script is known - while the
                  entry is running, and afterwards from the log it left behind, so a job opened
                  tomorrow still says how far it got. Deliberately outside the screenshot block
                  below: the picture is dropped a few minutes after a run ends, and the progress
                  should not vanish with it. */}
              {/* An operator sees that it is working, without the step numbers. Hiding the
                  progress bar from them would otherwise leave a screen that looks frozen
                  through a run that takes a minute or more. */}
              {!showsMachinery && live?.live && (
                <p className="mt-2 flex items-center gap-2 text-xs">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-sky-500" />
                  Entering this job into the ERP — this takes a minute or two.
                </p>
              )}
              {showsMachinery && progress && progress.total > 0 && (
                <div className="mt-2">
                  <div className="mb-1 flex items-center justify-between text-xs font-medium">
                    <span>
                      {live?.live ? "Entering" : "Reached"} step {progress.done} of{" "}
                      {progress.total}
                    </span>
                    <span className="tabular-nums opacity-70">{progress.percent}%</span>
                  </div>
                  <div
                    className="h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700"
                    role="progressbar"
                    aria-valuenow={progress.percent}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`ERP entry progress: step ${progress.done} of ${progress.total}`}
                  >
                    <div
                      className={`h-full rounded-full transition-[width] duration-500 ${
                        live?.live
                          ? "bg-sky-500"
                          : progress.percent === 100
                            ? "bg-emerald-500"
                            : "bg-amber-500"
                      }`}
                      style={{ width: `${progress.percent}%` }}
                    />
                  </div>
                </div>
              )}
              {/* THE LIVE VIEW - shown while the entry is happening, in place of the
                  after-the-fact screenshot. */}
              {live && live.screenshot && (
                <div className="mt-2">
                  <div className="mb-1 flex items-center justify-between text-xs">
                    <span className="inline-flex items-center gap-1.5 font-medium text-sky-700 dark:text-sky-300">
                      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-500" />
                      {live.live ? "Entering now - live" : "Finished"}
                    </span>
                    <a
                      href={`data:image/png;base64,${live.screenshot}`}
                      target="_blank"
                      rel="noreferrer"
                      className="font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                    >
                      Open full size ↗
                    </a>
                  </div>
                  <div className="max-h-[32rem] overflow-auto rounded border border-sky-400 dark:border-sky-600">
                    <img
                      src={`data:image/png;base64,${live.screenshot}`}
                      alt="The ERP screen as the entry happens"
                      className="block w-full"
                    />
                  </div>
                  {live.log.length > 0 && (
                    <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/5 p-2 font-mono text-[11px] dark:bg-white/5">
                      {live.log.slice(-14).join(String.fromCharCode(10))}
                    </pre>
                  )}
                </div>
              )}
              {!live && job.erp_screenshot && (
                <div className="mt-2">
                  <div className="mb-1 flex items-center justify-between text-xs opacity-80">
                    <span>The screen after the entry</span>
                    {/* A full-page capture of a bill of entry is far taller than this panel.
                        Opening it in a tab shows it at its own size, which is the point of
                        capturing it at twice the pixel density in the first place. */}
                    <a
                      href={`data:image/png;base64,${job.erp_screenshot}`}
                      target="_blank"
                      rel="noreferrer"
                      className="font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                    >
                      Open full size ↗
                    </a>
                  </div>
                  {/* Scrolls rather than squashing: the whole page was captured, so the
                      operator can read down it instead of squinting at a shrunken copy. */}
                  <div className="max-h-[32rem] overflow-auto rounded border border-slate-300 dark:border-slate-700">
                    <img
                      src={`data:image/png;base64,${job.erp_screenshot}`}
                      alt="The ERP screen after the entry"
                      className="block w-full"
                    />
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>
      )}

      {/* ------- ERP Submission: the job once it has settled -------
          Completed AND failed both land here — this panel reports the outcome whichever way
          it went, and narrowing it to "completed" would have silently hidden the failure
          screen, which is the one an operator most needs to see. (The ERP already having a
          record used to be its own separate "duplicate" status - now folded into "failed",
          on the user's own call: just Completed or Failed, nothing else.) */}
      {activeTab === "submission" &&
        (job.status === "completed" || job.status === "failed") && (
        <div className="flex flex-col gap-4">
          {(() => {
            const failed = job.status === "failed" || job.erp_status === "error" || job.erp_status === "failed";
            return (
              <Card className={`flex flex-col items-center justify-center gap-3 px-6 py-10 text-center ${failed ? "border-rose-200 dark:border-rose-500/30" : ""}`}>
                <div className={`flex h-14 w-14 items-center justify-center rounded-2xl ${failed ? "bg-rose-50 text-rose-600 dark:bg-rose-500/10" : "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10"}`}>
                  {failed ? (
                    <svg className="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
                  ) : (
                    <svg className="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" /></svg>
                  )}
                </div>
                <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-50">
                  {failed ? "Entry failed" : "Job completed"}
                </h2>
                <p className="max-w-md text-sm text-slate-500 dark:text-slate-400">
                  {failed ? (
                    <>The ERP entry for <b>{job.reference}</b> did not go through. {job.erp_reason}</>
                  ) : (
                    <>The ERP entry for <b>{job.reference}</b> has been submitted. This job now shows as Completed on the dashboard.</>
                  )}
                </p>
                {failed && job.erp_diagnosis && (
                  <div className="mt-1 max-w-lg rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-left dark:border-amber-500/30 dark:bg-amber-500/10">
                    <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
                      What the screen showed
                    </p>
                    <p className="text-sm text-amber-900 dark:text-amber-100">{job.erp_diagnosis}</p>
                  </div>
                )}
                {failed && job.erp_failed_field && (
                  <p className="text-xs text-rose-700 dark:text-rose-300">
                    Stopped at <b>{job.erp_failed_field}</b>
                    {job.erp_failed_value ? <> — value <span className="font-mono">{job.erp_failed_value}</span></> : null}
                  </p>
                )}
                <Link to={backToJobsPath} className="text-sm text-indigo-600 hover:underline">← Back to all jobs</Link>
              </Card>
            );
          })()}

          {/* Everything the script PICKED out of the ERP. On a failed run this still shows what
              was read before it stopped — the reference may already have been generated. */}
          {job.erp_captured && Object.keys(job.erp_captured).length > 0 && (
            <Card className="p-5">
              <h3 className="mb-1 text-sm font-semibold text-slate-900 dark:text-slate-50">
                Picked from the ERP
              </h3>
              <p className="mb-4 text-xs text-slate-500">
                Values and documents the entry read back out of the ERP for this job.
              </p>
              <div className="flex flex-col gap-3">
                {Object.entries(job.erp_captured)
                  // "internal" picks exist only to be re-entered into a later ERP screen —
                  // showing them would clutter the operator's view with plumbing.
                  .filter(([, raw]) => typeof raw === "string" || (raw.usage ?? "both") !== "internal")
                  .map(([key, raw]) => {
                  // Older runs stored a bare string; newer ones store a record. Handle both so
                  // a job captured before this change still displays.
                  const item = typeof raw === "string"
                    ? { label: key, value: raw, kind: "value", description: "", file: null }
                    : raw;
                  return (
                    <div key={key} className="rounded-xl border border-slate-200 p-3 dark:border-slate-700">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold text-slate-900 dark:text-slate-50">
                          {item.label || key}
                        </span>
                        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium uppercase text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                          {item.kind || "value"}
                        </span>
                      </div>
                      {item.description && (
                        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{item.description}</p>
                      )}
                      {item.kind === "image" && item.file ? (
                        /* The success screenshot — the whole reason for recording that step is
                           that somebody can LOOK at what the ERP ended up showing. A download
                           link is not looking at it. Click to open the full-size picture. */
                        <a
                          href={jobsApi.capturedFileUrl(job.id, item.file)}
                          target="_blank"
                          rel="noreferrer"
                          title="Open the full-size screenshot"
                          className="mt-2 block overflow-hidden rounded-lg border border-slate-200 hover:border-indigo-400 dark:border-slate-700"
                        >
                          <img
                            src={jobsApi.capturedFileUrl(job.id, item.file)}
                            alt={item.label || "The ERP screen at the end of the entry"}
                            className="w-full"
                          />
                        </a>
                      ) : item.kind === "document" && item.file ? (
                        <a
                          href={jobsApi.capturedFileUrl(job.id, item.file)}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-indigo-300 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-50 dark:border-indigo-500/40 dark:text-indigo-300"
                        >
                          ⬇ {item.file}
                        </a>
                      ) : item.value ? (
                        <p className={`mt-2 rounded-lg bg-slate-50 px-3 py-2 font-mono text-sm text-slate-900 dark:bg-slate-800 dark:text-slate-100 ${item.kind === "text" ? "whitespace-pre-wrap" : ""}`}>
                          {item.value}
                        </p>
                      ) : (
                        <p className="mt-2 text-xs italic text-slate-400">nothing was read for this item</p>
                      )}
                    </div>
                  );
                })}
              </div>
            </Card>
          )}
        </div>
      )}

      {/* ---------------- Data Extraction (Required Details Review) ----------------
          Dump Data and Manual Data Entry used to be their own separate stages, each with
          their own tab in the rail. Their content now renders here instead, below the
          extracted values, so everything to fill in before Cross Docs Verification is on
          one screen — nothing about what they show or how a value gets saved has changed. */}
      {activeTab === "extraction" && (
        <div className="flex flex-col gap-4">
          {!isAdditionalDetailsActive && (
            <ExtractionReview
              job={job}
              jobId={jobId}
              reload={load}
              readOnly={!canEditValues}
              activeFileId={activeFile?.id ?? null}
              setActiveFileId={setActiveFileId}
              insuranceMode={insuranceMode}
              onToggleInsuranceMode={switchInsuranceMode}
            />
          )}

          {/* Formerly the Dump Data tab — the customer's reference-sheet lookups (CTH/RITC). */}
          <DumpDataView job={job} reload={load} readOnly={!canEditValues} />

          {/* Formerly the Manual Data Entry tab — the fields the system asks a person for.
              Selected from the sidebar's own "Additional Details" row, below every document —
              that name is what the operator sees; "Manual Data Entry" stays an internal-only
              name for how the fields are asked and saved, same as it always was. */}
          {isAdditionalDetailsActive && (
            <>
              <Card className="p-5">
                <h2 className="font-semibold text-indigo-600 dark:text-indigo-400">
                  Additional Details
                </h2>
                <p className="mt-1 text-xs text-slate-500">
                  {manualFields.length === 0
                    ? "This template asks the operator for nothing — every field comes off a document or is worked out."
                    : manualPending.length === 0
                      ? `All ${manualFields.length} field(s) are answered.`
                      : `${manualPending.length} of ${manualFields.length} still to answer. The entry cannot be submitted until they are.`}
                </p>
              </Card>

              {groupedAdditionalDetailsFields.map(({ heading, items }) => {
                // Bill of Entry Type defaults to Advance (pre-selected, not auto-saved - see
                // ExtractedField's own defaultValue prop). When Advance is in effect - either
                // already saved, or still just this default - there is no Master Bill of
                // Lading yet either, so MBL No defaults to "NO MBL" instead of a blank box.
                const advanceFv = job.field_values.find(
                  (fv) => fv.row_index == null && fv.label_name === "AdvancePriorNormal",
                );
                const advanceValue =
                  (advanceFv?.corrected_value ?? advanceFv?.extracted_value ?? "").trim() || "A";
                return (
                  <Card key={heading} className="p-5">
                    <h3 className="mb-3 text-sm font-semibold text-slate-900 dark:text-slate-50">
                      {heading}
                    </h3>
                    <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
                      {items.map((fv) => {
                        // insurance_amount never renders on its own - it's shown (or not) from
                        // insurance_percentage's own slot below, via InsuranceModeField, so the
                        // toggle only ever appears once per card instead of as two separate rows.
                        if (fv.label_name === "insurance_amount") return null;
                        if (fv.label_name === "insurance_percentage") {
                          return (
                            <div key={fv.id} className="sm:col-span-2">
                              <InsuranceModeField
                                percentageFv={insurancePercentageFv}
                                amountFv={insuranceAmountFv}
                                mode={insuranceMode}
                                onToggle={switchInsuranceMode}
                                readOnly={!canEditValues}
                                reload={load}
                                labelOverridePercentage={ADDITIONAL_DETAILS_LABEL_OVERRIDES[fv.label_name]}
                                labelOverrideAmount={ADDITIONAL_DETAILS_LABEL_OVERRIDES["insurance_amount"]}
                              />
                            </div>
                          );
                        }
                        const defaultValue =
                          fv.label_name === "AdvancePriorNormal"
                            ? "A"
                            : fv.label_name === "MBL No" && advanceValue === "A"
                              ? "NO MBL"
                              : undefined;
                        const filterByLabel = DYNAMIC_REFERENCE_DROPDOWN_FILTER_BY[fv.label_name];
                        let comboOptions: string[] | undefined;
                        if (fv.custom_field_id && COMBOBOX_REFERENCE_FIELD_LABELS.has(fv.label_name)) {
                          // dedupe() - the reference table is a LEARNED (raw text -> corrected
                          // value) mapping, not a plain list of unique names: the same resolved
                          // value legitimately gets re-learned from several different raw texts
                          // (e.g. "EXPEDITORS INTERNATIONAL (INDIA) PVT LTD" corrected from both
                          // "EXPEDITORS KOREA LTD." and its own name on two different jobs) - a
                          // dropdown showing each resolved value once per row showed it twice.
                          comboOptions = dedupe(
                            (comboReferenceOptions[fv.custom_field_id] ?? []).map((o) => o.value),
                          );
                        } else if (fv.custom_field_id && filterByLabel) {
                          // e.g. branch_name ("ERP Quotation Branch") narrowed to the branches
                          // of whichever forwarder is currently set as ERP Quotation Name
                          // (Erp Entry Name) - same narrowing ExtractionReview's own copy of
                          // this mechanism does for relocated fields (CustomsHouseCode).
                          //
                          // Erp Entry Name specifically reads the LIVE, not-yet-saved pick (see
                          // liveErpEntryName above) rather than job.field_values' saved value -
                          // otherwise picking a name narrowed nothing until that pick was itself
                          // saved, so the operator had to save once just to see the branch list
                          // narrow, then again to save the branch. Anything else this map might
                          // ever filter by (today: nothing else in this card) falls back to the
                          // saved value, same as before.
                          const filterValue =
                            filterByLabel === "Erp Entry Name"
                              ? liveErpEntryName.toUpperCase()
                              : (
                                  job.field_values.find(
                                    (x) => x.row_index == null && x.label_name === filterByLabel,
                                  )?.corrected_value ??
                                  job.field_values.find(
                                    (x) => x.row_index == null && x.label_name === filterByLabel,
                                  )?.extracted_value ??
                                  ""
                                )
                                  .trim()
                                  .toUpperCase();
                          const rows = comboReferenceOptions[fv.custom_field_id] ?? [];
                          comboOptions = dedupe(
                            (
                              filterValue
                                ? rows.filter((r) => (r.match_values[0] ?? "").toUpperCase() === filterValue)
                                : rows
                            ).map((o) => o.value),
                          );
                        }
                        return (
                          <div key={fv.id}>
                            <ExtractedField
                              fv={fv}
                              readOnly={!canEditValues}
                              reload={load}
                              tone={fv.origin === "computed" ? "computed" : undefined}
                              labelOverride={ADDITIONAL_DETAILS_LABEL_OVERRIDES[fv.label_name]}
                              options={FIELD_SELECT_OPTIONS[fv.label_name]}
                              defaultValue={defaultValue}
                              hideBlankOption={NO_BLANK_DROPDOWN_OPTION_LABELS.has(fv.label_name)}
                              comboOptions={comboOptions}
                              // By request: the quotation name/branch match must come from the
                              // reference list, not free-typed text that happens to disagree
                              // with it - typing still filters the dropdown below, but only
                              // clicking a result saves anything; blurring with unpicked typed
                              // text reverts to the last saved value instead of saving it.
                              comboPickOnly={
                                fv.label_name === "Erp Entry Name" || fv.label_name === "branch_name"
                              }
                              onDraftChange={
                                fv.label_name === "Erp Entry Name"
                                  ? setLiveErpEntryName
                                  : fv.label_name === "branch_name"
                                    ? setLiveBranchName
                                    : undefined
                              }
                            />
                            {fv.ask_operator_hint && (
                              <p className="mt-1 text-[11px] text-slate-400">{fv.ask_operator_hint}</p>
                            )}
                          </div>
                        );
                      })}
                    </div>

                    {heading === "ERP Reference Mapping" && quotationAddressOptions?.length ? (
                      (() => {
                        // Live, pre-save drafts (see liveErpEntryName/liveBranchName above) -
                        // previews as the operator picks, not only once each is saved.
                        const nameValue = liveErpEntryName.toUpperCase();
                        const branchValue = liveBranchName.toUpperCase();
                        const match = quotationAddressOptions.find(
                          (o) =>
                            (o.match_values[0] ?? "").toUpperCase() === nameValue &&
                            (o.match_values[1] ?? "").toUpperCase() === branchValue,
                        );
                        return (
                          <div className="mt-3">
                            <label className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">
                              ERP Quotation Address
                            </label>
                            <textarea
                              readOnly
                              disabled
                              rows={3}
                              value={match?.value ?? ""}
                              placeholder={
                                nameValue && branchValue
                                  ? "No address on file for this quotation name/branch."
                                  : "Pick an ERP Quotation Name and ERP Quotation Branch first."
                              }
                              className="w-full resize-none rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 placeholder:text-slate-400 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-200"
                            />
                          </div>
                        );
                      })()
                    ) : null}
                  </Card>
                );
              })}

              {/* "One value per product line" (perRowAsks) is deliberately not rendered here
                  per request - these duty-notification fields (AIDC, Basic, ...) no longer
                  show on Additional Details. Display only: perRowAsks/ask_operator and the
                  underlying data are unchanged. */}
            </>
          )}
        </div>
      )}

      {/* ---------------- IRN Documents Upload (GK1) / IRN Processing (GK2) ---------------- */}
      {activeTab === "irn" && (
        <div className="space-y-4">
          <Card className="p-6">
            <p className="text-sm font-semibold text-slate-900 dark:text-slate-50">
              {displayLabelFor("irn")}
            </p>

            <div className="mt-4">{renderPrealert()}</div>

            {/* Supporting documents: any file, under a name the uploader chooses. Never OCR'd
                or extracted - purely stored against the job. */}
            <div className="mt-4 rounded-lg border border-slate-200 p-4 dark:border-slate-700">
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                Supporting Documents
              </p>

              {supportingDocs.length > 0 && (
                <ul className="mt-3 space-y-2">
                  {supportingDocs.map((doc) => (
                    <li
                      key={doc.id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-800/60"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-slate-800 dark:text-slate-100">
                          {doc.label}
                        </p>
                        <div className="mt-1 flex flex-wrap items-center gap-2">
                          {doc.files.map((f) =>
                            IMAGE_EXTENSIONS.some((ext) => f.original_name.toLowerCase().endsWith(ext)) ? (
                              <SupportingDocFileThumb
                                key={f.stored_as}
                                jobId={jobId}
                                docId={doc.id}
                                storedAs={f.stored_as}
                                name={f.original_name}
                              />
                            ) : (
                              <button
                                key={f.stored_as}
                                type="button"
                                onClick={() =>
                                  openSupportingDocumentFile(doc.id, f.stored_as, f.original_name)
                                }
                                className="text-xs text-indigo-600 hover:underline dark:text-indigo-400"
                              >
                                {f.original_name}
                              </button>
                            ),
                          )}
                        </div>
                        {renderIrnPlaceholder()}
                      </div>
                      <button
                        type="button"
                        onClick={() => handleIrnDelete(doc.id)}
                        className="text-xs text-rose-600 hover:underline dark:text-rose-400"
                      >
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <div className="mt-3 flex flex-wrap items-end gap-3">
                <div className="w-56">
                  <Input
                    label="Document name"
                    placeholder="e.g. Certificate of Origin"
                    value={irnLabel}
                    onChange={(e) => setIrnLabel(e.target.value)}
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
                    File(s)
                  </label>
                  <input
                    type="file"
                    multiple
                    onChange={(e) => setIrnFiles(Array.from(e.target.files ?? []))}
                    className="text-sm text-slate-600 dark:text-slate-300"
                  />
                </div>
                <Button
                  onClick={handleIrnUpload}
                  isLoading={irnUploading}
                  disabled={!irnLabel.trim() || irnFiles.length === 0}
                >
                  Upload
                </Button>
              </div>
              <p className="mt-2 text-xs text-slate-400">
                Optional — attach whatever is relevant under any name you choose, one or many
                files at a time. Nothing is required here.
              </p>
              {irnError && (
                <div className="mt-2">
                  <Alert>{irnError}</Alert>
                </div>
              )}
            </div>
          </Card>

          {/* Completion: GK1's own persisted tick (upload something above, or skip) vs GK2's
              unchanged dummy Approve & Proceed press. */}
          {user?.role === "gk2" ? (
            <Card className="p-8 text-center">
              <p className="mx-auto max-w-md text-sm text-slate-500">
                {irnApproved
                  ? "Marked complete — nothing real runs here yet, this is a stand-in."
                  : "Nothing real runs here yet — press below to mark it complete and move on."}
              </p>
              {irnApproved ? (
                <p className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600 dark:text-emerald-400">
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-xs text-white">✓</span>
                  Approved
                </p>
              ) : (
                <Button className="mt-4" onClick={() => setIrnApproved(true)}>
                  Approve & Proceed
                </Button>
              )}
            </Card>
          ) : (
            <Card className="p-6 text-center">
              {job.irn_documents_done ? (
                <p className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600 dark:text-emerald-400">
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-xs text-white">✓</span>
                  Marked complete
                </p>
              ) : (
                <>
                  <p className="mx-auto max-w-md text-sm text-slate-500">
                    Upload a document above if there's anything to attach, then choose one:
                  </p>
                  <div className="mt-3 flex flex-wrap items-center justify-center gap-3">
                    <Button onClick={handleIrnApprove} isLoading={irnApproving}>
                      Approval for IRN
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={handleIrnSkip}
                      isLoading={irnSkipping}
                    >
                      Skip — nothing to attach
                    </Button>
                  </div>
                </>
              )}
            </Card>
          )}
        </div>
      )}

      {/* The stages are chained: this is the only way forward, and it refuses while the
          current one is unfinished. Every screen stays readable either way — what is gated
          is advancing, not looking. Every stage now has its own explicit approve action
          inside its own content (IRN Processing's dummy button included), so this bar is
          just the plain "move on" prompt — it no longer needs a second, stage-specific
          confirm button of its own. */}
      {nextStage && (
        <div className="mt-6 flex items-center justify-between gap-4">
          <p className="text-xs text-slate-400">
            {blockedBecause(activeTab)
              ? "This stage is not finished yet."
              : `Ready to continue to ${displayLabelFor(nextStage.key)}.`}
          </p>
          <Button variant="secondary" onClick={goNext}>
            Next: {displayLabelFor(nextStage.key)} →
          </Button>
        </div>
      )}

      <Modal
        open={approveOpen}
        onClose={() => setApproveOpen(false)}
        title="You haven't changed anything — is that right?"
      >
        <p className="text-sm text-slate-600 dark:text-slate-300">
          {askPending.length} field{askPending.length === 1 ? "" : "s"} arrived already filled
          in and {askPending.length === 1 ? "was" : "were"} left as {askPending.length === 1 ? "it is" : "they are"}. Approving records
          {askPending.length === 1 ? " it" : " them"} as checked, and the entry can be submitted.
        </p>
        <div className="mt-3 max-h-52 overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-700">
          <table className="w-full text-left text-xs">
            <tbody>
              {askPending.slice(0, 40).map((fv) => (
                <tr key={fv.id} className="border-b border-slate-100 last:border-0 dark:border-slate-800">
                  <td className="px-3 py-1.5 text-slate-500">
                    {fv.label_name}
                    {fv.row_index != null && (
                      <span className="ml-1 text-slate-400">· line {fv.row_index}</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 font-medium text-slate-800 dark:text-slate-200">
                    {fv.value}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {askPending.length > 40 && (
          <p className="mt-1 text-[11px] text-slate-400">
            …and {askPending.length - 40} more.
          </p>
        )}
        {/* Named plainly, because approving a stale duty notification is the mistake this
            step exists to catch — these values change from one year to the next. */}
        <p className="mt-3 text-xs text-slate-400">
          Check the notification numbers are the current ones before approving.
        </p>
        <div className="mt-4 flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={() => setApproveOpen(false)}
            className="text-xs text-slate-500 hover:underline dark:text-slate-400"
          >
            Go back and change something
          </button>
          <Button onClick={approveUnchanged} isLoading={approving}>
            Approve and continue
          </Button>
        </div>
      </Modal>

      <Modal
        open={blocker !== null}
        onClose={() => setBlocker(null)}
        title="Finish the earlier stage first"
      >
        {/* Red, and it names the stage. The old wording described whatever screen you were
            standing on, which was rarely the thing actually blocking you — you can jump
            ahead in the rail, so the unfinished stage is usually one you have already
            walked past. */}
        <div className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 dark:border-rose-500/30 dark:bg-rose-500/10">
          <p className="text-sm font-semibold text-rose-800 dark:text-rose-300">
            {blocker ? displayLabelFor(blocker.key) : ""} is not finished
          </p>
          <p className="mt-1 text-sm text-rose-700 dark:text-rose-200">{blocker?.why}</p>
        </div>
        <p className="mt-3 text-xs text-slate-400">
          The stages run in order, so this one has to be settled before the job moves on. You
          can still open any stage to see what is there.
        </p>
        <div className="mt-4 flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={() => setBlocker(null)}
            className="text-xs text-slate-500 hover:underline dark:text-slate-400"
          >
            Stay here
          </button>
          <Button
            onClick={() => {
              const k = blocker?.key;
              setBlocker(null);
              if (k) setActiveTab(k);
            }}
          >
            Go to {blocker ? displayLabelFor(blocker.key) : ""} →
          </Button>
        </div>
      </Modal>

      {/* The job's own extracted data hashed the same as an already-settled job's - almost
          certainly the same shipment's paperwork arriving twice (a resend, a forward, the
          same PDF pulled from two mailboxes). Blocks nothing permanently: Hold just lets the
          operator look around before deciding, Approve treats it as genuinely separate,
          Delete removes it outright. */}
      <Modal
        open={job?.status === "possible_duplicate" && !dupDismissed}
        onClose={() => setDupDismissed(true)}
        title="This job may already exist"
      >
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 dark:border-amber-500/30 dark:bg-amber-500/10">
          <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">
            Matches {job?.duplicate_of_reference ?? "an existing job"}
          </p>
          <p className="mt-1 text-sm text-amber-700 dark:text-amber-200">
            The documents on this job extracted to the same data as{" "}
            <span className="font-semibold">{job?.duplicate_of_reference ?? "another job"}</span>
            . That usually means the same shipment's paperwork arrived twice — a resend, a
            forward, or the same file pulled from two mailboxes.
          </p>
        </div>
        {job?.duplicate_of_reference && (
          <Link
            to={`/jobs/${job.duplicate_of_job_id}`}
            className="mt-2 inline-block text-xs text-sky-600 hover:underline dark:text-sky-400"
          >
            Open {job.duplicate_of_reference} to compare →
          </Link>
        )}
        {dupError && <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{dupError}</p>}
        <p className="mt-3 text-xs text-slate-400">
          Approve if this is genuinely a separate shipment that happens to match. Delete if it
          is a real repeat. Hold to decide later — this job stays flagged until you do.
        </p>
        <div className="mt-4 flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={() => setDupDismissed(true)}
            disabled={dupBusy}
            className="text-xs text-slate-500 hover:underline dark:text-slate-400 disabled:opacity-50"
          >
            Hold — decide later
          </button>
          <div className="flex gap-2">
            <Button variant="danger" onClick={deleteDuplicateNow} disabled={dupBusy}>
              Delete
            </Button>
            <Button onClick={approveDuplicateNow} disabled={dupBusy}>
              Approve — it's a different job
            </Button>
          </div>
        </div>
      </Modal>

      {/* ONE FIELD AT A TIME. Submit used to paint a banner naming what was missing and leave
          the operator to find each one on the page - on a fourteen-line invoice that is a hunt
          through eighty boxes. This asks for them in order, says which line each belongs to,
          and counts down. The last one submits the entry itself, so nothing has to be found
          again afterwards. */}
      <Modal
        open={fixOpen}
        onClose={() => setFixOpen(false)}
        title={
          fixQueue.length
            ? `Fill in ${fixQueue.length} field${fixQueue.length === 1 ? "" : "s"} to submit`
            : "Ready to submit"
        }
      >
        {fixQueue.length === 0 ? (
          <div>
            <p className="text-sm text-slate-600 dark:text-slate-300">
              Everything is filled in. Submitting the entry now.
            </p>
            <div className="mt-4 flex justify-end">
              <Button
                onClick={() => {
                  setFixOpen(false);
                  submitEntry();
                }}
              >
                Submit entry
              </Button>
            </div>
          </div>
        ) : (
          (() => {
            const at = Math.min(fixAt, fixQueue.length - 1);
            const { fv, where } = fixQueue[at];
            const save = async () => {
              const val = fixDraft.trim();
              if (!val) {
                setFixError("This field has to have a value before the entry can be submitted.");
                return;
              }
              setFixSaving(true);
              setFixError(null);
              try {
                await jobsApi.correctFieldValue(fv.id, val);
                await load();
                // The queue is rebuilt from the job, so the one just answered drops out of it
                // and the NEXT unanswered field becomes index 0. Staying put is what advances.
                const remaining = fixQueue.length - 1;
                if (remaining <= 0) {
                  setFixOpen(false);
                  submitEntry();
                } else {
                  setFixAt(0);
                  setFixDraft("");
                }
              } catch {
                setFixError("That value could not be saved. Try again.");
              } finally {
                setFixSaving(false);
              }
            };
            return (
              <div>
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-xs font-medium text-slate-500 dark:text-slate-400">
                    {fixQueue.length} left
                  </span>
                  <div className="h-1.5 w-32 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                    <div
                      className="h-full rounded-full bg-indigo-500 transition-[width] duration-300"
                      style={{
                        width: `${Math.max(4, 100 - (fixQueue.length * 100) / Math.max(fixQueue.length + at, 1))}%`,
                      }}
                    />
                  </div>
                </div>

                <p className="text-xs uppercase tracking-wide text-slate-400">{where}</p>
                <label
                  htmlFor="fix-value"
                  className="mt-1 block text-base font-semibold text-slate-900 dark:text-slate-50"
                >
                  {fv.label_name}
                </label>
                {fv.ask_operator_hint && (
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    {fv.ask_operator_hint}
                  </p>
                )}
                {(fv.value ?? "").trim() && (
                  <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                    Read from the document:{" "}
                    <span className="font-mono text-slate-700 dark:text-slate-200">
                      {fv.value}
                    </span>
                  </p>
                )}
                <input
                  id="fix-value"
                  autoFocus
                  value={fixDraft}
                  onChange={(e) => setFixDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !fixSaving) save();
                  }}
                  placeholder="Type the value"
                  className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                />
                {fixError && (
                  <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{fixError}</p>
                )}

                <div className="mt-4 flex items-center justify-between">
                  <button
                    type="button"
                    onClick={() => setFixOpen(false)}
                    className="text-xs text-slate-500 hover:underline dark:text-slate-400"
                  >
                    Close and fill them on the page instead
                  </button>
                  <Button onClick={save} isLoading={fixSaving}>
                    {fixQueue.length === 1 ? "Save and submit entry" : "Save and next →"}
                  </Button>
                </div>
              </div>
            );
          })()
        )}
      </Modal>
    </AppShell>
  );
}

/** Which extracted value is focused right now, and where its mark sits on ITS OWN document
 *  (normalized 0-1, straight off JobFieldValue.mark_x/y/width/height) — lets DocViewer jump
 *  to the right page, zoom in, and draw a box over the exact spot so a person can cross-check
 *  the reading against the source page without hunting for it. */
export type DocHighlight = {
  fieldId: string;
  page: number; // 1-based
  x: number;
  y: number;
  width: number;
  height: number;
};

/** A document viewer for the Data Extraction screen: the page shown at a readable size in
 *  the panel, with zoom and page controls.
 *
 *  It replaced a strip of thumbnails that opened a modal on click. Checking twenty extracted
 *  values against a page means looking from one to the other and back — a picture you have to
 *  open, read, and close again before you can type makes that impossible, and a thumbnail is
 *  far too small to read a printed invoice from.
 *
 *  Pages are fetched through the API (they are behind auth, so a plain <img src> will not do)
 *  and the object URLs are revoked on unmount.
 */
function DocViewer({
  jobId,
  docId,
  pages,
  updatedAt,
  highlight,
}: {
  jobId: string;
  docId: string;
  pages: number;
  /** Bumped when this slot's file is replaced - see JobDocument.updated_at's own comment.
   *  A same-slot reupload keeps docId AND pages identical, so without this the viewer would
   *  keep showing the file that was just deleted instead of fetching the new one. */
  updatedAt: string;
  /** The field currently focused on the right, if its mark belongs to THIS document. */
  highlight?: DocHighlight | null;
}) {
  const [urls, setUrls] = useState<string[]>([]);
  const [page, setPage] = useState(0);
  const [zoom, setZoom] = useState(100);
  const [loading, setLoading] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    let alive = true;
    const created: string[] = [];
    setLoading(true);
    setPage(0);
    (async () => {
      const out: string[] = [];
      for (let p = 1; p <= pages; p++) {
        try {
          const u = await jobsApi.jobDocPageUrl(jobId, docId, p, updatedAt);
          created.push(u);
          out.push(u);
        } catch {
          /* a page that will not render is skipped rather than breaking the viewer */
        }
      }
      if (alive) {
        setUrls(out);
        setLoading(false);
      } else {
        created.forEach((u) => URL.revokeObjectURL(u));
      }
    })();
    return () => {
      alive = false;
      created.forEach((u) => URL.revokeObjectURL(u));
    };
  }, [jobId, docId, pages, updatedAt]);

  // Jump to the focused field's own page. Keyed on fieldId, not the whole object, so this
  // only fires when a DIFFERENT field is focused — not on every re-render.
  useEffect(() => {
    if (!highlight) return;
    const target = highlight.page - 1;
    if (target >= 0 && target < urls.length) setPage(target);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlight?.fieldId, urls.length]);

  // Zoom in enough to actually read the marked value, unless the reader already zoomed in
  // further themselves — this never zooms back OUT while they're cross-checking.
  useEffect(() => {
    if (!highlight || highlight.width <= 0 || highlight.height <= 0) return;
    setZoom((z) => (z < 150 ? 175 : z));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlight?.fieldId]);

  // Scroll the highlighted box to the centre of the panel, once the page image at the
  // current zoom has actually finished loading/laying out.
  useEffect(() => {
    if (!highlight || highlight.width <= 0 || highlight.height <= 0) return;
    if (highlight.page - 1 !== page) return;
    const container = containerRef.current;
    const img = imgRef.current;
    if (!container || !img) return;
    const h = highlight;
    const scrollToBox = () => {
      const cRect = container.getBoundingClientRect();
      const iRect = img.getBoundingClientRect();
      const boxCenterX = iRect.left - cRect.left + container.scrollLeft + h.x * iRect.width + (h.width * iRect.width) / 2;
      const boxCenterY = iRect.top - cRect.top + container.scrollTop + h.y * iRect.height + (h.height * iRect.height) / 2;
      container.scrollTo({
        left: Math.max(0, boxCenterX - container.clientWidth / 2),
        top: Math.max(0, boxCenterY - container.clientHeight / 2),
        behavior: "smooth",
      });
    };
    if (img.complete) {
      const id = requestAnimationFrame(scrollToBox);
      return () => cancelAnimationFrame(id);
    }
    img.addEventListener("load", scrollToBox, { once: true });
    return () => img.removeEventListener("load", scrollToBox);
  }, [highlight, page, zoom, urls]);

  if (loading) return <p className="text-sm text-slate-400">Loading the document…</p>;
  if (urls.length === 0) {
    return <p className="text-sm text-slate-400">This document has no pages to show.</p>;
  }

  const showBox = !!highlight && highlight.width > 0 && highlight.height > 0 && highlight.page - 1 === page;

  const btn =
    "flex h-7 w-7 items-center justify-center rounded text-slate-600 transition-colors hover:bg-slate-200 disabled:cursor-default disabled:opacity-30 dark:text-slate-300 dark:hover:bg-slate-700";

  return (
    <div className="flex flex-col">
      <div className="mb-2 flex items-center justify-between gap-2 rounded-lg bg-slate-100 px-2 py-1.5 dark:bg-slate-800">
        <div className="flex items-center gap-1">
          <button type="button" className={btn} onClick={() => setZoom((z) => Math.max(50, z - 25))}
            disabled={zoom <= 50} aria-label="Zoom out">−</button>
          <span className="w-12 text-center text-xs tabular-nums text-slate-500">{zoom}%</span>
          <button type="button" className={btn} onClick={() => setZoom((z) => Math.min(400, z + 25))}
            disabled={zoom >= 400} aria-label="Zoom in">+</button>
          <button
            type="button"
            onClick={() => setZoom(100)}
            className="ml-1 rounded px-2 py-1 text-[11px] text-slate-500 hover:bg-slate-200 dark:hover:bg-slate-700"
          >
            Fit
          </button>
        </div>
        {urls.length > 1 && (
          <div className="flex items-center gap-1">
            <button type="button" className={btn} onClick={() => setPage((n) => Math.max(0, n - 1))}
              disabled={page === 0} aria-label="Previous page">‹</button>
            <span className="text-xs tabular-nums text-slate-500">
              {page + 1} of {urls.length}
            </span>
            <button type="button" className={btn} onClick={() => setPage((n) => Math.min(urls.length - 1, n + 1))}
              disabled={page >= urls.length - 1} aria-label="Next page">›</button>
          </div>
        )}
        <a
          href={urls[page]}
          target="_blank"
          rel="noopener noreferrer"
          className="rounded px-2 py-1 text-[11px] text-indigo-600 hover:underline dark:text-indigo-400"
        >
          Open full size
        </a>
      </div>
      {/* Both axes scroll: zoomed past the panel width, the page has to be pannable sideways
          or the right-hand column of an invoice becomes unreachable. */}
      <div
        ref={containerRef}
        className="h-[34rem] overflow-auto rounded-lg border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-900"
      >
        <div className="relative inline-block">
          <img
            ref={imgRef}
            src={urls[page]}
            alt={`Page ${page + 1}`}
            style={{ width: `${zoom}%` }}
            className="block max-w-none"
          />
          {showBox && (
            <div
              className="pointer-events-none absolute rounded-sm ring-2 ring-amber-400 bg-amber-300/25 transition-all duration-300 dark:ring-amber-300 dark:bg-amber-400/20"
              style={{
                left: `${highlight!.x * 100}%`,
                top: `${highlight!.y * 100}%`,
                width: `${highlight!.width * 100}%`,
                height: `${highlight!.height * 100}%`,
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}

/** Dump Data — the values this job took from the customer's own reference sheet.
 *
 * Only those. The workbook has 298 columns across eight sheets, and showing them was a wall
 * of dashes that answered no question anyone had: what is worth checking before an entry
 * goes is the handful of values nobody read off a document and nobody typed — the CTH and
 * RITC looked up from a part code.
 *
 * They are editable here, because a part number missing from the sheet is deliberately left
 * blank rather than guessed, and this is where you would notice.
 */
function DumpDataView({
  job,
  reload,
  readOnly,
}: {
  job: JobDetail;
  reload: () => Promise<void>;
  readOnly: boolean;
}) {
  // self_filled is the server's own flag for "answered from the reference sheet", the same
  // one the submit gate uses — so this screen cannot drift from what the system believes.
  //
  // A row that carries an actual document line item (a mark read straight off the invoice)
  // now shows its own reference-sheet lookup right there on that same card, under Required
  // Details Review - see ExtractionReview's lineRows/lookedUpByRow. Showing it again here, in
  // a second "Line 1" disconnected from the part code it was looked up for, would just be the
  // same value in two places. Only a row with NO document line item of its own - or the
  // job-level fields at row_index null - still needs a home, and this is it.
  const rowsWithDocumentLineItem = useMemo(() => {
    const set = new Set<number>();
    for (const fv of job.field_values) {
      if (fv.row_index != null && fv.mark_id && fv.origin === "document") set.add(fv.row_index);
    }
    return set;
  }, [job.field_values]);

  const lookedUp = useMemo(() => {
    const rows = new Map<number, JobFieldValue[]>();
    for (const fv of job.field_values) {
      if (fv.self_filled !== true || fv.hidden_from_capture) continue;
      if (fv.row_index != null && rowsWithDocumentLineItem.has(fv.row_index)) continue;
      const key = fv.row_index ?? 0;
      const arr = rows.get(key) ?? [];
      arr.push(fv);
      rows.set(key, arr);
    }
    return [...rows.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([line, items]) => ({
        line,
        items: items.sort((a, b) => a.label_name.localeCompare(b.label_name)),
        // What the line is, so a code is checked against a product rather than a number.
        context:
          job.field_values.find(
            (f) =>
              f.row_index === line &&
              f.self_filled !== true &&
              /descri/i.test(f.label_name) &&
              (f.value ?? "").trim(),
          )?.value ?? "",
      }));
  }, [job.field_values, rowsWithDocumentLineItem]);

  const all = lookedUp.flatMap((r) => r.items);
  const missing = all.filter((fv) => !(fv.value ?? "").trim()).length;
  const mergedElsewhere = job.field_values.filter(
    (fv) =>
      fv.self_filled === true &&
      !fv.hidden_from_capture &&
      fv.row_index != null &&
      rowsWithDocumentLineItem.has(fv.row_index),
  ).length;

  if (all.length === 0) {
    return (
      <Card className="p-8 text-center">
        <p className="text-sm text-slate-500">
          {mergedElsewhere > 0
            ? `${mergedElsewhere} value(s) looked up from the reference sheet are shown on their own product line under Required Details Review.`
            : "Nothing on this job is looked up from a reference sheet."}
        </p>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Card className="p-5">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="font-semibold text-indigo-600 dark:text-indigo-400">Dump Data</h2>
          {missing > 0 && (
            <span className="rounded bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">
              {missing} not found
            </span>
          )}
        </div>
        <p className="mt-1 text-xs text-slate-500">
          {all.length} value(s) looked up in the customer's own export by part code. A part
          that is not on the sheet, or one the sheet gives two codes for, is left blank rather
          than guessed — type it here and it goes into the dump.
        </p>
        <div className="mt-3">
          <ColourKey />
        </div>
      </Card>

      {lookedUp.map(({ line, items, context }) => (
        <Card key={line} className="p-5">
          {line > 0 && (
            <p className="mb-3 flex items-baseline gap-2 text-xs">
              <span className="font-semibold text-slate-500">Product {line}</span>
              {context && <span className="min-w-0 truncate text-slate-400">{context}</span>}
            </p>
          )}
          <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
            {items.map((fv) => (
              <ExtractedField
                key={fv.id}
                fv={fv}
                readOnly={readOnly}
                reload={reload}
                tone="reference"
              />
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}


/** The colour language, defined ONCE and shown the same on every screen.
 *
 * Both screens had their own three-item legend listing only the colours that screen could
 * produce, so blue was explained on one and simply absent from the other — a reader could
 * not learn what the colours mean without visiting both. The key is the same everywhere;
 * what changes is which of them you happen to see.
 */
function ColourKey() {
  const items: { cls: string; label: string }[] = [
    {
      cls: "border-sky-200 bg-sky-50 dark:border-sky-500/30 dark:bg-sky-500/10",
      label: "read off a document",
    },
    {
      cls: "border-emerald-200 bg-emerald-50 dark:border-emerald-500/30 dark:bg-emerald-500/10",
      label: "from the reference sheet",
    },
    {
      cls: "border-violet-200 bg-violet-50 dark:border-violet-500/30 dark:bg-violet-500/10",
      label: "worked out from a document",
    },
    {
      cls: "border-amber-200 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-500/10",
      label: "you changed it",
    },
    {
      cls: "border-rose-300 bg-rose-50/60 dark:border-rose-500/40 dark:bg-rose-500/10",
      label: "blank — nothing found",
    },
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
      {items.map((i) => (
        <span key={i.label} className="flex items-center gap-1.5">
          <span className={`h-3 w-3 rounded-sm border ${i.cls}`} />
          {i.label}
        </span>
      ))}
    </div>
  );
}

/** One extracted field, editable in place.
 *
 * Shows the CURRENT value — the correction if one was typed, otherwise what extraction read.
 * Saving on blur rather than behind a Save button: an operator checking twenty fields against
 * a page should not have to press twenty buttons, and a value left half-typed and navigated
 * away from is the commonest way to lose work.
 *
 * A field that has been corrected is marked, so it stays obvious which values came off the
 * page and which a person overrode.
 */
function ExtractedField({
  fv,
  readOnly,
  reload,
  small,
  tone,
  note,
  onFocusField,
  labelOverride,
  options,
  defaultValue,
  hideBlankOption,
  multiline,
  wrapLabel,
  flagged,
  flagNote,
  hint,
  comboOptions,
  comboPickOnly,
  asyncSearchPicker,
  onBeforeSave,
  onDraftChange,
  transformBeforeSave,
  id,
}: {
  fv: JobFieldValue;
  readOnly: boolean;
  reload: () => Promise<void>;
  small?: boolean;
  /** Where the value came from, painted onto the box itself: green from the customer's
   *  reference sheet, blue read off a document. An empty box stays plain — nothing came
   *  from anywhere, so there is no source to show. */
  tone?: "reference" | "document" | "computed";
  /** A line under the box. Used to say when this copy of a field is a cross-check rather
   *  than the one the entry actually uses. */
  note?: string;
  /** Data Extraction only: tell the document preview which value is focused, so it can jump
   *  to and highlight this field's mark on the page. */
  onFocusField?: (fv: JobFieldValue) => void;
  /** Display label only - fv.label_name (the template's own configured name) is unchanged,
   *  so nothing that matches on it elsewhere is affected. */
  labelOverride?: string;
  /** A fixed, short set of valid values reads better as a dropdown than free text. */
  options?: FieldSelectOption[];
  /** Shown pre-selected/pre-filled when this field has no value of its own yet (never saved
   *  until the operator actually interacts with it, or another field's own value triggers an
   *  auto-fill - see MBL No's "NO MBL" default). Mirrors how a pre-filled-but-unconfirmed
   *  value already works everywhere else: still needs an explicit confirm before it counts as
   *  answered. */
  defaultValue?: string;
  /** A dropdown whose options already cover every real possibility (Advance/Prior/Normal,
   *  Transaction/Deferred) reads better with no blank choice to accidentally pick. */
  hideBlankOption?: boolean;
  /** A full address reads better as a few short lines in a taller box than as one long line
   *  scrolled sideways through - see MULTILINE_FIELD_LABELS. */
  multiline?: boolean;
  /** A label too long to read under the default single-line truncate - see
   *  WRAP_LABEL_FIELD_LABELS. */
  wrapLabel?: boolean;
  /** Painted red regardless of tone/corrected state, with `flagNote` shown under it - e.g. a
   *  product description that was translated from another language, so the operator knows to
   *  double-check it against the original printed text rather than trusting it blindly. */
  flagged?: boolean;
  flagNote?: string;
  /** A static, lightly-styled caption under the box - background on a business rule applied
   *  to this field (e.g. TOI's own EXW/FCA -> FOB mapping), not a warning and not tied to
   *  this job's own data the way `note`/`flagNote` are. */
  hint?: string;
  /** Previously-typed values offered alongside free text (see COMBOBOX_REFERENCE_FIELD_LABELS) -
   *  unlike `options`, picking one of these never closes off typing something new, UNLESS
   *  `comboPickOnly` is also set. */
  comboOptions?: string[];
  /** Only meaningful alongside `comboOptions`. By request (ERP Quotation Name/Branch): the
   *  value must come from the list, not free-typed text that happens to disagree with it.
   *  Typing still filters the dropdown exactly as before; clicking a result still saves
   *  immediately; but blurring with typed-and-unpicked text reverts to the last saved value
   *  instead of saving it. */
  comboPickOnly?: boolean;
  /** A single merged search-and-pick box for a reference-backed field too large to ever offer
   *  as a plain dropdown (ERP Consignee Name's ~25,000 organizations) - replaces the box +
   *  separate ReferenceSearchPicker underneath it that used to render for this one field (by
   *  request: one box, not two, and no free-typed value can ever disagree with the matched
   *  reference row - only a picked search result ever saves). Mutually exclusive with
   *  `options`/`comboOptions`. */
  asyncSearchPicker?: {
    jobId: string;
    customFieldId: string;
    placeholder?: string;
    /** Scopes the search to one country (ERP Consignee Country's own current value) - passed
     *  through to searchCustomFieldReferenceValues AS-IS, including "" (no country picked yet,
     *  which the backend deliberately returns zero results for rather than searching every
     *  country at once). `undefined` here means this field has no country concept at all. */
    country?: string;
    onPick: (value: string) => Promise<void>;
  };
  /** Runs before a save reaches the server - resolve false to cancel the save outright (the
   *  draft stays as typed, nothing is sent). Insurance Rate (%) uses this to require an
   *  uploaded certificate or a typed reason first (see InsuranceCertificatePopup) - every other
   *  field leaves this unset and saves exactly as before. */
  onBeforeSave?: (value: string) => Promise<boolean>;
  /** Fires on every keystroke/pick, BEFORE any save - the saved value (fv.corrected_value)
   *  only updates once the operator blurs/commits. A caller previewing something derived from
   *  this field live (e.g. ERP Quotation Address, keyed on ERP Quotation Name + ERP Quotation
   *  Branch) needs the in-progress draft, not the last-saved value. */
  onDraftChange?: (value: string) => void;
  /** Normalizes the draft right before it's sent (e.g. Shipment Country Code's own
   *  lowercase-to-uppercase conversion) - runs BEFORE onBeforeSave, which validates/rejects,
   *  never transforms. */
  transformBeforeSave?: (value: string) => string;
  /** DOM id on the root element - lets a caller scroll/center this exact box into view (see
   *  blockApproval's "drag the user to where the empty field is"). */
  id?: string;
}) {
  const current = fv.corrected_value ?? fv.extracted_value ?? "";
  const [draft, setDraft] = useState(current || defaultValue || "");
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const [comboOpen, setComboOpen] = useState(false);
  const [comboQuery, setComboQuery] = useState("");
  // asyncSearchPicker's own state - a debounced server-side search, same mechanism
  // ReferenceSearchPicker already used as a separate component, now inlined into this same
  // box instead of rendered as a second box underneath it.
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<jobsApi.CustomFieldReferenceSearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);

  // The job reloads after every save, so props change under us; re-sync unless the operator
  // is mid-edit on this very box.
  useEffect(() => {
    const next = current || defaultValue || "";
    setDraft(next);
    onDraftChange?.(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, defaultValue]);

  useEffect(() => {
    if (!asyncSearchPicker) return;
    // A country-scoped field (asyncSearchPicker.country !== undefined) with no country picked
    // yet never even calls the server - it would only come back empty anyway (see the backend's
    // own country-required rule), and skipping the call entirely lets the "pick a country
    // first" message below show instantly instead of after a round trip.
    if (searchQuery.trim().length < 2 || (asyncSearchPicker.country !== undefined && !asyncSearchPicker.country.trim())) {
      setSearchResults([]);
      setSearchLoading(false);
      return;
    }
    let cancelled = false;
    setSearchLoading(true);
    const handle = setTimeout(() => {
      jobsApi
        .searchCustomFieldReferenceValues(
          asyncSearchPicker.jobId, asyncSearchPicker.customFieldId, searchQuery.trim(), asyncSearchPicker.country,
        )
        .then((found) => {
          if (!cancelled) setSearchResults(found);
        })
        .catch(() => {
          if (!cancelled) setSearchResults([]);
        })
        .finally(() => {
          if (!cancelled) setSearchLoading(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, asyncSearchPicker?.jobId, asyncSearchPicker?.customFieldId, asyncSearchPicker?.country]);

  const dirty = draft !== current;

  const saveValue = async (value: string) => {
    if (readOnly) return;
    const transformed = transformBeforeSave ? transformBeforeSave(value) : value;
    if (onBeforeSave) {
      const proceed = await onBeforeSave(transformed);
      if (!proceed) return;
    }
    setSaving(true);
    setFailed(false);
    try {
      await jobsApi.correctFieldValue(fv.id, transformed);
      await reload();
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };

  const commit = async () => {
    if (!dirty || readOnly) return;
    await saveValue(draft);
  };

  const pickFromSearch = async (value: string) => {
    setSaving(true);
    setFailed(false);
    try {
      await asyncSearchPicker!.onPick(value);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };

  const corrected = fv.corrected_value != null && fv.corrected_value !== "";
  return (
    <div id={id} className="min-w-0">
      <div className="mb-1 flex items-center gap-1.5">
        <p
          className={`${wrapLabel ? "break-words" : "truncate"} font-semibold text-slate-900 dark:text-slate-100 ${
            small ? "text-[11px] font-medium text-slate-500 dark:text-slate-400" : "text-sm"
          }`}
        >
          {labelOverride ?? fv.label_name}
          {/* fv.mandatory - the mark/custom field's own ask_operator_required column, NOT
             ask_operator_required above (that one is zeroed for any field that isn't also an
             ask_operator item - see its own docstring in types/jobs.ts). Undefined means
             mandatory, only an explicit false means optional. */}
          {fv.mandatory !== false && (
            <span className="text-rose-600 dark:text-rose-400" aria-label="Mandatory">*</span>
          )}
        </p>
        {corrected && (
          <span className="shrink-0 rounded bg-amber-100 px-1 text-[10px] font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
            edited
          </span>
        )}
        {saving && <span className="shrink-0 text-[10px] text-slate-400">saving…</span>}
        {failed && (
          <span className="shrink-0 text-[10px] text-rose-600">could not save</span>
        )}
      </div>
      <div
        className="relative"
        // Closes the combo dropdown only once focus leaves this WHOLE box - the main input,
        // the panel's own search box, and every suggestion button all live inside this div, so
        // moving focus between them (e.g. clicking into the search box) never closes it early.
        onBlur={(e) => {
          if (comboOptions && !e.currentTarget.contains(e.relatedTarget as Node)) {
            setComboOpen(false);
          }
        }}
      >
      {(() => {
        const boxClassName = `w-full rounded-lg border px-3 py-2 text-sm transition-colors placeholder:italic placeholder:text-slate-400 focus:border-indigo-400 focus:outline-none ${
          failed
            ? "border-rose-300 text-slate-700 dark:border-rose-500/40 dark:text-slate-200"
            : flagged
              ? "border-rose-300 bg-rose-50/60 text-rose-900 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200"
              : corrected
              ? // An operator's own correction outranks the source colour: what matters most
                // about this value is no longer where it came from, but that a person changed it.
                "border-amber-200 bg-amber-50/60 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
              : !draft.trim()
                ? // Extraction found nothing for this field — flagged red so a blank box reads
                  // as "missing" rather than as "nothing to check," which the plain box before
                  // this let slide past unnoticed.
                  "border-rose-300 bg-rose-50/60 text-rose-900 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200"
                : tone === "reference"
                  ? "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200"
                  : tone === "document"
                    ? "border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-200"
                    : tone === "computed"
                      ? "border-violet-200 bg-violet-50 text-violet-900 dark:border-violet-500/30 dark:bg-violet-500/10 dark:text-violet-200"
                      : "border-slate-200 bg-white text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
        } ${readOnly ? "cursor-default" : ""} ${!readOnly && (dirty || corrected) ? "pr-16" : ""}`;

        return options ? (
          <select
            value={draft}
            disabled={readOnly}
            onFocus={() => onFocusField?.(fv)}
            onChange={async (e) => {
              const next = e.target.value;
              setDraft(next);
              onDraftChange?.(next);
              if (readOnly || next === current) return;
              setSaving(true);
              setFailed(false);
              try {
                await jobsApi.correctFieldValue(fv.id, next);
                await reload();
              } catch {
                setFailed(true);
              } finally {
                setSaving(false);
              }
            }}
            className={boxClassName}
          >
            {!hideBlankOption && <option value="">not found</option>}
            {options.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        ) : multiline ? (
          <textarea
            value={draft}
            readOnly={readOnly}
            rows={3}
            onChange={(e) => setDraft(e.target.value)}
            onFocus={() => onFocusField?.(fv)}
            // Blur still saves, as a safety net for anyone who types and tabs away - same
            // pattern as the single-line input below.
            onBlur={commit}
            placeholder="not found"
            className={`${boxClassName} resize-y`}
          />
        ) : asyncSearchPicker ? (
          <>
            <input
              type="text"
              // While searching, the box shows what's being TYPED to search, not the saved
              // value underneath it - exactly like ReferenceSearchPicker's own separate box
              // used to, just inlined into this one instead of rendered as a second box below.
              value={searchOpen ? searchQuery : draft}
              readOnly={readOnly}
              onFocus={() => {
                onFocusField?.(fv);
                setSearchQuery("");
                setSearchOpen(true);
              }}
              onChange={(e) => setSearchQuery(e.target.value)}
              // A short delay so a click on a result (below) lands before the blur closes the
              // list. No commit here on purpose - only a clicked result ever saves (see
              // asyncSearchPicker's own docstring); typed-and-unpicked text is simply dropped.
              onBlur={() => setTimeout(() => setSearchOpen(false), 150)}
              placeholder={asyncSearchPicker.placeholder ?? "not found"}
              // Same right-side padding a native <select> gets automatically, so the typed/
              // shown text never runs under the dropdown chevron drawn on top of it below.
              className={`${boxClassName} pr-9`}
            />
            {/* A plain <input> draws no dropdown arrow on its own, unlike the <select> branch
                above (ERP Consignee Branch) - drawn here by hand so this box reads the same way
                as a dropdown at a glance, even though it opens a search panel, not a fixed list,
                because the underlying reference table is far too large for a real <select>. */}
            <svg
              className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400 dark:text-slate-500"
              viewBox="0 0 20 20"
              fill="currentColor"
              aria-hidden="true"
            >
              <path
                fillRule="evenodd"
                d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z"
                clipRule="evenodd"
              />
            </svg>
            {/* Opens as soon as the box is clicked/focused, not only once 2+ characters are
                typed - clicking the box must visibly show SOMETHING (a panel to type into),
                never silently do nothing. The actual server search still only fires once the
                operator has typed enough to search against - this panel just makes clicking
                in the first place look like it did something. */}
            {searchOpen && (
              <div className="absolute z-10 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white text-xs shadow-lg dark:border-slate-700 dark:bg-slate-800">
                {asyncSearchPicker.country !== undefined && !asyncSearchPicker.country.trim() ? (
                  <p className="px-3 py-2 text-slate-400">Pick a country first.</p>
                ) : searchQuery.trim().length < 2 ? (
                  <p className="px-3 py-2 text-slate-400">Type at least 2 characters to search…</p>
                ) : searchLoading ? (
                  <p className="px-3 py-2 text-slate-400">Searching…</p>
                ) : searchResults.length === 0 ? (
                  <p className="px-3 py-2 text-slate-400">No matches.</p>
                ) : (
                  searchResults.map((r) => (
                    <button
                      key={r.value}
                      type="button"
                      disabled={saving}
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={async () => {
                        setSearchOpen(false);
                        setSearchQuery("");
                        await pickFromSearch(r.value);
                      }}
                      className="block w-full whitespace-normal break-words px-3 py-2 text-left text-slate-700 hover:bg-indigo-50 disabled:opacity-50 dark:text-slate-200 dark:hover:bg-indigo-500/10"
                    >
                      {r.label}
                    </button>
                  ))
                )}
              </div>
            )}
          </>
        ) : (
          <>
            <input
              value={draft}
              readOnly={readOnly}
              onChange={(e) => {
                setDraft(e.target.value);
                onDraftChange?.(e.target.value);
              }}
              onFocus={() => {
                onFocusField?.(fv);
                if (comboOptions) {
                  setComboQuery("");
                  setComboOpen(true);
                }
              }}
              // Blur still saves, as a safety net for anyone who types and tabs away - closing
              // the combo dropdown itself is handled by the wrapping div above, not here, so a
              // click into the panel's own search box (below) doesn't count as leaving the box.
              // comboPickOnly fields are the exception: only a clicked result ever saves (see
              // the button below), so typed-and-unpicked text just reverts instead of saving.
              onBlur={() => {
                if (comboOptions && comboPickOnly) {
                  setDraft(current);
                  onDraftChange?.(current);
                  return;
                }
                void commit();
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              }}
              placeholder="not found"
              className={boxClassName}
            />
            {/* Every known value, shown in full on open regardless of what's in the field above
                - a native <datalist> narrows itself to whatever matches the TYPED text as you
                type, which hid the very options an operator opens the list to compare against.
                This panel's own search box (separate from the field itself) narrows the list
                shown here instead; picking a suggestion sets the field outright (save path
                still remembers a genuinely new typed value for next time - correct_field_value). */}
            {comboOptions && comboOptions.length > 0 && comboOpen && (
              <div className="absolute z-10 mt-1 w-full overflow-hidden rounded-lg border border-slate-200 bg-white text-xs shadow-lg dark:border-slate-700 dark:bg-slate-800">
                <input
                  type="text"
                  autoFocus
                  value={comboQuery}
                  onChange={(e) => setComboQuery(e.target.value)}
                  placeholder="Search…"
                  className="w-full border-b border-slate-200 px-3 py-2 text-xs focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                />
                <div className="max-h-56 overflow-y-auto">
                  {comboOptions
                    .filter((opt) => opt.toLowerCase().includes(comboQuery.trim().toLowerCase()))
                    .map((opt) => (
                      <button
                        key={opt}
                        type="button"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => {
                          setDraft(opt);
                          onDraftChange?.(opt);
                          setComboOpen(false);
                          if (comboPickOnly) void saveValue(opt);
                        }}
                        className="block w-full truncate px-3 py-2 text-left text-slate-700 hover:bg-indigo-50 dark:text-slate-200 dark:hover:bg-indigo-500/10"
                      >
                        {opt}
                      </button>
                    ))}
                </div>
              </div>
            )}
          </>
        );
      })()}
      {!readOnly && !options && (dirty || saving) && (
        <button
          type="button"
          // Keeps focus in the input, so pressing Save does not blur it first.
          onMouseDown={(e) => e.preventDefault()}
          onClick={commit}
          disabled={!dirty || saving}
          className={`absolute right-1.5 top-1/2 -translate-y-1/2 rounded px-2 py-1 text-[11px] font-semibold transition-colors ${
            dirty
              ? "bg-indigo-600 text-white hover:bg-indigo-500"
              : "cursor-default bg-transparent text-emerald-600 dark:text-emerald-400"
          }`}
        >
          {saving ? "…" : "Save"}
        </button>
      )}
      </div>
      {note && <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-400">{note}</p>}
      {flagged && flagNote && (
        <p className="mt-1 text-[11px] text-rose-700 dark:text-rose-400">{flagNote}</p>
      )}
      {hint && <p className="mt-1 text-[11px] font-medium text-slate-500 dark:text-slate-400">{hint}</p>}
    </div>
  );
}

/** A search-as-you-type picker for a reference-backed field too large to ever offer as a plain
 * dropdown (see SEARCHABLE_REFERENCE_FIELD_LABELS) - always available, not just while the field
 * is blank, so the operator can look one up or correct it at any time. Typing searches the
 * field's reference table server-side (reference-search), debounced so every keystroke doesn't
 * fire its own request; each result's own label (r.label - e.g. "Country City(CODE)" for a
 * port, a plain organization name for a consignee) is whatever the backend formats for THAT
 * field, so the operator can match it against whatever they see on the document. Picking a
 * result commits it as this field's corrected value exactly like any other correction. */
function ReferenceSearchPicker({
  jobId,
  customFieldId,
  label,
  readOnly,
  onPick,
}: {
  jobId: string;
  customFieldId: string;
  /** This field's own label (fv.label_name) - picks the right placeholder wording from
   *  REFERENCE_SEARCH_PLACEHOLDERS, since this one component serves several very different
   *  reference fields (a port code, a consignee name, ...). */
  label: string;
  readOnly: boolean;
  onPick: (value: string) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<jobsApi.CustomFieldReferenceSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    if (query.trim().length < 2) {
      setResults([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const handle = setTimeout(() => {
      jobsApi
        .searchCustomFieldReferenceValues(jobId, customFieldId, query.trim())
        .then((found) => {
          if (!cancelled) setResults(found);
        })
        .catch(() => {
          if (!cancelled) setResults([]);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [query, jobId, customFieldId]);

  if (readOnly) return null;

  return (
    <div className="relative mt-1.5">
      <input
        type="text"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        // A short delay so a click on a result (below) lands before the blur closes the list.
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder={REFERENCE_SEARCH_PLACEHOLDERS[label] ?? `Search to find or correct ${label}…`}
        className="w-full rounded-lg border border-dashed border-slate-300 px-3 py-2 text-xs italic text-slate-600 placeholder:text-slate-400 focus:border-indigo-400 focus:not-italic focus:outline-none dark:border-slate-600 dark:bg-slate-900 dark:text-slate-300"
      />
      {open && query.trim().length >= 2 && (
        // Locked to the input's own width (not wider, as this used to try) - found live: this
        // field sits in the RIGHT column of a 2-column grid, often close to the page's own
        // right edge, so a panel allowed to grow wider than its input could extend past the
        // visible area, leaving a result's text only reachable by scrolling the whole page
        // sideways. whitespace-normal + break-words still wraps a long result onto several
        // lines rather than ever clipping it - the trade this makes on purpose, so the full
        // text is always visible without scrolling anything.
        <div className="absolute z-10 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white text-xs shadow-lg dark:border-slate-700 dark:bg-slate-800">
          {loading ? (
            <p className="px-3 py-2 text-slate-400">Searching…</p>
          ) : results.length === 0 ? (
            <p className="px-3 py-2 text-slate-400">No matches.</p>
          ) : (
            results.map((r) => (
              <button
                key={r.value}
                type="button"
                disabled={picking}
                onMouseDown={(e) => e.preventDefault()}
                onClick={async () => {
                  setPicking(true);
                  try {
                    await onPick(r.value);
                    setQuery("");
                    setResults([]);
                    setOpen(false);
                  } finally {
                    setPicking(false);
                  }
                }}
                className="block w-full whitespace-normal break-words px-3 py-2 text-left not-italic text-slate-700 hover:bg-indigo-50 disabled:opacity-50 dark:text-slate-200 dark:hover:bg-indigo-500/10"
              >
                {r.label}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

/** insurance_percentage and insurance_amount are mutually exclusive - BOTH fields show at once,
 *  each with its own checkbox beside it (radio-style: checking one unchecks the other; the
 *  already-checked one can't be unchecked on its own, since exactly one is always meant to be
 *  active). Only the checked field is editable - the other is shown read-only so it is obvious
 *  which one actually counts, without inviting an edit that would never be used.
 *
 *  Neither field's own stored value is ever touched by switching which is checked - an earlier
 *  version cleared the unchecked one's corrected_value outright, which permanently destroyed
 *  insurance_percentage's hardcoded tenant default the first time anyone checked "amount" and
 *  switched back. The ERP import stays exclusive by reading a separate, never-shown
 *  "insurance_mode" field instead (see switchInsuranceMode in JobRunPage, and
 *  entry_values_and_rows in jobs.py, which blanks whichever Excel column is inactive at read
 *  time). Used in both places insurance_percentage alone already showed (Additional Details,
 *  and the Custom document tab), with `percentageFv`/`amountFv` possibly undefined if this
 *  template has no such field at all, in which case this renders nothing. */
function InsuranceModeField({
  percentageFv,
  amountFv,
  mode,
  onToggle,
  readOnly,
  reload,
  labelOverridePercentage,
  labelOverrideAmount,
  onBeforeSave,
}: {
  percentageFv: JobFieldValue | undefined;
  amountFv: JobFieldValue | undefined;
  mode: "percentage" | "amount" | null;
  onToggle: (toAmount: boolean) => void;
  readOnly: boolean;
  reload: () => Promise<void>;
  labelOverridePercentage?: string;
  labelOverrideAmount?: string;
  onBeforeSave?: (value: string) => Promise<boolean>;
}) {
  if (!percentageFv && !amountFv) return null;
  // Only the active field's save is gated behind the insurance-certificate check - the
  // inactive one is read-only, so there is nothing for it to save in the first place.
  const row = (
    fv: JobFieldValue | undefined,
    isAmount: boolean,
    labelOverride: string | undefined,
  ) => {
    if (!fv) return null;
    const checked = mode === (isAmount ? "amount" : "percentage");
    return (
      <div>
        <label className="mb-1 flex items-center gap-1.5 text-[11px] font-medium text-slate-500 dark:text-slate-400">
          <input
            type="checkbox"
            checked={checked}
            onChange={() => {
              if (!checked) onToggle(isAmount);
            }}
            disabled={readOnly}
            className="h-3.5 w-3.5 rounded border-slate-300 text-indigo-600 focus:ring-2 focus:ring-indigo-500/40 dark:border-slate-600 dark:bg-slate-800"
          />
          Use this value
        </label>
        {/* Unchecked is read-only, but ExtractedField's own "blank = missing, flag it red"
            styling (see boxClassName above) has no idea this field is deliberately not the
            one being used - without dimming it, an untouched amount field would show the
            same alarming red "not found" box as a genuinely missing mandatory value. */}
        <div className={checked ? undefined : "opacity-40"}>
          <ExtractedField
            fv={fv}
            readOnly={readOnly || !checked}
            reload={reload}
            labelOverride={labelOverride}
            onBeforeSave={checked ? onBeforeSave : undefined}
          />
        </div>
        {fv.ask_operator_hint && (
          <p className="mt-1 text-[11px] text-slate-400">{fv.ask_operator_hint}</p>
        )}
      </div>
    );
  };
  return (
    <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
      {row(percentageFv, false, labelOverridePercentage)}
      {row(amountFv, true, labelOverrideAmount)}
    </div>
  );
}

/** Any two already-configured custom fields paired together (see the backend's
 * CustomField.paired_custom_field_id) - exactly one is ever the real answer for one product
 * line, and picking one sets it as `primary`'s own corrected value, since that is the field
 * name whatever export reads was built against. `primary` is the field the pairing is
 * configured ON; `other` is whichever field it names in its own paired_custom_field_id.
 * `syncTargets` (primary's own sync_field_ids, resolved to this row's values) also get set
 * to the SAME picked value - e.g. RITC following a CTH pick, because the two usually carry
 * the same code but are tracked as separate fields.
 *
 * `primary`'s OWN value is captured ONCE, on mount, into a ref - never re-read from
 * extracted_value after that. A row's real answer can live entirely in corrected_value with
 * an EMPTY extracted_value (nothing in a reference sheet, backfilled by a manual correction
 * at some point) - picking `other` and then picking `primary` back, if that "restore" used
 * extracted_value, would silently overwrite the real answer with that empty string. Captured
 * once up front, switching back always restores the value this field actually held before
 * this picker ever touched it.
 *
 * When `other` has nothing for this line there is nothing to pick between, so the caller
 * renders `primary`'s own box alone and skips this entirely. */
function DualSourcePicker({
  primary,
  other,
  syncTargets,
  readOnly,
  reload,
  applyToAllRows,
  flagged,
}: {
  primary: JobFieldValue;
  other: JobFieldValue | undefined;
  syncTargets: JobFieldValue[];
  readOnly: boolean;
  reload: () => Promise<void>;
  /** The CTH layout only: the hidden CTH field's own "mandatory and still blank" state,
   *  passed through so hiding that box does not also hide its warning. */
  flagged?: boolean;
  /** Ripples the choice just made to every OTHER product line's own copy of this same
   *  pairing - see applyPairChoiceToAllRows. Undefined only if this field somehow carries no
   *  custom_field_id of its own (should not happen for a real pairing). */
  applyToAllRows?: (which: "primary" | "other") => Promise<void>;
}) {
  const otherValue = (other?.corrected_value ?? other?.extracted_value ?? "").trim();
  const [saving, setSaving] = useState(false);

  const ownPrimaryValueRef = useRef<string | null>(null);
  if (ownPrimaryValueRef.current === null) {
    ownPrimaryValueRef.current = (primary.corrected_value ?? primary.extracted_value ?? "").trim();
  }
  const primaryValue = ownPrimaryValueRef.current;

  const [selected, setSelected] = useState<"primary" | "other" | null>(() => {
    const current = (primary.corrected_value ?? primary.extracted_value ?? "").trim();
    // primary's OWN raw computed value - UNLIKE primaryValue above, this is never touched by
    // picking "other" (which only ever overwrites corrected_value, so exports still read the
    // chosen value under primary's own name). Using primaryValue here was the bug: after
    // picking "other" once, every later reload re-mounts with primaryValue ALREADY equal to
    // otherValue (since that pick wrote it there), permanently hiding the choice from then on -
    // primary.extracted_value survives that write untouched, so it stays a reliable "would
    // primary and other have coincided anyway, with nothing ever picked" check.
    const rawPrimaryValue = (primary.extracted_value ?? "").trim();
    return current === otherValue && otherValue !== "" && rawPrimaryValue !== otherValue
      ? "other"
      : "primary";
  });

  // Each box is independently editable (not just pickable) - sometimes NEITHER the dump
  // lookup nor the document read is right, and the operator needs to type the real answer in
  // directly rather than only choosing between two wrong ones. Drafts are local so typing
  // doesn't round-trip through reload() on every keystroke; committed on blur/Enter, the same
  // pattern ExtractedField uses.
  const [primaryDraft, setPrimaryDraft] = useState(primaryValue);
  const [otherDraft, setOtherDraft] = useState(otherValue);
  useEffect(() => setOtherDraft(otherValue), [otherValue]);

  // `other` missing entirely (no such field on this row at all) is the only real bail-out -
  // guards the unconditional other.id/other.label_name reads below. A BLANK otherValue used
  // to bail out here too, which looked right (nothing to pick between) but actually broke
  // two things at once: the field vanished (see the parent's own pairedIds fix) AND, once
  // that was fixed to fall back to a plain box instead, typing a correction into it no longer
  // reached syncTargets (RITC No., etc.) the way commitPrimary below does - only this
  // component propagates a sync. Always rendering the picker, blank or not, is what keeps
  // sync working on every row, not just the ones where both sides happened to read something.
  if (!other) return null;

  const choose = async (which: "primary" | "other") => {
    if (readOnly || saving) return;
    if (which === selected) {
      // Clicking the already-selected box again unselects it - clears the saved answer so
      // the operator can pick again or type a fresh value, instead of being stuck once either
      // side has been chosen.
      setSaving(true);
      try {
        await jobsApi.correctFieldValue(primary.id, "");
        for (const target of syncTargets) {
          await jobsApi.correctFieldValue(target.id, "");
        }
        setSelected(null);
        setPrimaryDraft("");
        await reload();
      } finally {
        setSaving(false);
      }
      return;
    }
    const value = which === "primary" ? primaryValue : otherValue;
    setSaving(true);
    try {
      await jobsApi.correctFieldValue(primary.id, value);
      for (const target of syncTargets) {
        await jobsApi.correctFieldValue(target.id, value);
      }
      setSelected(which);
      // Ripples to every OTHER product line's own copy of this pairing (each keeping its OWN
      // values) - applyToAllRows does its own reload at the end covering this row too, so skip
      // the separate reload() below when it ran.
      if (applyToAllRows) {
        await applyToAllRows(which);
      } else {
        await reload();
      }
    } finally {
      setSaving(false);
    }
  };

  // Editing primary's OWN box: it is unconditionally the field exports actually read, so a
  // direct correction always carries the synced fields (RITC, etc.) along with it, same as
  // picking it would.
  const commitPrimary = async () => {
    if (readOnly || primaryDraft === primaryValue) return;
    setSaving(true);
    try {
      await jobsApi.correctFieldValue(primary.id, primaryDraft);
      ownPrimaryValueRef.current = primaryDraft;
      for (const target of syncTargets) await jobsApi.correctFieldValue(target.id, primaryDraft);
      await reload();
    } finally {
      setSaving(false);
    }
  };

  // Editing the OTHER box corrects THAT field's own value outright. It only also flows into
  // primary (+ synced fields) when "other" is the currently picked source - editing a
  // reference value that isn't actively feeding the real field shouldn't silently start
  // feeding it.
  const commitOther = async () => {
    if (readOnly || !other || otherDraft === otherValue) return;
    setSaving(true);
    try {
      await jobsApi.correctFieldValue(other.id, otherDraft);
      if (selected === "other") {
        await jobsApi.correctFieldValue(primary.id, otherDraft);
        for (const target of syncTargets) await jobsApi.correctFieldValue(target.id, otherDraft);
      }
      await reload();
    } finally {
      setSaving(false);
    }
  };

  const boxClass = (id: "primary" | "other") =>
    `min-w-0 flex-1 rounded-lg border px-2 py-1 text-xs placeholder:italic placeholder:text-slate-400 focus:outline-none ${
      selected === id
        ? "border-indigo-300 bg-indigo-50 text-indigo-900 dark:border-indigo-500/40 dark:bg-indigo-500/10 dark:text-indigo-200"
        : "border-slate-200 bg-slate-50 text-slate-500 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-400"
    } ${readOnly ? "cursor-default" : "focus:border-indigo-400"}`;

  // A pairing that feeds a field actually called CTH gets a different, simpler shape: ONE
  // box, which IS the CTH, with what the document read shown underneath as a reference.
  //
  // The two checkboxes made sense when neither source was privileged, but for the CTH they
  // are not equal: the reference sheet is the customer's own declared code, the document's
  // is whatever the supplier happened to print. Asking an operator to choose between them on
  // every product line, when the answer is almost always the sheet, is a decision they have
  // to make hundreds of times and get right every time.
  //
  // Only this case changes. A pairing that syncs somewhere else (Bhuvaneswari's, which feeds
  // RITC No. and has no CTH field at all) keeps the picker exactly as it was - those two
  // sources really are alternatives there, and silently picking one for them would be
  // choosing a customs code on their behalf.
  const cthTarget = syncTargets.find(
    (t) => (t.label_name ?? "").trim().toUpperCase() === "CTH",
  );

  if (cthTarget) {
    return (
      <div>
        <p className="mb-1 text-[11px] font-medium text-slate-500 dark:text-slate-400">
          CTH
          {flagged && <span className="ml-0.5 text-rose-500">*</span>}
        </p>
        <input
          type="text"
          value={primaryDraft}
          readOnly={readOnly}
          onChange={(e) => setPrimaryDraft(e.target.value)}
          onBlur={commitPrimary}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          }}
          // Blank when the reference sheet does not carry this part, on purpose - the
          // document's own reading is shown below to copy from, but nothing is filled in on
          // the operator's behalf. A code nobody confirmed should not be able to reach
          // customs just because somebody clicked past it.
          placeholder="not found — type the CTH"
          className={`w-full rounded-lg border px-2 py-1 text-xs placeholder:italic focus:outline-none ${
            flagged
              ? "border-rose-300 bg-rose-50 text-rose-900 placeholder:text-rose-400 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200"
              : "border-indigo-300 bg-indigo-50 text-indigo-900 placeholder:text-slate-400 dark:border-indigo-500/40 dark:bg-indigo-500/10 dark:text-indigo-200"
          } ${readOnly ? "cursor-default" : "focus:border-indigo-400"}`}
        />
        {/* What the document itself said. Read-only and deliberately plain: it is there to
            check the sheet against, and to copy from when the sheet has nothing. */}
        <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500">
          CTH (8-Digit Code)
          {" · "}
          {otherValue ? (
            <span className="font-mono text-slate-600 dark:text-slate-300">{otherValue}</span>
          ) : (
            <span className="italic">nothing read from the document</span>
          )}
        </p>
        {saving && <p className="mt-1 text-[10px] text-slate-400">saving…</p>}
      </div>
    );
  }

  return (
    <div>
      <p className="mb-1 text-[11px] font-medium uppercase tracking-wider text-slate-400">
        {primary.picker_heading?.trim() || "Pick which one is right for this line"}
      </p>
      <div className="flex flex-col gap-1.5">
        <label className={`flex items-center gap-2 ${readOnly ? "cursor-default" : "cursor-pointer"}`}>
          <input
            type="checkbox"
            checked={selected === "primary"}
            disabled={readOnly || saving}
            onChange={() => void choose("primary")}
          />
          <span className="w-28 shrink-0 truncate text-[11px] font-medium text-slate-500 dark:text-slate-400">
            {primary.label_name}
          </span>
          <input
            type="text"
            value={primaryDraft}
            readOnly={readOnly}
            onChange={(e) => setPrimaryDraft(e.target.value)}
            onBlur={commitPrimary}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            }}
            placeholder="—"
            className={boxClass("primary")}
          />
        </label>
        <label className={`flex items-center gap-2 ${readOnly ? "cursor-default" : "cursor-pointer"}`}>
          <input
            type="checkbox"
            checked={selected === "other"}
            disabled={readOnly || saving}
            onChange={() => void choose("other")}
          />
          <span className="w-28 shrink-0 truncate text-[11px] font-medium text-slate-500 dark:text-slate-400">
            {other.label_name}
          </span>
          <input
            type="text"
            value={otherDraft}
            readOnly={readOnly}
            onChange={(e) => setOtherDraft(e.target.value)}
            onBlur={commitOther}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            }}
            placeholder="—"
            className={boxClass("other")}
          />
        </label>
      </div>
      {saving && <p className="mt-1 text-[10px] text-slate-400">saving…</p>}
    </div>
  );
}

/** Data Extraction — the document on the left, what was read out of it on the right.
 *
 * Shows `extracted_value`: what the reader took OFF the page, before any operator
 * correction. Not `value`, which prefers a typed correction — the point of this screen is to
 * check the machine against the paper, and showing the human's own answer back to them would
 * defeat it. Read-only for the same reason; corrections belong on the entry screen.
 *
 * One panel per uploaded FILE, not per slot, so three invoices are three panels and each
 * sits beside its own pages.
 */
function ExtractionReview({
  job,
  jobId,
  reload,
  readOnly,
  activeFileId,
  setActiveFileId,
  insuranceMode,
  onToggleInsuranceMode,
}: {
  job: JobDetail;
  jobId: string;
  reload: () => Promise<void>;
  readOnly: boolean;
  // Which file is open — owned by JobRunPage now, not this component, so the rail's own
  // document checklist (under "Required Details Review") and this panel are always looking
  // at the same one instead of two separate ideas of "which file is open". Switching files
  // normally only happens from that checklist, but approving a document also advances here,
  // so the setter is passed down too.
  activeFileId: string | null;
  setActiveFileId: (id: string) => void;
  // insurance_percentage/insurance_amount's shared toggle state - owned by JobRunPage (see
  // switchInsuranceMode there) so this copy and Additional Details' own copy of the same field
  // never drift into two independent checkboxes. null = neither picked yet.
  insuranceMode: "percentage" | "amount" | null;
  onToggleInsuranceMode: (toAmount: boolean) => void;
}) {
  const files = useMemo(
    () =>
      // The order the API sends, which is the template's own — Bill of lading, Invoice,
      // Packing List, Freight Certificate. Sorting by name put "Fright Certificate" second,
      // alphabetically, which is not how anyone works through a shipment.
      (job.documents ?? []).filter((d) => d.is_uploaded),
    [job.documents],
  );
  const insurancePercentageFv = job.field_values.find(
    (fv) => fv.row_index == null && fv.label_name === "insurance_percentage",
  );
  const insuranceAmountFv = job.field_values.find(
    (fv) => fv.row_index == null && fv.label_name === "insurance_amount",
  );
  const { user } = useAuth();
  const isGk2 = user?.role === "gk2";
  const proceedLabel = isGk2 ? "Approve & Proceed" : "Submit for Approval";
  // GK2 re-reviews every document independently, on its own column — never GK1's.
  const isApproved = (d: JobDocument) => (isGk2 ? d.gk2_approved : d.approved);
  const active = files.find((f) => f.id === activeFileId) ?? files[0];
  const [approvingId, setApprovingId] = useState<string | null>(null);
  const approvedCount = files.filter(isApproved).length;

  // Delete/Reupload for the file on screen. Reupload is delete-then-upload into the SAME
  // slot rather than a raw upload over the existing file: the upload endpoint adds a new
  // file alongside whatever is already in a slot (a job can genuinely carry several files
  // per document type), so replacing the old one first is what makes this a true swap
  // rather than a second, duplicate file next to it.
  const [docActionBusy, setDocActionBusy] = useState(false);
  const [docActionError, setDocActionError] = useState<string | null>(null);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const reuploadInputRef = useRef<HTMLInputElement>(null);

  const deleteActiveDocument = async () => {
    if (!active) return;
    setDocActionError(null);
    setDocActionBusy(true);
    try {
      await jobsApi.deleteJobDocumentFile(jobId, active.id);
      await reload();
    } catch (err) {
      if (axios.isAxiosError(err)) setDocActionError(err.response?.data?.detail ?? "Could not delete that document.");
    } finally {
      setDocActionBusy(false);
      setConfirmDeleteOpen(false);
    }
  };

  const reuploadActiveDocument = async (file: File) => {
    if (!active) return;
    setDocActionError(null);
    setDocActionBusy(true);
    try {
      await jobsApi.deleteJobDocumentFile(jobId, active.id);
      await jobsApi.uploadJobDocument(jobId, active.template_document_id, file);
      await reload();
    } catch (err) {
      if (axios.isAxiosError(err)) setDocActionError(err.response?.data?.detail ?? "Reupload failed.");
    } finally {
      setDocActionBusy(false);
    }
  };

  // Which extracted value is focused, so the document preview can jump to and highlight its
  // mark. Cleared on switching documents — a field focused on one file's tab has no business
  // being drawn over a completely different file's page.
  const [focusedFv, setFocusedFv] = useState<JobFieldValue | null>(null);
  useEffect(() => {
    setFocusedFv(null);
  }, [active?.id]);
  // found_* (where this value's own text actually sits on THIS document) drives the
  // highlight, never mark_* (the template's static, one-time-drawn box) — every real
  // document has its own layout, so a fixed template position is only right by coincidence.
  // No found_* means no confident match was made; show no highlight rather than guess.
  const highlight: DocHighlight | null = useMemo(() => {
    if (!focusedFv || focusedFv.found_page == null) return null;
    return {
      fieldId: focusedFv.id,
      page: focusedFv.found_page,
      x: focusedFv.found_x ?? 0,
      y: focusedFv.found_y ?? 0,
      width: focusedFv.found_width ?? 0,
      height: focusedFv.found_height ?? 0,
    };
  }, [focusedFv]);

  const [approveError, setApproveError] = useState<string | null>(null);
  // Which fields are painted red right now, after a blocked "Submit for Approval" press - see
  // approveActive below. Cleared field-by-field, reactively, the moment each one actually gets
  // a value (the effect right after this one) - never all at once, so filling in 3 of 4 still
  // shows the 1 left, instead of the whole highlight vanishing only once every one is done.
  const [missingRequiredIds, setMissingRequiredIds] = useState<Set<string>>(new Set());
  useEffect(() => {
    setMissingRequiredIds((prev) => {
      if (prev.size === 0) return prev;
      const next = new Set(prev);
      for (const fv of job.field_values) {
        if (next.has(fv.id) && (fv.value ?? "").trim()) next.delete(fv.id);
      }
      return next.size === prev.size ? prev : next;
    });
  }, [job.field_values]);
  // Blocks the approval: shows the message as both the inline banner AND a native alert() (the
  // user wants it impossible to miss, not just a banner that can scroll out of view), flags
  // every listed field red, and scrolls/centers the FIRST one into view - "drag the user to
  // where the empty field is" rather than leaving them to hunt for it on a long document.
  const blockApproval = (message: string, ids: string[]) => {
    setApproveError(message);
    setMissingRequiredIds(new Set(ids));
    window.alert(message);
    const firstId = ids[0];
    if (firstId) {
      requestAnimationFrame(() => {
        document.getElementById(`field-${firstId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    }
  };
  const approveActive = async () => {
    if (!active) return;
    const approving = !isApproved(active);
    // Insurance Rate (%) lives on this document (relocated here from Additional Details) and
    // is mandatory at the job level already (ask_operator_required) - but that check only ever
    // runs at the job's own Final Submit, long after this document is approved. Catching it
    // here too means the operator finds out right where the field actually is, not several
    // screens later.
    if (approving && active.doc_type === "Custom" && (insurancePercentageFv || insuranceAmountFv)) {
      if (insuranceMode === null) {
        blockApproval(
          "You need to choose either Insurance Rate (%) or Insurance Amount first.",
          [insurancePercentageFv, insuranceAmountFv].filter((fv): fv is JobFieldValue => !!fv).map((fv) => fv.id),
        );
        return;
      }
      // Whichever of the two the operator picked (see InsuranceModeField) - a percentage-mode
      // job must not be blocked over an amount that was never meant to be filled in, and vice
      // versa.
      const insuranceFv = insuranceMode === "amount" ? insuranceAmountFv : insurancePercentageFv;
      const insuranceValue = (insuranceFv?.corrected_value ?? insuranceFv?.extracted_value ?? "").trim();
      if (!insuranceValue) {
        blockApproval(
          "You need to select or enter the insurance value.",
          insuranceFv ? [insuranceFv.id] : [],
        );
        return;
      }
    }
    // Every OTHER mandatory field this document's own tab actually shows (the same two pools
    // renderField below draws from - a plain extracted mark, or a relocated custom field like
    // insurance_percentage above) - found live: an operator could press through to Cross Docs
    // Verification with a mandatory field left blank, only to be bounced back there by a
    // generic "N field(s) still need an answer" popup with no indication of WHERE on the actual
    // form. Catching it right here, on the exact document it belongs to, highlights the actual
    // box instead.
    if (approving) {
      const hidden = HIDDEN_DOCUMENT_FIELD_LABELS_BY_DOC_TYPE[active.doc_type];
      const markFields = job.field_values.filter((fv) => {
        if (fv.row_index != null || !fv.mark_id || hidden?.has(fv.label_name)) return false;
        return fv.job_document_id === active.id ||
          (!fv.job_document_id && fv.document_name === active.name);
      });
      const relocatedLabels = new Set(RELOCATED_FIELD_LABELS_BY_DOC_TYPE[active.doc_type] ?? []);
      const relocated = relocatedLabels.size
        ? job.field_values.filter((fv) => fv.row_index == null && relocatedLabels.has(fv.label_name))
        : [];
      const pool = [...markFields, ...relocated];
      // fv.mandatory, not ask_operator_required - a document-read mark is never an
      // "ask_operator" item, which silently zeroed ask_operator_required for every one of
      // them regardless of its own real mandatory setting (found live: every Bill of Lading
      // mark has ask_operator=false). mandatory reads the raw column directly instead.
      const missing = pool.filter(
        (fv) => fv.mandatory !== false && !(fv.value ?? "").trim(),
      );
      // Format, not blank/missing - Shipment Country Code's own save path already rejects an
      // invalid value (ExtractedField's onBeforeSave), but a value saved before that existed,
      // or written some other way, is still caught here rather than silently approved.
      const invalidFormat = pool.filter(
        (fv) => COUNTRY_CODE_FORMAT_FIELD_LABELS.has(fv.label_name) && countryCodeFormatError(fv.value ?? "") !== null,
      );
      if (missing.length || invalidFormat.length) {
        const parts: string[] = [];
        if (missing.length) {
          parts.push(
            `${missing.length} mandatory field${missing.length === 1 ? "" : "s"} still need${
              missing.length === 1 ? "s" : ""
            } a value: ${[...new Set(missing.map((fv) => fv.label_name))].join(", ")}.`,
          );
        }
        if (invalidFormat.length) {
          parts.push(
            `${invalidFormat.length} field${invalidFormat.length === 1 ? "" : "s"} ${
              invalidFormat.length === 1 ? "has" : "have"
            } an invalid value: ${[...new Set(invalidFormat.map((fv) => fv.label_name))].join(", ")}.`,
          );
        }
        blockApproval(
          `This document can't be approved yet. ${parts.join(" ")}`,
          [...missing, ...invalidFormat].map((fv) => fv.id),
        );
        return;
      }
      // Container Detail's own standalone, per-row fields (container_number/container_type,
      // plus each row's own looked-up ContainerTypeCode/"CTR ISO CODE") - conditionally
      // mandatory, confirmed with the user: a row only needs validating once it actually
      // EXISTS (no container info on the document at all means nothing to check - an LCL
      // shipment is never blocked over containers it never had), but once a row exists, every
      // field on it that is itself marked mandatory must have a value.
      const standaloneRows = job.field_values.filter(
        (fv) => fv.row_index != null && fv.job_document_id === active.id && fv.standalone_multi_value,
      );
      if (standaloneRows.length) {
        const selfFilled = job.field_values.filter(
          (fv) => fv.row_index != null && fv.standalone_multi_value && fv.self_filled === true,
        );
        const missingContainer = [...standaloneRows, ...selfFilled].filter(
          (fv) => fv.mandatory !== false && !(fv.value ?? "").trim(),
        );
        if (missingContainer.length) {
          blockApproval(
            `This document can't be approved yet. ${missingContainer.length} container field${
              missingContainer.length === 1 ? "" : "s"
            } still need${missingContainer.length === 1 ? "s" : ""} a value: ${
              [...new Set(missingContainer.map((fv) => fv.label_name))].join(", ")
            }.`,
            missingContainer.map((fv) => fv.id),
          );
          return;
        }
      }
    }
    setApproveError(null);
    setMissingRequiredIds(new Set());
    setApprovingId(active.id);
    try {
      await jobsApi.approveDocument(jobId, active.id, approving);
      await reload();
      // Only advance on an actual approve, never on "Undo". Approving the LAST uploaded
      // document (whichever one that is for THIS job - a 3-document job with no Freight
      // Certificate stops at Packing List, a 4-document job stops at Freight Certificate)
      // moves on to Additional Details, the next real step, instead of leaving the operator
      // sitting on a document that is already done.
      if (approving) {
        const currentIndex = files.findIndex((f) => f.id === active.id);
        const next = files[currentIndex + 1];
        if (next) setActiveFileId(next.id);
        else setActiveFileId(ADDITIONAL_DETAILS_ID);
      }
    } finally {
      setApprovingId(null);
    }
  };

  // Values for the file on screen. Older jobs — extracted before values recorded which file
  // they came from — fall back to matching on the document name, so they still display.
  const fields = useMemo(() => {
    if (!active) return [];
    const mine = job.field_values.filter((fv) => fv.job_document_id === active.id);
    const rows = mine.length
      ? mine
      : job.field_values.filter(
          (fv) => !fv.job_document_id && fv.document_name === active.name,
        );
    // Only what was read off the page. A computed or looked-up field was never on the
    // document, so it has no business on the screen that checks the reading against it —
    // those belong to Dump Data.
    const hidden = HIDDEN_DOCUMENT_FIELD_LABELS_BY_DOC_TYPE[active.doc_type];
    return rows.filter(
      (fv) => fv.row_index == null && fv.mark_id && !hidden?.has(fv.label_name),
    );
  }, [job.field_values, active]);


  // Which document's copy of a field actually reaches the entry.
  //
  // A field marked on four documents is read from all four so they can be cross-checked, but
  // only ONE of those readings is used: the first non-empty one, in the template's order. The
  // rest exist to be compared against it. Without saying so, an operator correcting HBL No on
  // the Invoice tab would change a value that goes nowhere, and the entry would still carry
  // the Bill of Lading's — a correction that appears to have been made and has not.
  const usedFrom = useMemo(() => {
    const order = new Map(
      (job.documents ?? []).map((d, i) => [d.template_document_id, i] as const),
    );
    const winner = new Map<string, string>();
    const rows = job.field_values
      .filter((fv) => fv.row_index == null && fv.mark_id && (fv.value ?? "").trim())
      .sort(
        (a, b) =>
          (order.get(a.template_document_id ?? "") ?? 99) -
          (order.get(b.template_document_id ?? "") ?? 99),
      );
    for (const fv of rows) {
      if (!winner.has(fv.label_name)) winner.set(fv.label_name, fv.document_name);
    }
    return winner;
  }, [job.field_values, job.documents]);

  // Every marked field shows its own box on every document it's marked on - even one that came
  // back blank. This used to drop a cross-check copy that read nothing (the packing list has no
  // HBL number or gross weight) on the reasoning that there was nothing on the page to check
  // against it. But a blank box is not a dead end for the operator: ChatGPT returning nothing
  // doesn't mean the value doesn't exist anywhere - a call to the customer can still get it, and
  // with no box left to type it into, that answer had nowhere to go. The "Cross-check only"
  // note (see renderField's own `note` prop below) still says which document's copy the entry
  // actually uses, so a correction typed into a non-winning copy is never silently mistaken for
  // the one the entry reads.
  const visibleFields = fields;

  // Custom fields (no mark_id, so `fields` above never picks them up) relocated here from
  // Additional Details for display purposes only - see RELOCATED_FIELD_LABELS_BY_DOC_TYPE.
  const relocatedFields = useMemo(() => {
    if (!active) return [];
    const labels = RELOCATED_FIELD_LABELS_BY_DOC_TYPE[active.doc_type];
    if (!labels || !labels.length) return [];
    const wanted = new Set(labels);
    return job.field_values.filter((fv) => fv.row_index == null && wanted.has(fv.label_name));
  }, [job.field_values, active]);

  // Dropdown options for a DYNAMIC_REFERENCE_DROPDOWN_FILTER_BY field (e.g. CustomsHouseCode)
  // or a COMBOBOX_REFERENCE_FIELD_LABELS field relocated onto a document tab (Insurance Rate
  // (%), on Fright Certificate) - fetched once per custom_field_id rather than hardcoded, see
  // listCustomFieldReferenceOptions.
  const [dynamicReferenceOptions, setDynamicReferenceOptions] =
    useState<Record<string, jobsApi.CustomFieldReferenceOption[]>>({});
  useEffect(() => {
    const toFetch = relocatedFields.filter(
      (fv) =>
        (fv.label_name in DYNAMIC_REFERENCE_DROPDOWN_FILTER_BY ||
          COMBOBOX_REFERENCE_FIELD_LABELS.has(fv.label_name)) &&
        fv.custom_field_id &&
        !(fv.custom_field_id in dynamicReferenceOptions),
    );
    if (!toFetch.length) return;
    let cancelled = false;
    toFetch.forEach((fv) => {
      jobsApi
        .listCustomFieldReferenceOptions(jobId, fv.custom_field_id!)
        .then((options) => {
          if (cancelled) return;
          setDynamicReferenceOptions((prev) => ({ ...prev, [fv.custom_field_id!]: options }));
        })
        .catch(() => {
          if (!cancelled) setDynamicReferenceOptions((prev) => ({ ...prev, [fv.custom_field_id!]: [] }));
        });
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [relocatedFields, jobId]);
  // Suggested starting value for a SUGGESTED_VALUE_KEYED_ON_LABEL field relocated onto a
  // document tab - see suggestCustomFieldValue and JobRunPage's own copy of this same idea
  // (that one serves Additional Details fields; this component renders relocated ones instead,
  // so it needs its own state).
  const [suggestedValues, setSuggestedValues] = useState<Record<string, string | null>>({});
  useEffect(() => {
    const toFetch = relocatedFields.filter((fv) => {
      const keyedOn = SUGGESTED_VALUE_KEYED_ON_LABEL[fv.label_name];
      const current = fv.corrected_value ?? fv.extracted_value ?? "";
      return keyedOn && fv.custom_field_id && !current.trim() && !(fv.custom_field_id in suggestedValues);
    });
    if (!toFetch.length) return;
    let cancelled = false;
    toFetch.forEach((fv) => {
      jobsApi
        .suggestCustomFieldValue(jobId, fv.custom_field_id!, SUGGESTED_VALUE_KEYED_ON_LABEL[fv.label_name])
        .then((value) => {
          if (cancelled) return;
          setSuggestedValues((prev) => ({ ...prev, [fv.custom_field_id!]: value }));
        })
        .catch(() => {
          if (!cancelled) setSuggestedValues((prev) => ({ ...prev, [fv.custom_field_id!]: null }));
        });
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [relocatedFields, jobId]);

  // "ERP Importer Address / Branch Code"'s own options - deliberately NOT the generic
  // DYNAMIC_REFERENCE_DROPDOWN_FILTER_BY/dynamicReferenceOptions path CustomsHouseCode/
  // branch_name use (that one fetches a field's ENTIRE reference table once and filters it
  // client-side - fine at their scale, not at this one: one row per branch, ~31,000 total
  // across every consignee). Narrowed SERVER-SIDE instead (see listCustomFieldReferenceOptions'
  // own `key` param), refetched whenever the SAVED "ERP Consignee Name" value changes. Empty
  // while no consignee is picked yet, rather than falling back to "every row" the way the
  // generic path does for its own, much smaller tables - that fallback would mean fetching the
  // whole 31,000-row table, exactly what this is built to avoid.
  const savedConsigneeName = (
    job.field_values.find((fv) => fv.row_index == null && fv.label_name === "ERP Consignee Name")
      ?.corrected_value ??
    job.field_values.find((fv) => fv.row_index == null && fv.label_name === "ERP Consignee Name")
      ?.extracted_value ??
    ""
  ).trim();
  const consigneeBranchFieldId = job.field_values.find(
    (fv) => fv.row_index == null && fv.label_name === "ERP Importer Address / Branch Code",
  )?.custom_field_id;
  const [consigneeBranchOptions, setConsigneeBranchOptions] =
    useState<jobsApi.CustomFieldReferenceOption[]>([]);
  useEffect(() => {
    if (!savedConsigneeName || !consigneeBranchFieldId) {
      setConsigneeBranchOptions([]);
      return;
    }
    let cancelled = false;
    jobsApi
      .listCustomFieldReferenceOptions(jobId, consigneeBranchFieldId, savedConsigneeName)
      .then((options) => {
        if (!cancelled) setConsigneeBranchOptions(options);
      })
      .catch(() => {
        if (!cancelled) setConsigneeBranchOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [savedConsigneeName, consigneeBranchFieldId, jobId]);

  // ERP Consignee Country drives which country ERP Consignee Name's own search is scoped to
  // (see asyncSearchPicker.country) - read LIVE (not-yet-saved draft), same reasoning as
  // liveErpEntryName above: narrowing the Name search the instant the operator picks a
  // country, not only after that pick is itself saved.
  const consigneeNameFieldId = job.field_values.find(
    (fv) => fv.row_index == null && fv.label_name === "ERP Consignee Name",
  )?.custom_field_id;
  const savedConsigneeCountry = (
    job.field_values.find((fv) => fv.row_index == null && fv.label_name === "ERP Consignee Country")
      ?.corrected_value ??
    job.field_values.find((fv) => fv.row_index == null && fv.label_name === "ERP Consignee Country")
      ?.extracted_value ??
    ""
  ).trim();
  const [liveConsigneeCountry, setLiveConsigneeCountry] = useState(savedConsigneeCountry);
  useEffect(() => {
    setLiveConsigneeCountry(savedConsigneeCountry);
  }, [savedConsigneeCountry]);
  // The dropdown's own option list - every country ERP Consignee Name's reference table
  // actually has rows for, fetched once per field rather than hand-maintained separately (see
  // listCustomFieldReferenceCountries's own docstring).
  const [consigneeCountryOptions, setConsigneeCountryOptions] = useState<string[]>([]);
  useEffect(() => {
    if (!consigneeNameFieldId) {
      setConsigneeCountryOptions([]);
      return;
    }
    let cancelled = false;
    jobsApi
      .listCustomFieldReferenceCountries(jobId, consigneeNameFieldId)
      .then((countries) => {
        if (!cancelled) setConsigneeCountryOptions(countries);
      })
      .catch(() => {
        if (!cancelled) setConsigneeCountryOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [consigneeNameFieldId, jobId]);

  // Insurance Rate (%)'s own save-gate: ExtractedField's onBeforeSave resolves this promise
  // once InsuranceCertificatePopup is answered (a file uploaded, a reason typed, or cancelled).
  const [insurancePopup, setInsurancePopup] =
    useState<{ customFieldId: string; resolve: (ok: boolean) => void } | null>(null);
  function requireInsuranceCertificate(customFieldId: string): Promise<boolean> {
    return new Promise((resolve) => setInsurancePopup({ customFieldId, resolve }));
  }

  // Named sections for the flat field grid above (visibleFields + relocatedFields combined),
  // grouped the way an operator reads a shipment's paperwork rather than whatever order marks
  // happen to be stored in. null when this doc_type has no grouping configured (every other
  // template) - the caller then falls back to the plain ungrouped grid, unchanged.
  const groupedDocFields = useMemo(() => {
    if (!active) return null;
    const groups = FIELD_GROUPS_BY_DOC_TYPE[active.doc_type];
    if (!groups) return null;
    const all = [...visibleFields, ...relocatedFields];
    const byLabel = new Map(all.map((fv) => [fv.label_name, fv] as const));
    const used = new Set<string>();
    const sections = groups
      .map(({ heading, labels }) => {
        const items = labels
          .map((label) => byLabel.get(label))
          .filter((fv): fv is JobFieldValue => !!fv);
        items.forEach((fv) => used.add(fv.label_name));
        return { heading, items };
      })
      .filter((section) => section.items.length > 0);
    const leftover = all.filter((fv) => !used.has(fv.label_name));
    if (leftover.length) sections.push({ heading: "Other", items: leftover });
    return sections;
  }, [active, visibleFields, relocatedFields]);

  const lineRows = useMemo(() => {
    if (!active) return [];
    const mine = job.field_values.filter(
      (fv) => fv.row_index != null && fv.job_document_id === active.id && fv.mark_id,
    );
    const byRow = new Map<number, JobFieldValue[]>();
    for (const fv of mine) {
      const arr = byRow.get(fv.row_index!) ?? [];
      arr.push(fv);
      byRow.set(fv.row_index!, arr);
    }
    return [...byRow.entries()].sort((a, b) => a[0] - b[0]);
  }, [job.field_values, active]);

  // A standalone-row document (e.g. container numbers off the Bill of Lading) is its own
  // table, not a line-item family like the Invoice's products - it has no CTH/RITC to look
  // up and nothing to combine, so it gets its own heading instead of "Product N" and never
  // joins the cross-document reference-sheet lookup below.
  const docIsStandalone =
    lineRows.length > 0 && lineRows.every(([, cells]) => cells.every((fv) => fv.standalone_multi_value));
  // Wordable per tenant/template (FieldMark.standalone_group_heading) - "Container" for one
  // customer's Bill of Lading could be something else for another's. Falls back to the
  // field's own label_name when nothing was set.
  const standaloneHeading = docIsStandalone
    ? lineRows[0][1][0].standalone_group_heading?.trim() || lineRows[0][1][0].label_name
    : null;

  // The reference-sheet lookup for THIS SAME product line (the CTH/RITC code, keyed off the
  // part number sitting right there in the card above) - self_filled carries no mark and no
  // job_document_id of its own, only the row_index that ties it to one physical line, so it
  // has to be joined in here rather than by document. Shown alongside the line it was looked
  // up FOR instead of in a second, disconnected "Line 1" card further down the page under
  // Dump Data - a part code and the classification looked up for it belong on one card.
  const lookedUpByRow = useMemo(() => {
    const rows = new Map<number, JobFieldValue[]>();
    // A custom per-row field carries no job_document_id of its own (only set_index/row_index -
    // see the backend's own per-row write loop), so on a job with several invoices "row 1"
    // alone is ambiguous: set 1's row 1 and set 3's row 1 are two different products. Scoped
    // to the SAME invoice-set as the active document, the same normalization (blank = 1) the
    // backend itself uses when writing these rows.
    const activeSet = active?.set_index ?? 1;
    // Group every per-row custom field by (set, row) FIRST, so a pairing can be resolved
    // within its own row before deciding what belongs on this card - "is this field the
    // OTHER half of some other field's pairing" can't be answered by looking at one field
    // alone.
    const bySetRow = new Map<string, JobFieldValue[]>();
    for (const fv of job.field_values) {
      if (!fv.custom_field_id || fv.row_index == null) continue;
      const key = `${fv.set_index ?? 1}:${fv.row_index}`;
      const arr = bySetRow.get(key) ?? [];
      arr.push(fv);
      bySetRow.set(key, arr);
    }
    for (const [key, fvs] of bySetRow) {
      const [setPart, rowPart] = key.split(":");
      if (Number(setPart) !== activeSet) continue;
      const byId = new Map(fvs.map((fv) => [fv.custom_field_id, fv]));
      const pairedIds = new Set<string>();
      for (const fv of fvs) {
        if (!fv.paired_custom_field_id) continue;
        pairedIds.add(fv.id);
        const other = byId.get(fv.paired_custom_field_id);
        if (other) pairedIds.add(other.id);
      }
      // A reference-sheet lookup (self_filled) or either half of a picker pairing belongs
      // here. A plain "ask the operator" per-row field with no pairing of its own (a duty
      // notification number, say) belongs ONLY on Additional Details' "One value per
      // product line" - showing it here too is the same box in two places. A field marked
      // hidden_from_capture (e.g. ContainerTypeCode, computed purely for an ERP script's own
      // mapping) never belongs anywhere on this screen at all.
      const belongs = fvs.filter(
        (fv) => !fv.hidden_from_capture && (fv.self_filled === true || pairedIds.has(fv.id)),
      );
      if (belongs.length) rows.set(Number(rowPart), belongs);
    }
    return rows;
  }, [job.field_values, active]);

  // Picking a source (Document vs Dump) for one product's picker applies the SAME SOURCE
  // CHOICE to every other product line's own pairing too - each line keeps its OWN primary/
  // other values; only which SIDE wins carries across. A one-time ripple triggered by the
  // explicit pick itself (see DualSourcePicker's own choose()), not an ongoing sync -
  // correcting one line afterwards (typing a new value directly, or picking the other side
  // back just for that line) never touches the rest again.
  const applyPairChoiceToAllRows = async (customFieldId: string, which: "primary" | "other") => {
    for (const [n] of lineRows) {
      const rowFields = lookedUpByRow.get(n) ?? [];
      const byCF = new Map(rowFields.map((fv) => [fv.custom_field_id, fv] as const));
      const primary = byCF.get(customFieldId);
      if (!primary || !primary.paired_custom_field_id) continue;
      const other = byCF.get(primary.paired_custom_field_id);
      const otherVal = (other?.corrected_value ?? other?.extracted_value ?? "").trim();
      if (!other || !otherVal) continue; // nothing to pick between on this line
      const primaryVal = (primary.corrected_value ?? primary.extracted_value ?? "").trim();
      const value = which === "primary" ? primaryVal : otherVal;
      await jobsApi.correctFieldValue(primary.id, value);
      const syncTargets = (primary.sync_field_ids ?? [])
        .map((id) => byCF.get(id))
        .filter((fv): fv is JobFieldValue => !!fv);
      for (const target of syncTargets) {
        await jobsApi.correctFieldValue(target.id, value);
      }
    }
    await reload();
  };

  // Combined Description Configuration - picking an ordered set of this template's own
  // per-line fields (Part No, Description, ...), each with its own separator, and joining them
  // into one "Combined Description"-style field for every product of a CONSIGNEE (not just
  // this job) - see CombinedDescriptionConfigModal. Replaced the job screen's older inline
  // "Combine fields" modal entirely (confirmed with the user) - saving there recomputes this
  // job immediately too, via the jobId passed below, so the change is visible right away.
  const [compositeOpen, setCompositeOpen] = useState(false);
  const jobConsigneeFv = job.field_values.find(
    (fv) => fv.row_index == null && fv.label_name === "consignee_full_name",
  );
  const jobConsignee = (jobConsigneeFv?.corrected_value ?? jobConsigneeFv?.extracted_value ?? "").trim();

  if (!files.length) {
    return (
      <Card className="p-8 text-center">
        <p className="text-sm text-slate-500">
          Nothing has been uploaded yet. Add the documents on Document Capture, then run
          extraction.
        </p>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-4 py-2 text-xs dark:border-slate-700 dark:bg-slate-800/50">
        <span className="text-slate-500">
          {approvedCount} of {files.length} document{files.length === 1 ? "" : "s"} approved —
          every one needs "Approved and Proceed" before this can move to Cross Docs Verification.
        </span>
      </div>

      {/* The per-file switcher used to be a row of tabs here — it's the rail's own checklist
          under "Required Details Review" now (see stageNav in JobRunPage), so picking a
          document and seeing which ones are approved happens in one place, not two. */}

      <div className="grid items-start gap-4 lg:grid-cols-2">
        {/* Sticky: the fields column is longer than the page image, and checking a value
            against the document is impossible if the document scrolls away while you read
            the twentieth field. */}
        <Card className="p-5 lg:sticky lg:top-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="font-semibold text-indigo-600 dark:text-indigo-400">
              Document Preview
            </h2>
            {!readOnly && active && (
              <div className="flex items-center gap-2">
                <input
                  ref={reuploadInputRef}
                  type="file"
                  accept=".pdf,.png,.jpg,.jpeg,.doc,.docx,.xls,.xlsx"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    if (file) void reuploadActiveDocument(file);
                  }}
                />
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={docActionBusy || job.status === "extracting"}
                  isLoading={docActionBusy}
                  onClick={() => reuploadInputRef.current?.click()}
                >
                  Reupload
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  disabled={docActionBusy || job.status === "extracting"}
                  onClick={() => setConfirmDeleteOpen(true)}
                >
                  Delete Document
                </Button>
              </div>
            )}
          </div>
          {docActionError && (
            <p className="mb-2 text-xs text-rose-600 dark:text-rose-400">{docActionError}</p>
          )}
          {active && (
            <DocViewer jobId={jobId} docId={active.id} pages={active.page_count} updatedAt={active.updated_at} highlight={highlight} />
          )}
        </Card>

        <Modal
          open={confirmDeleteOpen}
          onClose={() => setConfirmDeleteOpen(false)}
          title="Delete this document?"
        >
          <p className="text-sm text-slate-600 dark:text-slate-300">
            This removes {active?.name ?? "this document"}'s uploaded file and every value read
            off it. Once a replacement is uploaded, re-run extraction to fill those values back
            in.
          </p>
          <div className="mt-4 flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={() => setConfirmDeleteOpen(false)}
              className="text-xs text-slate-500 hover:underline dark:text-slate-400"
            >
              Cancel
            </button>
            <Button variant="danger" isLoading={docActionBusy} onClick={() => void deleteActiveDocument()}>
              Delete document
            </Button>
          </div>
        </Modal>

        <Card className="p-5">
          <h2 className="mb-1 font-semibold text-indigo-600 dark:text-indigo-400">
            Extracted Data
          </h2>
          <p className="mb-3 text-xs text-slate-400">
            The fields read out of this document. Check them against the page on the left and
            correct anything wrong — a line item's own reference-sheet lookup sits right on its
            card below; anything looked up for the whole job is further down, under Dump Data.
          </p>
          <div className="mb-4">
            <ColourKey />
          </div>
          {visibleFields.length === 0 && lineRows.length === 0 && relocatedFields.length === 0 ? (
            <p className="text-sm text-slate-400">
              Nothing was read from this document yet.
            </p>
          ) : (
            <>
              {(() => {
                const renderField = (fv: JobFieldValue) => {
                  // insurance_amount never renders on its own - it's shown (or not) from
                  // insurance_percentage's own slot below, via InsuranceModeField, so the toggle
                  // only ever appears once per section instead of as two separate rows.
                  if (fv.label_name === "insurance_amount") return null;
                  if (fv.label_name === "insurance_percentage") {
                    return (
                      <div key={fv.id} id={`field-${fv.id}`} className="sm:col-span-2">
                        <InsuranceModeField
                          percentageFv={insurancePercentageFv}
                          amountFv={insuranceAmountFv}
                          mode={insuranceMode}
                          onToggle={onToggleInsuranceMode}
                          readOnly={readOnly}
                          reload={reload}
                          labelOverridePercentage={active && FIELD_LABEL_OVERRIDES_BY_DOC_TYPE[active.doc_type]?.[fv.label_name]}
                          labelOverrideAmount={active && FIELD_LABEL_OVERRIDES_BY_DOC_TYPE[active.doc_type]?.["insurance_amount"]}
                          onBeforeSave={
                            (insuranceMode === "amount" ? insuranceAmountFv : insurancePercentageFv)?.custom_field_id
                              ? () =>
                                  requireInsuranceCertificate(
                                    (insuranceMode === "amount" ? insuranceAmountFv : insurancePercentageFv)!.custom_field_id!,
                                  )
                              : undefined
                          }
                        />
                      </div>
                    );
                  }
                  const from = usedFrom.get(fv.label_name);
                  const isMark = !!fv.mark_id;
                  const filterByLabel = DYNAMIC_REFERENCE_DROPDOWN_FILTER_BY[fv.label_name];
                  const dynamicRows =
                    filterByLabel && fv.custom_field_id
                      ? dynamicReferenceOptions[fv.custom_field_id]
                      : undefined;
                  let options: FieldSelectOption[] | undefined = FIELD_SELECT_OPTIONS[fv.label_name];
                  if (dynamicRows !== undefined) {
                    const filterFv = job.field_values.find(
                      (x) => x.row_index == null && x.label_name === filterByLabel,
                    );
                    const filterValue = (filterFv?.corrected_value ?? filterFv?.extracted_value ?? "")
                      .trim()
                      .toUpperCase();
                    const rows = filterValue
                      ? dynamicRows.filter((r) => (r.match_values[0] ?? "").toUpperCase() === filterValue)
                      : dynamicRows;
                    options = dedupe(rows.map((r) => r.value)).map((v) => ({ value: v, label: v }));
                  }
                  // "ERP Importer Address / Branch Code" - narrowed to the picked consignee's
                  // own branches server-side (see consigneeBranchOptions above), not via the
                  // generic dynamicRows path above (too large a table for that - see its own
                  // comment). `options` left undefined (not []) until a consignee is picked -
                  // ExtractedField renders a plain text box for undefined, but an empty SELECT
                  // with nothing pickable for [] - "not found" text reads better than that.
                  if (fv.label_name === "ERP Importer Address / Branch Code") {
                    options = consigneeBranchOptions.length
                      ? dedupe(consigneeBranchOptions.map((o) => o.value)).map((v) => ({
                          value: v,
                          label: v,
                        }))
                      : undefined;
                  }
                  // ERP Consignee Country - a plain, hand-picked dropdown (not reference-backed
                  // itself), but its OWN option list is read off ERP Consignee Name's reference
                  // table (every country that table actually has rows for) rather than a
                  // separately hand-maintained list that could drift out of sync with it.
                  if (fv.label_name === "ERP Consignee Country") {
                    options = consigneeCountryOptions.map((v) => ({ value: v, label: v }));
                  }
                  // Always offered, not just while blank - the operator can look up or correct
                  // Shipment Port Code at any time, the same way every other reference-backed
                  // field's own dropdown is always available regardless of its current value.
                  // ERP Consignee Name is NOT included here any more - it used to render as this
                  // separate dashed box below its own plain, freely-editable value box; it now
                  // renders as ONE merged search box via asyncSearchPicker below instead (by
                  // request - two stacked boxes for one field, with the top one a free-text box
                  // that could silently disagree with whatever the search below had found).
                  const showSearchFallback =
                    fv.label_name !== "ERP Consignee Name" &&
                    SEARCHABLE_REFERENCE_FIELD_LABELS.has(fv.label_name) && !!fv.custom_field_id;
                  const comboOptions = COMBOBOX_REFERENCE_FIELD_LABELS.has(fv.label_name) && fv.custom_field_id
                    ? dedupe((dynamicReferenceOptions[fv.custom_field_id] ?? []).map((o) => o.value))
                    : undefined;
                  const suggestedDefault = fv.custom_field_id ? suggestedValues[fv.custom_field_id] : undefined;
                  return (
                    <div
                      key={fv.id}
                      id={`field-${fv.id}`}
                      className={FULL_WIDTH_FIELD_LABELS.has(fv.label_name) ? "sm:col-span-2" : undefined}
                    >
                      <ExtractedField
                        fv={fv}
                        readOnly={readOnly}
                        reload={reload}
                        tone={isMark ? "document" : "computed"}
                        onFocusField={isMark ? setFocusedFv : undefined}
                        labelOverride={active && FIELD_LABEL_OVERRIDES_BY_DOC_TYPE[active.doc_type]?.[fv.label_name]}
                        options={options}
                        comboOptions={comboOptions}
                        asyncSearchPicker={
                          fv.label_name === "ERP Consignee Name" && fv.custom_field_id
                            ? {
                                jobId,
                                customFieldId: fv.custom_field_id,
                                placeholder: REFERENCE_SEARCH_PLACEHOLDERS[fv.label_name],
                                country: liveConsigneeCountry,
                                onPick: async (value: string) => {
                                  await jobsApi.correctFieldValue(fv.id, value);
                                  await reload();
                                },
                              }
                            : undefined
                        }
                        defaultValue={suggestedDefault ?? undefined}
                        onBeforeSave={
                          fv.label_name === "insurance_percentage" && fv.custom_field_id
                            ? () => requireInsuranceCertificate(fv.custom_field_id!)
                            : COUNTRY_CODE_FORMAT_FIELD_LABELS.has(fv.label_name)
                            ? async (value: string) => {
                                const err = countryCodeFormatError(value);
                                if (err) {
                                  window.alert(err);
                                  return false;
                                }
                                return true;
                              }
                            : undefined
                        }
                        transformBeforeSave={
                          COUNTRY_CODE_FORMAT_FIELD_LABELS.has(fv.label_name)
                            ? (value: string) => value.trim().toUpperCase()
                            : undefined
                        }
                        multiline={MULTILINE_FIELD_LABELS.has(fv.label_name)}
                        wrapLabel={WRAP_LABEL_FIELD_LABELS.has(fv.label_name)}
                        note={
                          isMark && from && from !== fv.document_name
                            ? `Cross-check only — the entry uses ${from}'s copy.`
                            : undefined
                        }
                        hint={FIELD_HINTS[fv.label_name]}
                        flagged={missingRequiredIds.has(fv.id)}
                        flagNote={missingRequiredIds.has(fv.id) ? "Mandatory — needs a value before this document can be approved." : undefined}
                        onDraftChange={
                          fv.label_name === "ERP Consignee Country" ? setLiveConsigneeCountry : undefined
                        }
                      />
                      {showSearchFallback && (
                        <ReferenceSearchPicker
                          jobId={jobId}
                          customFieldId={fv.custom_field_id!}
                          label={fv.label_name}
                          readOnly={readOnly}
                          onPick={async (value) => {
                            await jobsApi.correctFieldValue(fv.id, value);
                            await reload();
                          }}
                        />
                      )}
                    </div>
                  );
                };

                return groupedDocFields ? (
                  <div className="flex flex-col gap-5">
                    {groupedDocFields.map(({ heading, items }, i) => (
                      <div
                        key={heading}
                        className={i > 0 ? "border-t border-slate-200 pt-5 dark:border-slate-700" : ""}
                      >
                        <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                          {heading}
                        </p>
                        <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
                          {items.map(renderField)}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
                    {visibleFields.map(renderField)}
                    {relocatedFields.map(renderField)}
                  </div>
                );
              })()}

              {lineRows.length > 0 && (
                <div className="mt-6">
                  <div className="mb-2 flex items-center justify-between">
                    <p className="text-xs font-medium uppercase tracking-wider text-slate-500">
                      {docIsStandalone ? `${standaloneHeading} Detail` : "Product Detail"}
                      {" · "}
                      {lineRows.length}
                    </p>
                    {!readOnly && !docIsStandalone && (
                      <button
                        type="button"
                        onClick={() => setCompositeOpen(true)}
                        className="rounded-md border border-teal-200 bg-teal-50 px-2.5 py-1 text-xs font-medium text-teal-700 hover:bg-teal-100 dark:border-teal-500/20 dark:bg-teal-500/10 dark:text-teal-300 dark:hover:bg-teal-500/20"
                      >
                        🧩 Combine fields
                      </button>
                    )}
                  </div>
                  <div className="flex flex-col gap-2">
                    {lineRows.map(([n, cells]) => {
                      // A field keyed on a standalone mark (ContainerTypeCode, keyed on
                      // container_type) belongs on THAT mark's own card (Container Detail),
                      // never a product card and vice versa - see standalone_multi_value's
                      // own derivation (job-detail serialization, cf_standalone).
                      const lookedUp = (lookedUpByRow.get(n) ?? []).filter((fv) =>
                        docIsStandalone ? fv.standalone_multi_value === true : !fv.standalone_multi_value,
                      );
                      // Any field whose own config names a pairing (primary) gets matched, on
                      // THIS row, to the field it names (other) and whatever it lists as sync
                      // targets - by custom_field_id, never by label text, since a Super Admin
                      // can name these fields however they like.
                      const byCustomFieldId = new Map(lookedUp.map((fv) => [fv.custom_field_id, fv]));
                      const pairs = lookedUp
                        .filter((fv) => fv.paired_custom_field_id)
                        .map((primary) => ({
                          primary,
                          other: byCustomFieldId.get(primary.paired_custom_field_id!),
                          syncTargets: (primary.sync_field_ids ?? [])
                            .map((id) => byCustomFieldId.get(id))
                            .filter((fv): fv is JobFieldValue => !!fv),
                        }));
                      // A pair whose `other` half genuinely exists on this row always goes
                      // through the picker below (DualSourcePicker renders it regardless of
                      // whether either side has a value yet - see its own comment) rather than
                      // falling back to a plain box here: typing a correction only reaches
                      // syncTargets (RITC No., etc.) through the picker's own commit handlers,
                      // so a plain box would silently drop that sync. `other` missing entirely
                      // (no such field on this row at all) is the one real case with nothing to
                      // pair against - that falls through to `rest` as an ordinary field.
                      const pairedIds = new Set<string>();
                      pairs.forEach(({ primary, other, syncTargets }) => {
                        if (!other) return;
                        pairedIds.add(primary.id);
                        pairedIds.add(other.id);
                        // A pairing that feeds a field called CTH draws ONE box, and that box
                        // IS the CTH (see DualSourcePicker). Leaving the CTH field in `rest`
                        // as well would put the same value in two boxes on the same card, and
                        // only one of them would carry the sync when edited.
                        const cth = syncTargets.find(
                          (t) => (t.label_name ?? "").trim().toUpperCase() === "CTH",
                        );
                        if (cth) pairedIds.add(cth.id);
                      });
                      const rest = lookedUp.filter((fv) => !pairedIds.has(fv.id));
                      // Purely a backing flag for product_description/Combined Description's own
                      // red highlight below - never a field of its own on screen (see
                      // TRANSLATED_FLAG_SOURCE_LABELS).
                      const languageFlag = cells.find((fv) => fv.label_name === "Product Description Language");
                      const isNonEnglish = (
                        (languageFlag?.corrected_value ?? languageFlag?.extracted_value ?? "").trim().toUpperCase()
                        === "NON-ENGLISH"
                      );
                      const visibleCells = cells.filter((fv) => fv.label_name !== "Product Description Language");
                      return (
                        <div
                          key={n}
                          className="rounded-lg border border-slate-200 p-3 dark:border-slate-700"
                        >
                          <p className="mb-2 text-xs font-semibold text-slate-500">
                            {docIsStandalone
                              ? `${standaloneHeading} ${n} — ${cells[0].value || "not found"}`
                              : `Product ${n}`}
                          </p>
                          <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
                            {visibleCells.map((fv) => (
                              <ExtractedField
                                key={fv.id}
                                id={`field-${fv.id}`}
                                fv={fv}
                                readOnly={readOnly}
                                reload={reload}
                                small
                                tone="document"
                                onFocusField={setFocusedFv}
                                labelOverride={active && FIELD_LABEL_OVERRIDES_BY_DOC_TYPE[active.doc_type]?.[fv.label_name]}
                                flagged={missingRequiredIds.has(fv.id) || (isNonEnglish && fv.label_name === "product_description")}
                                flagNote={
                                  missingRequiredIds.has(fv.id)
                                    ? "Mandatory — needs a value before this document can be approved."
                                    : "Not originally in English — translated."
                                }
                              />
                            ))}
                            {/* Looked up in the customer's own reference sheet by the part
                                code on this same line - left blank rather than guessed when
                                the sheet does not carry it, same as everywhere else this
                                colour appears. */}
                            {rest.map((fv) => (
                              <ExtractedField
                                key={fv.id}
                                id={`field-${fv.id}`}
                                fv={fv}
                                readOnly={readOnly}
                                reload={reload}
                                flagged={missingRequiredIds.has(fv.id) || (isNonEnglish && fv.label_name === "Combined Description")}
                                flagNote={
                                  missingRequiredIds.has(fv.id)
                                    ? "Mandatory — needs a value before this document can be approved."
                                    : "Not originally in English — translated."
                                }
                                small
                                tone="reference"
                                labelOverride={active && FIELD_LABEL_OVERRIDES_BY_DOC_TYPE[active.doc_type]?.[fv.label_name]}
                                options={FIELD_SELECT_OPTIONS[fv.label_name]}
                                multiline={MULTILINE_FIELD_LABELS.has(fv.label_name)}
                              />
                            ))}
                          </div>
                          {pairs.length > 0 && (
                            <div className="mt-3 flex flex-col gap-3">
                              {pairs.map(({ primary, other, syncTargets }) => (
                                <DualSourcePicker
                                  key={primary.id}
                                  primary={primary}
                                  other={other}
                                  syncTargets={syncTargets}
                                  readOnly={readOnly}
                                  reload={reload}
                                  applyToAllRows={
                                    primary.custom_field_id
                                      ? (which) => applyPairChoiceToAllRows(primary.custom_field_id!, which)
                                      : undefined
                                  }
                                  // The CTH field is hidden above when this pairing feeds
                                  // one, so its "mandatory and still blank" state has to
                                  // travel with it - otherwise a job is blocked from
                                  // approval by a red field nobody can see.
                                  flagged={syncTargets.some(
                                    (t) =>
                                      (t.label_name ?? "").trim().toUpperCase() === "CTH" &&
                                      missingRequiredIds.has(t.id),
                                  )}
                                />
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </>
          )}
        </Card>
      </div>

      {approveError && <Alert>{approveError}</Alert>}
      {!readOnly && active && (
        <div className="flex items-center justify-end gap-3 rounded-lg border border-slate-200 px-4 py-3 dark:border-slate-700">
          {isApproved(active) ? (
            <>
              <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
                ✓ Approved — {active.name}
              </span>
              <Button variant="secondary" onClick={approveActive} isLoading={approvingId === active.id}>
                Undo
              </Button>
            </>
          ) : (
            <Button onClick={approveActive} isLoading={approvingId === active.id}>
              {proceedLabel} — {active.name}
            </Button>
          )}
        </div>
      )}

      <CombinedDescriptionConfigModal
        open={compositeOpen}
        onClose={() => setCompositeOpen(false)}
        groupId={job.group_id}
        defaultConsignee={jobConsignee}
        jobId={jobId}
        onSaved={() => { void reload(); }}
      />
      {insurancePopup && (
        <InsuranceCertificatePopup
          jobId={jobId}
          customFieldId={insurancePopup.customFieldId}
          onResolve={(ok) => {
            insurancePopup.resolve(ok);
            setInsurancePopup(null);
          }}
        />
      )}
    </div>
  );
}

/** Insurance Rate (%)'s own save gate - exactly one of an uploaded insurance premium
 *  certificate or a typed reason for not having one, before the rate itself is allowed to
 *  save (see ExtractedField's onBeforeSave). Saved as an ordinary SupportingDocument, so it
 *  shows up on IRN Processing's own document list the same way any other attached file does. */
function InsuranceCertificatePopup({
  jobId,
  customFieldId,
  onResolve,
}: {
  jobId: string;
  customFieldId: string;
  onResolve: (ok: boolean) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = Boolean(file) !== Boolean(reason.trim());

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      await jobsApi.uploadSupportingDocumentOrReason(
        jobId, customFieldId, "Insurance Premium Certificate",
        file ? { files: [file] } : { reason: reason.trim() },
      );
      onResolve(true);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "Could not save this - try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={() => onResolve(false)} title="Insurance Premium Certificate" maxWidth="max-w-2xl">
      <p className="mb-4 text-sm text-slate-500 dark:text-slate-400">
        Upload the certificate, or say why there isn't one — exactly one of the two is needed
        before this rate can be saved.
      </p>
      <div className="grid gap-5 sm:grid-cols-2">
        <div>
          <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">
            Upload the insurance premium certificate
          </label>
          <input
            type="file"
            disabled={busy || Boolean(reason.trim())}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-indigo-50 file:px-3 file:py-2 file:text-sm file:font-medium file:text-indigo-700 disabled:opacity-50 dark:text-slate-300 dark:file:bg-indigo-500/10 dark:file:text-indigo-300"
          />
          {file && <p className="mt-1.5 truncate text-xs text-slate-500">{file.name}</p>}
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">
            Reason for not uploading insurance premium certificate
          </label>
          <textarea
            rows={4}
            disabled={busy || Boolean(file)}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. covered under an annual blanket policy"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800"
          />
        </div>
      </div>
      {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={() => onResolve(false)} disabled={busy}>Cancel</Button>
        <Button onClick={submit} isLoading={busy} disabled={!canSubmit}>Save</Button>
      </div>
    </Modal>
  );
}

/** One page a smart-upload could not confidently place - a draggable card labelled with its
 *  own filename and page number, dropped by the operator onto the correct document type on
 *  the left (see the slot cards' own onDrop in the Document Capture tab). Native HTML5 drag
 *  and drop - no library exists anywhere in this frontend, and this is one list to another
 *  with no reordering, so adding one would be overkill. */
function UnclassifiedPageCard({
  jobId, page, busy, readOnly, onDragStart, onDragEnd, onView,
}: {
  jobId: string;
  page: jobsApi.UnclassifiedPage;
  busy: boolean;
  readOnly: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onView: () => void;
}) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let created: string | null = null;
    (async () => {
      try {
        const u = await jobsApi.unclassifiedPageImageUrl(jobId, page.id);
        created = u;
        if (alive) setUrl(u);
        else URL.revokeObjectURL(u);
      } catch {
        /* no thumbnail - the label alone still lets the operator drag it */
      }
    })();
    return () => {
      alive = false;
      if (created) URL.revokeObjectURL(created);
    };
  }, [jobId, page.id]);

  return (
    <div
      draggable={!readOnly && !busy}
      onDragStart={(e) => { e.dataTransfer.setData("text/plain", page.id); onDragStart(); }}
      onDragEnd={onDragEnd}
      className={`flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-2 py-2 text-xs dark:border-amber-500/20 dark:bg-amber-500/10 ${
        readOnly || busy ? "" : "cursor-grab active:cursor-grabbing"
      } ${busy ? "opacity-50" : ""}`}
      title={readOnly ? undefined : "Drag onto the correct document type on the left"}
    >
      {url ? (
        <img src={url} alt="" className="h-12 w-9 shrink-0 rounded border border-amber-300 object-cover" />
      ) : (
        <div className="h-12 w-9 shrink-0 rounded border border-amber-300 bg-amber-100 dark:bg-amber-500/20" />
      )}
      <span className="min-w-0 truncate text-amber-800 dark:text-amber-300">
        {page.original_filename} — page {page.page_number}
      </span>
      {busy && <span className="shrink-0 text-amber-600">assigning…</span>}
      {!busy && url && (
        <button
          type="button"
          draggable={false}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onView(); }}
          className="ml-auto shrink-0 rounded border border-amber-300 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 hover:bg-amber-100 dark:border-amber-500/40 dark:text-amber-300 dark:hover:bg-amber-500/20"
        >
          View
        </button>
      )}
    </div>
  );
}

/** Full-screen, zoomable view of one unclassified page, with Prev/Next to step through the
 *  whole list without closing and reopening - so an operator can read small print and
 *  compare a run of pages before deciding which slot each one actually belongs in. Plain
 *  +/- buttons and Ctrl+scroll, no library - the rest of this file's zoom (JobDocPreview) is
 *  a simple click-to-fit view; this one additionally needs to zoom PAST fit-to-screen and
 *  step between pages. Portaled to <body> - see the fix note on the commit that added this,
 *  the header's backdrop-blur-sm otherwise traps a plain `fixed` overlay beneath it. */
function UnclassifiedPageViewer({
  jobId, pages, index, onIndexChange, onClose,
}: {
  jobId: string;
  pages: jobsApi.UnclassifiedPage[];
  index: number;
  onIndexChange: (i: number) => void;
  onClose: () => void;
}) {
  const page = pages[index];
  const [url, setUrl] = useState<string | null>(null);
  const [scale, setScale] = useState(1);
  const MIN_SCALE = 0.5;
  const MAX_SCALE = 4;
  const hasPrev = index > 0;
  const hasNext = index < pages.length - 1;

  useEffect(() => {
    let alive = true;
    let created: string | null = null;
    setUrl(null);
    setScale(1);
    (async () => {
      try {
        const u = await jobsApi.unclassifiedPageImageUrl(jobId, page.id);
        created = u;
        if (alive) setUrl(u);
        else URL.revokeObjectURL(u);
      } catch {
        /* nothing to show - the header/nav still work */
      }
    })();
    return () => {
      alive = false;
      if (created) URL.revokeObjectURL(created);
    };
  }, [jobId, page.id]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft" && hasPrev) onIndexChange(index - 1);
      else if (e.key === "ArrowRight" && hasNext) onIndexChange(index + 1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onIndexChange, index, hasPrev, hasNext]);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex flex-col bg-slate-900/85"
      onClick={onClose}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between gap-3 px-4 py-3" onClick={(e) => e.stopPropagation()}>
        <span className="min-w-0 truncate text-sm text-slate-200">
          {page.original_filename} — page {page.page_number}
          <span className="ml-2 text-slate-400">({index + 1} of {pages.length})</span>
        </span>
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={() => setScale((s) => Math.max(MIN_SCALE, s - 0.25))}
            className="rounded bg-slate-700 px-2.5 py-1 text-sm font-semibold text-white hover:bg-slate-600"
          >
            −
          </button>
          <span className="w-12 text-center text-xs text-slate-300">{Math.round(scale * 100)}%</span>
          <button
            type="button"
            onClick={() => setScale((s) => Math.min(MAX_SCALE, s + 0.25))}
            className="rounded bg-slate-700 px-2.5 py-1 text-sm font-semibold text-white hover:bg-slate-600"
          >
            +
          </button>
          <button
            type="button"
            onClick={() => setScale(1)}
            className="ml-1 rounded bg-slate-700 px-2 py-1 text-xs text-white hover:bg-slate-600"
          >
            Reset
          </button>
          <button
            type="button"
            onClick={onClose}
            className="ml-2 rounded bg-slate-700 px-2 py-1 text-xs text-white hover:bg-slate-600"
          >
            ✕ Close
          </button>
        </div>
      </div>
      <div
        className="relative flex-1 overflow-auto px-6 pb-6"
        onClick={(e) => e.stopPropagation()}
        onWheel={(e) => {
          if (!e.ctrlKey) return;
          e.preventDefault();
          setScale((s) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, s + (e.deltaY < 0 ? 0.25 : -0.25))));
        }}
      >
        {hasPrev && (
          <button
            type="button"
            onClick={() => onIndexChange(index - 1)}
            title="Previous unclassified page"
            className="fixed left-4 top-1/2 z-10 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full bg-slate-700/90 text-lg text-white hover:bg-slate-600"
          >
            ‹
          </button>
        )}
        {hasNext && (
          <button
            type="button"
            onClick={() => onIndexChange(index + 1)}
            title="Next unclassified page"
            className="fixed right-4 top-1/2 z-10 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full bg-slate-700/90 text-lg text-white hover:bg-slate-600"
          >
            ›
          </button>
        )}
        {url ? (
          <img
            src={url}
            alt={`${page.original_filename} — page ${page.page_number}`}
            style={{ transform: `scale(${scale})`, transformOrigin: "top center" }}
            className="mx-auto max-h-[85vh] w-auto max-w-full rounded-lg shadow-2xl"
          />
        ) : (
          <p className="mt-16 text-center text-sm text-slate-400">Loading…</p>
        )}
      </div>
    </div>,
    document.body,
  );
}

function JobDocPreview({
  jobId, docId, pages, updatedAt, readOnly, onPageMenu,
}: {
  jobId: string; docId: string; pages: number; updatedAt?: string;
  readOnly?: boolean;
  /** Opens the Copy-to-another-slot menu scoped to just this one page (see docMenu's own
   *  `pageNumber` field) - 1-indexed, with the click's own position to anchor the menu. */
  onPageMenu?: (pageNumber: number, x: number, y: number) => void;
}) {
  const [urls, setUrls] = useState<string[]>([]);
  const [zoom, setZoom] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const created: string[] = [];
    (async () => {
      const out: string[] = [];
      for (let p = 1; p <= Math.min(pages, 6); p++) {
        try {
          const u = await jobsApi.jobDocPageUrl(jobId, docId, p, updatedAt);
          created.push(u);
          out.push(u);
        } catch {
          /* skip a missing page */
        }
      }
      if (alive) setUrls(out);
      else created.forEach((u) => URL.revokeObjectURL(u));
    })();
    return () => {
      alive = false;
      created.forEach((u) => URL.revokeObjectURL(u));
    };
    // updatedAt isn't read in the body - it only forces a re-fetch when a same-slot
    // reupload leaves docId/pages unchanged (see DocViewer's identical comment).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, docId, pages, updatedAt]);

  if (urls.length === 0) return null;
  return (
    <>
      <div className="mt-2 flex flex-wrap gap-2">
        {urls.map((u, i) => (
          <div key={i} className="group relative">
            <button type="button" onClick={() => setZoom(u)} className="block" title={`Page ${i + 1} — click to enlarge`}>
              <img src={u} alt={`page ${i + 1}`} className="h-28 w-auto rounded border border-slate-300 object-cover transition group-hover:brightness-75 hover:ring-2 hover:ring-indigo-400 dark:border-slate-700" />
            </button>
            {!readOnly && onPageMenu && (
              // Hidden until the thumbnail is hovered, then a big, obvious circle - the earlier
              // small always-on corner mark was too easy to miss entirely.
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onPageMenu(i + 1, e.clientX, e.clientY); }}
                title={`Copy or move page ${i + 1} to another document type`}
                className="absolute right-1 top-1 flex h-7 w-7 items-center justify-center rounded-full bg-slate-900/80 text-base font-bold leading-none text-white opacity-0 shadow-lg transition hover:bg-slate-900 focus-visible:opacity-100 group-hover:opacity-100"
              >
                ⋯
              </button>
            )}
          </div>
        ))}
      </div>
      {zoom && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/70 p-6" onClick={() => setZoom(null)}>
          <img src={zoom} alt="document page" className="max-h-[90vh] max-w-[90vw] rounded-lg shadow-2xl" />
        </div>
      )}
    </>
  );
}

const IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".gif", ".webp"];

/** A Supporting Document's own file, previewed the same way Prealert now previews Document
 *  Capture's images - only for actual image files, since a PDF (the common case here) has no
 *  simple single-image thumbnail the way an OCR'd page does. Every other file type stays the
 *  plain click-to-open link this replaces, via isImageFile below. */
function SupportingDocFileThumb({
  jobId, docId, storedAs, name,
}: { jobId: string; docId: string; storedAs: string; name: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [zoom, setZoom] = useState(false);

  useEffect(() => {
    let alive = true;
    let created: string | null = null;
    (async () => {
      try {
        const u = await jobsApi.supportingDocumentFileUrl(jobId, docId, storedAs);
        created = u;
        if (alive) setUrl(u);
        else URL.revokeObjectURL(u);
      } catch {
        /* falls back to nothing - the plain link elsewhere still works */
      }
    })();
    return () => {
      alive = false;
      if (created) URL.revokeObjectURL(created);
    };
  }, [jobId, docId, storedAs]);

  if (!url) return <span className="text-xs text-slate-400">{name}…</span>;
  return (
    <>
      <button type="button" onClick={() => setZoom(true)} className="block" title={`${name} — click to enlarge`}>
        <img src={url} alt={name} className="h-20 w-auto rounded border border-slate-300 object-cover hover:ring-2 hover:ring-indigo-400 dark:border-slate-700" />
      </button>
      {zoom && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/70 p-6" onClick={() => setZoom(false)}>
          <img src={url} alt={name} className="max-h-[90vh] max-w-[90vw] rounded-lg shadow-2xl" />
        </div>
      )}
    </>
  );
}
