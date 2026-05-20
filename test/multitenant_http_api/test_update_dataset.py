"""Workspace-adapted run of upstream's test_update_dataset suite."""
from _bridge import import_upstream_tests

import_upstream_tests(
    "test/testcases/test_http_api/test_dataset_management/test_update_dataset.py",
    globals(),
)
