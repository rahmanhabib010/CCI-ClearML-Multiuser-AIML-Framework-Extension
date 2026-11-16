"""Authentik OIDC back-channel logout and ClearML session revocation.

Uses ClearML's existing Redis-backed revoked_tokens set, so authorization
checks in service_repo/auth/auth.py reject revoked JWT session IDs.
"""
import hashlib
import json
import logging
import time

import jwt
from flask import jsonify, make_response, request

from apiserver.redis_manager import redman
from apiserver.service_repo.auth.payload import Token

LOG = logging.getLogger(__name__)
EVENT = "http://schemas.openid.net/event/backchannel-logout"
PREFIX = "clearml:sso:slo:v1:"


def _redis():
    return redman.connection("apiserver")


def _key(kind, *parts):
    data = json.dumps(list(parts), separators=(",", ":"), ensure_ascii=True)
    return PREFIX + kind + ":" + hashlib.sha256(data.encode()).hexdigest()


def register_session(encoded_token, oidc_claims):
    """Register a newly issued ClearML JWT by verified Authentik identity."""
    token = Token.from_encoded_token(encoded_token)
    if not token.session_id:
        raise RuntimeError("ClearML SSO JWT has no session ID")
    issuer, subject = oidc_claims["iss"], oidc_claims["sub"]
    if not isinstance(subject, str) or not subject:
        raise ValueError("missing OIDC subject")
    claims = Token.decode(encoded_token)
    expiry = claims.get("exp")
    if not isinstance(expiry, (int, float)):
        raise RuntimeError("ClearML SSO JWT requires numeric exp")
    ttl = max(1, int(expiry - time.time()) + 120)
    sid = oidc_claims.get("sid")
    record = {"exp": int(expiry), "iss": issuer, "sub": subject, "sid": sid}
    session_key = _key("session", token.session_id)
    subject_key = _key("subject", issuer, subject)
    r = _redis()
    p = r.pipeline(transaction=True)
    p.setex(session_key, ttl, json.dumps(record))
    p.sadd(subject_key, token.session_id)
    p.expire(subject_key, max(ttl, 7200))
    if sid:
        sid_key = _key("sid", issuer, sid)
        p.sadd(sid_key, token.session_id)
        p.expire(sid_key, max(ttl, 7200))
    p.execute()


def verify_logout_token(encoded, discovery, client_id):
    if not isinstance(encoded, str) or not encoded or len(encoded) > 16000:
        raise ValueError("missing or oversized logout token")
    key = jwt.PyJWKClient(discovery["jwks_uri"]).get_signing_key_from_jwt(encoded).key
    claims = jwt.decode(
        encoded, key, algorithms=["RS256"],
        audience=client_id, issuer=discovery["issuer"],
        options={"require": ["iss", "aud", "iat", "jti", "events"]},
        leeway=60,
    )
    events = claims.get("events")
    if not isinstance(events, dict) or not isinstance(events.get(EVENT), dict):
        raise ValueError("missing OIDC back-channel logout event")
    if "nonce" in claims:
        raise ValueError("nonce prohibited in logout token")
    if not claims.get("sub") and not claims.get("sid"):
        raise ValueError("logout token lacks sub/sid")
    if not isinstance(claims.get("jti"), str) or not claims["jti"]:
        raise ValueError("invalid logout jti")
    now = time.time()
    if not isinstance(claims["iat"], (int, float)) or abs(now - claims["iat"]) > 300:
        raise ValueError("stale logout token")
    return claims


def revoke_sessions(claims):
    """Revoke OP session by sid, otherwise all sessions for issuer+subject."""
    r = _redis()
    issuer = claims["iss"]
    if claims.get("sid"):
        lookup = _key("sid", issuer, claims["sid"])
    else:
        lookup = _key("subject", issuer, claims["sub"])
    session_ids = r.smembers(lookup)
    now = int(time.time())
    for raw in session_ids:
        session_id = raw.decode() if isinstance(raw, bytes) else raw
        data = r.get(_key("session", session_id))
        if not data:
            continue
        details = json.loads(data)
        if details["iss"] != issuer:
            continue
        if claims.get("sub") and details["sub"] != claims["sub"]:
            continue
        if claims.get("sid") and details.get("sid") != claims["sid"]:
            continue
        exp = int(details["exp"])
        if exp > now:
            r.zadd("revoked_tokens", {session_id: exp})
    r.zremrangebyscore("revoked_tokens", 0, now)


def handle_backchannel(discovery, client_id):
    if request.content_length is not None and request.content_length > 20000:
        return ("Payload too large", 413)
    try:
        claims = verify_logout_token(request.form.get("logout_token"), discovery, client_id)
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        LOG.warning("Rejected invalid back-channel logout token")
        return ("Invalid logout token", 400)
    except Exception:
        LOG.exception("Logout verification unavailable")
        return ("Logout validation temporarily unavailable", 503)
    replay_key = _key("jti", claims["iss"], claims["jti"])
    r = _redis()
    if not r.set(replay_key, "1", nx=True, ex=600):
        return ("", 200)  # idempotent delivery
    try:
        revoke_sessions(claims)
    except Exception:
        r.delete(replay_key)  # permit a legitimate retry
        LOG.exception("Logout revocation failed")
        return ("Logout processing failed", 503)
    return ("", 200)


def session_status():
    """Browser poll: only monitor JWTs registered by this SSO integration."""
    encoded = request.cookies.get("clearml_token_basic")
    if not encoded:
        response = jsonify({"monitored": False, "active": False})
        response.headers["Cache-Control"] = "no-store"
        return response
    try:
        token = Token.from_encoded_token(encoded)
        tracked = bool(_redis().exists(_key("session", token.session_id)))
        if not tracked:
            result = {"monitored": False, "active": False}
        else:
            from apiserver.service_repo.auth.auth import is_token_revoked
            result = {"monitored": True, "active": not is_token_revoked(token)}
    except jwt.PyJWTError:
        result = {"monitored": False, "active": False}
    except Exception:
        LOG.exception("SSO status check failed")
        return ("Status unavailable", 503)
    response = make_response(jsonify(result))
    response.headers["Cache-Control"] = "no-store"
    if result["monitored"] and not result["active"]:
        response.delete_cookie("clearml_token_basic", path="/", secure=True, httponly=True, samesite="Lax")
    return response


def logged_out_page():
    response = make_response('''<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Signed out of ClearML</title></head><body style="font:16px system-ui;max-width:620px;margin:12vh auto;padding:1rem">
<h1>Signed out of ClearML</h1><p>Your Authentik session ended. Your ClearML session was revoked.</p>
<p><a href="/sso/start">Sign in again</a></p></body></html>''')
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie("clearml_token_basic", path="/", secure=True, httponly=True, samesite="Lax")
    return response
