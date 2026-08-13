"""Provider sandbox k8s — Jobs Kubernetes éphémères durcis.

CUSTOM B2B SaaS — provider sandbox k8s (spec
docs/superpowers/specs/2026-08-13-k8s-sandbox-provider-design.md).
Aucun import kubernetes au niveau module : lazy dans initialize().
"""

import base64
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from agent.sandbox.result_protocol import build_python_wrapper, extract_structured_result
from agent.sandbox.security_shared import analyze_python_code

from .base import ExecutionResult, SandboxInstance, SandboxProvider

logger = logging.getLogger(__name__)

_BOOTSTRAP = "import base64,os; exec(base64.b64decode(os.environ['SBX_CODE']).decode())"

# Job creation payload cap: keep well under the 1MiB etcd object limit once
# the manifest (labels, env, resources, ...) wraps the base64 wrapper.
_MAX_WRAPPER_B64_BYTES = 900 * 1024
# Cap applied to pod logs read back from the cluster (10 MiB).
_MAX_LOG_BYTES = 10 * 1024 * 1024
# How long we tolerate a Pending pod before surfacing a scheduling/image error.
_PENDING_GRACE_SECONDS = 60
# Extra poll budget past the Job's own activeDeadlineSeconds, to allow the
# Job controller itself to flip status to failed before we give up polling.
_POLL_DEADLINE_SLACK_SECONDS = 10
_POLL_INTERVAL_SECONDS = 0.5


def build_job_manifest(job_name, image, wrapper_b64, timeout,
                       memory_limit="1Gi", cpu_limit="1", ttl_seconds=300,
                       image_pull_secret="", node_selector=None):
    resources = {"memory": memory_limit, "cpu": cpu_limit}
    pod_spec = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "volumes": [
            {"name": "workspace", "emptyDir": {"medium": "Memory"}},
            {"name": "tmp", "emptyDir": {"medium": "Memory"}},
        ],
        "containers": [{
            "name": "sandbox",
            "image": image,
            "imagePullPolicy": "IfNotPresent",
            "command": ["python", "-c", _BOOTSTRAP],
            "env": [{"name": "SBX_CODE", "value": wrapper_b64},
                    {"name": "MPLCONFIGDIR", "value": "/tmp/matplotlib"}],
            "workingDir": "/workspace",
            "volumeMounts": [
                {"name": "workspace", "mountPath": "/workspace"},
                {"name": "tmp", "mountPath": "/tmp"},
            ],
            "resources": {"limits": dict(resources), "requests": dict(resources)},
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65534,
                "runAsGroup": 65534,
                "readOnlyRootFilesystem": True,
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
                "seccompProfile": {"type": "RuntimeDefault"},
            },
        }],
    }
    if image_pull_secret:
        pod_spec["imagePullSecrets"] = [{"name": image_pull_secret}]
    if node_selector:
        pod_spec["nodeSelector"] = dict(node_selector)
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name,
                     "labels": {"app": "ragflow-sandbox",
                                "ragflow.io/component": "code-exec"}},
        "spec": {"backoffLimit": 0,
                 "activeDeadlineSeconds": timeout,
                 "ttlSecondsAfterFinished": ttl_seconds,
                 "template": {"metadata": {"labels": {"app": "ragflow-sandbox"}},
                              "spec": pod_spec}},
    }


class K8sProvider(SandboxProvider):
    """Run sandboxed Python code as ephemeral, hardened Kubernetes Jobs.

    The `kubernetes` client library is imported lazily inside `initialize()`
    so this module stays importable (e.g. for `build_job_manifest` reuse, or
    when the provider simply isn't selected) on hosts that don't have it
    installed. Tests inject mocks directly on `self._batch` / `self._core`
    instead of stubbing the `kubernetes` package.
    """

    def __init__(self):
        self.namespace: str = "rag-sandbox"
        self.image: str = ""
        self.memory_limit: str = "1Gi"
        self.cpu_limit: str = "1"
        self.timeout_max: int = 120
        self.kubeconfig_path: str = ""
        self.image_pull_secret: str = ""
        self.node_selector: Dict[str, str] = {}
        self.ttl_seconds_after_finished: int = 300
        self._initialized: bool = False
        self._batch = None
        self._core = None

    def initialize(self, config: Dict[str, Any]) -> bool:
        """Initialize the provider: validate config, load kube config, probe the cluster."""
        self.namespace = str(config.get("namespace") or "rag-sandbox")
        self.image = str(config.get("image") or "")
        self.memory_limit = str(config.get("memory_limit") or "1Gi")
        self.cpu_limit = str(config.get("cpu_limit") or "1")
        self.timeout_max = int(config.get("timeout") or 120)
        self.kubeconfig_path = str(config.get("kubeconfig_path") or "")
        self.image_pull_secret = str(config.get("image_pull_secret") or "")
        self.node_selector = dict(config.get("node_selector") or {})
        self.ttl_seconds_after_finished = int(config.get("ttl_seconds_after_finished") or 300)
        self._initialized = False

        if not self.image:
            logger.warning("K8sProvider: 'image' is required in config.")
            return False

        try:
            from kubernetes import client, config as k8s_config
        except ImportError:
            logger.warning("K8sProvider: the 'kubernetes' package is not installed.")
            return False

        try:
            if self.kubeconfig_path:
                k8s_config.load_kube_config(config_file=self.kubeconfig_path)
            else:
                k8s_config.load_incluster_config()
        except Exception as exc:
            logger.warning("K8sProvider: failed to load kube config: %s", exc)
            return False

        try:
            self._batch = client.BatchV1Api()
            self._core = client.CoreV1Api()
            self._batch.list_namespaced_job(self.namespace, limit=1)
        except Exception as exc:
            logger.warning("K8sProvider: cluster connectivity check failed: %s", exc)
            return False

        self._initialized = True
        return True

    def create_instance(self, template: str = "python") -> SandboxInstance:
        if not self._initialized:
            raise RuntimeError("Provider not initialized. Call initialize() first.")
        if template not in ("python", "python3"):
            raise RuntimeError(f"K8sProvider only supports the 'python' template, got: {template!r}.")

        instance_id = str(uuid.uuid4())
        return SandboxInstance(
            instance_id=instance_id,
            provider="k8s",
            status="running",
            metadata={"namespace": self.namespace, "image": self.image},
        )

    def execute_code(
        self,
        instance_id: str,
        code: str,
        language: str,
        timeout: int = 10,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """Run `code` as a fresh Job. No exception ever escapes this method."""
        start = time.monotonic()
        job_name: Optional[str] = None
        job_created = False
        try:
            norm_lang = (language or "").strip().lower()
            if norm_lang not in ("python", "python3"):
                return self._error_result(
                    instance_id, start,
                    f"K8sProvider only supports the 'python' language, got: {language!r}.",
                )

            is_safe, violations = analyze_python_code(code)
            if not is_safe:
                return self._error_result(
                    instance_id, start,
                    "Code is unsafe: " + "; ".join(violations),
                    exit_code=-999,
                    extra_metadata={"violations": violations},
                )

            if not self._initialized or self._batch is None or self._core is None:
                return self._error_result(
                    instance_id, start,
                    "K8sProvider is not initialized. Call initialize() first.",
                )

            try:
                args_json = json.dumps(arguments or {}, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                return self._error_result(instance_id, start, f"Arguments are not JSON-serializable: {exc}")

            wrapper = build_python_wrapper(code, args_json)
            wrapper_b64 = base64.b64encode(wrapper.encode("utf-8")).decode("ascii")
            if len(wrapper_b64) > _MAX_WRAPPER_B64_BYTES:
                return self._error_result(
                    instance_id, start,
                    f"Code payload too large to run in a Kubernetes Job (max {_MAX_WRAPPER_B64_BYTES // 1024} KB base64-encoded).",
                )

            exec_timeout = int(timeout) if timeout else self.timeout_max
            exec_timeout = max(1, min(exec_timeout, self.timeout_max))

            job_name = f"sbx-{uuid.uuid4().hex[:8]}"
            manifest = build_job_manifest(
                job_name, self.image, wrapper_b64, exec_timeout,
                memory_limit=self.memory_limit, cpu_limit=self.cpu_limit,
                ttl_seconds=self.ttl_seconds_after_finished,
                image_pull_secret=self.image_pull_secret, node_selector=self.node_selector,
            )

            try:
                self._batch.create_namespaced_job(namespace=self.namespace, body=manifest)
                job_created = True
            except Exception as exc:
                logger.warning("K8sProvider: failed to create Job %s: %s", job_name, exc)
                return self._error_result(instance_id, start, f"Failed to create Kubernetes Job: {exc}")

            job_phase, pod = self._poll_job(job_name, exec_timeout)

            if job_phase == "pending_timeout":
                reason = self._pod_waiting_reason(pod)
                if reason in ("ImagePullBackOff", "ErrImagePull"):
                    message = f"Sandbox image {self.image!r} is inaccessible from the cluster (reason: {reason})."
                else:
                    message = (
                        f"Sandbox pod stayed Pending for more than {_PENDING_GRACE_SECONDS}s "
                        f"(reason: {reason or 'unknown'})."
                    )
                return self._error_result(instance_id, start, message, extra_metadata={"job_name": job_name})

            if job_phase == "timeout":
                return self._error_result(
                    instance_id, start,
                    f"Sandbox execution timed out after {exec_timeout}s.",
                    extra_metadata={"job_name": job_name},
                )

            if pod is None:
                pod = self._get_pod(job_name)
            pod_name = pod.metadata.name if pod is not None else None

            raw_logs = ""
            if pod_name:
                try:
                    raw_logs = self._core.read_namespaced_pod_log(pod_name, self.namespace) or ""
                except Exception as exc:
                    logger.warning("K8sProvider: failed to read logs for pod %s: %s", pod_name, exc)
                    raw_logs = ""

            if len(raw_logs) > _MAX_LOG_BYTES:
                return self._error_result(
                    instance_id, start,
                    f"Sandbox logs are too large/volumineux to process safely (cap: {_MAX_LOG_BYTES // (1024 * 1024)} MB).",
                    extra_metadata={"job_name": job_name},
                )

            cleaned_stdout, structured = extract_structured_result(raw_logs)
            value = structured.get("value")
            artifacts: List[Dict[str, Any]] = []
            result_value = value
            if isinstance(value, dict) and "artifacts" in value:
                artifacts = value.get("artifacts") or []
                result_value = value.get("value")

            metadata = {
                "instance_id": instance_id,
                "job_name": job_name,
                "pod_name": pod_name,
                "result_present": structured.get("present", False),
                "result_value": result_value,
                "result_type": structured.get("type"),
                "artifacts": artifacts,
            }

            if job_phase == "failed":
                return ExecutionResult(
                    stdout=cleaned_stdout,
                    stderr=cleaned_stdout or "Sandbox Job failed.",
                    exit_code=1,
                    execution_time=time.monotonic() - start,
                    metadata=metadata,
                )

            return ExecutionResult(
                stdout=cleaned_stdout,
                stderr="",
                exit_code=0,
                execution_time=time.monotonic() - start,
                metadata=metadata,
            )

        except Exception as exc:  # pragma: no cover - safety net, no exception must escape.
            logger.exception("K8sProvider: unexpected error while executing code")
            return self._error_result(instance_id, start, f"Unexpected sandbox error: {exc}")
        finally:
            if job_created and job_name:
                try:
                    self._batch.delete_namespaced_job(job_name, self.namespace, propagation_policy="Foreground")
                except Exception as exc:
                    logger.warning("K8sProvider: failed to delete Job %s: %s", job_name, exc)

    def destroy_instance(self, instance_id: str) -> bool:
        # Jobs are ephemeral and self-cleaned by execute_code's finally block;
        # there is no persistent resource tied to instance_id to tear down.
        return True

    def health_check(self) -> bool:
        if not self._initialized or self._batch is None:
            return False
        try:
            self._batch.list_namespaced_job(self.namespace, limit=1)
            return True
        except Exception as exc:
            logger.warning("K8sProvider: health check failed: %s", exc)
            return False

    def get_supported_languages(self) -> List[str]:
        return ["python"]

    def get_supported_templates(self) -> List[str]:
        return ["python"]

    @staticmethod
    def get_config_schema() -> Dict[str, Dict]:
        return {
            "namespace": {
                "type": "string",
                "required": False,
                "label": "Kubernetes Namespace",
                "default": "rag-sandbox",
                "placeholder": "rag-sandbox",
                "description": "Namespace where sandbox Jobs are created.",
                "scope": "deployment",
                "readonly": True,
            },
            "image": {
                "type": "string",
                "required": True,
                "label": "Sandbox Image",
                "default": "",
                "placeholder": "harbor.internal/ragflow/sandbox-python:latest",
                "description": "Container image used to run the sandboxed Python code.",
                "scope": "deployment",
                "readonly": False,
            },
            "memory_limit": {
                "type": "string",
                "required": False,
                "label": "Memory Limit",
                "default": "1Gi",
                "placeholder": "1Gi",
                "description": "Memory request/limit applied to the sandbox pod. Common format: 512Mi or 1Gi.",
                "scope": "deployment",
                "readonly": False,
            },
            "cpu_limit": {
                "type": "string",
                "required": False,
                "label": "CPU Limit",
                "default": "1",
                "placeholder": "1",
                "description": "CPU request/limit applied to the sandbox pod.",
                "scope": "deployment",
                "readonly": False,
            },
            "timeout": {
                "type": "integer",
                "required": False,
                "label": "Max Execution Timeout (seconds)",
                "default": 120,
                "min": 1,
                "max": 120,
                "description": "Hard cap on Job activeDeadlineSeconds. Per-call timeouts above this are clamped.",
                "scope": "deployment",
                "readonly": False,
            },
            "kubeconfig_path": {
                "type": "string",
                "required": False,
                "label": "Kubeconfig Path",
                "default": "",
                "placeholder": "/etc/ragflow/kubeconfig",
                "description": "Path to a kubeconfig file. Leave empty to use in-cluster configuration.",
                "scope": "deployment",
                "readonly": True,
            },
            "image_pull_secret": {
                "type": "string",
                "required": False,
                "label": "Image Pull Secret",
                "default": "",
                "placeholder": "regcred",
                "description": "Name of the imagePullSecrets entry used to pull the sandbox image.",
                "scope": "deployment",
                "readonly": True,
            },
            "node_selector": {
                "type": "object",
                "required": False,
                "label": "Node Selector",
                "default": {},
                "description": "Optional nodeSelector applied to the sandbox pod.",
                "scope": "deployment",
                "readonly": True,
            },
            "ttl_seconds_after_finished": {
                "type": "integer",
                "required": False,
                "label": "Job TTL After Finished (seconds)",
                "default": 300,
                "min": 0,
                "max": 3600,
                "description": "How long a finished Job is kept before Kubernetes garbage-collects it.",
                "scope": "deployment",
                "readonly": True,
            },
        }

    # -- internal helpers -----------------------------------------------

    def _poll_job(self, job_name: str, exec_timeout: int):
        """Poll a Job until it succeeds/fails, its pod is stuck Pending too long,
        or the deadline passes. Returns (phase, last_seen_pod)."""
        deadline = time.monotonic() + exec_timeout + _POLL_DEADLINE_SLACK_SECONDS
        pending_since: Optional[float] = None
        pod = None
        while True:
            status = None
            try:
                job = self._batch.read_namespaced_job(job_name, self.namespace)
                status = job.status
            except Exception as exc:
                logger.warning("K8sProvider: failed to read Job %s status: %s", job_name, exc)

            if status is not None:
                if getattr(status, "succeeded", None):
                    return "succeeded", pod
                if getattr(status, "failed", None):
                    return "failed", pod

            pod = self._get_pod(job_name)
            if pod is not None and getattr(pod.status, "phase", None) == "Pending":
                if pending_since is None:
                    pending_since = time.monotonic()
                elif time.monotonic() - pending_since > _PENDING_GRACE_SECONDS:
                    return "pending_timeout", pod
            else:
                pending_since = None

            if time.monotonic() > deadline:
                return "timeout", pod
            time.sleep(_POLL_INTERVAL_SECONDS)

    def _get_pod(self, job_name: str):
        try:
            pods = self._core.list_namespaced_pod(self.namespace, label_selector=f"job-name={job_name}")
        except Exception as exc:
            logger.warning("K8sProvider: failed to list pods for Job %s: %s", job_name, exc)
            return None
        items = getattr(pods, "items", None) or []
        return items[0] if items else None

    @staticmethod
    def _pod_waiting_reason(pod) -> Optional[str]:
        if pod is None:
            return None
        statuses = getattr(pod.status, "container_statuses", None) or []
        for container_status in statuses:
            state = getattr(container_status, "state", None)
            waiting = getattr(state, "waiting", None) if state is not None else None
            reason = getattr(waiting, "reason", None) if waiting is not None else None
            if reason:
                return reason
        return None

    @staticmethod
    def _error_result(
        instance_id: str,
        start: float,
        message: str,
        exit_code: int = 1,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        metadata: Dict[str, Any] = {"instance_id": instance_id, "artifacts": []}
        if extra_metadata:
            metadata.update(extra_metadata)
        return ExecutionResult(
            stdout="",
            stderr=message,
            exit_code=exit_code,
            execution_time=time.monotonic() - start,
            metadata=metadata,
        )
