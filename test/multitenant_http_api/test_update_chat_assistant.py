"""Workspace-adapted run of upstream's test_update_chat_assistant suite."""
from _bridge import import_upstream_tests

import_upstream_tests(
    "test/testcases/test_http_api/test_chat_assistant_management/test_update_chat_assistant.py",
    globals(),
)
