import base64
import ctypes
import os
from ctypes import wintypes


ENCRYPTED_VALUE_PREFIX = "dpapi:v1:"


class DataProtectionError(Exception):
    pass


class DataBlob(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        (
            "data",
            ctypes.POINTER(
                ctypes.c_ubyte
            ),
        ),
    ]


def require_windows() -> None:
    if os.name != "nt":
        raise DataProtectionError(
            "Windows data protection is unavailable."
        )


def get_windows_libraries():
    require_windows()

    crypt32 = ctypes.WinDLL(
        "crypt32",
        use_last_error=True,
    )

    kernel32 = ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    )

    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]

    crypt32.CryptProtectData.restype = (
        wintypes.BOOL
    )

    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]

    crypt32.CryptUnprotectData.restype = (
        wintypes.BOOL
    )

    kernel32.LocalFree.argtypes = [
        ctypes.c_void_p,
    ]

    kernel32.LocalFree.restype = (
        ctypes.c_void_p
    )

    return crypt32, kernel32


def create_data_blob(
    value: bytes,
) -> tuple[DataBlob, ctypes.Array]:
    buffer_size = max(1, len(value))

    buffer = ctypes.create_string_buffer(
        value,
        buffer_size,
    )

    blob = DataBlob(
        size=len(value),
        data=ctypes.cast(
            buffer,
            ctypes.POINTER(
                ctypes.c_ubyte
            ),
        ),
    )

    return blob, buffer


def protect_text(value: str) -> str:
    crypt32, kernel32 = (
        get_windows_libraries()
    )

    encoded_value = value.encode("utf-8")

    input_blob, input_buffer = (
        create_data_blob(encoded_value)
    )

    output_blob = DataBlob()

    succeeded = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Artemis private data",
        None,
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    )

    if not succeeded:
        error_code = ctypes.get_last_error()

        raise DataProtectionError(
            "Windows could not protect the value "
            f"(error {error_code})."
        )

    try:
        protected_bytes = ctypes.string_at(
            output_blob.data,
            output_blob.size,
        )
    finally:
        if output_blob.data:
            kernel32.LocalFree(
                output_blob.data
            )

    protected_token = base64.b64encode(
        protected_bytes
    ).decode("ascii")

    return (
        ENCRYPTED_VALUE_PREFIX
        + protected_token
    )


def unprotect_text(token: str) -> str:
    if not token.startswith(
        ENCRYPTED_VALUE_PREFIX
    ):
        raise DataProtectionError(
            "The value is not an Artemis "
            "encrypted value."
        )

    encoded_token = token[
        len(ENCRYPTED_VALUE_PREFIX):
    ]

    try:
        protected_bytes = base64.b64decode(
            encoded_token,
            validate=True,
        )
    except ValueError as error:
        raise DataProtectionError(
            "The encrypted value is malformed."
        ) from error

    crypt32, kernel32 = (
        get_windows_libraries()
    )

    input_blob, input_buffer = (
        create_data_blob(protected_bytes)
    )

    output_blob = DataBlob()

    succeeded = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    )

    if not succeeded:
        error_code = ctypes.get_last_error()

        raise DataProtectionError(
            "Windows could not decrypt the value "
            f"(error {error_code})."
        )

    try:
        unprotected_bytes = ctypes.string_at(
            output_blob.data,
            output_blob.size,
        )
    finally:
        if output_blob.data:
            kernel32.LocalFree(
                output_blob.data
            )

    try:
        return unprotected_bytes.decode(
            "utf-8"
        )
    except UnicodeDecodeError as error:
        raise DataProtectionError(
            "The decrypted value is invalid."
        ) from error


def is_protected_value(value: str) -> bool:
    return value.startswith(
        ENCRYPTED_VALUE_PREFIX
    )