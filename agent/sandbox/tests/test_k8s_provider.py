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
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent.sandbox.providers.k8s import K8sProvider
from agent.sandbox.result_protocol import RESULT_MARKER_PREFIX

# NOTE: adapted from the brief's literal fixture. `build_python_wrapper` /
# `extract_structured_result` (agent/sandbox/result_protocol.py) actually wrap
# main()'s return value as one `__RAGFLOW_RESULT__:<base64>` log line carrying
# {"present": bool, "value": <return value>, "type": "json"} -- there is no
# top-level "__sandbox_result__"/"artifacts" envelope. K8sProvider has no
# filesystem to scan for artifacts after the ephemeral pod is deleted (unlike
# LocalProvider/SSHProvider), so the convention here is: main() may return a
# dict shaped {"value": ..., "artifacts": [...]} and K8sProvider promotes the
# nested "artifacts" list into ExecutionResult.metadata["artifacts"]. Artifact
# dicts themselves follow the real `ArtifactItem` schema
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
