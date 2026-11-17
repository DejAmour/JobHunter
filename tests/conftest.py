"""Never let a test touch the real credential store or the real home directory.

Two incidents made this file necessary:

- The Workday tests register employers and remove them again, and removal
  deletes the stored password. One of them used the name "M&G", which is a real
  employer with a real password, and a test run wiped it. The credential store
  is namespaced by an environment variable so that cannot happen again.

- `choices.json` records what a form chose, keyed by tenant. A test that runs
  against a fixture page must never write to the real one, or a later live run
  inherits a decision made by a test.
"""

import os
import tempfile
from pathlib import Path

os.environ["JOBHUNTER_CREDENTIALS"] = "jobhunter-test"
os.environ.setdefault("JOBHUNTER_HOME", str(Path(tempfile.mkdtemp(prefix="jobhunter-test-home-"))))
