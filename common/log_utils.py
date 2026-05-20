#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import os
import os.path
import logging
import re
from logging.handlers import RotatingFileHandler
from common.file_utils import get_project_base_directory

initialized_root_logger = False
pkg_levels = {}  # module-level to allow runtime modification


# ---------------------------------------------------------------------------
# Secret masking
#
# Authenticated requests routinely produce log lines that include the bearer
# token (debug messages, exceptions, third-party libs that dump headers, ...).
# In a multi-tenant SaaS, leaking those tokens to logs is both a credential
# disclosure (an ops engineer reading logs gets a working API key) and a
# GDPR/RGPD reportable event. We attach a logging.Filter on the root logger
# that rewrites the record before it reaches any handler.
#
# Patterns are intentionally conservative — false positives are cheaper than
# false negatives. If new secret formats appear, add them here.
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    # RAGFlow workspace API keys: "ragflow-" + base64url-ish payload.
    (re.compile(r"ragflow-[A-Za-z0-9_\-]{16,}"), "ragflow-***"),
    # JWT — three base64url segments separated by dots, leading "ey".
    (re.compile(r"\bey[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "ey***.***.***"),
    # OpenAI-style secret keys (and other "sk-..." conventions).
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "sk-***"),
    # Anthropic/Cerebras keys (sk-ant-... / csk-...).
    (re.compile(r"\bcsk-[A-Za-z0-9_\-]{16,}"), "csk-***"),
    # Google service-account / OAuth refresh tokens (1//... pattern).
    (re.compile(r"\b1//[A-Za-z0-9_\-]{20,}"), "1//***"),
    # Generic key/token form fields and JSON values.
    (
        re.compile(
            r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)"
            r"\s*[=:]\s*[\"']?([A-Za-z0-9_\-\.]{8,})[\"']?"
        ),
        r"\1=***",
    ),
    # Authorization / Bearer headers (case-insensitive, value masked).
    (
        re.compile(r"(?i)\b(authorization|bearer)\b[\s:]+[A-Za-z0-9_\-\.]{16,}"),
        r"\1 ***",
    ),
]


def _mask_secrets(text: str) -> str:
    """Apply every ``_SECRET_PATTERNS`` substitution to ``text``."""
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class SecretMaskingFilter(logging.Filter):
    """Rewrite log records so credentials never reach the handlers.

    Both ``record.msg`` and any string positional ``record.args`` are masked
    before formatting. We avoid eager formatting (would defeat lazy
    %-formatting in production code paths) by leaving non-string args alone.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _mask_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: _mask_secrets(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    _mask_secrets(a) if isinstance(a, str) else a for a in record.args
                )
        return True

def init_root_logger(logfile_basename: str, log_format: str = "%(asctime)-15s %(levelname)-8s %(process)d %(message)s"):
    global initialized_root_logger, pkg_levels
    if initialized_root_logger:
        return
    initialized_root_logger = True

    logger = logging.getLogger()
    logger.handlers.clear()
    log_path = os.path.abspath(os.path.join(get_project_base_directory(), "logs", f"{logfile_basename}.log"))

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    formatter = logging.Formatter(log_format)

    secret_filter = SecretMaskingFilter()

    handler1 = RotatingFileHandler(log_path, maxBytes=10*1024*1024, backupCount=5)
    handler1.setFormatter(formatter)
    handler1.addFilter(secret_filter)
    logger.addHandler(handler1)

    handler2 = logging.StreamHandler()
    handler2.setFormatter(formatter)
    handler2.addFilter(secret_filter)
    logger.addHandler(handler2)

    # Belt-and-suspenders: also attach to the root logger itself so any
    # handler added after init (third-party libs, debug code) still gets
    # the masking applied.
    logger.addFilter(secret_filter)

    logging.captureWarnings(True)

    LOG_LEVELS = os.environ.get("LOG_LEVELS", "")
    for pkg_name_level in LOG_LEVELS.split(","):
        terms = pkg_name_level.split("=")
        if len(terms)!= 2:
            continue
        pkg_name, pkg_level = terms[0], terms[1]
        pkg_name = pkg_name.strip()
        pkg_level = logging.getLevelName(pkg_level.strip().upper())
        if not isinstance(pkg_level, int):
            pkg_level = logging.INFO
        pkg_levels[pkg_name] = logging.getLevelName(pkg_level)

    for pkg_name in ['peewee', 'pdfminer']:
        if pkg_name not in pkg_levels:
            pkg_levels[pkg_name] = logging.getLevelName(logging.WARNING)
    if 'root' not in pkg_levels:
        pkg_levels['root'] = logging.getLevelName(logging.INFO)

    for pkg_name, pkg_level in pkg_levels.items():
        pkg_logger = logging.getLogger(pkg_name)
        pkg_logger.setLevel(pkg_level)

    msg = f"{logfile_basename} log path: {log_path}, log levels: {pkg_levels}"
    logger.info(msg)


def set_log_level(pkg_name: str, level: str) -> bool:
    """Set log level for a package at runtime. Returns True if successful."""
    global pkg_levels
    level_value = logging.getLevelName(level.strip().upper())
    if not isinstance(level_value, int):
        return False
    pkg_levels[pkg_name] = logging.getLevelName(level_value)
    pkg_logger = logging.getLogger(pkg_name)
    pkg_logger.setLevel(level_value)
    return True


def get_log_levels() -> dict:
    """Get current log levels for all packages."""
    global pkg_levels
    return dict(pkg_levels)


def log_exception(e, *args):
    logging.exception(e)
    for a in args:
        try:
            text = getattr(a, "text")
        except Exception:
            text = None
        if text is not None:
            logging.error(text)
            raise Exception(text)
        logging.error(str(a))
    raise e
