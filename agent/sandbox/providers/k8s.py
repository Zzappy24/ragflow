"""Provider sandbox k8s — Jobs Kubernetes éphémères durcis.

CUSTOM B2B SaaS — provider sandbox k8s (spec
docs/superpowers/specs/2026-08-13-k8s-sandbox-provider-design.md).
Aucun import kubernetes au niveau module : lazy dans initialize().
"""

_BOOTSTRAP = "import base64,os; exec(base64.b64decode(os.environ['SBX_CODE']).decode())"


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
