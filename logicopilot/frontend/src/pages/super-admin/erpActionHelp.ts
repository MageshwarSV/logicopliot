/** What each ERP step type does, how to use it, and a worked example.
 *
 *  Shown behind the circled-i next to every action. Examples use real Logi-Sys style
 *  selectors and values rather than placeholders, because "selector: #foo" teaches nobody
 *  what to actually type.
 */
export interface ActionHelp {
  title: string;
  what: string;
  how: string[];
  example?: string;
}

export const ACTION_HELP: Record<string, ActionHelp> = {
  scroll: {
    title: "Scroll the page",
    what:
      "Moves the page up or down. The recorder shows one screen of the ERP at a time, so on a " +
      "long entry form the fields further down cannot be clicked or mapped until you scroll to " +
      "them. Each scroll is recorded, so playback is looking at the same part of the screen you " +
      "were — which matters for grids that only load their rows once they come into view.",
    how: [
      "Press ▲ Up / ▼ Down above the live browser — the page moves and a step is recorded",
      "Press it again and the two merge into one step, not two",
      "Scroll back to where you started and the step disappears — nothing to replay",
      "Value is pixels: positive scrolls down, negative scrolls up",
      "You rarely need this just to click something — playback scrolls to an element on its own",
    ],
    example: "action: scroll\nvalue:  600      (down one screen; -600 goes back up)",
  },

  double_click: {
    title: "Double click",
    what:
      "Double-clicks an element. Some ERP grids and read-only fields only open on a double " +
      "click — a single one just highlights the row and nothing happens.",
    how: [
      "Click the element in the live browser — a normal click step is recorded",
      "Tap the SAME spot again within a second — the step becomes a double click",
      "The status line confirms: 'Step N changed to a DOUBLE click'",
      "Or add the step by hand and choose double_click as the action",
    ],
    example: "action:   double_click\nselector: #gridInvoices tr:nth-of-type(2)",
  },

  pick_date: {
    title: "Pick a date (pick_date)",
    what:
      "Puts a date into a date field, whether it accepts typing or only a pop-up calendar. " +
      "Many ERP date boxes are read-only on purpose — typing does nothing and the value never " +
      "arrives. This tries typing first, checks it stuck, and otherwise opens the calendar, " +
      "steps to the right month and clicks the day.",
    how: [
      "Click the date field in the live browser",
      "Choose pick_date as the action",
      "Put the date in Value, or map a date field such as Invoice Date",
      "Dates are read DAY first: 06/12/2026 is 6 December, never 12 June",
    ],
    example: "action:   pick_date\nselector: #ctl00_txtInvDate\nvalue:    2026-06-25",
  },
  upload: {
    title: "Attach a file (upload)",
    what:
      "Attaches one of the job's uploaded documents to a file input. The step stores the " +
      "document NAME, never a path on disk, so the same script works on any server.",
    how: [
      "Click the ERP's Browse / Choose file control",
      "Choose upload as the action",
      "In Value put the document name as it appears on the template — Invoice, Bill of lading, Package List",
      "At run time the operator's uploaded copy of that document is attached",
    ],
    example: "action:   upload\nselector: #ctl00_fileUpload\nvalue:    Invoice",
  },
  row_action: {
    title: "Click inside a grid row (row_action)",
    what:
      "Finds the table row containing a value and clicks a control inside THAT row. A grid's " +
      "Edit / Update / Select links are identical on every row, so an ordinary click always " +
      "hits the first one — this picks the right row by its own content.",
    how: [
      "Choose row_action as the action",
      "Value = text that identifies the row, e.g. the invoice number",
      "Description = the control to click in that row, e.g. Edit",
      "Selector can stay as the grid or the page — the row is found by its text",
    ],
    example: "action:      row_action\nvalue:       INV-002\ndescription: Edit",
  },

  // ---------------------------------------------------------------- existing
  navigate: {
    title: "Go to page (navigate)",
    what: "Loads a URL. Normally only the first step — the recorder already opens the script's URL for you.",
    how: ["Add the step manually", "Put the full URL in Value"],
    example: "action:   navigate\nvalue:    https://liveimpex.softlinkglobal.com/Home",
  },
  click: {
    title: "Click",
    what: "Clicks a button, link or menu item. Recorded automatically when you click on the live browser image.",
    how: ["Click the element in the live browser", "The step is recorded with its selector"],
    example: "action:   click\nselector: #btnSaveBOE",
  },
  fill: {
    title: "Type into a field (fill)",
    what: "Types a value into a text input, key by key so type-ahead lists open. Verifies the value stayed and re-enters up to 3 times if the ERP clears it.",
    how: [
      "Click the input in the live browser, or drag a data field onto it",
      "Choose a data field (from the job) or a fixed value",
    ],
    example: "action:      fill\nselector:    #txtInvoiceNo\nfield_label: invoice_number     -> types CH20261122",
  },
  select: {
    title: "Choose from a dropdown (select)",
    what: "Picks an option from a <select>. Matches loosely, so 'Sea' still finds 'SEA - By Sea'.",
    how: ["Click the dropdown in the live browser", "Pick the option, or map a data field"],
    example: "action:      select\nselector:    #ddlTransportMode\nfield_label: TransportModeCode   -> selects S",
  },
  autocomplete: {
    title: "Search-and-pick (autocomplete)",
    what: "For a searchable dropdown: types the value, waits for the suggestion list, then clicks the matching entry. Use this when a plain fill leaves the field looking filled but unaccepted.",
    how: [
      "Click the field in the live browser",
      "Tick 'this is a searchable dropdown' in the popup",
      "Map the data field",
    ],
    example: "action:      autocomplete\nselector:    #txtSupplier\nfield_label: consigner_name  -> types, waits, picks the match",
  },
  submit: {
    title: "Submit",
    what: "The click that commits the entry. Before it runs, EVERY value already entered is re-checked and re-entered if the ERP cleared it — so nothing is submitted half-filled.",
    how: ["Click the save/submit button in the live browser", "Change the action to submit"],
    example: "action:   submit\nselector: #btnSubmitBOE",
  },
  wait: {
    title: "Wait a fixed time",
    what: "Pauses for a set number of seconds. Prefer 'Wait for element' — a fixed wait is either too short on a slow day or wasted time on a fast one.",
    how: ["Add the step manually", "Put the number of seconds in Value"],
    example: "action:   wait\nvalue:    3        -> pauses 3 seconds",
  },
  ai_action: {
    title: "Let AI decide (ai_action)",
    what: "Describe a goal in plain words and the AI reads the page and clicks whatever moves the flow toward it. Use it for screens that vary — a warning popup that only sometimes appears.",
    how: ["Add an AI step", "Write the goal in plain English"],
    example: 'action: ai_action\ngoal:   "dismiss any warning popup and get back to the entry form"',
  },
  switch_tab: {
    title: "Jump to another tab",
    what: "The ERP often opens the next screen in a new tab or window on submit. This follows it, so recording and playback both continue on the right page.",
    how: [
      "Submit, and wait for the new tab to appear in the tab bar",
      "Click that tab — the switch is recorded for you",
    ],
    example:
      "action: switch_tab\nvalue:  1                       (tab index)\ndescr:  .../BOE/Confirmation      (matched on URL first)",
  },

  // ---------------------------------------------------------------- new
  get_text: {
    title: "Read a value back (get_text)",
    what: "Reads text OFF the ERP page and saves it on the job. This is how you capture the reference the ERP generates — the Bill of Entry number — so a completed job can be reconciled later. Without it a successful entry leaves no ERP-side record.",
    how: [
      "Click the element showing the value, or leave the selector empty to search the whole page",
      "Put a name in 'Capture as' — that is the key it is stored under",
      "Optionally add a regular expression in Value to pull just the number out",
    ],
    example:
      'action:     get_text\nselector:   (empty = whole page)\nvalue:      BE No:\\s*(\\d+)\ncapture_as: be_number\n\n-> page says "BE No: 1234567 dated 14/08/2026"\n-> job.erp_captured = { "be_number": "1234567" }',
  },
  assert_text: {
    title: "Check the page says it worked (assert_text)",
    what: "Fails the run unless the given text is on the page. Put it after submit so a silent failure is reported instead of the job being marked complete on a click that did nothing.",
    how: ["Click the success message element (or leave the selector empty)", "Put the text you expect in Value"],
    example:
      'action:   assert_text\nselector: .alert-success\nvalue:    generated successfully\n\n-> not found: run FAILS with "expected \'generated successfully\'"',
  },
  wait_for: {
    title: "Wait for something to appear (wait_for)",
    what: "Pauses until an element or a piece of text shows up, then continues immediately. Replaces guessing with a fixed wait.",
    how: [
      "Click the element you are waiting for, or leave the selector empty and put the text in Value",
      "Optionally set a timeout in seconds (default 20)",
    ],
    example:
      "action:   wait_for\nselector: #boeNumberLabel\ntimeout:  30\n\n-> continues the moment it appears; fails after 30s",
  },
  wait_gone: {
    title: "Wait for something to disappear (wait_gone)",
    what: "Pauses until an element goes away — a loading spinner or a modal overlay. Without it the next click can land on the overlay instead of the form underneath.",
    how: ["Click the spinner/overlay in the live browser", "Optionally set a timeout in seconds"],
    example: "action:   wait_gone\nselector: .loading-overlay\ntimeout:  30",
  },
  hover: {
    title: "Hover the mouse",
    what: "Moves the pointer over an element without clicking. Needed for menus that only open on hover — clicking them directly does nothing.",
    how: ["Add the step manually and give the selector of the menu item"],
    example: "action:   hover\nselector: #menuImports        -> submenu opens, then click the child",
  },
  send_keys: {
    title: "Press keys (send_keys)",
    what: "Presses keyboard keys on an element. Grid cells often only commit on Tab or Enter, and some ERPs save on Ctrl+S. Separate several keys with +++.",
    how: [
      "Give the selector of the field to press the keys on",
      "Put the key names in Value; join a sequence with +++",
    ],
    example:
      "action:   send_keys\nselector: #gridQty_1\nvalue:    Tab+++Enter      -> presses Tab, then Enter\n\nother keys: Escape, Control+s, F4, ArrowDown",
  },
  check: {
    title: "Tick / untick a checkbox",
    what: "Sets a checkbox to an exact state. Safer than a click, which TOGGLES — clicking an already-ticked box silently unticks it and nothing downstream notices.",
    how: ["Click the checkbox in the live browser", "Put true or false in Value (empty means tick)"],
    example: "action:   check\nselector: #chkUnderSec46\nvalue:    true        -> ticked (use false to untick)",
  },
  radio: {
    title: "Select a radio button",
    what: "Selects one option in a radio group. Give the selector of the specific option you want, not the group.",
    how: ["Click the radio option in the live browser"],
    example: "action:   radio\nselector: input[name='beType'][value='H']",
  },
  clear: {
    title: "Empty a field (clear)",
    what: "Blanks a field. Useful when the ERP pre-fills a default you must remove before typing, since typing on top can append instead of replace.",
    how: ["Give the selector of the field to empty"],
    example: "action:   clear\nselector: #txtRemarks",
  },
  dialog: {
    title: "Answer a browser popup (dialog)",
    what: "Pre-answers a native alert / confirm / prompt box. Add it BEFORE the step that triggers the popup. Without it a confirm-on-submit is auto-dismissed, which silently cancels the entry.",
    how: [
      "Add the step immediately before the submit that raises the popup",
      "Put accept or dismiss in Value",
      "For a prompt box, put the text to type in 'prompt text'",
    ],
    example:
      'action: dialog\nvalue:  accept\n\n-> next step clicks Submit, ERP asks "Confirm entry?", we click OK',
  },
  download: {
    title: "Capture a document (download)",
    what: "Clicks a link or button and keeps the file the ERP returns — a filed Bill of Entry PDF, a duty challan. The file is stored against the job and the operator can download it from their Completed screen.",
    how: ["Click the download link in the live browser", "Choose Pick data -> Document", "Give it a label"],
    example:
      "action:     download\nselector:   #lnkPrintBOE\ncapture_as: filed_boe\n\n" +
      "-> operator's Completed screen offers 'filed_boe' as a download",
  },
};

/** Line-item and tab features get help too — these are not step actions. */
export const FEATURE_HELP: Record<string, ActionHelp> = {
  checkpoint: {
    title: "Checkpoint",
    what:
      "The ⚑ marks the moment this script has finished logging in and navigating, and is " +
      "standing on the entry screen with nothing typed yet. Everything above the flag is " +
      "setup; everything below it is the actual entry.",
    how: [
      "While recording: the moment you are logged in and the entry screen is up, press ⚑ Capture checkpoint in the toolbar — it marks the step you just performed",
      "Or afterwards: click ⚑ on any step in the recorded list",
      "An amber CHECKPOINT line appears under that step; press either control again to clear it",
      "Setting one reveals the 'Stay open at the checkpoint' option",
      "Save. Only flagged scripts behave differently — without a flag, every job opens the browser, runs all the steps and closes, exactly as before",
    ],
    example:
      "1 navigate  2 fill username  3 fill password  4 click Login  5 click Imports ▾  " +
      "6 click Bill of Entry ⚑  7 fill Invoice No  8 fill Value  9 click Submit. " +
      "Steps 1–6 are setup and take ~25 seconds every run; 7–9 are the entry.",
  },
  stay_open: {
    title: "Stay open at the checkpoint",
    what:
      "Keeps the logged-in session the script reached at the ⚑ checkpoint. The next job " +
      "starts from the entry screen instead of typing the username and password again.",
    how: [
      "Set a ⚑ checkpoint first, then tick this box and save",
      "Run 1 logs in normally; the session is saved the moment the script passes the flag",
      "Every run after that opens on the entry screen and starts at the step below the flag",
      "If the ERP has logged the session out, or the entry screen has moved, the saved session is discarded and the full script replays from step 1 — a stale session costs time, never a failed job",
      "Sessions older than 30 minutes are ignored and the login is replayed",
    ],
    example:
      "With the 9-step script above: run 1 does all 9 steps. Run 2 opens on the Bill of Entry " +
      "screen and does steps 7, 8, 9 only — about 25 seconds saved, and no repeated login for " +
      "the ERP to rate-limit or lock.",
  },
  multi_value: {
    title: "Field with many values",
    what: "A field ticked 'multiple values' holds one value per invoice line — a job may bring 1 or 40. An ERP form rarely takes them all in one box, so you choose how they go in.",
    how: [
      "Drag the field onto the input",
      "Pick 'Drop all at once' to join every value into that one box",
      "Or pick 'One value at a time' to loop",
    ],
    example:
      "product_description has 14 rows\n\nall at once -> one box gets:\n  1A000000314B - PA-RF-Gasket, 1A000001550A - shield-cover, ...\n\none at a time -> 14 separate entries",
  },
  pick_data: {
    title: "Pick data (read out of the ERP)",
    what: "Most steps PUT data into the ERP. This one takes data OUT — the reference it generated, a status message, or a file it produced — labels it, and shows it to the operator on their Completed screen once the job finishes. Without it a successful entry leaves nothing you can reconcile against the ERP later.",
    how: [
      "Click the element on the page that shows the value (or the download link, for a file)",
      "Choose Pick data instead of Enter data",
      "Give it a Label — that is the name the operator sees",
      "Choose the kind: Value, Text, or Document",
      "Optionally write a Description explaining what it is; the operator sees it beside the value",
    ],
    example:
      "Label:       be_number\n" +
      "Kind:        Value\n" +
      "Usage:       Both\n" +
      "Description: Bill of Entry number from ICEGATE — quote on the duty challan\n" +
      "Pattern:     BE No:\\s*(\\d+)\n\n" +
      '-> page shows "BE No: 1234567 dated 14/08/2026"\n' +
      "-> a later step can enter it by mapping the field  be_number\n" +
      "-> operator's Completed screen shows:\n" +
      "     be_number   1234567\n" +
      '     "Bill of Entry number from ICEGATE — quote on the duty challan"',
  },
  row_loop: {
    title: "One value at a time (row loop)",
    what: "Records what you do for ONE line item, then repeats it for every value the job has. You never record 14 rows by hand and it adapts to any row count.",
    how: [
      "Choose 'One value at a time' when you drop the field",
      "Do row 1 by hand: the value is typed, then click across to the next input or press 'add row'",
      "Click 'End of row'",
      "Do whatever closes the table, then click 'End of table'",
    ],
    example:
      "recorded: [type value] [click Qty] [click Add Row]  + end: [click Items Done]\n\nplayback for 14 rows:\n  value1 -> Qty -> AddRow -> value2 -> Qty -> AddRow -> ... -> Items Done",
  },
  tabs: {
    title: "Tabs and windows",
    what: "Lists every tab and popup window the ERP has open. Submitting often opens the next screen in a new tab; clicking it here switches the view AND records the jump so playback follows the same path.",
    how: [
      "Watch the strip after a submit — a new tab appears within ~3 seconds",
      "Click it to switch and record the jump",
      "Carry on recording; the steps now apply to that tab",
    ],
    example:
      "2 tabs   1. Live Impex ●   2. BOE Confirmation\n\n-> click tab 2, step recorded: switch_tab -> .../BOE/Confirmation",
  },
};
