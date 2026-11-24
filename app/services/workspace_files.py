import hashlib
import os
import tempfile
from pathlib import Path, PureWindowsPath
from threading import Lock
from uuid import uuid4

from app.config import get_settings
from app.services.local_computer import (
    LocalComputerError,
    MAX_FILE_BYTES,
    TEXT_EXTENSIONS,
    allowed_folders,
)


file_write_lock = Lock()


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def resolve_file_target(
    folder_id: str,
    relative_path: str,
) -> Path:
    root = allowed_folders().get(folder_id)

    if root is None:
        raise LocalComputerError(
            "This folder is not designated for Artemis access."
        )

    if not relative_path or "\x00" in relative_path:
        raise LocalComputerError("Enter a valid relative file path.")

    # Apply Windows path rules even when checked on another platform.
    windows_path = PureWindowsPath(relative_path)

    if windows_path.drive or windows_path.root:
        raise LocalComputerError(
            "Use a path relative to Artemis Workspace."
        )

    for part in windows_path.parts:
        if (
            part == ".."
            or any(character in part for character in '<>:"|?*')
            or part.endswith((" ", "."))
        ):
            raise LocalComputerError(
                "The file path contains an unsupported component."
            )

        stem = part.split(".", 1)[0].upper()
        reserved = {"CON", "PRN", "AUX", "NUL"}
        reserved.update(f"COM{number}" for number in range(1, 10))
        reserved.update(f"LPT{number}" for number in range(1, 10))

        if stem in reserved:
            raise LocalComputerError(
                "Windows device names cannot be used as file names."
            )

    root = root.resolve(strict=True)
    target = root.joinpath(*windows_path.parts)

    # Require an existing parent directory.
    try:
        parent = target.parent.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise LocalComputerError(
            "The parent folder does not exist."
        ) from error

    if not parent.is_relative_to(root):
        raise LocalComputerError(
            "The requested path is outside Artemis Workspace."
        )

    target = parent / target.name

    if target.is_symlink():
        raise LocalComputerError(
            "Writing through a symbolic link is not supported."
        )

    if not target.resolve().is_relative_to(root):
        raise LocalComputerError(
            "The requested path is outside Artemis Workspace."
        )

    if target.suffix.lower() not in TEXT_EXTENSIONS:
        raise LocalComputerError(
            "This file extension is not supported for text writing."
        )

    return target


def read_existing_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise LocalComputerError(
            "The requested file does not exist or is not a regular file."
        )

    with path.open("rb") as handle:
        content = handle.read(MAX_FILE_BYTES + 1)

    if len(content) > MAX_FILE_BYTES:
        raise LocalComputerError(
            f"The file exceeds the {MAX_FILE_BYTES}-byte limit."
        )

    if b"\x00" in content:
        raise LocalComputerError(
            "The file contains unsupported binary data."
        )

    try:
        content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise LocalComputerError(
            "The existing file must use UTF-8 text encoding."
        ) from error

    return content


def workspace_file_version(
    folder_id: str,
    relative_path: str,
) -> str:
    try:
        target = resolve_file_target(folder_id, relative_path)
        return content_hash(read_existing_bytes(target))
    except OSError as error:
        raise LocalComputerError(
            "The file version could not be read."
        ) from error


def save_workspace_text(
    *,
    relative_path: str,
    content: str,
    mode: str = "create",
    folder_id: str = "workspace",
    expected_sha256: str | None = None,
) -> dict:
    if mode not in {"create", "replace", "append"}:
        raise LocalComputerError(
            "File mode must be create, replace, or append."
        )

    if "\x00" in content:
        raise LocalComputerError(
            "File content cannot contain null characters."
        )

    try:
        supplied_bytes = content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise LocalComputerError(
            "The supplied content could not be encoded as UTF-8."
        ) from error

    if len(supplied_bytes) > MAX_FILE_BYTES:
        raise LocalComputerError(
            f"The supplied content exceeds {MAX_FILE_BYTES} bytes."
        )

    temporary_path = None

    try:
        with file_write_lock:
            target = resolve_file_target(folder_id, relative_path)
            previous = None
            backup_id = None

            if mode == "create":
                if target.exists():
                    raise LocalComputerError(
                        "This file already exists. Use Replace file "
                        "or Append to file explicitly."
                    )
                updated = supplied_bytes
            else:
                previous = read_existing_bytes(target)

                if expected_sha256 is None:
                    raise LocalComputerError(
                        "Editing requires the current file version."
                    )

                if content_hash(previous) != expected_sha256:
                    raise LocalComputerError(
                        "The file changed before editing. Read it again "
                        "and submit a new edit."
                    )

                updated = (
                    previous + supplied_bytes
                    if mode == "append"
                    else supplied_bytes
                )

            if len(updated) > MAX_FILE_BYTES:
                raise LocalComputerError(
                    f"The resulting file would exceed {MAX_FILE_BYTES} bytes."
                )

            # Prepare the complete new contents before changing the target.
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".artemis-write-",
                dir=target.parent,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())

            if previous is not None:
                # Backups stay outside the chat-readable workspace.
                backup_directory = (
                    get_settings().data_directory
                    / "workspace-backups"
                )
                backup_directory.mkdir(parents=True, exist_ok=True)

                backup_id = str(uuid4())
                backup_path = backup_directory / f"{backup_id}.bak"

                with backup_path.open("xb") as handle:
                    handle.write(previous)
                    handle.flush()
                    os.fsync(handle.fileno())

                # Recheck after preparing the backup.
                if read_existing_bytes(target) != previous:
                    raise LocalComputerError(
                        "The file changed while preparing the edit. "
                        "No replacement was applied."
                    )

            if mode == "create":
                # Linking the prepared file fails if the target already exists.
                # It never overwrites an existing file.
                try:
                    os.link(temporary_path, target)
                except FileExistsError as error:
                    raise LocalComputerError(
                        "The file was created by another operation. "
                        "It was not overwritten."
                    ) from error
            else:
                os.replace(temporary_path, target)
                temporary_path = None

            return {
                "folder_id": folder_id,
                "relative_path": target.relative_to(
                    allowed_folders()[folder_id].resolve()
                ).as_posix(),
                "mode": mode,
                "size_bytes": len(updated),
                "sha256": content_hash(updated),
                "backup_id": backup_id,
            }

    except OSError as error:
        raise LocalComputerError(
            f"The file operation failed: {error.strerror or type(error).__name__}"
        ) from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass