"""Workspace-adapted run of upstream's test_download_document suite."""
from _bridge import import_upstream_tests

import_upstream_tests(
    "test/testcases/test_http_api/test_file_management_within_dataset/test_download_document.py",
    globals(),
)
