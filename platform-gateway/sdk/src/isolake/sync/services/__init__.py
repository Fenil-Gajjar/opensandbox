#
# Copyright 2025 Alibaba Group Holding Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""
Synchronous service interfaces (Protocols) for the sync SDK.
"""

from isolake.sync.services.command import CommandsSync
from isolake.sync.services.diagnostics import DiagnosticsSync
from isolake.sync.services.egress import CredentialVaultSync, EgressSync
from isolake.sync.services.filesystem import FilesystemSync
from isolake.sync.services.health import HealthSync
from isolake.sync.services.metrics import MetricsSync
from isolake.sync.services.sandbox import SandboxesSync

__all__ = [
    "CommandsSync",
    "CredentialVaultSync",
    "DiagnosticsSync",
    "EgressSync",
    "FilesystemSync",
    "HealthSync",
    "MetricsSync",
    "SandboxesSync",
]
