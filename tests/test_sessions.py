"""Locked is not the same failure as wrong, and the difference costs an account.

Workday locks an account after repeated failed sign-ins. A tool that treats
"locked" as "try the other door" turns a five-minute reset into a support ticket,
so the distinction is tested rather than assumed.
"""

from __future__ import annotations

from jobhunter import sessions


def test_a_locked_page_is_recognised():
    assert sessions.locked_text("Your account is locked. Contact support.")
    assert sessions.locked_text("This account has been temporarily locked.")
    assert sessions.locked_text("ACCOUNT LOCKED")


def test_a_wrong_password_is_not_a_lock():
    assert not sessions.locked_text("The username or password you entered is incorrect.")
    assert not sessions.locked_text("")


def test_the_reset_hint_names_the_recovery_command():
    """The console message is the only thing standing between a user and a retry loop."""
    import inspect

    source = inspect.getsource(sessions.login_workday)
    assert "account recover" in source
    assert "not retrying" in source
