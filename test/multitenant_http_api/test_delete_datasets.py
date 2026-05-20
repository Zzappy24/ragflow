"""Workspace-adapted run of upstream's test_delete_datasets suite."""
from _bridge import import_upstream_tests

import_upstream_tests(
    "test/testcases/test_http_api/test_dataset_management/test_delete_datasets.py",
    globals(),
)
