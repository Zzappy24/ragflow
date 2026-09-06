#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
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
import logging
import os
import sys
import time
import uuid
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from quart import Blueprint, Quart, request, g, current_app, session, jsonify
from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer
from quart_cors import cors
from common.constants import StatusEnum, RetCode
from api.db.db_models import close_connection, APIToken
from api.db.services import UserService
from api.utils.json_encode import CustomJSONEncoder
from api.utils import commands

from quart_auth import Unauthorized as QuartAuthUnauthorized
from werkzeug.exceptions import Unauthorized as WerkzeugUnauthorized
from quart_schema import QuartSchema
from common import settings
from api.utils.api_utils import server_error_response, get_json_result
from api.constants import API_VERSION
from common.exceptions import ModelException
from common.misc_utils import get_uuid

settings.init_settings()

__all__ = ["app"]

UNAUTHORIZED_MESSAGE = "<Unauthorized '401: Unauthorized'>"


def _unauthorized_message(error):
    if error is None:
        return UNAUTHORIZED_MESSAGE

    description = getattr(error, "description", None)
    if description:
        return description

    try:
        return repr(error)
    except Exception:
        return UNAUTHORIZED_MESSAGE

app = Quart(__name__)
# expose_headers is critical: without it, quart_cors's after-request hook
# overwrites Access-Control-Expose-Headers to an empty string, which prevents
# browser JS from reading the Authorization header on the login/bridge
# responses — login appears to succeed at the HTTP layer but the frontend can
# never capture the session token, so the user silently bounces back to the
# login page. Chrome is permissive on localhost and masked this bug; Safari
# enforces the spec and surfaces it as "Fetch API cannot load ... due to
# access control checks". See construct_response in common/connection_utils.py.
_cors_origins_env = os.environ.get("CORS_ORIGINS", "")
_cors_allow_origin = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] or "*"
if _cors_allow_origin == "*":
    import logging as _logging_cors
    _logging_cors.warning("SECURITY: CORS_ORIGINS not set — allowing all origins. Set CORS_ORIGINS in production.")
app = cors(app, allow_origin=_cors_allow_origin, expose_headers=["Authorization"])

# openapi supported
QuartSchema(app)

app.url_map.strict_slashes = False
app.json_encoder = CustomJSONEncoder
app.errorhandler(Exception)(server_error_response)

# Configure Quart timeouts for slow LLM responses (e.g., local Ollama on CPU)
# Default Quart timeouts are 60 seconds which is too short for many LLM backends
app.config["RESPONSE_TIMEOUT"] = int(os.environ.get("QUART_RESPONSE_TIMEOUT", 600))
app.config["BODY_TIMEOUT"] = int(os.environ.get("QUART_BODY_TIMEOUT", 600))

## convince for dev and debug
# app.config["LOGIN_DISABLED"] = True
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "redis"
app.config["SESSION_REDIS"] = settings.decrypt_database_config(name="redis")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "true").lower() == "true"
app.config["MAX_CONTENT_LENGTH"] = int(
    os.environ.get("MAX_CONTENT_LENGTH", 128 * 1024 * 1024)
)
app.config['SECRET_KEY'] = settings.get_secret_key()
app.secret_key = settings.get_secret_key()
commands.register_commands(app)


# CUSTOM B2B SaaS — slow-request log : toute requête plus lente que
# SLOW_REQUEST_LOG_MS (défaut 500, 0 = désactivé) est loguée en WARNING avec
# sa durée — c'est la boussole pour savoir OÙ optimiser au lieu de deviner.
SLOW_REQUEST_LOG_MS = int(os.environ.get("SLOW_REQUEST_LOG_MS", "500"))

# CUSTOM B2B SaaS — plafond de session : le token signé (itsdangerous) était
# vérifié SANS max_age → session éternelle tant que l'access_token en base ne
# tournait pas (logout/re-login). Politique actée 2026-09-02 : access court
# (60 min côté panel), SESSION 7 JOURS, révocation serveur instantanée
# (rotation du access_token en base). 0 = désactivé (comportement historique).
SESSION_MAX_AGE_S = int(os.environ.get("RAGFLOW_SESSION_MAX_AGE_S", str(7 * 24 * 3600))) or None


@app.before_request
async def assign_request_id():
    g.request_id = str(uuid.uuid4())
    g._req_t0 = time.monotonic()
    logging.info("REQ %s %s %s", g.request_id, request.method, request.path)


@app.after_request
async def set_security_headers(response):
    t0 = getattr(g, "_req_t0", None)
    if SLOW_REQUEST_LOG_MS > 0 and t0 is not None:
        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms >= SLOW_REQUEST_LOG_MS:
            logging.warning(
                "SLOW %s %s %s -> %s in %.0fms",
                getattr(g, "request_id", ""), request.method, request.path,
                response.status_code, elapsed_ms,
            )
    response.headers["X-Request-Id"] = getattr(g, "request_id", "")
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self' *; "
        "frame-ancestors 'none';"
    )
    if os.environ.get("COOKIE_SECURE", "true").lower() == "true":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

from functools import wraps
from typing import ParamSpec, TypeVar
from collections.abc import Awaitable, Callable, Iterable
from werkzeug.local import LocalProxy

T = TypeVar("T")
P = ParamSpec("P")

AUTH_JWT = "JWT"
AUTH_API = "API"
AUTH_BETA = "BETA"
DEFAULT_AUTH_TYPES = (AUTH_JWT, AUTH_API)


def _normalize_auth_types(auth_types=None):
    if auth_types is None:
        return set(DEFAULT_AUTH_TYPES)
    if isinstance(auth_types, str):
        return {auth_types.upper()}
    if isinstance(auth_types, Iterable):
        return {str(auth_type).upper() for auth_type in auth_types}
    return {str(auth_types).upper()}


def _load_user_from_session():
    """Resolve the current user from the session cookie set by ``login_user()``.

    OAuth/OIDC callbacks call ``login_user(user)`` which writes ``_user_id``
    into the session. The frontend's response interceptor wipes the
    Authorization header from localStorage on the first 401, so post-redirect
    requests can arrive with no header at all — we still want to honour the
    server-side session in that window.

    The same access-token validity rules used by the JWT path are applied
    here so that tokens revoked by ``logout`` (which rewrites the column to
    ``INVALID_<hex>``) or shortened by data corruption can't keep a stale
    session authenticated.
    """
    user_id = session.get("_user_id")
    if not user_id:
        return None
    try:
        users = UserService.query(id=user_id, status=StatusEnum.VALID.value)
    except Exception:
        logging.exception("load_user from session failed")
        return None
    if not users:
        return None
    user = users[0]
    access_token = str(user.access_token or "").strip()
    # CUSTOM B2B SaaS — le cookie doit porter le token en vigueur : sans ce
    # lien, un cookie capturé restait valide 31 jours, après logout, re-login
    # et rotation (audit 2026-09-06). Cookie antérieur à cette règle = refus.
    if session.get("_access_token") != access_token:
        return None
    if not access_token or len(access_token) < 32 or access_token.startswith("INVALID_"):
        return None
    logging.debug("Authenticated request via session fallback for user_id=%s", user_id)
    g.auth_type = AUTH_JWT
    g.user = user
    return user


def _load_user(auth_types=None):
    # CUSTOM PERF: per-request negative cache. Upstream's positive cache below
    # (`if getattr(g, "user", None)`) only short-circuits AFTER successful
    # auth. Without `_user_resolved`, every `current_user` access on an
    # unauthenticated request re-runs the full JWT/Beta/API token chain.
    # Profiling showed 3× re-resolution per authenticated request (~30% of
    # total time) on hot endpoints. Cache the failure too.
    if getattr(g, "_user_resolved", False) and not getattr(g, "user", None):
        return None

    explicit_auth_types = auth_types is not None
    auth_types = _normalize_auth_types(auth_types)
    if getattr(g, "user", None) and (not explicit_auth_types or getattr(g, "auth_type", None) in auth_types):
        return g.user

    g._user_resolved = True

    # No Authorization header, try to load user from session cookie if JWT auth is allowed
    authorization = request.headers.get("Authorization")
    if not authorization:
        return _load_user_from_session() if AUTH_JWT in auth_types else None

    # Extract auth_token based on whether Authorization starts with "bearer" (case-insensitive)
    if authorization[:7].lower() == "bearer ":
        parts = authorization.split(maxsplit=1)
        if len(parts) < 2:
            logging.warning("Authorization header has invalid bearer format")
            return _load_user_from_session() if AUTH_JWT in auth_types else None
        auth_token = parts[1]
    else:
        auth_token = authorization

    g.user = None
    g.auth_type = None
    g.auth_error_message = None

    # Try Beta token
    if AUTH_BETA in auth_types:
        try:
            objs = APIToken.query(beta=auth_token)
            if objs:
                user = UserService.query(id=objs[0].tenant_id, status=StatusEnum.VALID.value)
                if user:
                    g.auth_type = AUTH_BETA
                    g.user = user[0]
                    # CUSTOM B2B SaaS — la ligne du jeton (dialog_id lié) sert aux
                    # routes beta pour borner ce que ce jeton PUBLIC peut atteindre
                    # (cf. api/utils/beta_scope.py, audit 2026-09-06).
                    g.beta_token = objs[0]
                    return user[0]
            g.auth_error_message = 'Authentication error: API key is invalid! '
        except Exception as e_beta:
            logging.warning(f"load_user from beta token got exception {e_beta}")
            g.auth_error_message = 'Authentication error: API key is invalid!'

    # Try JWT decoding
    if AUTH_JWT in auth_types:
        try:
            jwt = Serializer(secret_key=settings.get_secret_key())
            # max_age : au-delà, SignatureExpired → repli session/anonyme →
            # re-login. Cf. SESSION_MAX_AGE_S ci-dessus.
            access_token = str(jwt.loads(auth_token, max_age=SESSION_MAX_AGE_S))

            if not access_token or not access_token.strip():
                logging.warning("Authentication attempt with empty access token")
                return _load_user_from_session()

            if len(access_token.strip()) < 32:
                logging.warning(f"Authentication attempt with invalid token format: {len(access_token)} chars")
                return _load_user_from_session()

            user = UserService.query(access_token=access_token, status=StatusEnum.VALID.value)
            if user:
                if not user[0].access_token or not user[0].access_token.strip():
                    logging.warning(f"User {user[0].email} has empty access_token in database")
                    return _load_user_from_session()
                g.auth_type = AUTH_JWT
                g.user = user[0]
                return user[0]
            return _load_user_from_session()
        except Exception as e_jwt:
            logging.warning(f"load_user from jwt got exception {e_jwt}")

    # JWT decode failed, try as api_token
    if AUTH_API in auth_types:
        try:
            objs = APIToken.query(token=auth_token)
            if objs:
                # CUSTOM B2B SaaS — workspace tokens have tenant_id = workspace
                # tenant_id, NOT a user_id. Upstream's `UserService.query(id=tenant_id)`
                # then finds only the synthetic `ws-*@internal` user, which has no
                # ws_member rows and consequently fails every @require_permission
                # check. We resolve to the human creator stored in ApiKeyScope first
                # so the RBAC layer sees the real owner. Falls back to upstream
                # behavior for legacy/unscoped tokens.
                scope = None
                try:
                    from api.db.services.workspace_service import ApiKeyScopeService
                    scope = ApiKeyScopeService.get_by_token(auth_token)
                except Exception as e_scope:
                    logging.warning(f"load_user: ApiKeyScope resolution failed: {e_scope}")
                if scope is not None:
                    # CUSTOM B2B SaaS — clé scopée (audit 2026-09-06) : expiration et
                    # statut vérifiés ici aussi (avant, seul token_required les
                    # voyait → une clé expirée marchait sur toute l'API /api/v1) ;
                    # créateur invalide (désactivé, supprimé) = REFUS, jamais de
                    # repli sur l'utilisateur technique du tenant (la clé d'un
                    # ancien salarié devenait un jeton tenant-wide) ; permissions
                    # de la clé propagées à require_permission.
                    from datetime import datetime as _dt
                    if str(getattr(scope, "status", "1")) != "1":
                        logging.warning("load_user: API key disabled")
                        return None
                    if scope.expires_at and scope.expires_at < _dt.utcnow():
                        logging.warning("load_user: API key expired")
                        return None
                    user = UserService.query(id=scope.created_by, status=StatusEnum.VALID.value) if scope.created_by else []
                    if not user or not (user[0].access_token or "").strip() or str(getattr(user[0], "is_active", "1")) == "0":
                        logging.warning("load_user: API key creator invalid or inactive, refusing")
                        return None
                    g.auth_type = AUTH_API
                    g.user = user[0]
                    g.api_key_permissions = list(scope.permissions or [])
                    # Inject the workspace context so add_tenant_id_to_kwargs
                    # resolves to the workspace tenant (not the user's
                    # personal tenant). Mirrors what the X-Workspace-Id
                    # middleware does for browser/JWT requests.
                    if scope.workspace_id:
                        g._ws_header = scope.workspace_id
                        # Pre-resolve the workspace tenant_id so that
                        # `active_tenant_id()` returns the workspace tenant
                        # rather than the user's personal one.
                        g.active_tenant_id = objs[0].tenant_id
                    return user[0]

                # Fallback: legacy unscoped token → resolve via tenant_id (upstream)
                user = UserService.query(id=objs[0].tenant_id, status=StatusEnum.VALID.value)
                if user:
                    if not user[0].access_token or not user[0].access_token.strip():
                        logging.warning(f"User {user[0].email} has empty access_token in database")
                        return _load_user_from_session() if AUTH_JWT in auth_types else None
                    g.auth_type = AUTH_API
                    g.user = user[0]
                    return user[0]
                logging.warning(f"load_user: No user found for tenant_id={objs[0].tenant_id} from APIToken")
            else:
                logging.warning(f"load_user: No APIToken found for token={auth_token[:10]}...")
        except Exception as e_api_token:
            logging.warning(f"load_user from api token got exception {e_api_token}")

    return _load_user_from_session() if AUTH_JWT in auth_types else None


current_user = LocalProxy(_load_user)


def login_required(func: Callable[P, Awaitable[T]] = None, auth_types=None) -> Callable[P, Awaitable[T]]:
    """A decorator to restrict route access to authenticated users.

    This should be used to wrap a route handler (or view function) to
    enforce that only authenticated requests can access it. Note that
    it is important that this decorator be wrapped by the route
    decorator and not vice, versa, as below.

    .. code-block:: python

        @app.route('/')
        @login_required
        async def index():
            ...

    If the request is not authenticated a
    `quart.exceptions.Unauthorized` exception will be raised.

    """

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            timing_enabled = os.getenv("RAGFLOW_API_TIMING")
            t_start = time.perf_counter() if timing_enabled else None
            user = _load_user(auth_types)
            if timing_enabled:
                logging.info(
                    "api_timing login_required auth_ms=%.2f path=%s",
                    (time.perf_counter() - t_start) * 1000,
                    request.path,
                )
            if not user:  # or not session.get("_user_id"):
                if _normalize_auth_types(auth_types) == {AUTH_BETA}:
                    return get_json_result(
                        code=RetCode.DATA_ERROR,
                        message=getattr(g, "auth_error_message", None) or "Authorization is not valid!",
                    )
                raise QuartAuthUnauthorized()
            # CUSTOM: track active users (admin panel "active users" counter).
            try:
                from api.utils.api_utils import _track_active_user
                _track_active_user(user.id, getattr(user, "email", "") or "")
            except Exception:
                pass
            return await current_app.ensure_async(func)(*args, **kwargs)

        return wrapper

    if func is None:
        return decorator
    return decorator(func)


def login_user(user, remember=False, duration=None, force=False, fresh=True):
    """
    Logs a user in. You should pass the actual user object to this. If the
    user's `is_active` property is ``False``, they will not be logged in
    unless `force` is ``True``.

    This will return ``True`` if the login attempt succeeds, and ``False`` if
    it fails (i.e. because the user is inactive).

    :param user: The user object to log in.
    :type user: object
    :param remember: Whether to remember the user after their session expires.
        Defaults to ``False``.
    :type remember: bool
    :param duration: The amount of time before the remember cookie expires. If
        ``None`` the value set in the settings is used. Defaults to ``None``.
    :type duration: :class:`datetime.timedelta`
    :param force: If the user is inactive, setting this to ``True`` will log
        them in regardless. Defaults to ``False``.
    :type force: bool
    :param fresh: setting this to ``False`` will log in the user with a session
        marked as not "fresh". Defaults to ``True``.
    :type fresh: bool
    """
    if not force and not user.is_active:
        return False

    session["_user_id"] = user.id
    session["_fresh"] = fresh
    session["_id"] = get_uuid()
    # CUSTOM B2B SaaS — lien cookie ↔ access_token (cf. _load_user_from_session).
    session["_access_token"] = str(getattr(user, "access_token", "") or "").strip()
    return True


def logout_user():
    """
    Logs a user out. (You do not need to pass the actual user.) This will
    also clean up the remember me cookie if it exists.
    """
    if "_user_id" in session:
        session.pop("_user_id")

    if "_fresh" in session:
        session.pop("_fresh")

    if "_id" in session:
        session.pop("_id")
    session.pop("_access_token", None)

    COOKIE_NAME = "remember_token"
    cookie_name = current_app.config.get("REMEMBER_COOKIE_NAME", COOKIE_NAME)
    if cookie_name in request.cookies:
        session["_remember"] = "clear"
        if "_remember_seconds" in session:
            session.pop("_remember_seconds")

    return True


def search_pages_path(page_path):
    app_path_list = [
        path for path in page_path.glob("*_app.py") if not path.name.startswith(".")
    ]
    api_path_list = [
        path for path in page_path.glob("*sdk/*.py") if not path.name.startswith(".")
    ]
    app_path_list.extend(api_path_list)
    restful_api_path_list = [
        path for path in page_path.glob("*restful_apis/*.py") if not path.name.startswith(".")
    ]
    app_path_list.extend(restful_api_path_list)
    return app_path_list


def register_page(page_path):
    path = f"{page_path}"

    page_name = page_path.stem.removesuffix("_app")
    module_name = ".".join(
        page_path.parts[page_path.parts.index("api"): -1] + (page_name,)
    )

    spec = spec_from_file_location(module_name, page_path)
    page = module_from_spec(spec)
    page.app = app
    page.manager = Blueprint(page_name, module_name)
    sys.modules[module_name] = page
    spec.loader.exec_module(page)
    page_name = getattr(page, "page_name", page_name)
    restful_api_path = "\\restful_apis\\" if sys.platform.startswith("win") else "/restful_apis/"
    # CUSTOM B2B SaaS: SDK pages also belong under /api/{API_VERSION}. Upstream
    # only checks restful_api_path; auto-merge dropped our sdk_path definition.
    # Restore it so SDK blueprints register at /api/v1/* like restful ones.
    sdk_path = "\\sdk\\" if sys.platform.startswith("win") else "/sdk/"
    url_prefix = (
        f"/api/{API_VERSION}" if sdk_path in path or restful_api_path in path else f"/{API_VERSION}/{page_name}"
    )

    app.register_blueprint(page.manager, url_prefix=url_prefix)
    return url_prefix


pages_dir = [
    Path(__file__).parent,
    Path(__file__).parent.parent / "api" / "apps",
    Path(__file__).parent.parent / "api" / "apps" / "restful_apis",
    Path(__file__).parent.parent / "api" / "apps" / "sdk",
]

client_urls_prefix = [
    register_page(path) for directory in pages_dir for path in search_pages_path(directory)
]

# Register backward compatibility routes for deprecated APIs
from api.apps.backward_compat import register_backward_compat_routes
register_backward_compat_routes(app)


@app.errorhandler(404)
async def not_found(error):
    logging.error(f"The requested URL {request.path} was not found")
    message = f"Not Found: {request.path}"
    response = {
        "code": RetCode.NOT_FOUND,
        "message": message,
        "data": None,
        "error": "Not Found",
    }
    return jsonify(response), RetCode.NOT_FOUND


@app.errorhandler(401)
async def unauthorized(error):
    logging.warning("Unauthorized request")
    return get_json_result(code=RetCode.UNAUTHORIZED, message=_unauthorized_message(error)), RetCode.UNAUTHORIZED


@app.errorhandler(QuartAuthUnauthorized)
async def unauthorized_quart_auth(error):
    logging.warning("Unauthorized request (quart_auth)")
    return get_json_result(code=RetCode.UNAUTHORIZED, message=repr(error)), RetCode.UNAUTHORIZED


@app.errorhandler(WerkzeugUnauthorized)
async def unauthorized_werkzeug(error):
    logging.warning("Unauthorized request (werkzeug)")
    return get_json_result(code=error.code, message=error.description), RetCode.UNAUTHORIZED


@app.errorhandler(ModelException)
async def handle_model_exception(error):
    logging.warning("Forbidden request")
    return get_json_result(code=RetCode.BAD_REQUEST, message=repr(error)), 200

@app.teardown_request
def _db_close(exception):
    if exception:
        logging.exception(f"Request failed: {exception}")
    close_connection()
