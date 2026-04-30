"""Workspace-adapted run of upstream's test_create_session_with_chat_assistant suite."""
from _bridge import import_upstream_tests

import_upstream_tests(
    "test/testcases/test_http_api/test_session_management/test_create_session_with_chat_assistant.py",
    globals(),
)
