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
# distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

"""
Sandbox providers package.

This package contains:
- base.py: Base interface for all sandbox providers
- manager.py: Provider manager for managing active provider
- self_managed.py: Self-managed provider implementation (wraps existing executor_manager)
- aliyun_codeinterpreter.py: Aliyun Code Interpreter provider implementation
  Official Documentation: https://help.aliyun.com/zh/functioncompute/fc/sandbox-sandbox-code-interepreter
- e2b.py: E2B provider implementation
- local.py: Local process provider implementation
- ssh.py: Remote SSH provider implementation
"""

from .base import SandboxProvider, SandboxInstance, ExecutionResult, SandboxProviderConfigError
from .manager import ProviderManager

# CUSTOM B2B SaaS — providers en import PARESSEUX (PEP 562) : l'import eager
# chargeait les SDK cloud tiers de TOUS les providers dans les pods api dès le
# premier code_exec, même configuré k8s — dont agentrun (Alibaba Cloud), qui
# imprimait son warning bilingue dans les logs, pour un SDK jamais utilisé.
# Hygiène souveraineté + logs propres. `from ... import XxxProvider` continue
# de fonctionner à l'identique, l'import réel n'ayant lieu qu'à l'accès.
_PROVIDER_MODULES = {
    "SelfManagedProvider": ".self_managed",
    "AliyunCodeInterpreterProvider": ".aliyun_codeinterpreter",
    "E2BProvider": ".e2b",
    "LocalProvider": ".local",
    "SSHProvider": ".ssh",
}


def __getattr__(name):
    module_path = _PROVIDER_MODULES.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_path, __name__), name)

__all__ = [
    "SandboxProvider",
    "SandboxInstance",
    "ExecutionResult",
    "SandboxProviderConfigError",
    "ProviderManager",
    "SelfManagedProvider",
    "AliyunCodeInterpreterProvider",
    "E2BProvider",
    "LocalProvider",
    "SSHProvider",
]
