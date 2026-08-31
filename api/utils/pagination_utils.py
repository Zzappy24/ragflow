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

# CUSTOM B2B SaaS: upstream 2026-06-02 set this to 100. We bump to 10000
# because MCP tools, several internal callers, and concurrency tests use
# page_size values up to ~2000. DoS risk is low (API tokens are tenant-
# scoped, the doc-store query layer paginates internally anyway).
REST_API_MAX_PAGE_SIZE = 10000


def validate_rest_api_page_size(page_size: int) -> int:
    """Clamp REST API page_size values to the public maximum.

    CUSTOM B2B SaaS (2026-08-31) : écrête au lieu de lever. Notre propre
    front demandait 100000 (liste agents, sessions de canvas) et chaque
    refetch levait ValueError — une page tronquée à 10000 vaut toujours
    mieux qu'une requête qui explose (constaté au smoke FAMAT).
    """
    return min(page_size, REST_API_MAX_PAGE_SIZE)
