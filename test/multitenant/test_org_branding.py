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
        # bannière d'accueil par organisation (2026-09-07)
        assert hasattr(Organisation, "banner_mode")
        assert hasattr(Organisation, "banner")

    def test_banner_mode_and_image_validated(self):
        from management.server.routers.orgs import validate_branding

        png = "data:image/png;base64,iVBORw0KGgo="
        assert validate_branding(None, None, banner=png, banner_mode="org") is None
        assert validate_branding(None, None, banner_mode="cyllene") is None
        assert validate_branding(None, None, banner_mode="blanche") is not None, "mode inconnu accepté"
        assert validate_branding(None, None, banner="data:text/html;base64,PHNjcmlwdD4=") is not None, "bannière non image acceptée"
        assert validate_branding(None, None, banner="data:image/png;base64," + "A" * 1_500_000) is not None, "bannière sans limite de taille"

    def test_banner_wired_end_to_end(self):
        """Panel (saisie) → API produit (lecture) → front (rendu)."""
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[2]
        api_src = (root / "api/apps/restful_apis/branding_api.py").read_text()
        assert '"banner_mode"' in api_src and '"banner"' in api_src
        panel = (root / "management/web/src/pages/organisations/branding-card.tsx").read_text()
        assert "banner_mode" in panel and "Bannière d'accueil" in panel
        front = (root / "web/src/pages/home/banner.tsx").read_text()
        assert "useFetchOrgBranding" in front and "banner_mode === 'org'" in front
        assert "banner-cyllene.jpg" in front, "la montagne Cyllene doit rester le défaut"


class TestBrandingNeverForcesLogout:
    """CUSTOM B2B SaaS — incident 2026-09-05 : boucle de login.

    GET /api/v1/branding est appelé sur CHAQUE page (useApplyOrgBranding),
    parfois avant que le front n'ait épinglé un workspace (login /login,
    navigation privée, session fraîche). L'intercepteur front transforme
    tout 401 en déconnexion → boucle. Un endpoint cosmétique ne doit donc
    JAMAIS lever 401 faute de workspace : il doit renvoyer {}.

    Contrat épinglé statiquement : la route utilise maybe_active_tenant_id
    (renvoie None) et non active_tenant_id (lève Unauthorized).
    """

    def test_branding_route_uses_non_raising_tenant_resolver(self):
        import pathlib

        src = pathlib.Path(
            pathlib.Path(__file__).resolve().parents[2]
            / "api/apps/restful_apis/branding_api.py"
        ).read_text()
        assert "maybe_active_tenant_id" in src, (
            "branding doit utiliser maybe_active_tenant_id (renvoie None)"
        )
        # active_tenant_id() lève 401 → déconnexion en boucle. Interdit ici.
        # (maybe_active_tenant_id contient la sous-chaîne active_tenant_id,
        # donc on vérifie l'absence d'un APPEL nu active_tenant_id().)
        assert "= active_tenant_id()" not in src and "return active_tenant_id()" not in src, (
            "branding ne doit pas appeler active_tenant_id() (lève 401)"
        )
