"""Workspace-adapted run of upstream's test_delete_chunks suite."""
from _bridge import import_upstream_tests

import_upstream_tests(
    "test/testcases/test_http_api/test_chunk_management_within_dataset/test_delete_chunks.py",
    globals(),
)
