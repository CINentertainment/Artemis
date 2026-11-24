from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.database import get_database
from app.routes.approvals import build_approval_response
from app.schemas import ApprovalResponse
from app.services.approvals import ApprovalContentError
from app.services.local_computer import (
    LocalComputerError,
    computer_capabilities,
    execute_application_open,
    list_local_files,
    read_local_text,
    request_application_open,
)


router = APIRouter(
    prefix="/computer",
    tags=["local computer"],
)


class OpenApplicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str


@router.get("/capabilities")
def read_capabilities() -> dict:
    return computer_capabilities()


@router.get("/files")
def list_files(
    folder_id: str = Query(
        default="workspace",
        min_length=1,
        max_length=50,
    ),
    relative_path: str = Query(
        default="",
        max_length=1000,
    ),
) -> dict:
    try:
        return list_local_files(
            folder_id=folder_id,
            relative_path=relative_path,
        )
    except LocalComputerError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error


@router.get("/files/text")
def read_text_file(
    relative_path: str = Query(
        min_length=1,
        max_length=1000,
    ),
    folder_id: str = Query(
        default="workspace",
        min_length=1,
        max_length=50,
    ),
) -> dict:
    try:
        return read_local_text(
            folder_id=folder_id,
            relative_path=relative_path,
        )
    except LocalComputerError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error


@router.post(
    "/applications/requests",
    response_model=ApprovalResponse,
    status_code=201,
)
def request_open_application(
    request: OpenApplicationRequest,
    database: Session = Depends(get_database),
) -> ApprovalResponse:
    try:
        approval = request_application_open(
            database,
            application_id=request.application_id,
        )
    except LocalComputerError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

    return build_approval_response(approval)


@router.post("/applications/requests/{approval_id}/execute")
def execute_open_application(
    approval_id: UUID,
    database: Session = Depends(get_database),
) -> dict:
    try:
        return execute_application_open(
            database,
            approval_id=approval_id,
        )
    except LocalComputerError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error
    except ApprovalContentError as error:
        raise HTTPException(
            status_code=500,
            detail="The approved action could not be decrypted.",
        ) from error