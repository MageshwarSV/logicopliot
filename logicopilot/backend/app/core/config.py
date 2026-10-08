from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./logicopilot.db"

    jwt_secret_key: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    cors_origins: str = "http://localhost:30300"
    cookie_secure: bool = False

    # Document-extraction stack (onboarding wizard: OCR + prompt generation).
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    # The model production document classification/extraction use when a Super Admin flips
    # the "extraction_engine" Settings toggle to "gpt5_mini_vision" (see
    # app/models/system_setting.py) - kept as its own env-overridable setting, not shared
    # with openai_model above, so it can be pointed at a different model without a code
    # change if this one ever needs more plumbing than expected. Never read by the Template
    # Wizard's own training flow, which always uses openai_model.
    vision_engine_model: str = "gpt-5-mini"
    docai_project_id: str = ""
    docai_location: str = "us"
    docai_processor_id: str = ""
    google_application_credentials: str = "google-credentials.json"
    uploads_dir: str = "uploads"
    # Verified live against real jobs (Cisco commercial invoice Seller/Ship-From splice, and
    # JOB-92F564's Bill of Lading Shipper-vs-carrier-letterhead splice) before enabling: 22/22
    # fields correct afterward vs 19/22 before, zero regressions found. On: run_extraction and
    # the mark/custom-field recompute paths (app/api/v1/jobs.py) prefer docai.py's
    # structured_text over layout_text for a page where the structure engine
    # (app/core/structure_engine.py) found a genuine multi-column region to fix - falling back
    # to layout_text untouched for every other page. See structure_engine.py's own docstring
    # for what this does and why.
    structure_engine_enabled: bool = True

    # Email auto-pull (IMAP). Documents arrive as attachments; matched to a customer
    # by the sender/recipient address configured per template as `pull_email`.
    gmail_user: str = ""
    gmail_app_password: str = ""
    imap_host: str = "imap.gmail.com"
    # Minutes between automatic inbox polls. 0 disables the background poller
    # (documents are then only pulled when someone clicks "Pull email now").
    email_poll_minutes: int = 0
    # How many mailboxes are read AT ONCE is a live, database-backed Super Admin setting now
    # (see app.core.system_settings.get_email_poll_workers / SettingsPage), not an env value -
    # changing it should not need a restart. See that module for why: reading a message is
    # genuinely slow (OCR every page, then AI calls per document), so one mailbox with a busy
    # morning used to make every OTHER connected mailbox wait behind it in a sequential loop.

    # How much of the application's own logging reaches the process output. INFO is the useful
    # default: it is what carries "the analyser chose this slot for that file" and the rest of
    # the per-step detail that makes a bad outcome diagnosable. Set WARNING for a quiet log.
    log_level: str = "INFO"

    # Show a real Chrome window for the ERP recorder + entry playback (headed).
    # Requires a desktop session on the machine running the backend. Set false for
    # servers/headless environments.
    browser_headless: bool = True
    # Hard cap on browsers held open at a script's checkpoint ("stay open"). Each one holds
    # roughly 300 MB for as long as it is parked, so this must never be raised blindly:
    # check free memory on the host first. 0 disables parking altogether.
    max_parked_sessions: int = 1

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
