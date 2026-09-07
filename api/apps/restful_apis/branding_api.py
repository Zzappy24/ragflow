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
"""
CUSTOM B2B SaaS — DA (identité visuelle) par organisation.

GET /api/v1/branding — logo (data-URI) + couleur d'accent de l'organisation
du workspace actif. Appelé à CHAQUE chargement de page par le hook
``useApplyOrgBranding`` : donc chemin CHAUD.

Deux garde-fous perf (incident starvation 2026-09-05) :
  1. Les 2 lookups Peewee sont SYNCHRONES — offloadés hors de l'event loop
     async via ``thread_pool_exec`` (un appel DB sync dans une coroutine
     fige l'event loop du worker et, multiplié par la fréquence de cet
     endpoint, contribue à la starvation).
  2. Cache mémoire par tenant (TTL court) : le branding ne change qu'à une
     édition panel, inutile de taper la DB à chaque page. Positifs ET
     absences cachés (une org sans branding renvoie {} tout aussi souvent).
"""

import os
import time

from api.apps import login_required
from api.db.db_models import Organisation, Workspace
from api.utils.api_utils import get_json_result, server_error_response
from api.utils.tenant_context import maybe_active_tenant_id
from common.misc_utils import thread_pool_exec

_BRANDING_CACHE: dict[str, tuple[dict, float]] = {}
_BRANDING_TTL_S = float(os.environ.get("BRANDING_CACHE_TTL_S", "60"))


def _load_branding(tenant_id: str) -> dict:
    """Lookup SYNCHRONE (Peewee) — appelé dans un thread, jamais sur l'event loop."""
    ws = Workspace.get_or_none(Workspace.tenant_id == tenant_id)
    if not ws:
        return {}
    org = Organisation.get_or_none(Organisation.id == ws.org_id)
    if not org or getattr(org, "status", "1") != "1":
        return {}
    return {
        "logo": org.logo or None,
        "brand_color": org.brand_color or None,
        "org_name": org.name,
        # bannière d'accueil (2026-09-07) : "org" = bannière de l'organisation
        "banner_mode": getattr(org, "banner_mode", None) or "cyllene",
        "banner": getattr(org, "banner", None) or None,
    }


@manager.route("/branding", methods=["GET"])  # noqa: F821
@login_required
async def get_branding():
    try:
        # maybe_active_tenant_id() renvoie None sans lever d'erreur : le
        # branding est cosmétique et appelé sur CHAQUE page (useApplyOrgBranding),
        # y compris juste après un login /login qui n'a pas encore épinglé de
        # workspace. active_tenant_id() lèverait un 401 → l'intercepteur front
        # transforme tout 401 en déconnexion → boucle de login (incident
        # 2026-09-05, sessions fraîches / navigation privée). Pas de workspace
        # = pas de branding d'org, on renvoie {} et le thème Cyllene par défaut.
        tenant_id = maybe_active_tenant_id()
        if not tenant_id:
            return get_json_result(data={})

        cached = _BRANDING_CACHE.get(tenant_id)
        if cached and time.monotonic() - cached[1] < _BRANDING_TTL_S:
            return get_json_result(data=cached[0])

        data = await thread_pool_exec(_load_branding, tenant_id)
        _BRANDING_CACHE[tenant_id] = (data, time.monotonic())
        return get_json_result(data=data)
    except Exception as e:
        return server_error_response(e)
