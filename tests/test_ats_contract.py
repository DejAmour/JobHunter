"""The contract every filler implements, and the guarantee `run` stays generic.

Before this, `run` contained two `if kind == "workday"` branches and a 16-argument
signature, and adding an ATS meant copying six loops. These tests are the guard
against that coming back: a filler is a subclass, `run` dispatches once, and no
ATS-specific knowledge leaks into it.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from jobhunter import fill


def test_every_registered_filler_is_an_ats():
    for name, cls in fill.FILLERS.items():
        assert issubclass(cls, fill.Ats), name
        assert cls().name == name


def test_a_single_page_filler_is_driven_through_fill_form():
    class Recorder(fill.Ats):
        name = "recorder"

        def __init__(self) -> None:
            self.seen = None

        def fill_form(self, page, values, ctx, report) -> None:
            self.seen = ctx
            report.filled.append("x")

    recorder = Recorder()
    report = recorder.fill(SimpleNamespace(url="https://example.test"), fill.Values(), "ctx")
    assert recorder.seen == "ctx"
    assert report.filled == ["x"]
    assert report.ats == "recorder"


def test_the_base_class_refuses_to_run_an_unimplemented_filler():
    with pytest.raises(NotImplementedError):
        fill.Ats().fill_form(None, None, None, None)


def test_workday_is_a_wizard_and_says_so():
    assert "fill" in fill.Workday.__dict__, "Workday owns the loop, so it overrides fill"
    assert fill.Workday.needs_account is True
    parameters = inspect.signature(fill.Workday.fill).parameters
    assert "login" in parameters
    assert "email" not in parameters and "password" not in parameters


def test_run_knows_nothing_about_any_particular_ats():
    # Comments are allowed to mention Workday; code is not.
    code = "\n".join(
        line
        for line in inspect.getsource(fill.run).splitlines()
        if not line.strip().startswith("#")
    )
    assert '"workday"' not in code
    assert "'workday'" not in code
    assert "Workday()" not in code
    assert "Greenhouse()" not in code
    assert "greenhouse" not in code


def test_login_carries_everything_a_wall_can_need():
    login = fill.Login(email="a@b.test", password="x", create=True, on_code=lambda: "123")
    assert login.email == "a@b.test"
    assert login.create is True
    assert callable(login.on_code)
