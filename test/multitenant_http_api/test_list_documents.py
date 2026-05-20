"""Workspace-adapted run of upstream's test_list_documents suite."""
from _bridge import import_upstream_tests

import_upstream_tests(
    "test/testcases/test_http_api/test_file_management_within_dataset/test_list_documents.py",
    globals(),
)
