import pytest

from agent.sandbox.providers.k8s import build_job_manifest


@pytest.fixture
def manifest():
    return build_job_manifest("sbx-abc12345", "harbor.example/x:1", "QUJD", timeout=60)


def _container(m):
    return m["spec"]["template"]["spec"]["containers"][0]


def test_job_shape(manifest):
    assert manifest["apiVersion"] == "batch/v1" and manifest["kind"] == "Job"
    assert manifest["metadata"]["name"] == "sbx-abc12345"
    assert manifest["metadata"]["labels"]["app"] == "ragflow-sandbox"
    spec = manifest["spec"]
    assert spec["backoffLimit"] == 0
    assert spec["activeDeadlineSeconds"] == 60
    assert spec["ttlSecondsAfterFinished"] == 300


def test_pod_hardening(manifest):
    pod = manifest["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["automountServiceAccountToken"] is False
    sc = _container(manifest)["securityContext"]
    assert sc["runAsNonRoot"] is True
    assert sc["runAsUser"] == 65534 and sc["runAsGroup"] == 65534
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"] == {"drop": ["ALL"]}
    assert sc["seccompProfile"] == {"type": "RuntimeDefault"}


def test_volumes_are_memory_tmpfs(manifest):
    pod = manifest["spec"]["template"]["spec"]
    vols = {v["name"]: v for v in pod["volumes"]}
    assert vols["workspace"]["emptyDir"]["medium"] == "Memory"
    assert vols["tmp"]["emptyDir"]["medium"] == "Memory"
    mounts = {m["name"]: m["mountPath"] for m in _container(manifest)["volumeMounts"]}
    assert mounts == {"workspace": "/workspace", "tmp": "/tmp"}
    assert _container(manifest)["workingDir"] == "/workspace"


def test_resources_and_code_env(manifest):
    c = _container(manifest)
    assert c["imagePullPolicy"] == "IfNotPresent"  # `latest` forcerait Always sur kind
    assert c["resources"]["limits"] == {"memory": "1Gi", "cpu": "1"}
    assert c["resources"]["requests"] == {"memory": "1Gi", "cpu": "1"}
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["SBX_CODE"] == "QUJD"
    assert c["command"][0] == "python" and "SBX_CODE" in c["command"][2]


def test_optional_pull_secret_and_node_selector():
    m = build_job_manifest("sbx-x", "img", "QQ==", 30,
                           image_pull_secret="harbor-pull-secret",
                           node_selector={"kubernetes.io/arch": "amd64"})
    pod = m["spec"]["template"]["spec"]
    assert pod["imagePullSecrets"] == [{"name": "harbor-pull-secret"}]
    assert pod["nodeSelector"] == {"kubernetes.io/arch": "amd64"}
    # absents par défaut
    m2 = build_job_manifest("sbx-y", "img", "QQ==", 30)
    pod2 = m2["spec"]["template"]["spec"]
    assert "imagePullSecrets" not in pod2 and "nodeSelector" not in pod2
