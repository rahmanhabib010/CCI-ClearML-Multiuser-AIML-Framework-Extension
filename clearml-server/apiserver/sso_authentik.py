"""Opt-in OIDC browser authentication with JIT user provisioning."""
import os
import secrets
import hashlib
import logging
from urllib.parse import urlencode

import jwt
import requests
from flask import Blueprint, jsonify, make_response, redirect, request
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from apiserver.bll.auth import AuthBLL
from apiserver import sso_logout_store as slo_store
from apiserver.database.model.auth import User
from mongoengine.errors import NotUniqueError
from apiserver.apimodels.auth import CreateUserRequest
from apiserver.database.model.auth import Role
from apiserver.database.model.company import Company
from apiserver.sso_identity import SSOIdentityLink


sso_blueprint = Blueprint("authentik_sso", __name__)
sso_log = logging.getLogger(__name__)

def enabled():
    return os.environ.get("CLEARML_SSO_ENABLED", "false").lower() == "true"

def env(name):
    result = os.environ.get(name, "").strip()
    if not result:
        raise RuntimeError("Missing SSO configuration: " + name)
    return result

def state_signer():
    return URLSafeTimedSerializer(env("CLEARML_SSO_STATE_SECRET"), salt="clearml-oidc-state-v1")

def discovery():
    issuer = env("CLEARML_SSO_ISSUER").rstrip("/")
    if not issuer.startswith("https://"):
        raise RuntimeError("OIDC issuer must use HTTPS")
    response = requests.get(issuer + "/.well-known/openid-configuration", timeout=8)
    response.raise_for_status()
    doc = response.json()
    if doc.get("issuer", "").rstrip("/") != issuer:
        raise RuntimeError("OIDC issuer mismatch")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not doc.get(key, "").startswith("https://"):
            raise RuntimeError("Invalid OIDC discovery endpoint: " + key)
    return doc

def disabled_response():
    return ("SSO disabled", 404)

@sso_blueprint.get("/sso/config")
def public_config():
    return jsonify({"enabled": enabled()})

@sso_blueprint.get("/sso/start")
def start():
    if not enabled():
        return disabled_response()
    try:
        d = discovery()
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        import base64, hashlib
        code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(32)
        signed = state_signer().dumps({"state": state, "nonce": nonce, "verifier": code_verifier})
        args = dict(response_type="code", client_id=env("CLEARML_SSO_CLIENT_ID"),
                    redirect_uri=env("CLEARML_SSO_CALLBACK_URL"), scope="openid email profile",
                    state=state, nonce=nonce, code_challenge=code_challenge,
                    code_challenge_method="S256")
        response = redirect(d["authorization_endpoint"] + "?" + urlencode(args), code=302)
        response.set_cookie("clearml_sso_state", signed, max_age=600, secure=True,
                            httponly=True, samesite="Lax", path="/sso")
        return response
    except Exception:
        sso_blueprint.logger.exception("SSO start error") if hasattr(sso_blueprint,'logger') else None
        return ("SSO configuration failure", 503)


def _resolve_sso_user(claims, email):
    """Return linked user or provision one. Never link by email alone."""
    issuer = str(claims["iss"])
    subject = str(claims["sub"])
    if not issuer or not subject:
        return None, ("Invalid OIDC subject", 401)
    link_id = hashlib.sha256((issuer + "\x00" + subject).encode("utf-8")).hexdigest()
    link = SSOIdentityLink.objects(id=link_id).first()
    if link is not None:
        if link.issuer != issuer or link.subject != subject:
            return None, ("OIDC identity collision", 403)
        if not link.user_id:
            return None, ("SSO user provisioning pending administrator reconciliation", 409)
        user = User.objects(id=link.user_id, company=link.company).first()
        if user is None:
            return None, ("Linked ClearML user is unavailable", 403)
        return user, None

    # Don't silently claim an existing account, even if the email is verified.
    if User.objects(email__iexact=email).first() is not None:
        return None, ("Existing ClearML email requires explicit administrator account linking", 403)

    company_id = env("CLEARML_SSO_DEFAULT_COMPANY")
    if Company.objects(id=company_id).first() is None:
        raise RuntimeError("SSO default company does not exist")

    # Reserve issuer+subject before creation. Other concurrent callbacks will
    # encounter the reservation and will not make another user.
    try:
        link = SSOIdentityLink(
            id=link_id, issuer=issuer, subject=subject,
            email_at_creation=email, company=company_id,
        )
        link.save(force_insert=True)
    except NotUniqueError:
        return None, ("SSO login in progress; retry after completion", 409)

    created_id = None
    try:
        display_name = (str(claims.get("name") or "").strip() or email)[:200]
        creation = CreateUserRequest(
            name=display_name,
            email=email,
            company=company_id,
            role=Role.user,
            given_name=str(claims.get("given_name") or "")[:200],
            family_name=str(claims.get("family_name") or "")[:200],
        )
        created_id = AuthBLL.create_user(request=creation)
        # Keep reservation if this write fails. Never provision another user
        # automatically after an uncertain partial creation.
        updated = SSOIdentityLink.objects(id=link_id, user_id__exists=False).update_one(
            set__user_id=created_id,
        )
        if updated != 1:
            raise RuntimeError("SSO identity reservation was changed during provisioning")
        user = User.objects(id=created_id, company=company_id).first()
        if user is None:
            raise RuntimeError("Provisioned ClearML user cannot be found")
        sso_log.info("Created SSO user id=%s for identity=%s", created_id, link_id)
        return user, None
    except Exception:
        # Keep reservation even when creation failed: underlying DB writes may
        # have partially completed. Administrator must reconcile this record.
        sso_log.exception("SSO provisioning failed for identity=%s", link_id)
        raise


@sso_blueprint.get("/sso/callback")
def callback():
    if not enabled():
        return disabled_response()
    if request.args.get("error"):
        return ("Identity provider refused sign-in", 401)
    signed = request.cookies.get("clearml_sso_state")
    code, state = request.args.get("code"), request.args.get("state")
    if not signed or not code or not state:
        return ("Missing OIDC callback state", 400)
    try:
        expected = state_signer().loads(signed, max_age=600)
        if not secrets.compare_digest(expected["state"], state):
            return ("OIDC state mismatch", 400)
        d = discovery()
        exchange = requests.post(d["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": env("CLEARML_SSO_CALLBACK_URL"),
            "client_id": env("CLEARML_SSO_CLIENT_ID"),
            "client_secret": env("CLEARML_SSO_CLIENT_SECRET"),
            "code_verifier": expected["verifier"],
        }, timeout=10)
        exchange.raise_for_status()
        id_token = exchange.json()["id_token"]
        # The OIDC identity token is signed and checked against the provider's JWKS.
        key = jwt.PyJWKClient(d["jwks_uri"]).get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(id_token, key, algorithms=["RS256"],
                            audience=env("CLEARML_SSO_CLIENT_ID"), issuer=d["issuer"],
                            options={"require": ["exp", "iat", "iss", "sub", "aud"]})
        if not secrets.compare_digest(str(claims.get("nonce", "")), expected["nonce"]):
            return ("OIDC nonce mismatch", 401)
        email = claims.get("email", "").strip().lower()
        if not email or claims.get("email_verified") is not True:
            return ("A verified email claim is required", 403)
        user, failure = _resolve_sso_user(claims, email)
        if failure:
            return failure
        token = AuthBLL.get_token_for_user(
            user_id=user.id, company_id=user.company,
            expiration_sec=int(env("CLEARML_SSO_TOKEN_TTL_SEC")),
        ).token
        slo_store.register_session(token, claims)
        response = make_response(redirect(env("CLEARML_SSO_WEB_URL"), code=302))
        response.set_cookie("clearml_token_basic", token, secure=True, httponly=True,
                            samesite="Lax", path="/")
        response.delete_cookie("clearml_sso_state", path="/sso")
        return response
    except (BadSignature, SignatureExpired):
        return ("Invalid or expired OIDC state", 400)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("SSO callback failed")
        return ("SSO callback failed; check server logs", 503)


@sso_blueprint.post("/sso/backchannel-logout")
def backchannel_logout():
    if not enabled():
        return disabled_response()
    try:
        return slo_store.handle_backchannel(discovery(), env("CLEARML_SSO_CLIENT_ID"))
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Back-channel logout failed")
        return ("Back-channel logout unavailable", 503)


@sso_blueprint.get("/sso/session-status")
def sso_session_status():
    if not enabled():
        return disabled_response()
    return slo_store.session_status()


@sso_blueprint.get("/sso/logged-out")
def sso_logged_out():
    return slo_store.logged_out_page()


@sso_blueprint.get("/sso/local-logout")
def sso_local_logout():
    """ClearML-only sign-out landing page; never starts OIDC by itself."""
    from flask import Response
    from apiserver.service_repo.auth.payload import Token
    from apiserver.service_repo.auth.auth import is_token_revoked

    encoded = request.cookies.get("clearml_token_basic")
    if encoded:
        try:
            token = Token.from_encoded_token(encoded)
            if not is_token_revoked(token):
                return redirect("/dashboard", code=302)
        except Exception:
            # Invalid or expired token: stay on the signed-out page.
            pass

    html = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signed out of ClearML</title>
<style>body{font-family:system-ui,sans-serif;background:#f7f8fa;color:#222;
margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center}
main{background:#fff;max-width:530px;padding:36px;border-radius:12px;
box-shadow:0 6px 30px #0001;margin:20px}h1{font-size:24px}
a{color:#155cbd}</style></head><body><main>
<h1>You are no longer signed in to ClearML</h1>
<p>Try again from Authentik! Open the Authentik portal and click the ClearML icon to create a new ClearML session.</p>
<p><a href="https://authentik.ccixgtestbed.org/">Go to Authentik</a></p>
<p><small>After signing in again, refreshing this tab will reopen your ClearML dashboard.</small></p>
</main><script>
if(new URLSearchParams(window.location.search).get('notice')==='1'){
  window.history.replaceState({},'',window.location.pathname);
  window.alert('You are no longer signed in to ClearML. Try again from Authentik!');
}
</script></body></html>"""
    response = make_response(html, 200)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.delete_cookie("clearml_token_basic", path="/", secure=True,
                           httponly=True, samesite="Lax")
    return response
