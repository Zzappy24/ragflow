"""CUSTOM B2B SaaS — DA par organisation : épingle le contrat de validation.

La fonction pure ``validate_branding`` (management/server/routers/orgs.py)
est la seule barrière entre un org_admin et la colonne Organisation.logo /
brand_color servie telle quelle au front produit. Ces tests garantissent
qu'un merge ou un refactor ne relâche pas le format (data-URI image only,
taille bornée, #rrggbb strict).
"""

import pytest

from management.server.routers.orgs import (
    _BRANDING_LOGO_MAX_BYTES,
    validate_branding,
)


class TestValidateBranding:
    def test_empty_values_are_valid_meaning_reset(self):
        assert validate_branding(None, None) is None
        assert validate_branding("", "") is None

    def test_valid_logo_and_color(self):
        logo = "data:image/png;base64,iVBORw0KGgo="
        assert validate_branding(logo, "#2d6dbb") is None
        assert validate_branding(logo, "#FFFFFF") is None

    @pytest.mark.parametrize(
        "logo",
        [
            "https://evil.example/logo.png",  # URL externe — jamais
            "data:text/html;base64,PHNjcmlwdD4=",  # pas une image
            "data:image/gif;base64,R0lGOD",  # mime non whitelisté
            "iVBORw0KGgo=",  # base64 nu sans data-URI
        ],
    )
    def test_bad_logo_rejected(self, logo):
        assert validate_branding(logo, None) is not None

    def test_oversized_logo_rejected(self):
        logo = "data:image/png;base64," + "A" * _BRANDING_LOGO_MAX_BYTES
        assert validate_branding(logo, None) is not None

    @pytest.mark.parametrize(
        "color",
        ["2d6dbb", "#2d6db", "#2d6dbb00", "rgb(1,2,3)", "#zzzzzz", "bleu"],
    )
    def test_bad_color_rejected(self, color):
        assert validate_branding(None, color) is not None

    def test_organisation_model_has_branding_columns(self):
        from api.db.db_models import Organisation

        assert hasattr(Organisation, "logo")
        assert hasattr(Organisation, "brand_color")
