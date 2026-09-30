export interface Mark {
  id: string;
  document_id: string;
  label_name: string;
  page_number: number;
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
  detected_anchor: string | null;
  example_value: string | null;
  anchor_variations: string[] | null;
  semantic_description: string | null;
  value_format_hint: string | null;
  extraction_prompt: string | null;
  correction_prompt: string | null;
  tenant_format_prompt: string | null;
  verify_with_other_document: boolean;
  /** Operator must confirm this value before ERP entry. */
  ask_operator?: boolean;
  /** false = optional: asked for, but never blocks Submit Entry. */
  ask_operator_required?: boolean;
  /** One value per line item instead of one per job (the CTH on each product line). */
  per_row?: boolean;
  /** Guidance shown to the operator, e.g. "T for Transaction, D for Deferred". */
  ask_operator_hint?: string | null;
  /** This document repeats this field — extraction returns one value per table row. */
  is_multi_value?: boolean;
  /** The extracted value is looked up in its own reference table (keyed on itself); a
   *  match replaces it, no match returns empty. */
  is_target_value?: boolean;
  /** is_target_value only: match on the same real-world thing despite OCR/formatting noise
   *  ("KUEHNE + NAGEL PVT. LTD." vs "KUEHNE+NAGEL") instead of requiring identical text. */
  fuzzy_match?: boolean;
}

export interface TemplateDocument {
  id: string;
  group_id: string;
  name: string;
  doc_type: string;
  order_index: number;
  page_count: number;
  is_uploaded: boolean;
  /** False = a job can pass Document Capture without this one ever being uploaded. */
  is_required?: boolean;
  marks: Mark[];
}

export interface CrossDocLink {
  id: string;
  group_id: string;
  /** Exactly one of source_mark_id / source_custom_field_id is set - see the backend
   *  CrossDocLink model. A custom field can be the SOURCE of a check but never the target. */
  source_mark_id?: string | null;
  source_custom_field_id?: string | null;
  target_mark_id: string;
  condition: string;
}

export interface TemplateGroup {
  id: string;
  tenant_id: string;
  name: string;
  status: string;
  /** Which transport mode this template is for — one of MODES below. */
  mode?: string | null;
  ruling_prompt?: string | null;
}

export interface CustomField {
  id: string;
  group_id: string;
  label_name: string;
  kind: "hardcoded" | "ai" | "lookup" | string;
  hardcoded_value: string | null;
  ai_prompt: string | null;
  source_document_ids: string[] | null;
  /** Operator must confirm this value before ERP entry. */
  ask_operator?: boolean;
  /** false = optional: asked for, but never blocks Submit Entry. */
  ask_operator_required?: boolean;
  /** One value per line item instead of one per job — the CTH on each product line. */
  per_row?: boolean;
  /** kind="ai" only: reads its own document(s) directly and returns every instance found
   *  (one JobFieldValue per row) instead of a single value — the AI-computed counterpart to
   *  a Mark ticked "multiple values in this document". */
  multi_value_from_document?: boolean;
  /** Guidance shown to the operator, e.g. "T for Transaction, D for Deferred". */
  ask_operator_hint?: string | null;
  /** kind="lookup": which line field's value is looked up in the reference sheet. */
  lookup_key_label?: string | null;
  /** Which of the sheet's columns that value is matched against. */
  lookup_match_columns?: string[] | null;
  /** Which column's value is brought back. */
  lookup_return_column?: string | null;
  /** This field is cross-verified against a mark on another document - see
   *  CrossDocLink.source_custom_field_id. The tick itself; the pairing(s) are separate rows. */
  verify_with_other_document?: boolean;
  /** The computed value is looked up in its own reference table (keyed on itself); a match
   *  replaces it, no match returns empty. */
  is_target_value?: boolean;
  /** is_target_value only: match on the same real-world thing despite OCR/formatting noise
   *  ("KUEHNE + NAGEL PVT. LTD." vs "KUEHNE+NAGEL") instead of requiring identical text. */
  fuzzy_match?: boolean;
  /** Typed live into the ERP while recording a script — see Mark.example_value. Never the
   *  field's real answer on any actual job. */
  example_value?: string | null;
  /** Links this field to another already-configured custom field into a picker pair - each
   *  half keeps computing its own value exactly as its own kind already does; pairing only
   *  adds a UI picker + a write-through onto THIS field's own corrected value. */
  paired_custom_field_id?: string | null;
  /** Heading shown above the two options, e.g. "CTH - pick which one is right for this
   *  line". Blank falls back to a generic heading. */
  picker_heading?: string | null;
  /** Other custom_field ids that also get set to whichever value is picked, on the same row
   *  (e.g. RITC following the CTH pick). */
  sync_field_ids?: string[] | null;
  /** kind="composite": ordered label_names of other fields on the same product line, joined
   *  with a single space (blank pieces skipped) to become this field's own value - e.g.
   *  Description + Part No + Material Code combined into one "product_description". */
  composite_source_labels?: string[] | null;
}

/** A customer's reference sheet — their own export, used to answer what the documents cannot.
 *  For Nokia it is 22,000 material codes against the CTH each one is declared under. */
export interface ReferenceSheet {
  attached?: boolean;
  /** A real master is parsed off-thread (25-30+ seconds for a large one) rather than inline
   *  on the upload/status request — true while that background parse is still running.
   *  materials/columns/sample are meaningless until this clears; poll the status route again
   *  in a few seconds. */
  processing?: boolean;
  /** The upload route reports whether anything usable was found in the file. */
  ok?: boolean;
  file_name?: string;
  /** How many keys were usable. A row with no code, or a description the sheet gives two
   *  different codes for, is deliberately not counted — it cannot be matched on safely. */
  materials: number;
  sample?: { material: string; cth: string }[];
  /** The sheet's own headings, returned by the upload so the screen never waits for them. */
  columns?: string[];
  detail?: string;
}

export interface TemplateGroupDetail extends TemplateGroup {
  documents: TemplateDocument[];
  cross_doc_links: CrossDocLink[];
  custom_fields?: CustomField[];
  /** fields — the script types every value; excel — the job becomes one workbook to attach. */
  entry_mode?: "fields" | "excel" | string;
  excel_config?: {
    source?: "blank" | "template" | string;
    /** The single-sheet shape, still read for configurations saved before multi-sheet. */
    sheet?: string;
    header_row?: number;
    file_name?: string;
    columns?: { column?: string; header: string; field: string }[];
    /** A workbook with several sheets to fill carries one entry per sheet instead. */
    sheets?: {
      sheet?: string;
      header_row?: number;
      /** job — one row for the whole job; line — one row per line item. */
      scope?: "job" | "line" | "fixed" | string;
      columns?: { column?: string; header: string; field: string }[];
    }[];
  } | null;
}

export interface DemoFieldResult {
  mark_id: string;
  label_name: string;
  extracted_value: string | null;
  matched_anchor: string | null;
  /** Every row found, for multi-value fields. */
  extracted_values?: (string | null)[] | null;
}

export interface DemoResult {
  document_id: string;
  results: DemoFieldResult[];
}

export interface DocumentDeclaration {
  name: string;
  doc_type: string;
  /** False = optional — Document Capture can complete without it. Default true. */
  is_required?: boolean;
}

export const MARK_COLORS: Record<string, string> = {
  red: "#ef4444",
  green: "#22c55e",
  blue: "#3b82f6",
  yellow: "#eab308",
  purple: "#a855f7",
};

export const DATA_TYPES = ["String", "Number", "Date", "Currency"] as const;
export const DOC_TYPES = ["BL", "Invoice", "PackingList", "Custom"] as const;

/** The transport modes a tenant can be licensed for and a template is built for — kept in
 *  one place so the tenant picker and the template wizard never drift apart. Mirrors
 *  backend app/core/modes.py exactly. */
export const MODES = [
  "Train Export", "Train Import",
  "Air Export", "Air Import",
  "Sea Import", "Sea Export",
  "Road Export", "Road Import",
] as const;
