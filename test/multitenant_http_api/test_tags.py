"""Workspace-adapted run of upstream's test_tags suite (datasets/<id>/tags)."""
from _bridge import import_upstream_tests

import_upstream_tests(
    "test/testcases/test_http_api/test_dataset_management/test_tags.py",
    globals(),
)
