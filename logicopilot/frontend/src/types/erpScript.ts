export interface ErpStep {
  action: "navigate" | "click" | "fill" | "select" | "wait" | "submit" | "scroll" | string;
  selector?: string | null;
  value?: string | null;
  field_label?: string | null; // template field mapped into this input
  prompt?: string | null; // AI rule: decide the value at run time from the job's data
  goal?: string | null; // AI step: natural-language goal for AI to pick what to click
  frames?: string[] | null; // iframe chain (top -> innermost) the element lives in
  options?: string[] | null; // captured dropdown options
  description?: string | null;
  /** Line-item fields only. An ERP form rarely takes a whole table in one box:
   *  "all_at_once" puts every value in this input joined by `join_with`;
   *  "per_row" types value 1, replays `row_steps`, types value 2, replays `row_steps`, …
   *  and runs `end_steps` once after the last value. `row_steps` is recorded by doing ONE
   *  row, so the same block replays for however many rows a job turns out to have. */
  /** get_text: the key this value is stored under in job.erp_captured (e.g. "be_number"). */
  capture_as?: string | null;
  /** wait_for / wait_gone: seconds before giving up. Default 20. */
  timeout?: number | string | null;
  /** dialog: text to type into a prompt() box. Empty for alert/confirm. */
  prompt_text?: string | null;
  /** "Pick data" steps: what kind of thing is being read out, and the description the
   *  operator sees beside it on their Completed screen. */
  capture_kind?: "value" | "text" | "document" | null;
  capture_description?: string | null;
  /** Where the picked value is needed. "internal" = reusable as [[label]] in a later step but
   *  hidden from the operator; "output" = shown to the operator only; "both" = default. */
  capture_usage?: "internal" | "output" | "both" | null;
  /** Where inside the element to click, as a fraction of its width and height. Recorded from
   *  where the Super Admin actually pressed. Absent = the centre, which is right for a button
   *  and wrong for a table row, whose centre is whatever column sits in the middle. */
  click_fx?: number | null;
  click_fy?: number | null;
  /** Optional step: if its element isn't on screen, skip it instead of failing the run.
   *  For screens that only appear sometimes — a "previous session was cleared" warning, an
   *  "are you sure?" confirm, a new-version notice. Recorded as an ordinary click, each of
   *  those breaks every run where it does NOT appear. */
  is_optional?: boolean | null;
  /** This step is THE success signal — the text/element proving the ERP accepted the entry.
   *  Passes -> job completed. Fails -> the screen is diagnosed by AI and the job is failed. */
  is_success_check?: boolean | null;
  multi_mode?: "all_at_once" | "per_row" | null;
  join_with?: string | null;
  row_steps?: ErpStep[] | null;
  end_steps?: ErpStep[] | null;
  /** This step is the CHECKPOINT — login and navigation are done, the entry screen is up. */
  is_checkpoint?: boolean | null;
}

export interface ErpScript {
  id: string;
  tenant_id: string;
  name: string;
  url: string;
  has_login: boolean;
  login_username: string | null;
  template_ids: string[] | null;
  steps: ErpStep[] | null;
  status: string; // draft | ready
  notes: string | null;
  /** 0-based index of the step marked as the checkpoint; null = no checkpoint. */
  checkpoint_index: number | null;
  /** Reuse the logged-in session reached at the checkpoint for the next job. */
  stay_open: boolean;
}
