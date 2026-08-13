#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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
import base64
import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent.sandbox.providers.k8s import K8sProvider, build_k8s_python_wrapper
from agent.sandbox.result_protocol import RESULT_MARKER_PREFIX

# NOTE: adapted from the brief's literal fixture. `build_python_wrapper` /
# `extract_structured_result` (agent/sandbox/result_protocol.py) actually wrap
# main()'s return value as one `__RAGFLOW_RESULT__:<base64>` log line carrying
# {"present": bool, "value": <return value>, "type": "json"} -- there is no
# top-level "__sandbox_result__"/"artifacts" envelope. The *real* contract
# (agent/tools/code_exec.py's tool prompt) is: sandboxed code writes files to
# an artifacts/ directory and "the sandbox will automatically collect these
# files" -- it does NOT need to smuggle artifacts through its return value at
# all (FAMAT recipes call `fr.spc_chart(d, out_dir="artifacts")` then
# `return json.dumps(d)`, a bare string). K8sProvider's
# `build_k8s_python_wrapper` implements that by scanning ./artifacts/ inside
# the pod and emitting the collected files as a sibling "artifacts" key next
# to "value"/"type"/"present" in the marker payload (see
# test_execute_success_collects_artifacts_from_directory_when_main_returns_string
# below, which exercises the real wrapper end-to-end via subprocess).
# WRAPPED_OK below instead covers the backward-compat path K8sProvider keeps:
# main() itself returning a dict shaped {"value": ..., "artifacts": [...]}.
# Artifact dicts themselves follow the real `ArtifactItem` schema
# (executor_manager/models/schemas.py): name/mime_type/size/content_b64, not
# the brief's placeholder "mime" key.
WRAPPED_OK = RESULT_MARKER_PREFIX + base64.b64encode(json.dumps({
    "present": True,
    "type": "json",
    "value": {
        "value": "42",
        "artifacts": [{"name": "spc.svg", "content_b64": base64.b64encode(b"<svg/>").decode(),
                       "mime_type": "image/svg+xml", "size": len(b"<svg/>")}],
    },
}).encode("utf-8")).decode("ascii")


def _provider(job_status="succeeded", logs=WRAPPED_OK):
    p = K8sProvider()
    p.namespace = "rag-sandbox"
    p.image = "img:1"
    p.timeout_max = 120
    p._batch = MagicMock()
    p._core = MagicMock()
    p._initialized = True
    status = SimpleNamespace(succeeded=1 if job_status == "succeeded" else None,
                             failed=1 if job_status == "failed" else None,
                             conditions=None)
    p._batch.read_namespaced_job.return_value = SimpleNamespace(status=status)
    pod = SimpleNamespace(metadata=SimpleNamespace(name="sbx-x-pod"),
                          status=SimpleNamespace(phase="Succeeded",
                                                 container_statuses=None))
    p._core.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])
    p._core.read_namespaced_pod_log.return_value = logs
    return p


def test_execute_success_parses_result_and_artifacts():
    p = _provider()
    r = p.execute_code("i", "def main():\n    return 42", "python", timeout=30)
    assert r.exit_code == 0
    assert r.metadata["artifacts"][0]["name"] == "spc.svg"
    p._batch.create_namespaced_job.assert_called_once()
    body = p._batch.create_namespaced_job.call_args
    assert (body.kwargs.get("namespace") or body.args[0]) == "rag-sandbox"
    p._batch.delete_namespaced_job.assert_called_once()  # cleanup


def test_ast_rejection_short_circuits():
    p = _provider()
    r = p.execute_code("i", "import socket\ndef main():\n    return 1", "python")
    assert r.exit_code == -999 and "unsafe" in r.stderr.lower()
    p._batch.create_namespaced_job.assert_not_called()


def test_nodejs_unsupported():
    p = _provider()
    r = p.execute_code("i", "function main(){}", "nodejs")
    assert r.exit_code != 0 and "python" in r.stderr.lower()
    p._batch.create_namespaced_job.assert_not_called()


def test_job_failure_returns_typed_error():
    p = _provider(job_status="failed", logs="Traceback ...")
    r = p.execute_code("i", "def main():\n    raise ValueError('x')", "python")
    assert r.exit_code != 0
    p._batch.delete_namespaced_job.assert_called_once()


def test_timeout_clamped_to_max():
    p = _provider()
    p.execute_code("i", "def main():\n    return 1", "python", timeout=9999)
    manifest = p._batch.create_namespaced_job.call_args.kwargs["body"]
    assert manifest["spec"]["activeDeadlineSeconds"] == 120


def test_oversized_logs_rejected():
    p = _provider(logs="x" * (10 * 1024 * 1024 + 1))
    r = p.execute_code("i", "def main():\n    return 1", "python")
    assert r.exit_code != 0 and "volum" in r.stderr.lower()


def test_initialize_without_lib_or_cluster_returns_false():
    p = K8sProvider()
    ok = p.initialize({"namespace": "rag-sandbox", "image": ""})  # image manquante
    assert ok is False


def test_execute_success_collects_artifacts_from_directory_when_main_returns_string():
    # Exercises the real directory-scan contract end-to-end: build the actual
    # wrapper via build_k8s_python_wrapper and run it in a real subprocess
    # (not a hand-forged fixture), so the fixture cannot drift from what the
    # implementation truly produces. main() writes a file under artifacts/
    # and returns a bare STRING (json.dumps(...)) -- matching the FAMAT
    # recipe shape (`fr.spc_chart(d, out_dir="artifacts")` then
    # `return json.dumps(d)`), which is exactly the shape Finding 1 flagged
    # as producing zero attachments under the old {"value","artifacts"}-dict
    # convention.
    code = (
        "import os, json\n"
        "def main():\n"
        "    os.makedirs('artifacts', exist_ok=True)\n"
        "    with open('artifacts/spc.svg', 'w') as f:\n"
        "        f.write('<svg/>')\n"
        "    return json.dumps({'ok': True})\n"
    )
    wrapper = build_k8s_python_wrapper(code, "{}")
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [sys.executable, "-c", wrapper], cwd=tmp, capture_output=True, text=True, timeout=10
        )
    assert proc.returncode == 0, proc.stderr

    p = _provider(logs=proc.stdout)
    # The code passed to execute_code here is deliberately unrelated to the
    # code that produced `proc.stdout` above -- execute_code never actually
    # runs `code` locally, it only builds the Job manifest from it and then
    # parses whatever the (mocked) pod logs contain, exactly like the other
    # tests in this file.
    r = p.execute_code("i", "def main():\n    return 1", "python")
    assert r.exit_code == 0
    assert r.metadata["result_value"] == json.dumps({"ok": True})
    assert len(r.metadata["artifacts"]) == 1
    artifact = r.metadata["artifacts"][0]
    assert artifact["name"] == "spc.svg"
    assert artifact["mime_type"] == "image/svg+xml"
    assert base64.b64decode(artifact["content_b64"]) == b"<svg/>"


def test_pending_image_pull_backoff_reports_actionable_error_immediately():
    p = _provider()
    # Job never reaches a terminal status -- only the Pending pod's waiting
    # reason should end the poll loop, and it must do so immediately (no 60s
    # wait), otherwise the default 10s per-call timeout would give up with a
    # generic timeout error before this actionable message is ever reached.
    status = SimpleNamespace(succeeded=None, failed=None, conditions=None)
    p._batch.read_namespaced_job.return_value = SimpleNamespace(status=status)
    waiting = SimpleNamespace(reason="ImagePullBackOff")
    container_status = SimpleNamespace(state=SimpleNamespace(waiting=waiting))
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="sbx-x-pod"),
        status=SimpleNamespace(phase="Pending", container_statuses=[container_status]),
    )
    p._core.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])

    r = p.execute_code("i", "def main():\n    return 1", "python", timeout=10)

    assert r.exit_code != 0
    assert "image inaccessible" in r.stderr.lower()
    p._batch.create_namespaced_job.assert_called_once()
    p._batch.delete_namespaced_job.assert_called_once()
