"""Never let a test touch the real credential store.

The Workday tests register employers and remove them again, and removal deletes
the stored password. One of them used the name "M&G", which is a real employer
with a real password, and a test run wiped it. The store is namespaced by an
environment variable so that cannot happen again — set here, for every test,
unconditionally.
"""

import os

os.environ["JOBHUNTER_CREDENTIALS"] = "jobhunter-test"
