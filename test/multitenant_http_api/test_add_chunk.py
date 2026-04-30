"""Workspace-adapted run of upstream's test_add_chunk suite."""
from _bridge import import_upstream_tests

import_upstream_tests(
    "test/testcases/test_http_api/test_chunk_management_within_dataset/test_add_chunk.py",
    globals(),
)
