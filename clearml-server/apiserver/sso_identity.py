"""OIDC identity links stored in the ClearML auth database.

A link is RESERVED before user creation. An interrupted reservation stays pending
and requires administrator reconciliation rather than risking an account takeover.
"""
from datetime import datetime

from mongoengine import Document, StringField, DateTimeField

from apiserver.database import Database


class SSOIdentityLink(Document):
    meta = {
        "db_alias": Database.auth,
        "collection": "clearml_sso_identity_links",
        "strict": True,
        "indexes": ["user_id"],
    }

    # sha256(issuer + NUL + subject), so issuer+subject pairs are unique.
    id = StringField(primary_key=True)
    issuer = StringField(required=True)
    subject = StringField(required=True)
    email_at_creation = StringField(required=True)
    company = StringField(required=True)
    user_id = StringField()  # Unset means pending creation / needs reconciliation.
    created = DateTimeField(default=datetime.utcnow)
