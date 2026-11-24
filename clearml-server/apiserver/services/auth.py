import re
import secrets
import os
from urllib.parse import urlparse

from flask import request as flask_request

from apiserver.apierrors import errors
from apiserver.apimodels.auth import (
    GetTokenResponse,
    GetTokenForUserRequest,
    GetTokenRequest,
    ValidateTokenRequest,
    ValidateResponse,
    CreateUserRequest,
    CreateUserResponse,
    CreateCredentialsResponse,
    GetCredentialsResponse,
    RevokeCredentialsResponse,
    CredentialsResponse,
    RevokeCredentialsRequest,
    EditUserReq,
    CreateCredentialsRequest,
    EditCredentialsRequest,
)
from apiserver.apimodels.base import UpdateResponse
from apiserver.bll.auth import AuthBLL
from apiserver.config import info
from apiserver.config_repo import config
from apiserver.database.errors import translate_errors_context
from apiserver.database.model.auth import User, Role
from apiserver.service_repo import APICall, endpoint
from apiserver.service_repo.auth import Token
from apiserver.service_repo.auth.auth import is_token_revoked, revoke_auth_token
from apiserver.service_repo.auth.fixed_user import FixedUser
from apiserver.sso_admin import is_trusted_sso_admin

log = config.logger(__file__)


def _require_named_admin_for_credentials(call: APICall):
    """Allow fixed admin or a server-pinned, verified SSO administrator."""
    if not _is_fixed_credentials_admin(call):
        raise errors.forbidden.RoleNotAllowed("API credentials are admin-only")


def _is_fixed_credentials_admin(call: APICall) -> bool:
    identity = getattr(call, "identity", None)
    if identity is None:
        return False
    if FixedUser.enabled():
        fixed_admin = FixedUser.get_by_username("admin")
        if (fixed_admin and identity.user == fixed_admin.id
                and identity.company == fixed_admin.company):
            return True
    return is_trusted_sso_admin(identity.user, identity.company)


@endpoint(
    "auth.login",
    request_data_model=GetTokenRequest,
    response_data_model=GetTokenResponse,
)
def login(call: APICall, *_, **__):
    """Generates a token based on the authenticated user (intended for use with credentials)"""
    call.result.data_model = AuthBLL.get_token_for_user(
        user_id=call.identity.user,
        company_id=call.identity.company,
        expiration_sec=call.data_model.expiration_sec,
    )

    # Add authorization cookie
    call.result.set_auth_cookie(call.result.data_model.token)


@endpoint("auth.logout", min_version="2.2")
def logout(call: APICall, *_, **__):
    revoke_auth_token(call.auth)
    call.result.set_auth_cookie(None)


@endpoint(
    "auth.get_token_for_user",
    request_data_model=GetTokenForUserRequest,
    response_data_model=GetTokenResponse,
)
def get_token_for_user(call: APICall, _: str, request: GetTokenForUserRequest):
    """Generates a token based on a requested user and company. INTERNAL."""
    if call.identity.role not in Role.get_system_roles():
        if call.identity.role != Role.admin and call.identity.user != request.user:
            raise errors.bad_request.InvalidUserId(
                "cannot generate token for another user"
            )
        if call.identity.company != request.company:
            raise errors.bad_request.InvalidId(
                "cannot generate token in another company"
            )

    call.result.data_model = AuthBLL.get_token_for_user(
        user_id=request.user,
        company_id=request.company,
        expiration_sec=request.expiration_sec,
    )


@endpoint(
    "auth.validate_token",
    request_data_model=ValidateTokenRequest,
    response_data_model=ValidateResponse,
)
def validate_token_endpoint(call: APICall, _, __):
    """Validate a token and return identity if valid. INTERNAL."""
    try:
        # if invalid, decoding will fail
        token = Token.from_encoded_token(call.data_model.token)
        call.result.data_model = ValidateResponse(
            valid=not is_token_revoked(token),
            user=token.identity.user,
            company=token.identity.company,
        )
    except Exception as e:
        call.result.data_model = ValidateResponse(valid=False, msg=e.args[0])


@endpoint(
    "auth.create_user",
    request_data_model=CreateUserRequest,
    response_data_model=CreateUserResponse,
)
def create_user(call: APICall, _, request: CreateUserRequest):
    """Create a user from. INTERNAL."""
    if (
        call.identity.role not in Role.get_system_roles()
        and request.company != call.identity.company
    ):
        raise errors.bad_request.InvalidId("cannot create user in another company")

    user_id = AuthBLL.create_user(request=request, call=call)
    call.result.data_model = CreateUserResponse(id=user_id)


@endpoint("auth.create_credentials", response_data_model=CreateCredentialsResponse)
def create_credentials(call: APICall, _, request: CreateCredentialsRequest):
    _require_named_admin_for_credentials(call)
    if _is_protected_user(call.identity.user):
        raise errors.bad_request.InvalidUserId("protected identity")

    credentials = AuthBLL.create_credentials(
        user_id=call.identity.user,
        company_id=call.identity.company,
        role=call.identity.role,
        label=request.label,
    )
    call.result.data_model = CreateCredentialsResponse(credentials=credentials)


@endpoint("auth.edit_credentials")
def edit_credentials(call: APICall, company_id: str, request: EditCredentialsRequest):
    _require_named_admin_for_credentials(call)
    identity = call.identity
    access_key = request.access_key

    updated = User.objects(
        id=identity.user,
        company=company_id,
        credentials__match={"key": access_key},
    ).update_one(set__credentials__S__label=request.label)
    if not updated:
        raise errors.bad_request.InvalidAccessKey(
            "invalid user or invalid access key",
            user=identity.user,
            access_key=access_key,
            company=company_id,
        )

    call.result.data = {"updated": updated}


@endpoint(
    "auth.revoke_credentials",
    request_data_model=RevokeCredentialsRequest,
    response_data_model=RevokeCredentialsResponse,
)
def revoke_credentials(call: APICall, _, __):
    _require_named_admin_for_credentials(call)
    identity = call.identity
    access_key = call.data_model.access_key

    if _is_protected_user(call.identity.user):
        raise errors.bad_request.InvalidUserId("protected identity")

    with translate_errors_context():
        query = dict(
            id=identity.user, company=identity.company, credentials__key=access_key
        )
        updated = User.objects(**query).update_one(pull__credentials__key=access_key)
        if not updated:
            raise errors.bad_request.InvalidUser(
                "invalid user or invalid access key", **query
            )

        call.result.data_model = RevokeCredentialsResponse(revoked=updated)


@endpoint("auth.get_credentials", response_data_model=GetCredentialsResponse)
def get_credentials(call: APICall, _, __):
    _require_named_admin_for_credentials(call)
    identity = call.identity

    with translate_errors_context():
        query = dict(id=identity.user, company=identity.company)
        user = User.objects(**query).first()
        if not user:
            raise errors.bad_request.InvalidUserId(**query)

        # we return ONLY the key IDs, never the secrets (want a secret? create new credentials)
        call.result.data_model = GetCredentialsResponse(
            credentials=[
                CredentialsResponse(
                    access_key=c.key,
                    last_used=c.last_used,
                    label=c.label,
                    last_used_from=c.last_used_from,
                )
                for c in user.credentials
            ]
        )


@endpoint(
    "auth.edit_user", request_data_model=EditUserReq, response_data_model=UpdateResponse
)
def update(call: APICall, company_id: str, _):
    fields = {
        k: v
        for k, v in call.data_model.to_struct().items()
        if k != "user" and v is not None
    }

    with translate_errors_context():
        result = User.objects(company=company_id, id=call.data_model.user).update(
            **fields, full_result=True, upsert=False
        )

        if not result.matched_count:
            raise errors.bad_request.InvalidUserId()

    call.result.data_model = UpdateResponse(
        updated=result.modified_count, fields=fields
    )


@endpoint("auth.fixed_users_mode")
def fixed_users_mode(call: APICall, *_, **__):
    server_errors = {
        name: error
        for name, error in zip(
            ("missed_es_upgrade", "es_connection_error"),
            (info.missed_es_upgrade, info.es_connection_error),
        )
        if error
    }

    data = {
        "enabled": FixedUser.enabled(),
        "guest": {"enabled": FixedUser.guest_enabled()},
        "server_errors": server_errors,
    }
    guest_user = FixedUser.get_guest_user()
    if guest_user:
        data["guest"]["name"] = guest_user.name
        data["guest"]["username"] = guest_user.username
        data["guest"]["password"] = guest_user.password

    call.result.data = data


@endpoint("auth.credential_admin_status")
def credential_admin_status(call: APICall, *_, **__):
    """Return a permission derived from the authenticated server identity."""
    call.result.data = {
        "can_manage_credentials": _is_fixed_credentials_admin(call),
    }


def _admin_target_user(call: APICall, user_id: str) -> User:
    """Resolve target by immutable ID; never trust submitted names or roles."""
    _require_named_admin_for_credentials(call)
    if not isinstance(user_id, str) or not user_id:
        raise errors.bad_request.InvalidUserId("Missing target user")
    user = User.objects(id=user_id, company=call.identity.company, role=Role.user).first()
    fixed_admin = FixedUser.get_by_username("admin")
    if (
        not user
        or _is_protected_user(user.id)
        or (fixed_admin and user.id == fixed_admin.id)
    ):
        raise errors.bad_request.InvalidUserId("Target must be a lab user or worker")
    return user



# Self-service credentials are deliberately separate from admin credential creation.
# The browser session is mandatory: SDK API keys cannot mint new SDK API keys.
def _self_service_credential_user(call: APICall):
    origin = flask_request.headers.get("Origin", "").rstrip("/")
    configured_origins = os.environ.get(
        "CLEARML_SELF_SERVICE_ALLOWED_ORIGINS", "https://clearml.ccixgtestbed.org"
    )
    allowed_origins = {
        value.strip().rstrip("/") for value in configured_origins.split(",") if value.strip()
    }
    if not origin or origin not in allowed_origins:
        raise errors.forbidden.RoleNotAllowed("Request must originate from an allowed WebApp")

    cookie_token = flask_request.cookies.get("clearml_token_basic")
    if not cookie_token:
        raise errors.forbidden.RoleNotAllowed("An authenticated browser session is required")
    try:
        browser_token = Token.from_encoded_token(cookie_token)
        if is_token_revoked(browser_token):
            raise ValueError("Revoked session")
        session_identity = browser_token.identity
        if (session_identity.user != call.identity.user or
                session_identity.company != call.identity.company):
            raise ValueError("Session identity mismatch")
    except Exception:
        raise errors.forbidden.RoleNotAllowed("Invalid or expired browser session")

    user = User.objects(id=call.identity.user, company=call.identity.company,
                        role=Role.user).first()
    if (user is None or user.is_worker_service or
            _is_protected_user(call.identity.user) or
            _is_fixed_credentials_admin(call)):
        raise errors.forbidden.RoleNotAllowed("Account is not eligible for self-service credentials")

    # SSO eligibility is grounded in the existing immutable issuer+subject link;
    # a matching email address or untrusted browser claim is insufficient.
    from apiserver.sso_identity import SSOIdentityLink
    linked_sso = SSOIdentityLink.objects(user_id=user.id, company=user.company).first()
    fixed_allowed = {
        value.strip() for value in
        os.environ.get("CLEARML_SELF_SERVICE_FIXED_USER_IDS", "").split(",")
        if value.strip()
    }
    if linked_sso is None and user.id not in fixed_allowed:
        raise errors.forbidden.RoleNotAllowed(
            "No trusted SSO identity mapping or explicitly allowed lab account"
        )
    return user


@endpoint("auth.self_service_credential_status")
def self_service_credential_status(call: APICall, *_, **__):
    """Browser-only: report whether user can request one credential pair."""
    user = _self_service_credential_user(call)
    call.result.data = {
        "eligible": True,
        "has_credentials": bool(user.credentials),
    }


@endpoint("auth.my_credentials")
def my_credentials(call: APICall, *_, **__):
    """List only the current eligible browser user's own credential metadata."""
    user = _self_service_credential_user(call)
    call.result.data = {
        "credentials": [
            {
                "access_key": c.key,
                "label": c.label or "",
                "last_used": c.last_used.isoformat() if c.last_used else None,
            }
            for c in user.credentials
        ]
    }


@endpoint("auth.revoke_my_credentials")
def revoke_my_credentials(call: APICall, *_, **__):
    """Browser-only self-revocation; user ID cannot be submitted by the caller."""
    user = _self_service_credential_user(call)
    access_key = call.data.get("access_key")
    if not isinstance(access_key, str) or not access_key:
        raise errors.bad_request.InvalidAccessKey("Missing access key")
    with translate_errors_context():
        revoked = User.objects(
            id=user.id,
            company=user.company,
            role=Role.user,
            credentials__key=access_key,
        ).update_one(pull__credentials__key=access_key)
    if not revoked:
        raise errors.bad_request.InvalidAccessKey("Credential not found in your account")
    call.result.data = {"revoked": bool(revoked)}


@endpoint("auth.request_my_credentials")
def request_my_credentials(call: APICall, *_, **__):
    """Issue one key pair to the verified browser-session user, never to a caller-supplied ID."""
    user = _self_service_credential_user(call)
    # Fail closed if credentials already exist; the admin can revoke old keys.
    # This check is not a database-level concurrency lock: rate-limit requests
    # at the proxy and use an atomic issuance lock for high-concurrency deployments.
    if user.credentials:
        raise errors.forbidden.RoleNotAllowed(
            "Credentials already exist. Contact your administrator for rotation."
        )
    credential = AuthBLL.create_credentials(
        user_id=user.id,
        company_id=user.company,
        role=user.role,
        label="self-service SDK",
    )
    call.result.data = {
        "credentials": {
            "access_key": credential.access_key,
            "secret_key": credential.secret_key,
            "label": credential.label,
        }
    }


@endpoint("auth.admin_credential_targets")
def admin_credential_targets(call: APICall, *_, **__):
    """List eligible users and credential *identifiers*, never secrets."""
    _require_named_admin_for_credentials(call)
    fixed_admin = FixedUser.get_by_username("admin")
    targets = []
    for user in User.objects(company=call.identity.company, role=Role.user).order_by("name"):
        if _is_protected_user(user.id) or (fixed_admin and user.id == fixed_admin.id):
            continue
        targets.append({
            "id": user.id,
            "name": user.name,
            "email": user.email or "",
            "kind": "worker" if user.is_worker_service else "user",
            "credentials": [{
                "access_key": cred.key,
                "label": cred.label or "",
                "last_used": cred.last_used.isoformat() if cred.last_used else None,
            } for cred in user.credentials],
        })
    call.result.data = {"targets": targets}


@endpoint("auth.admin_create_user_credentials")
def admin_create_user_credentials(call: APICall, *_, **__):
    user = _admin_target_user(call, call.data.get("user_id"))
    label = str(call.data.get("label") or "").strip()[:120]
    credential = AuthBLL.create_credentials(
        user_id=user.id, company_id=user.company, role=user.role, label=label,
    )
    # This is the only response that contains the newly issued secret.
    call.result.data = {
        "user_id": user.id,
        "credentials": {
            "access_key": credential.access_key,
            "secret_key": credential.secret_key,
            "label": credential.label,
        },
    }


@endpoint("auth.admin_revoke_user_credentials")
def admin_revoke_user_credentials(call: APICall, *_, **__):
    user = _admin_target_user(call, call.data.get("user_id"))
    key = call.data.get("access_key")
    if not isinstance(key, str) or not key:
        raise errors.bad_request.InvalidAccessKey("Missing access key")
    with translate_errors_context():
        updated = User.objects(
            id=user.id, company=user.company, credentials__key=key,
        ).update_one(pull__credentials__key=key)
    if not updated:
        raise errors.bad_request.InvalidAccessKey("Credential not found for user")
    call.result.data = {"revoked": bool(updated)}


@endpoint("auth.admin_show_credentials")
def admin_show_credentials(call: APICall, *_, **__):
    """Reveal one credential to the fixed admin, never to an SDK user."""
    _require_named_admin_for_credentials(call)
    user_id = call.data.get("user_id")
    key = call.data.get("access_key")
    if not isinstance(key, str) or not key:
        raise errors.bad_request.InvalidAccessKey("Missing access key")
    if user_id == call.identity.user:
        user = User.objects(id=call.identity.user, company=call.identity.company).first()
    else:
        user = _admin_target_user(call, user_id)
    if not user:
        raise errors.bad_request.InvalidUserId("Account not found")
    for credential in user.credentials:
        if credential.key == key:
            call.result.data = {
                "credentials": {
                    "access_key": credential.key,
                    "secret_key": credential.secret,
                    "label": credential.label or "",
                }
            }
            return
    raise errors.bad_request.InvalidAccessKey("Credential not found for account")


@endpoint("auth.admin_create_worker_identity")
def admin_create_worker_identity(call: APICall, *_, **__):
    _require_named_admin_for_credentials(call)
    name = str(call.data.get("name") or "").strip().lower()
    if not re.fullmatch(r"(?:gpu|cpu|pipeline)-worker-[a-z0-9][a-z0-9-]{0,47}", name):
        raise errors.bad_request.FieldsValueError(
            "Use a name such as gpu-worker-01, cpu-worker-01 or pipeline-worker-01"
        )
    if User.objects(company=call.identity.company, name=name).first():
        raise errors.bad_request.FieldsValueError("Worker identity name already exists")
    request = CreateUserRequest(
        name=name,
        company=call.identity.company,
        role=Role.user,
        email="worker-{}@example.com".format(secrets.token_hex(12)),
    )
    created_id = AuthBLL.create_user(request=request, call=call)
    User.objects(id=created_id, company=call.identity.company).update_one(
        set__is_worker_service=True
    )
    call.result.data = {"id": created_id, "name": name}


def _is_protected_user(user_id):
    return user_id.strip("_") in config.get("secure.credentials")
