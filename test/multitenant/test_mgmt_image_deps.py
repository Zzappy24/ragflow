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


# Boot entry point — uvicorn imports this. We trace transitively all
# local imports from here to build the full set of files loaded at boot.
BOOT_ENTRY = "management/server/main.py"

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
# CUSTOM B2B SaaS — `mcp` volontairement RETIRÉ de ce set:
# on a un dossier local `./mcp/` (partiel, client.py + server.py), MAIS le
# SDK pip officiel `mcp>=1.19.0` fournit `mcp.client.session` etc. qui ne
# sont PAS dans le local. Un import `from mcp.client.session import ...`
# résout donc au pip, pas au local. Marquer `mcp` comme LOCAL faisait
# sauter le check → l'incident 2026-07-03 (mgmt-backend 500 sur
# /admin/workspaces/*/stats) est passé silencieusement. Le test doit
# vérifier que `mcp` est dans Dockerfile.management dès qu'il apparaît
# dans le boot chain (aujourd'hui lazy-importé donc absent, mais un futur
# merge upstream peut le remettre en top-level).
LOCAL = frozenset({
    "api", "admin", "agent", "common", "conf", "deepdoc", "management",
    "memory", "rag", "sdk", "test", "tools",
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
    "word2number": "word2number",
    "roman_numbers": "roman-numbers",
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


def extract_imports(path: Path, deep: bool = False) -> list[tuple[str, int]]:
    """Return list of (full_dotted_module_name, level) for imports.

    level=0 absolute, level>0 relative.

    deep=False: only top-level (module-load-time) imports.
    deep=True: also imports nested inside functions/methods/conditionals.
    Use deep=True for the boot entry point (management/server/main.py),
    whose lifespan() function imports `common.settings` LAZILY when
    uvicorn calls it at startup — that import IS part of the boot chain
    and triggers transitive loading.
    """
    import ast
    if not path.exists():
        return []
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return []
    out: list[tuple[str, int]] = []
    nodes_to_scan = ast.walk(tree) if deep else tree.body
    for node in nodes_to_scan:
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, 0))
        elif isinstance(node, ast.ImportFrom):
            # `from X.Y import Z, W` semantically may import X.Y.Z and X.Y.W
            # as submodules (if they exist as .py files). Python's import
            # machinery does this automatically — we must too, otherwise
            # `from common import settings` resolves only `common/__init__.py`
            # and never visits `common/settings.py`.
            base = node.module or ""
            level = node.level or 0
            if base:
                out.append((base, level))
            for alias in node.names:
                if alias.name == "*":
                    continue
                full = f"{base}.{alias.name}" if base else alias.name
                out.append((full, level))
    return out


def resolve_local_module(dotted: str, level: int, from_path: Path) -> Path | None:
    """Given a dotted import like `common.settings` (absolute, level=0) or
    `foo.bar` from `level=2` relative, find the matching .py / __init__.py
    file under repo root. Return None if it's not a local module (i.e. pip)
    or doesn't exist."""
    if level == 0:
        # Absolute import — first part must be a known local top-level pkg.
        top = dotted.split(".")[0]
        if top not in LOCAL:
            return None
        base = REPO_ROOT
        parts = dotted.split(".")
    else:
        # Relative import — anchor at from_path's package dir.
        # level=1 = same dir, level=2 = parent dir, etc.
        anchor = from_path.parent
        for _ in range(level - 1):
            anchor = anchor.parent
        base = anchor
        parts = dotted.split(".") if dotted else []

    # Try as a .py module: parts[:-1] are dirs, parts[-1] is file
    candidate_py = base.joinpath(*parts).with_suffix(".py")
    if candidate_py.exists() and candidate_py.is_file():
        return candidate_py
    # Try as a package: parts/__init__.py
    candidate_pkg = base.joinpath(*parts) / "__init__.py"
    if candidate_pkg.exists() and candidate_pkg.is_file():
        return candidate_pkg
    return None


def trace_boot_chain(entry: Path) -> tuple[set[Path], set[str]]:
    """Walk all imports starting from `entry`. Follow local imports
    transitively. For the boot ENTRY file (management/server/main.py),
    we scan ALL imports including those inside lifespan() — they execute
    at uvicorn startup and trigger the transitive chain. For other files,
    we scan only top-level imports (those that run at module load time).
    Return (visited_files, external_imports).
    """
    visited: set[Path] = set()
    external: set[str] = set()
    queue: list[Path] = [entry]
    while queue:
        path = queue.pop()
        if path in visited:
            continue
        visited.add(path)
        # ENTRY file — scan deep (capture imports inside lifespan() etc).
        # All other files — only top-level (those execute on `import`).
        deep = (path == entry)
        for dotted, level in extract_imports(path, deep=deep):
            local_path = resolve_local_module(dotted, level, path)
            if local_path is not None:
                if local_path not in visited:
                    queue.append(local_path)
                continue
            if level > 0:
                continue
            top = dotted.split(".")[0]
            if top in STDLIB or top in LOCAL or top in KNOWN_LAZY_IMPORTS:
                continue
            external.add(top)
    return visited, external


def normalise_for_pip(import_name: str) -> str:
    """Return the pip-package name to look up in the Dockerfile deps."""
    if import_name in IMPORT_TO_PIP:
        return IMPORT_TO_PIP[import_name].lower().replace("_", "-")
    return import_name.lower().replace("_", "-")


def test_mgmt_image_has_all_boot_chain_deps():
    """Every external import in the boot chain must have its pip dep in
    Dockerfile.management. The boot chain is computed transitively from
    management/server/main.py — no manual file list to maintain.

    Adding a new import in common/, api/, or a transitively-loaded
    rag/* module without updating the slim image causes a runtime
    ModuleNotFoundError. This test catches the drift BEFORE deploy."""
    pip_deps = parse_dockerfile_pip_deps()
    # Transitives pip installs automatically when we declare the parent.
    # These are NOT in Dockerfile.management explicitly but ARE in the venv.
    pip_deps.update({
        "pydantic", "pydantic-core", "anyio", "starlette", "h11", "click",
        "typing-extensions", "annotated-types", "yarl", "aiohttp",
        "websocket-client", "websocket",
        "google",             # via google-cloud-storage
        "azure",              # via azure-* SDKs
        "elasticsearch",      # via elasticsearch-dsl
        "elastic-transport",  # via elasticsearch
        "infinity",           # via infinity-sdk
        "valkey",             # explicit pip install
        "botocore",           # via boto3
        "urllib3",            # via requests / boto3
        "playhouse",          # subpkg of peewee
        "requests",           # via several SDKs
    })

    _, external_imports = trace_boot_chain(REPO_ROOT / BOOT_ENTRY)

    missing: dict[str, str] = {}
    for imp in external_imports:
        pip_name = normalise_for_pip(imp)
        if pip_name not in pip_deps:
            missing[pip_name] = imp

    if missing:
        lines = [
            "The following pip packages are imported at boot by the",
            "management backend (traced transitively from",
            f"{BOOT_ENTRY}) but NOT installed in Dockerfile.management.",
            "Add them to the pip install block:",
            "",
        ]
        for pkg, imp in sorted(missing.items()):
            lines.append(f"  - {pkg}  (import name: `{imp}`)")
        lines.append("")
        lines.append("If a real lazy import escaped detection (inside a")
        lines.append("function/method), add it to KNOWN_LAZY_IMPORTS.")
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


def _is_path_covered_by_copy(rel_path: str, copied: set[str]) -> bool:
    """A repo-relative path is 'covered' by a COPY directive if either:
      - it's listed directly, or
      - any of its parent dirs is COPY'd (recursive copy).
    """
    if rel_path in copied:
        return True
    parts = rel_path.split("/")
    for i in range(1, len(parts)):
        prefix = "/".join(parts[:i])
        if prefix in copied or (prefix + "/") in copied:
            return True
    return False


def test_mgmt_image_has_required_boot_files():
    """Files that the boot chain opens via open() (not import) must be
    COPY'd into Dockerfile.management. Otherwise the pod crashes with
    FileNotFoundError — which the import-deps test cannot catch."""
    copied = parse_dockerfile_copies()
    missing = [f for f in REQUIRED_FILES_AT_BOOT if not _is_path_covered_by_copy(f, copied)]
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


def test_mgmt_image_copies_all_boot_chain_source_files():
    """Every Python file visited by the transitive boot chain trace must
    be COPY'd into the image. Otherwise the import fails at runtime with
    ModuleNotFoundError even though the pip dep is installed.

    This catches the case where we add a pip dep for `nltk` but forget
    to COPY the directory that actually IMPORTS nltk (e.g. memory/services/
    or rag/nlp/synonym.py)."""
    copied = parse_dockerfile_copies()
    visited, _ = trace_boot_chain(REPO_ROOT / BOOT_ENTRY)
    missing: list[str] = []
    for path in sorted(visited):
        rel = str(path.relative_to(REPO_ROOT))
        if not _is_path_covered_by_copy(rel, copied):
            missing.append(rel)
    if missing:
        # Group by top-level directory for a more actionable message —
        # usually the fix is to COPY the whole subdir.
        by_dir: dict[str, list[str]] = {}
        for f in missing:
            top = f.split("/", 2)
            key = "/".join(top[:2]) if len(top) >= 2 else top[0]
            by_dir.setdefault(key, []).append(f)
        lines = [
            "The following Python files are loaded at boot (via transitive",
            f"trace from {BOOT_ENTRY}) but NOT present in the slim image:",
            "",
        ]
        for d, files in sorted(by_dir.items()):
            lines.append(f"  COPY --chown=ragflow:ragflow {d} /ragflow/{d}")
            for f in files[:3]:
                lines.append(f"      ({f})")
            if len(files) > 3:
                lines.append(f"      ... +{len(files) - 3} more files")
        raise AssertionError("\n".join(lines))


def test_main_image_has_python_magic_for_upload_validation():
    """python-magic + libmagic1 doivent être dans l'image PRINCIPALE.

    check_blob_matches_extension (api/utils/file_utils.py) dégrade
    silencieusement en no-op quand python-magic manque (`except
    ImportError: return None`) : la validation profonde du contenu des
    uploads disparaît sans aucun symptôme. C'est arrivé — le check a
    tourné en no-op d'avril à juillet 2026 (python-magic jamais déclaré).
    Ce test rend l'absence bruyante.
    """
    pyproject = (REPO_ROOT / "pyproject.toml").read_text()
    assert re.search(r'"python-magic[>=<~\d.]*"', pyproject), (
        "python-magic absent de pyproject.toml : la validation MIME des "
        "uploads (check_blob_matches_extension) devient un no-op silencieux. "
        "Fix : uv add python-magic")

    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert "libmagic1" in dockerfile, (
        "libmagic1 absent du Dockerfile principal : python-magic ne peut pas "
        "charger la lib C -> ImportError -> validation MIME no-op. "
        "Fix : ajouter libmagic1 à la liste apt install")
