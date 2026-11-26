from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone
from openpyxl import Workbook

from booking.models import Booking, Organization, Warehouse

# VIN, которых «нет» в сервисе учёта. Заглушка учёта будет считать найденными все остальные.
NOT_IN_ACCOUNTING = {"XWEPC81BDR0004421"}

SAMPLE_XLSX = settings.BASE_DIR / "sample_vins.xlsx"


def seed_vin(warehouse_id, day_offset, n):
    # Детерминированный VIN из 17 символов: одинаковый при каждом запуске seed.
    return f"SEED{warehouse_id:02d}{day_offset + 50:03d}{n:08d}"


class Command(BaseCommand):
    help = "Пересоздаёт тестовые данные: организации, склады, записи и пример Excel"

    @transaction.atomic
    def handle(self, *args, **options):
        # RESTART IDENTITY сбрасывает счётчики id: организации всегда получают id 1, 2, 3,
        # и org_id в выпущенных тестовых токенах остаётся верным после повторного seed.
        tables = ", ".join(m._meta.db_table for m in (Booking, Warehouse, Organization))
        with connection.cursor() as cursor:
            cursor.execute(f"TRUNCATE {tables} RESTART IDENTITY")

        company = Organization.objects.create(name="Астана Моторс", type=Organization.OrganizationType.COMPANY)
        step = Organization.objects.create(name="Степь Моторс", type=Organization.OrganizationType.DISTRIBUTOR)
        alatau = Organization.objects.create(name="Алатау Авто", type=Organization.OrganizationType.DISTRIBUTOR)

        astana1 = Warehouse.objects.create(name="Астана-1", distributor=step, daily_limit=40)
        Warehouse.objects.create(name="Караганда", distributor=step, daily_limit=20)
        almaty2 = Warehouse.objects.create(name="Алматы-2", distributor=alatau, daily_limit=30)
        Warehouse.objects.create(name="Шымкент", distributor=alatau, daily_limit=25)

        today = timezone.localdate()
        # (склад, смещение от сегодня в днях, сколько машин записать)
        plan = [
            (astana1, -10, 30),  # прошлое: видно при листании календаря назад
            (astana1, -3, 15),
            (astana1, 1, 12),
            (astana1, 2, 25),
            (astana1, 3, 37),  # осталось 3 места: на этот день грузим sample_vins.xlsx
            (astana1, 4, 40),  # мест нет совсем
            (almaty2, 1, 10),  # записи чужого дистрибьютора
            (almaty2, 2, 5),
        ]
        bookings = [
            Booking(
                warehouse=wh,
                date=today + timedelta(days=offset),
                vin=seed_vin(wh.id, offset, n),
                created_by="seed",
                created_by_org=wh.distributor,
            )
            for wh, offset, count in plan
            for n in range(count)
        ]
        Booking.objects.bulk_create(bookings)

        self.write_sample_xlsx(already_booked_vin=seed_vin(almaty2.id, 1, 0))

        free_day = today + timedelta(days=3)
        self.stdout.write(self.style.SUCCESS(
            f"Создано: 3 организации, 4 склада, {len(bookings)} записей.\n"
            f"Пример Excel: {SAMPLE_XLSX.name} — грузить на склад «Астана-1», дата {free_day:%d.%m.%Y} (свободно 3 из 40).\n"
            f"Ожидаемый итог: записано 3 из 9, остальные строки — с причинами."
        ))

    def write_sample_xlsx(self, already_booked_vin):
        wb = Workbook()
        ws = wb.active
        ws.title = "Машины"
        rows = [
            "LVVDB21B4RD012401",   # 2: записана
            "LVVDB21B4RD012402",   # 3: записана
            "Z94C241BBRR10887",    # 4: неверный VIN — 16 символов
            "XWEPC81BDR0004421",   # 5: нет в учёте
            already_booked_vin,    # 6: уже есть активная запись (на чужом складе)
            "LVVDB21B4RD012401",   # 7: повторяется в файле (строка 2)
            "Z94C241BBRR108880",   # 8: записана — третье, последнее свободное место
            "Z94C241BBRR108881",   # 9: не хватило мест
            "Z94C241BBRR108882",   # 10: не хватило мест
        ]
        ws.append(["VIN"])
        for vin in rows:
            ws.append([vin])
        wb.save(SAMPLE_XLSX)
