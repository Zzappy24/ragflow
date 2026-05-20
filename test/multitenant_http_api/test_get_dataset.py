"""Workspace-adapted run of upstream's test_get_dataset suite.

Imports upstream's test classes verbatim. Bridge mechanics in conftest.py
+ _bridge.py. Updates flow automatically at every upstream merge.
"""
from _bridge import import_upstream_tests

import_upstream_tests(
    "test/testcases/test_http_api/test_dataset_management/test_get_dataset.py",
    globals(),
)
