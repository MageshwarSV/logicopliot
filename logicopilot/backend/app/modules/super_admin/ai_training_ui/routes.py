from fastapi import APIRouter, HTTPException, status

from . import schemas

router = APIRouter(tags=["Super Admin / AI Training UI"])

_NOT_IMPLEMENTED = "Not implemented yet — see README.md in this feature folder."


@router.post("/field-mappings/{field_mapping_id}/train", response_model=schemas.TrainingCorrectionRead, status_code=status.HTTP_201_CREATED)
def submit_training_correction(field_mapping_id: int, payload: schemas.TrainingCorrectionCreate):
    """Stores a manual correction as a few-shot example for future extraction runs."""
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.get("/field-mappings/{field_mapping_id}/training-history", response_model=list[schemas.TrainingCorrectionRead])
def get_training_history(field_mapping_id: int):
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)
