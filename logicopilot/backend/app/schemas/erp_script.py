from pydantic import BaseModel, ConfigDict


class ErpStep(BaseModel):
    action: str  # navigate | click | fill | select | wait | submit
    selector: str | None = None
    value: str | None = None
    field_label: str | None = None  # template field mapped to this input (a data "pill")
    prompt: str | None = None  # AI rule: decide the value at run time from the job's data
    goal: str | None = None  # AI step: natural-language goal for AI to pick what to click
    frames: list[str] | None = None  # iframe chain (top -> innermost) the element lives in
    options: list[str] | None = None  # captured dropdown options
    description: str | None = None

    # --- line-item fields ("multiple values in this document") -------------------------
    # A field marked multi-value yields one value per table row, and an ERP form almost never
    # takes them in a single box: you type row 1, click across to the next input or an "add
    # row" button, type row 2, and so on, then a final action closes the table. So a step
    # bound to such a field records HOW to enter many values, not just where.
    #   all_at_once — put every value in this one input, joined by `join_with`.
    #   per_row     — type value 1, replay `row_steps`, type value 2, replay `row_steps`, …
    #                 and after the last value replay `end_steps` once.
    # The Super Admin records `row_steps` by performing ONE row and marking the end of it, so
    # the same block replays however many rows a given job happens to have.
    # --- reading values back OUT of the ERP, and synchronisation --------------------------
    # Without these declared, pydantic drops them on save and a get_text step would come back
    # with nowhere to store its value and no regex to find it — silently doing nothing.
    capture_as: str | None = None  # key under which get_text stores the value (e.g. be_number)
    timeout: float | str | None = None  # wait_for / wait_gone: seconds before giving up
    prompt_text: str | None = None  # dialog: text typed into a prompt() box

    # A step can PUT data into the ERP or PICK data back out of it. When picking, the operator
    # needs to know what they are looking at once the job finishes, so the label and this
    # description travel with the value all the way to their completed screen.
    #   value    — a short field: a reference number, a duty amount
    #   text     — a longer block: a status message, an address
    #   document — a file the ERP produced, captured with the download action
    capture_kind: str | None = None  # "value" | "text" | "document"
    capture_description: str | None = None  # shown to the operator beside the captured item
    # Where the picked value is needed. Data is often read out of an ERP only to be typed back
    # into a later screen of the SAME ERP, in which case the operator never needs to see it.
    #   internal — reuse in a later step as [[label]]; not shown to the operator
    #   output   — shown on the operator's completed screen only
    #   both     — reusable AND shown
    capture_usage: str | None = None  # "internal" | "output" | "both"  (default: both)

    # WHERE INSIDE THE ELEMENT the Super Admin pressed, as a fraction of its width and height.
    # Replaying a click clicks the CENTRE of the element, and for a results row the centre is
    # the middle column - often a link, so a double-click meant to open the row navigated away
    # instead. Kept as a fraction so it lands on the same column when the grid is a different
    # width. Absent means the centre, which is right for a button.
    click_fx: float | None = None
    click_fy: float | None = None

    # Marks this step as the CHECKPOINT: the point the script has finished logging in and
    # navigating, and is standing on the entry screen. The flag lives on the step (not just as
    # an index) so reordering or deleting steps can never leave it pointing at the wrong one.
    is_checkpoint: bool | None = None

    # Marks this step as OPTIONAL: if its element is not on screen, skip it and carry on
    # instead of failing the run.
    #
    # ERPs are full of screens that appear only sometimes — a "your previous session was
    # cleared" warning, an "are you sure?" confirm, a notice about a new version. Recorded as
    # an ordinary click, each one breaks every run where it does NOT appear; left out, it
    # breaks every run where it does. Optional is the honest answer: dismiss it when it is
    # there, ignore it when it is not.
    is_optional: bool | None = None

    # Marks this step as the SUCCESS SIGNAL: the text or element that proves the ERP accepted
    # the entry. When it passes the job is completed; when it fails the screen is handed to AI
    # for a plain-words explanation and the entry is reported as failed.
    is_success_check: bool | None = None

    multi_mode: str | None = None  # "all_at_once" | "per_row"
    join_with: str | None = None  # all_at_once only; defaults to ", "
    row_steps: list["ErpStep"] | None = None
    end_steps: list["ErpStep"] | None = None


# row_steps/end_steps refer to ErpStep itself, so the forward reference must be resolved.
ErpStep.model_rebuild()


class ErpScriptCreate(BaseModel):
    name: str
    url: str
    has_login: bool = False
    login_username: str | None = None
    login_password: str | None = None
    template_ids: list[str] = []


class ErpScriptUpdate(BaseModel):
    # 0-based index of the step marked as the checkpoint; null clears it.
    checkpoint_index: int | None = None
    stay_open: bool | None = None
    name: str | None = None
    url: str | None = None
    has_login: bool | None = None
    login_username: str | None = None
    login_password: str | None = None
    template_ids: list[str] | None = None
    steps: list[ErpStep] | None = None
    status: str | None = None
    notes: str | None = None


class ErpScriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    checkpoint_index: int | None = None
    stay_open: bool = False

    id: str
    tenant_id: str
    name: str
    url: str
    has_login: bool
    login_username: str | None
    template_ids: list[str] | None
    steps: list[ErpStep] | None
    status: str
    notes: str | None
