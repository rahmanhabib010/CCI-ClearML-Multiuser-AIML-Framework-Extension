"""Trusted SSO administrator identity (OIDC issuer and immutable subject).

Both values are provisioned by the server operator, never supplied by an API
caller. A matching, server-created issuer/subject -> ClearML user link must
also exist. A username, email, or user-supplied role is never sufficient.
"""

import hashlib
import hmac
import os


def is_trusted_sso_admin(user_id: str, company_id: str) -> bool:
    issuer = os.environ.get("CLEARML_SSO_ADMIN_ISSUER", "").strip()
    subject = os.environ.get("CLEARML_SSO_ADMIN_SUB", "").strip()
    if not (issuer and subject and user_id and company_id):
        return False

    # The callback writes this link only after verifying the OIDC ID token's
    # signature, issuer, audience, expiry and nonce. Never link by email.
    from apiserver.sso_identity import SSOIdentityLink

    link_id = hashlib.sha256(
        (issuer + "\x00" + subject).encode("utf-8")
    ).hexdigest()
    link = SSOIdentityLink.objects(id=link_id).first()
    return bool(
        link
        and hmac.compare_digest(link.issuer, issuer)
        and hmac.compare_digest(link.subject, subject)
        and link.user_id == user_id
        and link.company == company_id
    )
