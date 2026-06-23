"""
Static test — verify that every external Python import loaded at boot by
the management backend has its pip dep declared in Dockerfile.management.

The slim mgmt image (ragflow-mgmt) installs a hand-curated subset of the
project's deps. When upstream adds a new top-level import in common/ or
api/db/, the mgmt pod crashes at boot with `ModuleNotFoundError` because
the dep isn't in the slim image — even though it's in the fat ragflow
image. This test catches that drift BEFORE the bad image is pushed.

Run:
    uv run pytest test/multitenant/test_mgmt_image_deps.py -v

If it fails, the assertion message tells you:
  - Which file added the new import (with line number)
  - Which pip package to add to Dockerfile.management
  - The reason it matters (boot chain)
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile.management"


# Files in the management backend's BOOT CHAIN (loaded at module import time
# when uvicorn imports management.server.main). Anything outside this list
# can still be in the image but doesn't need its deps installed (the slim
# image accepts ImportError on rarely-used paths — e.g. rag.utils.tavily_conn
# would fail because we don't ship `tavily`, but it's never imported at boot).
#
# Glob patterns relative to repo root. Add a new entry when adding code that
# runs at module-load time inside the mgmt process.
BOOT_GLOBS = [
    "management/server/main.py",
    "management/server/*.py",
    "management/server/auth/*.py",
    "management/server/models/*.py",
    "management/server/routers/*.py",
    "management/server/services/*.py",
    "management/server/config.py",
    # common/ — settings.py is imported by main.py:lifespan and eagerly
    # loads most of its siblings + every rag/utils/*_conn module.
    "common/settings.py",
    "common/__init__.py",
    "common/config_utils.py",
    "common/constants.py",
    "common/decorator.py",
    "common/file_utils.py",
    "common/float_utils.py",
    "common/misc_utils.py",
    "common/time_utils.py",
    "common/crypto_utils.py",
    "common/encryption_utils.py",
    "common/text_utils.py",
    "common/metadata_utils.py",
    "common/metadata_infinity_filter.py",
    "common/network_utils.py",
    "common/tag_feature_utils.py",
    "common/token_utils.py",
    # NOTE: common/data_source/* (sharepoint/dropbox/slack/jira/gitlab/
    # google_drive/onedrive/airtable/notion/asana/zendesk/...) are LAZY —
    # imported only when the admin actually configures that data source.
    # They are NOT in the boot chain. Leaving them out of BOOT_GLOBS so
    # the test doesn't flag their SDK deps as missing.
    "common/doc_store/*.py",
    # rag/utils/ — only the _conn.py files eagerly imported by
    # common/settings.py. lazy_image / tavily_conn / file_utils are
    # intentionally excluded: their imports (PIL / tavily / pypdf) aren't
    # in the slim image, and these modules are never loaded at boot.
    "rag/utils/__init__.py",
    "rag/utils/encrypted_storage.py",
    "rag/utils/storage_factory.py",
    "rag/utils/table_es_metadata.py",
    "rag/utils/raptor_utils.py",
    "rag/utils/tts_cache.py",
    "rag/utils/es_conn.py",
    "rag/utils/infinity_conn.py",
    "rag/utils/ob_conn.py",
    "rag/utils/opensearch_conn.py",
    "rag/utils/minio_conn.py",
    "rag/utils/redis_conn.py",
    "rag/utils/s3_conn.py",
    "rag/utils/oss_conn.py",
    "rag/utils/azure_sas_conn.py",
    "rag/utils/azure_spn_conn.py",
    "rag/utils/gcs_conn.py",
    "rag/utils/opendal_conn.py",
    # api/db/ — db_models is imported by management.server.main:lifespan
    # via init_database_tables. services/__init__.py auto-loads user_service.
    "api/__init__.py",
    "api/constants.py",
    "api/settings.py",
    "api/db/__init__.py",
    "api/db/db_models.py",
    "api/db/services/__init__.py",
    "api/db/services/common_service.py",
    "api/db/services/user_service.py",
    "api/db/services/tenant_llm_service.py",
    "api/db/services/tenant_model_provider_service.py",
    "api/db/services/tenant_model_instance_service.py",
    "api/db/services/tenant_model_service.py",
    "api/db/services/workspace_service.py",
    "api/db/services/org_service.py",
    "api/db/services/langfuse_service.py",
    "api/db/joint_services/tenant_model_service.py",
    "api/utils/__init__.py",
    "api/utils/crypt.py",
    "api/utils/tenant_utils.py",
    "api/common/__init__.py",
    "api/common/exceptions.py",
    "api/common/base64.py",
    "api/common/check_team_permission.py",
]

# Python stdlib (incomplete but covers everything we use). When an import
# matches this set, it's not a pip dep.
STDLIB = frozenset({
    "__future__", "abc", "argparse", "array", "ast", "asyncio", "atexit",
    "base64", "binascii", "bisect", "builtins", "calendar", "codecs",
    "collections", "concurrent", "configparser", "contextlib", "contextvars",
    "copy", "csv", "dataclasses", "datetime", "decimal", "email", "encodings",
    "enum", "errno", "filecmp", "fnmatch", "fractions", "functools", "gc",
    "getpass", "glob", "gzip", "hashlib", "heapq", "hmac", "html", "http",
    "imaplib", "importlib", "inspect", "io", "ipaddress", "itertools", "json",
    "locale", "logging", "math", "mimetypes", "numbers", "operator", "os",
    "pathlib", "pickle", "platform", "pwd", "queue", "random", "re", "secrets",
    "select", "shutil", "signal", "socket", "sqlite3", "ssl", "stat",
    "statistics", "string", "struct", "subprocess", "sys", "tarfile",
    "tempfile", "threading", "time", "timeit", "tomli", "tomllib", "traceback",
    "tracemalloc", "types", "typing", "unicodedata", "urllib", "uuid",
    "warnings", "weakref", "wsgiref", "zipfile", "zoneinfo", "_thread",
    "wave",
})

# Local packages — we own the code, no pip install needed.
LOCAL = frozenset({
    "api", "admin", "agent", "common", "conf", "deepdoc", "management",
    "mcp", "memory", "rag", "sdk", "test", "tools",
})

# Map Python import name → top-level pip package name (when they differ).
# Only entries where the name differs from the import are listed; others
# are matched directly.
IMPORT_TO_PIP = {
    "Cryptodome": "pycryptodomex",
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "elastic_transport": "elastic-transport",
    "elasticsearch_dsl": "elasticsearch-dsl",
    "google": "google-cloud-storage",   # multiple google.* packages — we use cloud.storage
    "googleapiclient": "google-api-python-client",
    "jwt": "PyJWT",
    "mypy_boto3_s3": "mypy-boto3-s3",
    "opensearchpy": "opensearch-py",
    "ruamel": "ruamel.yaml",
    "yaml": "PyYAML",
    "azure": "azure-storage-blob",      # multiple azure.* packages — pick most common
}

# Packages that we know are NOT in Dockerfile.management because they are
# only reachable via code paths the mgmt backend doesn't execute at boot,
# even though they appear in a top-level import of a scanned file. These
# are tolerated — the import would fail at runtime if the code ran, but
# none of these paths are hit during boot.
KNOWN_LAZY_IMPORTS = frozenset({
    # If a file in the boot list ever ends up needing one of these at
    # boot time, ADD IT to Dockerfile.management instead of growing this
    # set — that's the whole point of the test.
})


def parse_dockerfile_pip_deps() -> set[str]:
    """Extract package names from `pip install "name>=X.Y"` lines.

    Best-effort regex — handles the format we use in Dockerfile.management
    (continuation lines with `name>=ver` quoted strings)."""
    text = DOCKERFILE.read_text()
    # Match `"pkg_name>=..."`, `"pkg_name==..."`, `"pkg_name"`. Picks the
    # part before any version operator.
    deps = set()
    for match in re.finditer(r'"([A-Za-z0-9_\-.\[\]]+?)(?:[><=!~][^"]*)?"', text):
        name = match.group(1)
        # Strip extras like `uvicorn[standard]` → `uvicorn`
        name = re.sub(r"\[.*\]$", "", name)
        deps.add(name.lower().replace("_", "-"))
    return deps


def extract_top_level_imports(path: Path) -> set[str]:
    """Return the set of top-level Python imports in a source file.

    Use Python's ast module for reliable parsing — handles `import X as Y`,
    `from X import A, B`, multi-line parenthesised imports, etc. Only
    captures imports at module top level (not inside functions/classes).
    """
    import ast
    if not path.exists():
        return set()
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return set()
    imports: set[str] = set()
    for node in tree.body:  # iterate top-level statements only
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # Skip relative imports — those resolve to local modules,
            # not pip packages (`from . import X` has node.level >= 1,
            # `from .foo import X` has module='foo' but level=1 too).
            if node.level and node.level > 0:
                continue
            if node.module:
                imports.add(node.module.split(".")[0])
    return imports


def normalise_for_pip(import_name: str) -> str:
    """Return the pip-package name to look up in the Dockerfile deps."""
    if import_name in IMPORT_TO_PIP:
        return IMPORT_TO_PIP[import_name].lower().replace("_", "-")
    return import_name.lower().replace("_", "-")


def test_mgmt_image_has_all_boot_chain_deps():
    """Every external import in a boot file must have its pip dep in
    Dockerfile.management. Adding a new import in common/ or api/db/
    without updating the slim image causes a runtime crash in prod."""
    pip_deps = parse_dockerfile_pip_deps()
    # Add a few transitive deps that pip installs automatically (we don't
    # list them in Dockerfile.management but they ARE in the venv). These
    # are deps-of-our-deps, not standalone imports we made.
    pip_deps.update({
        # Transitives pip pulls automatically when we install the parents.
        # Adding here keeps the test from flagging them as missing.
        "pydantic", "pydantic-core", "anyio", "starlette", "h11", "click",
        "typing-extensions", "annotated-types", "yarl", "aiohttp",
        "websocket-client", "websocket",
        "google",         # via google-cloud-storage
        "azure",          # via azure-* SDKs
        "elasticsearch",  # via elasticsearch-dsl
        "elastic-transport",  # via elasticsearch
        "infinity",       # via infinity-sdk
        "valkey",         # explicitly installed
        "botocore",       # via boto3
        "urllib3",        # via requests / boto3
        "playhouse",      # subpkg of peewee
        "requests",       # via several SDKs
    })

    missing: dict[str, list[tuple[str, int]]] = {}

    for glob_pat in BOOT_GLOBS:
        for path in REPO_ROOT.glob(glob_pat):
            if path.name == "__pycache__":
                continue
            for imp in extract_top_level_imports(path):
                if imp in STDLIB or imp in LOCAL or imp in KNOWN_LAZY_IMPORTS:
                    continue
                pip_name = normalise_for_pip(imp)
                if pip_name in pip_deps:
                    continue
                # Found a top-level import not in the Dockerfile.
                missing.setdefault(pip_name, []).append(
                    (str(path.relative_to(REPO_ROOT)), imp),
                )

    if missing:
        lines = ["The following pip packages are imported at boot by the",
                 "management backend but NOT installed in Dockerfile.management.",
                 "Add them to the pip install block:",
                 ""]
        for pkg, sites in sorted(missing.items()):
            lines.append(f"  - {pkg}")
            for path, imp in sites[:3]:
                lines.append(f"      (imported as `{imp}` in {path})")
            if len(sites) > 3:
                lines.append(f"      ... and {len(sites) - 3} more")
        lines.append("")
        lines.append("If the import is genuinely lazy (inside a function, never")
        lines.append("hit at boot), add the module to BOOT_GLOBS exclusions or")
        lines.append("KNOWN_LAZY_IMPORTS in this test file.")
        raise AssertionError("\n".join(lines))


# ============================================================================
# File presence checks — config files that the boot code opens (not imports)
# must be COPY'd into the image. Missed copies are the second most common
# cause of mgmt pod boot failure (after missing pip deps).
# ============================================================================

# Files that common/* and api/* code opens at startup via os.path / open().
# If they're not in the image at /ragflow/<path>, the boot crashes with
# FileNotFoundError. Each entry is the path relative to repo root and
# expected to land at the same relative path inside /ragflow in the image.
REQUIRED_FILES_AT_BOOT = [
    # common/config_utils.py:read_config() always opens conf/service_conf.yaml.
    # conf/local.service_conf.yaml is optional and mounted by the chart via
    # initContainer (envsubst on the .template), so we don't require it.
    "conf/service_conf.yaml",
    # api/utils/crypt.py loads RSA keys at module top level. Dev keys ship
    # in the repo; prod overrides via K8s Secret mount.
    "conf/private.pem",
    "conf/public.pem",
]


def parse_dockerfile_copies() -> set[str]:
    """Extract source paths from `COPY <src> <dst>` lines.

    Returns the set of repo-relative source paths. Multi-source COPY
    (e.g. `COPY a b c /dst/`) is handled — all but the last token are
    treated as sources.
    """
    text = DOCKERFILE.read_text()
    sources: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("COPY"):
            continue
        # Strip `COPY` keyword and any `--chown=...` / `--from=...` flags.
        tokens = stripped.split()
        tokens = [t for t in tokens[1:] if not t.startswith("--")]
        if len(tokens) < 2:
            continue
        # Skip COPY --from=builder (those reference build stage paths,
        # not repo paths).
        if "--from=" in stripped:
            continue
        # Everything except the last token is a source path.
        for src in tokens[:-1]:
            sources.add(src.lstrip("./"))
    return sources


def test_mgmt_image_has_required_boot_files():
    """Files that the boot chain opens via open() (not import) must be
    COPY'd into Dockerfile.management. Otherwise the pod crashes with
    FileNotFoundError — which the import-deps test cannot catch."""
    copied = parse_dockerfile_copies()
    missing = []
    for required in REQUIRED_FILES_AT_BOOT:
        # COPY can list the source as `conf/service_conf.yaml` directly,
        # or list the parent dir `conf/` which copies everything. Match
        # either.
        if required in copied:
            continue
        parent = required.split("/", 1)[0] + "/"
        if parent in copied or parent.rstrip("/") in copied:
            continue
        missing.append(required)

    if missing:
        lines = ["The following files are opened at boot by the mgmt backend",
                 "but NOT COPY'd into Dockerfile.management:",
                 ""]
        for f in missing:
            lines.append(f"  - {f}")
        lines.append("")
        lines.append("Add a 'COPY --chown=ragflow:ragflow <file> /ragflow/<file>'")
        lines.append("line to Dockerfile.management.")
        raise AssertionError("\n".join(lines))
