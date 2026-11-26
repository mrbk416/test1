from django.core.management.base import BaseCommand
from booking.models import Organization

from datetime import datetime, timedelta, timezone
import jwt
from django.conf import settings

class Command(BaseCommand):
    help = "Выпускает тестовые JWT-токены"

    def handle(self, *args, **options):
        for org in Organization.objects.all():
            payload = {
                "sub": f"user-{org.id}",
                "org_id": org.id,
                "org_type": org.type,
                "exp": datetime.now(timezone.utc) + timedelta(days=30),
            }
            token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
            self.stdout.write(f"{org.name}: {token}")