"""
Static invariant — every initContainer that runs `envsubst` on the
service_conf.yaml.template MUST have BOTH:
  - configMapRef (HOST/PORT values like MINIO_HOST, MYSQL_HOST, ...)
  - secretRef    (passwords like MINIO_PASSWORD)

The template references both kinds of variables in `${VAR}` placeholders;
missing either pulls a substitution to the empty string, which YAML
parses as None, and downstream Python (Minio(None, ...)) crashes with
'can only concatenate str (not "NoneType") to str'.

This invariant was violated by commit de8495ac5 (June 2026, devops side)
when MINIO_HOST switched from a Helm-rendered value to ${MINIO_HOST}.
The task-executor chart was patched 1 day later (f1b841aa0) but the
ragflow-api chart kept the partial envFrom for 10 more days until
d88a57270. This test makes that drift impossible to recur silently.

Run:
    uv run pytest test/multitenant/test_helm_envsubst_envfrom.py -v
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHARTS_DIR = REPO_ROOT / "helm" / "ragflow" / "charts"


def _collect_envsubst_init_containers():
    """Yield (chart_name, container_name, container_dict) for every
    initContainer in any subchart deployment.yaml whose `command`/`args`
    string contains the literal token 'envsubst'."""
    for chart_dir in sorted(CHARTS_DIR.iterdir()):
        if not chart_dir.is_dir():
            continue
        deployment_path = chart_dir / "templates" / "deployment.yaml"
        if not deployment_path.exists():
            continue
        # We can't safely yaml.load a Helm template (jinja tags), but
        # initContainer blocks are plain enough to inspect as text. We
        # walk the structure with a tiny custom slicer instead.
        text = deployment_path.read_text()
        # Strip Helm directives so the YAML loads. {{- ... -}} can span
        # multiple lines (toYaml | nindent) so use a non-greedy multi-line
        # regex, then drop the leftover bare `{{` `}}` lines too.
        import re
        stripped = re.sub(r"{{-?.*?-?}}", "", text, flags=re.DOTALL)
        # Some templates reference `.Values...` inside double-braces only;
        # after the regex above, any leftover `{{` line is malformed YAML,
        # so blank it out.
        stripped = re.sub(r"{{[^}]*}}", "REDACTED", stripped)
        try:
            docs = list(yaml.safe_load_all(stripped))
        except yaml.YAMLError:
            # If the template can't be parsed even after stripping, skip —
            # we don't want false positives blocking unrelated commits.
            continue
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            init_containers = (
                doc.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("initContainers", [])
            )
            for c in init_containers or []:
                command_text = " ".join(c.get("command") or []) + " ".join(c.get("args") or [])
                # Only flag the service_conf rendering — other render-* init
                # containers (e.g. nginx config in mgmt-frontend) deliberately
                # use env: direct injection and don't reference the ConfigMap.
                if "envsubst" in command_text and "service_conf.yaml" in command_text:
                    yield chart_dir.name, c.get("name", "?"), c


def test_envsubst_init_containers_load_both_configmap_and_secret():
    missing = []
    for chart, name, container in _collect_envsubst_init_containers():
        env_from = container.get("envFrom") or []
        kinds = {next(iter(item.keys())) for item in env_from if isinstance(item, dict)}
        if "configMapRef" not in kinds or "secretRef" not in kinds:
            missing.append((chart, name, sorted(kinds)))

    if missing:
        lines = [
            "The following initContainer(s) call envsubst on the",
            "service_conf.yaml.template but do NOT load BOTH a ConfigMap",
            "and a Secret in their envFrom. ${MINIO_HOST}, ${MYSQL_HOST},",
            "${REDIS_HOST} etc. live in the ConfigMap; ${MINIO_PASSWORD}",
            "etc. live in the Secret. Missing either silently substitutes",
            "to empty string → YAML None → TypeError at first use.",
            "",
        ]
        for chart, name, kinds in missing:
            lines.append(f"  chart={chart}  initContainer={name}  has envFrom={kinds}")
        lines.append("")
        lines.append("History: this invariant was first broken by commit")
        lines.append("de8495ac5 in June 2026 and the api chart stayed broken")
        lines.append("for 10 days. Don't let it happen again.")
        raise AssertionError("\n".join(lines))
