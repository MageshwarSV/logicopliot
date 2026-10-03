"""Imported by Alembic's env.py so autogenerate can see every model via Base.metadata."""

from app.db.base_class import Base  # noqa: F401
from app.models.tenant import Tenant  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.user_type import UserType  # noqa: F401
from app.models.refresh_token import RefreshToken  # noqa: F401
from app.models.template_group import TemplateGroup  # noqa: F401
from app.models.template_document import TemplateDocument  # noqa: F401
from app.models.field_mark import FieldMark  # noqa: F401
from app.models.cross_doc_link import CrossDocLink  # noqa: F401
from app.models.job import Job, JobDocument, JobFieldValue  # noqa: F401
from app.models.template_review import TemplateReview  # noqa: F401
from app.models.user_template import UserTemplateAssignment  # noqa: F401
from app.models.erp_access import ErpAccessRequest  # noqa: F401
from app.models.erp_script import ErpScript  # noqa: F401
from app.models.custom_field import CustomField  # noqa: F401
from app.models.custom_field_reference import CustomFieldReferenceValue  # noqa: F401
from app.models.composite_consignee_default import CompositeFieldConsigneeDefault  # noqa: F401
from app.models.supporting_document import SupportingDocument  # noqa: F401
from app.models.job_irn_signature import JobIrnSignature  # noqa: F401
from app.models.job_failure import JobFailureReport  # noqa: F401
from app.models.email_seen import EmailSeen  # noqa: F401
from app.models.job_event import JobEvent  # noqa: F401
from app.models.pending_email import PendingEmail  # noqa: F401
from app.models.system_setting import SystemSetting  # noqa: F401
from app.models.custom_filter_page import CustomFilterPage  # noqa: F401

# Importing this installs the before_flush listener that writes a job's history. It
# lives here so every entry point that touches the database gets it - the API, the
# email poller thread and the background ERP runner alike.
import app.db.job_history  # noqa: F401,E402
