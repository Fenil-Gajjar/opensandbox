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
Isolake service interfaces.

Protocol definitions for sandbox services.
"""

from isolake.services.command import Commands
from isolake.services.diagnostics import Diagnostics
from isolake.services.egress import CredentialVault, Egress
from isolake.services.filesystem import Filesystem
from isolake.services.health import Health
from isolake.services.metrics import Metrics
from isolake.services.sandbox import Sandboxes

__all__ = [
    "Commands",
    "CredentialVault",
    "Diagnostics",
    "Egress",
    "Filesystem",
    "Health",
    "Metrics",
    "Sandboxes",
]
