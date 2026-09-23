from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.tenants import router as tenants_router
from app.api.v1.users import router as users_router
from app.api.v1.user_types import router as user_types_router
from app.api.v1.template_groups import router as template_groups_router
from app.api.v1.template_documents import router as template_documents_router
from app.api.v1.field_marks import router as field_marks_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.reviews import router as reviews_router
from app.api.v1.erp_scripts import router as erp_scripts_router
from app.api.v1.email import router as email_router
from app.api.v1.entry_browser import router as entry_browser_router
from app.api.v1.pending_mail import router as pending_mail_router
from app.api.v1.system_settings import router as system_settings_router
from app.api.v1.custom_filter_pages import router as custom_filter_pages_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)
api_router.include_router(tenants_router)
api_router.include_router(users_router)
api_router.include_router(user_types_router)
api_router.include_router(template_groups_router)
api_router.include_router(template_documents_router)
api_router.include_router(field_marks_router)
api_router.include_router(jobs_router)
api_router.include_router(reviews_router)
api_router.include_router(erp_scripts_router)
api_router.include_router(email_router)
api_router.include_router(entry_browser_router)
api_router.include_router(pending_mail_router)
api_router.include_router(system_settings_router)
api_router.include_router(custom_filter_pages_router)
