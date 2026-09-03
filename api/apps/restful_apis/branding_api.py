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

GET /api/v1/branding — renvoie le logo (data-URI base64) et la couleur
d'accent de l'organisation du workspace actif. Le front applique la
couleur sur ``--accent-primary`` et le logo dans le header après login ;
la page de login reste toujours aux couleurs Cyllene (produit Cyllene).

Tenant personnel (pas de workspace actif) ou org sans branding → objet
vide, le front garde le thème Cyllene par défaut. L'édition se fait dans
le panel admin (``management/server/routers/orgs.py`` — routes branding).
"""

from api.apps import login_required
from api.db.db_models import Organisation, Workspace
from api.utils.api_utils import get_json_result, server_error_response
from api.utils.tenant_context import active_tenant_id


@manager.route("/branding", methods=["GET"])  # noqa: F821
@login_required
async def get_branding():
    try:
        tenant_id = active_tenant_id()
        ws = Workspace.get_or_none(Workspace.tenant_id == tenant_id)
        if not ws:
            return get_json_result(data={})
        org = Organisation.get_or_none(Organisation.id == ws.org_id)
        if not org or getattr(org, "status", "1") != "1":
            return get_json_result(data={})
        return get_json_result(
            data={
                "logo": org.logo or None,
                "brand_color": org.brand_color or None,
                "org_name": org.name,
            }
        )
    except Exception as e:
        return server_error_response(e)
