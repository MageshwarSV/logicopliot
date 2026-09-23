from fastapi import APIRouter, HTTPException

from . import schemas

router = APIRouter(tags=["Super Admin / AI Prompt Editor"])

_NOT_IMPLEMENTED = "Not implemented yet — see README.md in this feature folder."


@router.put("/field-mappings/{field_mapping_id}/transformation-prompt", response_model=schemas.TransformationPromptRead)
def upsert_transformation_prompt(field_mapping_id: int, payload: schemas.TransformationPromptUpsert):
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.get("/field-mappings/{field_mapping_id}/transformation-prompt", response_model=schemas.TransformationPromptRead)
def get_transformation_prompt(field_mapping_id: int):
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)
