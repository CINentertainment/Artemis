from hashlib import sha256
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ForeignKey, String, Text, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.database import Base, get_database
from app.models import Conversation
from app.services.encryption import (
    DataProtectionError,
    protect_text,
    unprotect_text,
)


router = APIRouter(
    prefix="/conversation-folders",
    tags=["conversation folders"],
)


class ConversationFolder(Base):
    __tablename__ = "conversation_folders"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )


class ConversationFolderMembership(Base):
    __tablename__ = "conversation_folder_memberships"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    folder_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_folders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class FolderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)


class AssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Null explicitly means move back to Unfiled.
    folder_id: UUID | None


def commit(database: Session) -> None:
    try:
        database.commit()
    except IntegrityError:
        database.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "That folder name already exists, or a referenced "
                "conversation or folder changed. Refresh the archive."
            ),
        ) from None
    except Exception:
        database.rollback()
        raise


def clean_name(value: str) -> str:
    name = " ".join(value.split())

    if not name:
        raise HTTPException(
            status_code=422,
            detail="Enter a folder name.",
        )

    if name.casefold() == "unfiled":
        raise HTTPException(
            status_code=422,
            detail="Unfiled is reserved for conversations without a folder.",
        )

    return name


def name_hash(name: str) -> str:
    return sha256(name.casefold().encode("utf-8")).hexdigest()


def folder_data(folder: ConversationFolder) -> dict:
    try:
        name = unprotect_text(folder.name)
    except DataProtectionError:
        raise HTTPException(
            status_code=500,
            detail="A folder name could not be decrypted.",
        ) from None

    return {"id": folder.id, "name": name}


def get_folder(
    database: Session,
    folder_id: UUID,
) -> ConversationFolder:
    folder = database.get(ConversationFolder, str(folder_id))

    if folder is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation folder not found.",
        )

    return folder


@router.get("")
def read_archive_folders(
    database: Session = Depends(get_database),
) -> dict:
    folders = [
        folder_data(folder)
        for folder in database.scalars(
            select(ConversationFolder)
        ).all()
    ]
    folders.sort(key=lambda folder: folder["name"].casefold())

    memberships = database.scalars(
        select(ConversationFolderMembership)
        .join(
            Conversation,
            Conversation.id
            == ConversationFolderMembership.conversation_id,
        )
        .join(
            ConversationFolder,
            ConversationFolder.id
            == ConversationFolderMembership.folder_id,
        )
    ).all()

    return {
        "folders": folders,
        "memberships": {
            membership.conversation_id: membership.folder_id
            for membership in memberships
        },
    }


@router.post("", status_code=201)
def create_folder(
    request: FolderRequest,
    database: Session = Depends(get_database),
) -> dict:
    name = clean_name(request.name)

    folder = ConversationFolder(
        name=protect_text(name),
        name_hash=name_hash(name),
    )
    database.add(folder)
    commit(database)
    database.refresh(folder)

    return folder_data(folder)


@router.patch("/{folder_id}")
def rename_folder(
    folder_id: UUID,
    request: FolderRequest,
    database: Session = Depends(get_database),
) -> dict:
    folder = get_folder(database, folder_id)
    name = clean_name(request.name)

    folder.name = protect_text(name)
    folder.name_hash = name_hash(name)
    commit(database)

    return folder_data(folder)


@router.delete("/{folder_id}")
def remove_folder(
    folder_id: UUID,
    database: Session = Depends(get_database),
) -> dict:
    folder = get_folder(database, folder_id)

    database.execute(
        delete(ConversationFolderMembership).where(
            ConversationFolderMembership.folder_id == folder.id
        )
    )
    database.delete(folder)
    commit(database)

    return {
        "deleted": True,
        "conversations_preserved": True,
    }


@router.put("/assign/{conversation_id}")
def assign_conversation(
    conversation_id: UUID,
    request: AssignmentRequest,
    database: Session = Depends(get_database),
) -> dict:
    conversation = database.get(Conversation, str(conversation_id))

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    folder = (
        get_folder(database, request.folder_id)
        if request.folder_id is not None
        else None
    )

    membership = database.get(
        ConversationFolderMembership,
        conversation.id,
    )

    if folder is None:
        if membership is not None:
            database.delete(membership)
    elif membership is None:
        database.add(ConversationFolderMembership(
            conversation_id=conversation.id,
            folder_id=folder.id,
        ))
    else:
        membership.folder_id = folder.id

    commit(database)

    return {
        "conversation_id": conversation.id,
        "folder_id": folder.id if folder is not None else None,
    }