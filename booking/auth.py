import jwt
from django.conf import settings
from ninja.security import HttpBearer
from .models import Organization


class JWTAuth(HttpBearer):
    def authenticate(self, request, token):
        try:
            data = jwt.decode(
                token,
                settings.JWT_SECRET,
                algorithms=[settings.JWT_ALGORITHM],
                options={
                    "require": ["exp", "sub", "org_id", "org_type"]
                }
            )
        except jwt.InvalidTokenError:
            return None

        if type(data["org_id"]) is not int:
            return None

        if data["org_type"] not in Organization.OrganizationType.values:
            return None

        return data

