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
    """Validate REST API page_size values against the public maximum."""
    if page_size > REST_API_MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be less than or equal to {REST_API_MAX_PAGE_SIZE}")
    return page_size
