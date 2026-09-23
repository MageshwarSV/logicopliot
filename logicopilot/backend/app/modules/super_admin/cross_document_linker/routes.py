from fastapi import APIRouter, HTTPException, status

from . import schemas

router = APIRouter(tags=["Super Admin / Cross-Document Linker"])

_NOT_IMPLEMENTED = "Not implemented yet — see README.md in this feature folder."


@router.post("/verification-links", response_model=schemas.VerificationLinkRead, status_code=status.HTTP_201_CREATED)
def create_verification_link(payload: schemas.VerificationLinkCreate):
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.get("/tenants/{tenant_id}/verification-links", response_model=list[schemas.VerificationLinkRead])
def list_verification_links(tenant_id: int):
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.delete("/verification-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_verification_link(link_id: int):
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)
