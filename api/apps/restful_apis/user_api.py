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
import string
import os
import re
import secrets
import time
from datetime import datetime
import base64

from quart import make_response, redirect, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from api.apps.auth import get_auth_client
from api.db import FileType, UserTenantRole
from api.db.services.file_service import FileService
from api.db.services.user_service import TenantService, UserService, UserTenantService
from common.time_utils import current_timestamp, datetime_format, get_format_time
from common.misc_utils import download_img, get_uuid
from common.constants import RetCode
from common.connection_utils import construct_response
from api.utils.api_utils import (
    get_data_error_result,
    get_json_result,
    get_request_json,
    server_error_response,
    validate_request,
)
from api.utils.nickname_validation import validate_nickname
from api.utils.crypt import decrypt
from api.utils.internal_secret import check_internal_secret as _check_internal_secret  # CUSTOM B2B SaaS
from rag.utils.redis_conn import REDIS_CONN
from api.apps import login_required, current_user, login_user, logout_user
from api.apps.extensions.rbac import require_permission, Permission
from api.utils.web_utils import (
    send_email_html,
    OTP_LENGTH,
    OTP_TTL_SECONDS,
    ATTEMPT_LIMIT,
    ATTEMPT_LOCK_SECONDS,
    RESEND_COOLDOWN_SECONDS,
    otp_keys,
    hash_code,
    captcha_key,
)
from common import settings


@manager.route("/auth/login", methods=["POST"])  # noqa: F821
async def login():
    """
    User login endpoint.
    ---
    tags:
      - User
    parameters:
      - in: body
        name: body
        description: Login credentials.
        required: true
        schema:
          type: object
          properties:
            email:
              type: string
              description: User email.
            password:
              type: string
              description: User password.
    responses:
      200:
        description: Login successful.
        schema:
          type: object
      401:
        description: Authentication failed.
        schema:
          type: object
    """
    json_body = await get_request_json()
    if not json_body:
        logging.warning("Login failed: invalid or empty JSON body")
        return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message="Unauthorized!")

    email = json_body.get("email", "")

    _rl_key = f"login_attempt:{email}"
    _rl_limit = 10
    try:
        _attempts = REDIS_CONN.REDIS.incr(_rl_key)
        if _attempts == 1:
            REDIS_CONN.REDIS.expire(_rl_key, 3600)
        if _attempts > _rl_limit:
            return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message="Too many login attempts. Try again in 1 hour.")
    except Exception:
        pass

    users = UserService.query(email=email)
    if not users:
        logging.warning("Login failed: email not registered")
        return get_json_result(
            data=False,
            code=RetCode.AUTHENTICATION_ERROR,
            message=f"Email: {email} is not registered!",
        )

    password = json_body.get("password")
    try:
        password = decrypt(password)
    except BaseException:
        logging.warning("Login failed: password decryption error")
        return get_json_result(data=False, code=RetCode.SERVER_ERROR, message="Fail to crypt password")

    user = UserService.query_user(email, password)

    if user and hasattr(user, 'is_active') and user.is_active == "0":
        logging.warning("Login failed: disabled account for user_id=%s", user.id)
        return get_json_result(
            data=False,
            code=RetCode.FORBIDDEN,
            message="This account has been disabled, please contact the administrator!",
        )
    elif user:
        # CUSTOM B2B SaaS: clear login-attempt rate-limit counter on success
        # (the counter is incremented in REDIS_CONN at line ~105 above).
        try:
            REDIS_CONN.REDIS.delete(_rl_key)
        except Exception:
            pass
        user.access_token = get_uuid()
        login_user(user)
        user.last_login_time = get_format_time()
        user.update_time = current_timestamp()
        user.update_date = datetime_format(datetime.now())
        user.save()
        logging.info("Login successful: user_id=%s", user.id)
        msg = "Welcome back!"

        return await construct_response(data=user.to_safe_dict(for_self=True), auth=user.get_id(), message=msg)
    else:
        logging.warning("Login failed: wrong credentials")
        return get_json_result(
            data=False,
            code=RetCode.AUTHENTICATION_ERROR,
            message="Email and password do not match!",
        )


@manager.route("/internal/bridge/prepare", methods=["POST"])  # noqa: F821
async def internal_bridge_prepare():
    # CUSTOM B2B SaaS — route service-à-service (panel → api) : fabrique un code
    # de connexion pour N'IMPORTE QUEL user_id. Sans le secret partagé, c'était
    # une usurpation de compte ouverte à quiconque connaît un user_id
    # (constaté 2026-09-06). Cf. api/utils/internal_secret.py.
    ok_secret, err = _check_internal_secret()
    if not ok_secret:
        return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message=err)
    json_body = await get_request_json() or {}
    user_id = (json_body.get("user_id") or "").strip()
    ws_id = (json_body.get("ws_id") or "").strip()
    if not (user_id and ws_id):
        return get_json_result(data=False, code=RetCode.ARGUMENT_ERROR, message="Missing user_id or ws_id")

    ok_user, user = UserService.get_by_id(user_id)
    if not ok_user or not user:
        return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message="User not found")
    if hasattr(user, "is_active") and user.is_active == "0":
        return get_json_result(data=False, code=RetCode.FORBIDDEN, message="Account disabled")

    from api.db.services.workspace_service import WorkspaceService, WsMemberService
    ok_ws, ws = WorkspaceService.get_by_id(ws_id)
    if not ok_ws or not ws or ws.status != "1":
        return get_json_result(data=False, code=RetCode.FORBIDDEN, message="Workspace not found or disabled")
    membership = WsMemberService.get_membership(ws_id, user_id)
    if not membership:
        is_super = bool(getattr(user, "is_superuser", False))
        is_org_admin = False
        if not is_super:
            try:
                from api.db.services.org_service import OrgMemberService
                om = OrgMemberService.get_membership(ws.org_id, user_id)
                is_org_admin = bool(om and om.role == "org_admin")
            except Exception:
                pass
        if not (is_super or is_org_admin):
            return get_json_result(data=False, code=RetCode.FORBIDDEN, message="Not a workspace member")

    import json as _json
    code = secrets.token_urlsafe(32)
    try:
        REDIS_CONN.REDIS.set(f"bridge_code:{code}", _json.dumps({"user_id": user_id, "ws_id": ws_id}), ex=30)
    except Exception as e:
        logging.exception("bridge prepare: Redis SET failed: %s", e)
        return get_json_result(data=False, code=RetCode.SERVER_ERROR, message="Failed to create bridge code")

    return get_json_result(data={"code": code})


@manager.route("/internal/invite/prepare", methods=["POST"])  # noqa: F821
async def internal_invite_prepare():
    # CUSTOM B2B SaaS — même garde que internal_bridge_prepare : un code
    # d'invitation permet de POSER le mot de passe du compte visé.
    ok_secret, err = _check_internal_secret()
    if not ok_secret:
        return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message=err)
    json_body = await get_request_json() or {}
    user_id = (json_body.get("user_id") or "").strip()
    ttl = int(json_body.get("ttl", 60 * 60 * 48))
    if not user_id:
        return get_json_result(data=False, code=RetCode.ARGUMENT_ERROR, message="Missing user_id")

    ok_user, user = UserService.get_by_id(user_id)
    if not ok_user or not user:
        return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message="User not found")

    # CUSTOM B2B SaaS — pas de code d'invitation pour un compte déjà actif
    # (cf. set_initial_password : ce serait un changement de mot de passe).
    if str(getattr(user, "is_active", "0")) == "1":
        return get_json_result(data=False, code=RetCode.FORBIDDEN, message="Account already active")

    code = secrets.token_urlsafe(32)
    try:
        REDIS_CONN.REDIS.set(f"invite_code:{code}", user_id, ex=ttl)
    except Exception as e:
        logging.exception("invite prepare: Redis SET failed: %s", e)
        return get_json_result(data=False, code=RetCode.SERVER_ERROR, message="Failed to create invite code")

    return get_json_result(data={"code": code})


@manager.route("/bridge", methods=["POST"])  # noqa: F821
async def bridge_login():
    import json as _json
    json_body = await get_request_json() or {}
    code = (json_body.get("code") or "").strip()
    if not code:
        return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message="Missing bridge code")

    try:
        raw = REDIS_CONN.REDIS.getdel(f"bridge_code:{code}")
    except Exception as e:
        logging.exception("bridge login: Redis GETDEL failed: %s", e)
        return get_json_result(data=False, code=RetCode.SERVER_ERROR, message="Bridge exchange failed")

    if not raw:
        return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message="Invalid or expired bridge code")

    try:
        payload = _json.loads(raw)
        user_id = payload["user_id"]
        ws_id = payload["ws_id"]
    except Exception:
        return get_json_result(data=False, code=RetCode.SERVER_ERROR, message="Malformed bridge payload")

    ok_user, user = UserService.get_by_id(user_id)
    if not ok_user or not user:
        return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message="User not found")
    if hasattr(user, "is_active") and user.is_active == "0":
        return get_json_result(data=False, code=RetCode.FORBIDDEN, message="Account disabled")

    # CUSTOM B2B SaaS — jamais un User brut dans une réponse : to_dict()/to_json()
    # exposaient le hash du mot de passe ET l'access_token (secret derrière le
    # token signé). to_safe_dict(for_self=True) les retire, garde l'email.
    response_data = dict(user.to_safe_dict(for_self=True))
    response_data["active_workspace_id"] = ws_id
    user.access_token = get_uuid()
    login_user(user)
    user.update_time = current_timestamp()
    user.update_date = datetime_format(datetime.now())
    user.save()
    return await construct_response(data=response_data, auth=user.get_id(), message="Bridge login successful")


@manager.route("/set_initial_password", methods=["POST"])  # noqa: F821
async def set_initial_password():
    json_body = await get_request_json() or {}
    code = (json_body.get("code") or "").strip()
    raw_password = json_body.get("password")
    if not code or not raw_password:
        return get_json_result(
            data=False, code=RetCode.ARGUMENT_ERROR, message="Missing code or password"
        )

    try:
        password = decrypt(raw_password)
    except BaseException:
        return get_json_result(
            data=False, code=RetCode.SERVER_ERROR, message="Fail to crypt password"
        )

    if not isinstance(password, str) or len(password) < 8:
        return get_json_result(
            data=False,
            code=RetCode.ARGUMENT_ERROR,
            message="Password must be at least 8 characters",
        )

    try:
        raw_user_id = REDIS_CONN.REDIS.getdel(f"invite_code:{code}")
    except Exception as e:
        logging.exception("set_initial_password: Redis GETDEL failed: %s", e)
        return get_json_result(data=False, code=RetCode.SERVER_ERROR, message="Invite exchange failed")
    if not raw_user_id:
        return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message="Invalid or expired invite code")
    user_id = raw_user_id.decode() if isinstance(raw_user_id, bytes) else raw_user_id

    ok_user, user = UserService.get_by_id(user_id)
    if not ok_user or not user:
        return get_json_result(
            data=False, code=RetCode.AUTHENTICATION_ERROR, message="User not found"
        )
    # CUSTOM B2B SaaS — un code d'invitation ne sert qu'à ACTIVER un compte.
    # Sur un compte déjà actif il poserait un nouveau mot de passe : prise de
    # contrôle si le lien fuit ou est renvoyé par un tiers (audit 2026-09-06,
    # F1). Le mot de passe oublié a son propre flux (OTP par email).
    if str(getattr(user, "is_active", "0")) == "1":
        return get_json_result(data=False, code=RetCode.FORBIDDEN, message="Account already active")

    # Flip is_active → 1, store the real password hash, mint a fresh session
    # token, and log the user in. Copy to a new dict before mutating to avoid
    # clobbering peewee's internal __data__ reference (same gotcha the bridge
    # endpoint hit — see comment there).
    user.password = generate_password_hash(password)
    user.is_active = "1"
    user.access_token = get_uuid()
    user.update_time = current_timestamp()
    user.update_date = datetime_format(datetime.now())
    user.save()

    # CUSTOM B2B SaaS — jamais un User brut dans une réponse (cf. bridge_login).
    response_data = dict(user.to_safe_dict(for_self=True))

    # --- CYLLENE CUSTOM CODE ---
    # Resolve the user's default workspace so the frontend can set
    # X-Workspace-Id immediately after login (no personal-tenant fallback).
    try:
        from api.db.services.workspace_service import WsMemberService, WorkspaceService
        memberships = WsMemberService.list_workspaces_for_user(user_id)
        default_ws_id = None
        if memberships:
            ok, ws = WorkspaceService.get_by_id(memberships[0].workspace_id)
            if ok and ws and ws.status == "1":
                default_ws_id = ws.id
        response_data["default_workspace_id"] = default_ws_id
    except Exception:
        response_data["default_workspace_id"] = None
    # --- END CYLLENE CUSTOM CODE ---

    login_user(user)
    return await construct_response(
        data=response_data,
        auth=user.get_id(),
        message="Password set, welcome aboard!",
    )


@manager.route("/login/channels", methods=["GET"])  # noqa: F821
async def get_login_channels():
    """
    Get all supported authentication channels.
    """
    try:
        channels = []
        for channel, config in settings.OAUTH_CONFIG.items():
            channels.append(
                {
                    "channel": channel,
                    "display_name": config.get("display_name", channel.title()),
                    "icon": config.get("icon", "sso"),
                }
            )
        return get_json_result(data=channels)
    except Exception as e:
        logging.exception(e)
        return get_json_result(data=[], message=f"Load channels failure, error: {str(e)}", code=RetCode.EXCEPTION_ERROR)


@manager.route("/auth/login/<channel>", methods=["GET"])  # noqa: F821
async def oauth_login(channel):
    channel_config = settings.OAUTH_CONFIG.get(channel)
    if not channel_config:
        raise ValueError(f"Invalid channel name: {channel}")
    auth_cli = get_auth_client(channel_config)

    state = get_uuid()
    session["oauth_state"] = state
    auth_url = auth_cli.get_authorization_url(state)
    logging.info("OAuth login initiated: channel='%s', state='%s'", channel, state)
    return redirect(auth_url)


@manager.route("/auth/oauth/<channel>/callback", methods=["GET"])  # noqa: F821
async def oauth_callback(channel):
    """
    Handle the OAuth/OIDC callback for various channels dynamically.
    """
    try:
        channel_config = settings.OAUTH_CONFIG.get(channel)
        if not channel_config:
            raise ValueError(f"Invalid channel name: {channel}")
        auth_cli = get_auth_client(channel_config)

        # Check the state
        state = request.args.get("state")
        if not state or state != session.get("oauth_state"):
            return redirect("/?error=invalid_state")
        session.pop("oauth_state", None)

        # Obtain the authorization code
        code = request.args.get("code")
        if not code:
            return redirect("/?error=missing_code")

        # Exchange authorization code for access token
        if hasattr(auth_cli, "async_exchange_code_for_token"):
            token_info = await auth_cli.async_exchange_code_for_token(code)
        else:
            token_info = auth_cli.exchange_code_for_token(code)
        access_token = token_info.get("access_token")
        if not access_token:
            return redirect("/?error=token_failed")

        id_token = token_info.get("id_token")

        # Fetch user info
        if hasattr(auth_cli, "async_fetch_user_info"):
            user_info = await auth_cli.async_fetch_user_info(access_token, id_token=id_token)
        else:
            user_info = auth_cli.fetch_user_info(access_token, id_token=id_token)
        if not user_info.email:
            return redirect("/?error=email_missing")

        # Login or register
        users = UserService.query(email=user_info.email)
        user_id = get_uuid()

        if not users:
            try:
                try:
                    avatar = await download_img(user_info.avatar_url)
                except Exception as e:
                    logging.exception(e)
                    avatar = ""

                users = user_register(
                    user_id,
                    {
                        "access_token": get_uuid(),
                        "email": user_info.email,
                        "avatar": avatar,
                        "nickname": user_info.nickname,
                        "login_channel": channel,
                        "last_login_time": get_format_time(),
                        "is_superuser": False,
                    },
                )

                if not users:
                    raise Exception(f"Failed to register {user_info.email}")
                if len(users) > 1:
                    raise Exception(f"Same email: {user_info.email} exists!")

                # Try to log in
                user = users[0]
                login_user(user)
                return redirect(f"/?auth={user.get_id()}")

            except Exception as e:
                rollback_user_registration(user_id)
                logging.exception(e)
                return redirect(f"/?error={str(e)}")

        # User exists, try to log in
        user = users[0]
        user.access_token = get_uuid()
        if user and hasattr(user, 'is_active') and user.is_active == "0":
            return redirect("/?error=user_inactive")

        login_user(user)
        user.save()
        return redirect(f"/?auth={user.get_id()}")
    except Exception as e:
        logging.exception(e)
        return redirect(f"/?error={str(e)}")


@manager.route("/auth/logout", methods=["POST"])  # noqa: F821
@login_required
async def log_out():
    """
    User logout endpoint.
    ---
    tags:
      - User
    security:
      - ApiKeyAuth: []
    responses:
      200:
        description: Logout successful.
        schema:
          type: object
    """
    user = current_user._get_current_object() if hasattr(current_user, "_get_current_object") else current_user
    user_id = user.id
    user.access_token = f"INVALID_{secrets.token_hex(16)}"
    saved = user.save()
    if saved == 0:
        logging.error("Logout failed to persist access token update: user_id=%s", user_id)
        return get_json_result(code=RetCode.SERVER_ERROR, data=False, message="Failed to update access token")
    logout_user()
    logging.info("Logout: user_id=%s, access_token invalidated", user_id)
    return get_json_result(data=True)


@manager.route("/users/me", methods=["PATCH"])  # noqa: F821
@login_required
async def setting_user():
    """
    Update user settings.
    ---
    tags:
      - User
    security:
      - ApiKeyAuth: []
    parameters:
      - in: body
        name: body
        description: User settings to update.
        required: true
        schema:
          type: object
          properties:
            nickname:
              type: string
              description: New nickname.
            email:
              type: string
              description: New email.
    responses:
      200:
        description: Settings updated successfully.
        schema:
          type: object
    """
    update_dict = {}
    request_data = await get_request_json()
    if request_data.get("password"):
        new_password = request_data.get("new_password")
        if not check_password_hash(current_user.password, decrypt(request_data["password"])):
            return get_json_result(
                data=False,
                code=RetCode.AUTHENTICATION_ERROR,
                message="Password error!",
            )

        if new_password:
            plain_new = decrypt(new_password)
            pwd_error = _validate_password_strength(plain_new)
            if pwd_error:
                return get_json_result(data=False, message=pwd_error, code=RetCode.ARGUMENT_ERROR)
            update_dict["password"] = generate_password_hash(plain_new)

    for k in request_data.keys():
        if k in [
            "password",
            "new_password",
            "email",
            "status",
            "is_superuser",
            "login_channel",
            "is_anonymous",
            "is_active",
            "is_authenticated",
            "last_login_time",
        ]:
            continue
        update_dict[k] = request_data[k]

    if "nickname" in update_dict:
        error_message, error_code = validate_nickname(update_dict["nickname"])
        if error_message:
            return get_json_result(data=False, message=error_message, code=error_code)
        update_dict["nickname"] = update_dict["nickname"].strip()

    try:
        UserService.update_by_id(current_user.id, update_dict)
        return get_json_result(data=True)
    except Exception as e:
        logging.exception(e)
        return get_json_result(data=False, message="Update failure!", code=RetCode.EXCEPTION_ERROR)


@manager.route("/users/me", methods=["GET"])  # noqa: F821
@login_required
async def user_profile():
    """
    Get user profile information.
    ---
    tags:
      - User
    security:
      - ApiKeyAuth: []
    responses:
      200:
        description: User profile retrieved successfully.
        schema:
          type: object
          properties:
            id:
              type: string
              description: User ID.
            nickname:
              type: string
              description: User nickname.
            email:
              type: string
              description: User email.
    """
    # Enrich with RBAC context so the frontend can role-gate menu items and
    # render the workspace switcher without making N extra requests.
    # CUSTOM B2B SaaS — to_safe_dict, pas to_dict : le profil renvoyait le hash
    # du mot de passe et l'access_token brut au navigateur (constaté 2026-09-06).
    data = current_user.to_safe_dict(for_self=True)
    try:
        from quart import request as _req
        from api.db.services.workspace_service import WorkspaceService, WsMemberService
        from api.db.services.org_service import OrgMemberService

        active_ws_id = _req.headers.get("X-Workspace-Id") or None
        data["active_workspace_id"] = active_ws_id

        # ws_role for the active workspace (None if no workspace header)
        ws_role = None
        if active_ws_id:
            m = WsMemberService.get_membership(active_ws_id, current_user.id)
            if m:
                ws_role = m.role
        data["ws_role"] = ws_role

        # org_role: primary org membership (the user's first org)
        org_role = None
        org_id = None
        try:
            oms = OrgMemberService.query(user_id=current_user.id, status="1")
            if oms:
                org_role = oms[0].role
                org_id = oms[0].org_id
        except Exception:
            pass
        data["org_role"] = org_role
        data["org_id"] = org_id

        # workspaces the user can access (member of) — used by the switcher.
        workspaces = []
        try:
            from api.db.services.org_service import OrgService
            org_name_cache: dict = {}

            def _org_name(oid: str) -> str:
                if oid not in org_name_cache:
                    ok_o, o = OrgService.get_by_id(oid)
                    org_name_cache[oid] = o.name if ok_o and o else oid
                return org_name_cache[oid]

            memberships = WsMemberService.list_workspaces_for_user(current_user.id)
            # CUSTOM PERF: was N+1 — one WorkspaceService.get_by_id per membership.
            # Bulk-load via WHERE id IN (...) so a user with K workspaces costs 1
            # query instead of K. Audit on 2026-05-01 caught a 5x duplicate query
            # here for a CI user with 5 workspaces.
            from api.db.db_models import Workspace
            ws_ids = [m.workspace_id for m in memberships]
            ws_by_id = (
                {ws.id: ws for ws in Workspace.select().where(Workspace.id.in_(ws_ids))}
                if ws_ids else {}
            )
            for m in memberships:
                ws = ws_by_id.get(m.workspace_id)
                if ws and ws.status == "1":
                    workspaces.append({
                        "id": ws.id,
                        "name": ws.name,
                        "org_id": ws.org_id,
                        "org_name": _org_name(ws.org_id),
                        "role": m.role,
                        "tenant_id": ws.tenant_id,
                    })
        except Exception:
            pass

        # Org admins implicitly have access to every workspace in their org,
        # even without an explicit WsMember row — surface those too so the
        # switcher dropdown matches what the bridge / RBAC middleware will
        # actually let them open.
        if org_role == "org_admin" and org_id:
            try:
                seen = {w["id"] for w in workspaces}
                for ws in WorkspaceService.list_by_org(org_id):
                    if ws.id in seen:
                        continue
                    workspaces.append({
                        "id": ws.id,
                        "name": ws.name,
                        "org_id": ws.org_id,
                        "org_name": _org_name(ws.org_id),
                        "role": "ws_admin",  # implicit
                        "tenant_id": ws.tenant_id,
                    })
            except Exception:
                pass
        data["workspaces"] = workspaces
    except Exception:
        logging.exception("user_profile RBAC enrichment failed")
    return get_json_result(data=data)


def rollback_user_registration(user_id):
    try:
        UserService.delete_by_id(user_id)
    except Exception:
        pass
    try:
        TenantService.delete_by_id(user_id)
    except Exception:
        pass
    try:
        u = UserTenantService.query(tenant_id=user_id)
        if u:
            UserTenantService.delete_by_id(u[0].id)
    except Exception:
        pass


def _check_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    """Return True if the caller is within the rate limit, False if exceeded."""
    try:
        attempts = REDIS_CONN.REDIS.incr(key)
        if attempts == 1:
            REDIS_CONN.REDIS.expire(key, window_seconds)
        return attempts <= limit
    except Exception:
        return True  # Redis unavailable → fail open (don't block legit users)


def _validate_password_strength(plain_password: str) -> str | None:
    """Return an error message if the password is too weak, or None if it's acceptable."""
    if len(plain_password) < 8:
        return "Password must be at least 8 characters long."
    if not re.search(r"[A-Z]", plain_password):
        return "Password must contain at least one uppercase letter."
    if not re.search(r"[0-9]", plain_password):
        return "Password must contain at least one digit."
    return None


def user_register(user_id, user):
    user["id"] = user_id
    tenant = {
        "id": user_id,
        "name": user["nickname"] + "‘s Kingdom",
        "llm_id": settings.CHAT_MDL,
        "embd_id": settings.EMBEDDING_MDL,
        "asr_id": settings.ASR_MDL,
        "parser_ids": settings.PARSERS,
        "img2txt_id": settings.IMAGE2TEXT_MDL,
        "rerank_id": settings.RERANK_MDL,
    }
    usr_tenant = {
        "tenant_id": user_id,
        "user_id": user_id,
        "invited_by": user_id,
        "role": UserTenantRole.OWNER,
    }
    file_id = get_uuid()
    file = {
        "id": file_id,
        "parent_id": file_id,
        "tenant_id": user_id,
        "created_by": user_id,
        "name": "/",
        "type": FileType.FOLDER.value,
        "size": 0,
        "location": "",
    }

    # tenant_llm = get_init_tenant_llm(user_id)

    if not UserService.save(**user):
        return None
    TenantService.insert(**tenant)
    UserTenantService.insert(**usr_tenant)
    # TenantLLMService.insert_many(tenant_llm)
    FileService.insert(file)
    return UserService.query(email=user["email"])


@manager.route("/users", methods=["POST"])  # noqa: F821
@validate_request("nickname", "email", "password")
async def user_add():
    """
    Register a new user.
    ---
    tags:
      - User
    parameters:
      - in: body
        name: body
        description: Registration details.
        required: true
        schema:
          type: object
          properties:
            nickname:
              type: string
              description: User nickname.
            email:
              type: string
              description: User email.
            password:
              type: string
              description: User password.
    responses:
      200:
        description: Registration successful.
        schema:
          type: object
    """

    if not settings.REGISTER_ENABLED:
        return get_json_result(
            data=False,
            message="User registration is disabled!",
            code=RetCode.OPERATING_ERROR,
        )

    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    if not _check_rate_limit(f"register_attempt:{client_ip}", limit=10, window_seconds=3600):
        return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message="Too many registration attempts. Try again in 1 hour.")

    req = await get_request_json()
    email_address = req["email"]

    # Validate the email address
    if not re.match(r"^[\w\._-]+@([\w_-]+\.)+[\w-]{2,}$", email_address):
        return get_json_result(
            data=False,
            message=f"Invalid email address: {email_address}!",
            code=RetCode.OPERATING_ERROR,
        )

    # Check if the email address is already used
    if UserService.query(email=email_address):
        return get_json_result(
            data=False,
            message=f"Email: {email_address} has already registered!",
            code=RetCode.OPERATING_ERROR,
        )

    # Construct user info data
    nickname = req["nickname"]
    error_message, error_code = validate_nickname(nickname)
    if error_message:
        return get_json_result(data=False, message=error_message, code=error_code)
    nickname = nickname.strip()
    plain_password = decrypt(req["password"])
    pwd_error = _validate_password_strength(plain_password)
    if pwd_error:
        return get_json_result(data=False, message=pwd_error, code=RetCode.ARGUMENT_ERROR)

    user_dict = {
        "access_token": get_uuid(),
        "email": email_address,
        "nickname": nickname,
        "password": plain_password,
        "login_channel": "password",
        "last_login_time": get_format_time(),
        "is_superuser": False,
    }

    user_id = get_uuid()
    try:
        users = user_register(user_id, user_dict)
        if not users:
            raise Exception(f"Fail to register {email_address}.")
        if len(users) > 1:
            raise Exception(f"Same email: {email_address} exists!")
        user = users[0]
        login_user(user)
        return await construct_response(
            data=user.to_safe_dict(for_self=True),
            auth=user.get_id(),
            message=f"{nickname}, welcome aboard!",
        )
    except Exception as e:
        rollback_user_registration(user_id)
        logging.exception(e)
        return get_json_result(
            data=False,
            message=f"User registration failure, error: {str(e)}",
            code=RetCode.EXCEPTION_ERROR,
        )


@manager.route("/users/me/models", methods=["GET"])  # noqa: F821
@login_required
async def tenant_info():
    """
    Get tenant information.
    ---
    tags:
      - Tenant
    security:
      - ApiKeyAuth: []
    responses:
      200:
        description: Tenant information retrieved successfully.
        schema:
          type: object
          properties:
            tenant_id:
              type: string
              description: Tenant ID.
            name:
              type: string
              description: Tenant name.
            llm_id:
              type: string
              description: LLM ID.
            embd_id:
              type: string
              description: Embedding model ID.
    """
    try:
        tenants = TenantService.get_info_by(current_user.id)
        if tenants and tenants[0].get("llm_id") and tenants[0].get("embd_id"):
            return get_json_result(data=tenants[0])

        # CUSTOM B2B SaaS — workspace-default model fallback.
        # `get_info_by` only returns a row when the user OWNS a tenant.
        # A plain workspace member (viewer/editor) owns nothing, so the row
        # is empty and the frontend would push them to /user-setting/model
        # — a route gated by @require_permission(LLM_CONFIGURE) they can't
        # reach (403), leaving them stuck with no usable model. The RAG path
        # already falls back to the workspace defaults via
        # tenant_model_service; this makes the HTTP surface consistent so
        # the UI sees a configured model and never shows the warning.
        from api.utils.tenant_context import maybe_active_tenant_id

        ws_tid = maybe_active_tenant_id()
        if ws_tid:
            ok, ws = TenantService.get_by_id(ws_tid)
            if ok and ws.llm_id and ws.embd_id:
                ws_role = ""
                if tenants:
                    ws_role = tenants[0].get("role", "")
                return get_json_result(
                    data={
                        "tenant_id": ws.id,
                        "name": ws.name,
                        "llm_id": ws.llm_id,
                        "embd_id": ws.embd_id,
                        "rerank_id": ws.rerank_id,
                        "asr_id": ws.asr_id,
                        "img2txt_id": ws.img2txt_id,
                        "tts_id": ws.tts_id,
                        "parser_ids": ws.parser_ids,
                        "role": ws_role,
                    }
                )

        if not tenants:
            return get_data_error_result(message="Tenant not found!")
        return get_json_result(data=tenants[0])
    except Exception as e:
        return server_error_response(e)


@manager.route("/users/me/models", methods=["PATCH"])  # noqa: F821
@login_required
@require_permission(Permission.LLM_CONFIGURE)  # CUSTOM B2B SaaS — workspace default models are admin-only
@validate_request("tenant_id", "asr_id", "embd_id", "img2txt_id", "llm_id")
async def set_tenant_info():
    """
    Update tenant information.
    ---
    tags:
      - Tenant
    security:
      - ApiKeyAuth: []
    parameters:
      - in: body
        name: body
        description: Tenant information to update.
        required: true
        schema:
          type: object
          properties:
            tenant_id:
              type: string
              description: Tenant ID.
            llm_id:
              type: string
              description: LLM ID.
            embd_id:
              type: string
              description: Embedding model ID.
            asr_id:
              type: string
              description: ASR model ID.
            img2txt_id:
              type: string
              description: Image to Text model ID.
    responses:
      200:
        description: Tenant information updated successfully.
        schema:
          type: object
    """
    req = await get_request_json()
    try:
        # CUSTOM B2B SaaS — le tenant visé est TOUJOURS le workspace actif :
        # le tenant_id du corps permettait de réécrire les modèles (et toute
        # colonne Tenant) de n'importe quel workspace de la plateforme
        # (audit 2026-09-06). Champs autorisés : les identifiants de modèles.
        from api.utils.tenant_context import active_tenant_id
        req.pop("tenant_id", None)
        allowed = {"llm_id", "embd_id", "asr_id", "img2txt_id", "rerank_id", "tts_id"}
        update = {k: v for k, v in req.items() if k in allowed}
        if not update:
            return get_json_result(data=False, code=RetCode.ARGUMENT_ERROR, message="Nothing to update")
        TenantService.update_by_id(active_tenant_id(), update)
        return get_json_result(data=True)
    except Exception as e:
        return server_error_response(e)


@manager.route("/auth/password/forgot/captcha", methods=["POST"])  # noqa: F821
async def forget_get_captcha():
    """
    GET /forget/captcha?email=<email>
    - Generate an image captcha and cache it in Redis under key captcha:{email} with TTL = OTP_TTL_SECONDS.
    - Returns the captcha as a PNG image.
    """
    email = (request.args.get("email") or "")
    if not email:
        return get_json_result(data=False, code=RetCode.ARGUMENT_ERROR, message="email is required")

    users = UserService.query(email=email)
    if not users:
        return get_json_result(data=False, code=RetCode.DATA_ERROR, message="invalid email")

    # Generate captcha text
    allowed = string.ascii_uppercase + string.digits
    captcha_text = "".join(secrets.choice(allowed) for _ in range(OTP_LENGTH))
    REDIS_CONN.set(captcha_key(email), captcha_text, 60) # Valid for 60 seconds

    from captcha.image import ImageCaptcha
    image = ImageCaptcha(width=300, height=120, font_sizes=[50, 60, 70])
    img_bytes = image.generate(captcha_text).read()
    response = await make_response(img_bytes)
    response.headers.set("Content-Type", "image/JPEG")
    return response


@manager.route("/auth/password/forgot/otp", methods=["POST"])  # noqa: F821
async def forget_send_otp():
    """
    POST /forget/otp
    - Verify the image captcha stored at captcha:{email} (case-insensitive).
    - On success, generate an email OTP (A–Z with length = OTP_LENGTH), store hash + salt (and timestamp) in Redis with TTL, reset attempts and cooldown, and send the OTP via email.
    """
    req = await get_request_json()
    email = req.get("email") or ""
    captcha = (req.get("captcha") or "").strip()

    if not email or not captcha:
        return get_json_result(data=False, code=RetCode.ARGUMENT_ERROR, message="email and captcha required")

    users = UserService.query(email=email)
    if not users:
        return get_json_result(data=False, code=RetCode.DATA_ERROR, message="invalid email")

    stored_captcha = REDIS_CONN.get(captcha_key(email))
    if not stored_captcha:
        return get_json_result(data=False, code=RetCode.NOT_EFFECTIVE, message="invalid or expired captcha")
    if (stored_captcha or "").strip().lower() != captcha.lower():
        return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message="invalid or expired captcha")

    # Delete captcha to prevent reuse
    REDIS_CONN.delete(captcha_key(email))

    k_code, k_attempts, k_last, k_lock = otp_keys(email)
    now = int(time.time())
    last_ts = REDIS_CONN.get(k_last)
    if last_ts:
        try:
            elapsed = now - int(last_ts)
        except Exception:
            elapsed = RESEND_COOLDOWN_SECONDS
        remaining = RESEND_COOLDOWN_SECONDS - elapsed
        if remaining > 0:
            return get_json_result(data=False, code=RetCode.NOT_EFFECTIVE, message=f"you still have to wait {remaining} seconds")

    # Generate OTP (uppercase letters only) and store hashed
    otp = "".join(secrets.choice(string.ascii_uppercase) for _ in range(OTP_LENGTH))
    code_hash = hash_code(otp, settings.SECRET_KEY.encode())
    REDIS_CONN.set(k_code, code_hash, OTP_TTL_SECONDS)
    REDIS_CONN.set(k_attempts, 0, OTP_TTL_SECONDS)
    REDIS_CONN.set(k_last, now, OTP_TTL_SECONDS)
    REDIS_CONN.delete(k_lock)

    ttl_min = OTP_TTL_SECONDS // 60

    try:
        await send_email_html(
            subject="Your Password Reset Code",
            to_email=email,
            template_key="reset_code",
            code=otp,
            ttl_min=ttl_min,
        )

    except Exception as e:
        logging.exception(e)
        return get_json_result(data=False, code=RetCode.SERVER_ERROR, message="failed to send email")

    return get_json_result(data=True, code=RetCode.SUCCESS, message="verification passed, email sent")


def _verified_key(email: str) -> str:
    return f"otp:verified:{email}"


@manager.route("/auth/password/forgot/otp/verify", methods=["POST"])  # noqa: F821
async def forget_verify_otp():
    """
    Verify email + OTP only. On success:
    - consume the OTP and attempt counters
    - set a short-lived verified flag in Redis for the email
    Request JSON: { email, otp }
    """
    req = await get_request_json()
    email = req.get("email") or ""
    otp = (req.get("otp") or "").strip()

    if not all([email, otp]):
        return get_json_result(data=False, code=RetCode.ARGUMENT_ERROR, message="email and otp are required")

    users = UserService.query(email=email)
    if not users:
        return get_json_result(data=False, code=RetCode.DATA_ERROR, message="invalid email")

    # Verify OTP from Redis
    k_code, k_attempts, k_last, k_lock = otp_keys(email)
    if REDIS_CONN.get(k_lock):
        return get_json_result(data=False, code=RetCode.NOT_EFFECTIVE, message="too many attempts, try later")

    stored = REDIS_CONN.get(k_code)
    if not stored:
        return get_json_result(data=False, code=RetCode.NOT_EFFECTIVE, message="expired otp")

    stored_hash = str(stored)
    calc = hash_code(otp.upper(), settings.SECRET_KEY.encode())
    if calc != stored_hash:
        # bump attempts
        try:
            attempts = int(REDIS_CONN.get(k_attempts) or 0) + 1
        except Exception:
            attempts = 1
        REDIS_CONN.set(k_attempts, attempts, OTP_TTL_SECONDS)
        if attempts >= ATTEMPT_LIMIT:
            REDIS_CONN.set(k_lock, int(time.time()), ATTEMPT_LOCK_SECONDS)
        return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message="expired otp")

    # Success: consume OTP and attempts; mark verified
    REDIS_CONN.delete(k_code)
    REDIS_CONN.delete(k_attempts)
    REDIS_CONN.delete(k_last)
    REDIS_CONN.delete(k_lock)

    # set verified flag with limited TTL, reuse OTP_TTL_SECONDS or smaller window
    try:
        REDIS_CONN.set(_verified_key(email), "1", OTP_TTL_SECONDS)
    except Exception:
        return get_json_result(data=False, code=RetCode.SERVER_ERROR, message="failed to set verification state")

    return get_json_result(data=True, code=RetCode.SUCCESS, message="otp verified")


@manager.route("/auth/password/reset", methods=["POST"])  # noqa: F821
async def forget_reset_password():
    """
    Reset password after successful OTP verification.
    Requires: { email, new_password, confirm_new_password }
    Steps:
    - check verified flag in Redis
    - update user password
    - auto login
    - clear verified flag
    """
    
    req = await get_request_json()
    email = req.get("email") or ""
    new_pwd = req.get("new_password")
    new_pwd2 = req.get("confirm_new_password")

    if not all([email, new_pwd, new_pwd2]):
        return get_json_result(data=False, code=RetCode.ARGUMENT_ERROR, message="email and passwords are required")

    if not _check_rate_limit(f"pwd_reset:{email}", limit=5, window_seconds=3600):
        return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message="Too many password reset attempts. Try again in 1 hour.")

    new_pwd_base64 = decrypt(new_pwd)
    new_pwd_string = base64.b64decode(new_pwd_base64).decode('utf-8')
    new_pwd2_string = base64.b64decode(decrypt(new_pwd2)).decode('utf-8')

    # CUSTOM B2B SaaS — même règle de force que le profil et l'invitation
    # (upstream avait oublié ce point d'entrée) : >= 8, 1 majuscule, 1 chiffre.
    pwd_error = _validate_password_strength(new_pwd_string)
    if pwd_error:
        return get_json_result(data=False, code=RetCode.ARGUMENT_ERROR, message=pwd_error)

    if not REDIS_CONN.get(_verified_key(email)):
        return get_json_result(data=False, code=RetCode.AUTHENTICATION_ERROR, message="email not verified")

    new_pwd_base64 = decrypt(new_pwd)
    new_pwd_string = base64.b64decode(new_pwd_base64).decode('utf-8')
    new_pwd2_string = base64.b64decode(decrypt(new_pwd2)).decode('utf-8')

    if new_pwd_string != new_pwd2_string:
        return get_json_result(data=False, code=RetCode.ARGUMENT_ERROR, message="passwords do not match")

    users = UserService.query_user_by_email(email=email)
    if not users:
        return get_json_result(data=False, code=RetCode.DATA_ERROR, message="invalid email")
    
    user = users[0]
    try:
        UserService.update_user_password(user.id, new_pwd_base64)
    except Exception as e:
        logging.exception(e)
        return get_json_result(data=False, code=RetCode.EXCEPTION_ERROR, message="failed to reset password")

    # clear verified flag
    try:
        REDIS_CONN.delete(_verified_key(email))
    except Exception:
        pass

    msg = "Password reset successful. Logged in."
    return await construct_response(data=user.to_safe_dict(for_self=True), auth=user.get_id(), message=msg)


