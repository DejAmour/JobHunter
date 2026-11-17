"""Passwords, kept where Windows keeps them.

A Workday application needs an account, and an account needs a password. The
password cannot live in ``config.toml``: that file is committed, backed up, and
read by anyone who ever gets a copy of this machine's folder. It cannot live in a
session file either — cookies expire, and the next application needs a fresh
sign-in.

So the secret goes to the Windows Credential Manager, under
``jobhunter/<scope>/<slug>``, where it is encrypted by the OS against the logged-in
user. This module is the only thing that ever reads it back, and it is read at the
moment of typing, into memory, and dropped when the process ends.

Two backends: the native Windows one (ctypes, no dependency), and ``keyring`` as a
fallback on other platforms. If neither is available, storing an account fails
loudly rather than silently writing a password to disk in plain text.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

PREFIX = "jobhunter"

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


class CredentialError(RuntimeError):
    """The platform store refused, or is not available."""


@dataclass(frozen=True)
class Secret:
    username: str
    password: str


def _prefix() -> str:
    """The namespace inside the store.

    Tests set JOBHUNTER_CREDENTIALS=jobhunter-test so they can add and remove
    employers without touching the ones in use. They did touch them once: a test
    that registered "M&G" and then removed it deleted the real M&G password.
    """
    return os.environ.get("JOBHUNTER_CREDENTIALS", PREFIX)


def target(scope: str, slug: str) -> str:
    return f"{_prefix()}/{scope}/{slug}"


# --- Windows Credential Manager ------------------------------------------


def _win():
    import ctypes
    import ctypes.wintypes as wt

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]

    class CREDENTIAL_ATTRIBUTEW(ctypes.Structure):
        _fields_ = [
            ("Keyword", wt.LPWSTR),
            ("Flags", wt.DWORD),
            ("ValueSize", wt.DWORD),
            ("Value", ctypes.POINTER(wt.BYTE)),
        ]

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wt.DWORD),
            ("Type", wt.DWORD),
            ("TargetName", wt.LPWSTR),
            ("Comment", wt.LPWSTR),
            ("LastWritten", FILETIME),
            ("CredentialBlobSize", wt.DWORD),
            ("CredentialBlob", ctypes.POINTER(wt.BYTE)),
            ("Persist", wt.DWORD),
            ("AttributeCount", wt.DWORD),
            ("Attributes", ctypes.POINTER(CREDENTIAL_ATTRIBUTEW)),
            ("TargetAlias", wt.LPWSTR),
            ("UserName", wt.LPWSTR),
        ]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wt.DWORD]
    advapi.CredWriteW.restype = wt.BOOL
    advapi.CredReadW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
    advapi.CredReadW.restype = wt.BOOL
    advapi.CredDeleteW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD]
    advapi.CredDeleteW.restype = wt.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    advapi.CredFree.restype = None
    return ctypes, wt, advapi, CREDENTIALW


def _win_write(key: str, secret: Secret) -> None:
    ctypes, wt, advapi, CREDENTIALW = _win()
    blob = secret.password.encode("utf-16-le")
    if len(blob) > 2560:
        raise CredentialError("password too long for the Credential Manager")
    buffer = ctypes.create_string_buffer(blob, len(blob))
    credential = CREDENTIALW(
        Flags=0,
        Type=CRED_TYPE_GENERIC,
        TargetName=key,
        Comment="jobhunter: employer account",
        CredentialBlobSize=len(blob),
        CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(wt.BYTE)),
        Persist=CRED_PERSIST_LOCAL_MACHINE,
        UserName=secret.username,
    )
    if not advapi.CredWriteW(ctypes.byref(credential), 0):
        raise CredentialError(f"CredWrite failed: {ctypes.get_last_error()}")


def _win_read(key: str) -> Secret | None:
    ctypes, wt, advapi, CREDENTIALW = _win()
    pointer = ctypes.POINTER(CREDENTIALW)()
    if not advapi.CredReadW(key, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        if ctypes.get_last_error() == ERROR_NOT_FOUND:
            return None
        raise CredentialError(f"CredRead failed: {ctypes.get_last_error()}")
    try:
        credential = pointer.contents
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        # Generic credentials written by this module are UTF-16LE; ones written
        # by cmdkey or another tool may not be, so fall back rather than crash.
        try:
            password = raw.decode("utf-16-le")
        except UnicodeDecodeError:
            password = raw.decode("utf-8", "replace")
        return Secret(username=credential.UserName or "", password=password)
    finally:
        advapi.CredFree(pointer)


def _win_delete(key: str) -> bool:
    ctypes, wt, advapi, _ = _win()
    if not advapi.CredDeleteW(key, CRED_TYPE_GENERIC, 0):
        if ctypes.get_last_error() == ERROR_NOT_FOUND:
            return False
        raise CredentialError(f"CredDelete failed: {ctypes.get_last_error()}")
    return True


# --- keyring fallback -----------------------------------------------------


def _keyring():
    try:
        import keyring  # noqa: PLC0415

        return keyring
    except ImportError:
        return None


def _keyring_write(key: str, secret: Secret) -> None:
    ring = _keyring()
    assert ring is not None
    ring.set_password(key, secret.username or "account", secret.password)


def _keyring_read(key: str) -> Secret | None:
    ring = _keyring()
    if ring is None:
        return None
    password = ring.get_password(key, "account")
    if password is None:
        return None
    return Secret(username="account", password=password)


# --- public API ----------------------------------------------------------


def backend() -> str:
    """Which store is in use: 'windows', 'keyring', or '' if none."""
    if sys.platform == "win32":
        try:
            _win()
            return "windows"
        except Exception:  # noqa: BLE001
            pass
    if _keyring() is not None:
        return "keyring"
    return ""


def available() -> bool:
    return backend() != ""


def store(scope: str, slug: str, secret: Secret) -> None:
    key = target(scope, slug)
    which = backend()
    if which == "windows":
        _win_write(key, secret)
    elif which == "keyring":
        _keyring_write(key, secret)
    else:
        raise CredentialError(
            "no credential store available: install 'keyring' or run on Windows"
        )


def load(scope: str, slug: str) -> Secret | None:
    key = target(scope, slug)
    which = backend()
    if which == "windows":
        return _win_read(key)
    if which == "keyring":
        return _keyring_read(key)
    return None


def forget(scope: str, slug: str) -> bool:
    key = target(scope, slug)
    which = backend()
    if which == "windows":
        return _win_delete(key)
    if which == "keyring" and _keyring() is not None:
        try:
            _keyring().delete_password(key, "account")
            return True
        except Exception:  # noqa: BLE001
            return False
    return False
