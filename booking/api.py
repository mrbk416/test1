from ninja import NinjaAPI
from .auth import JWTAuth
import calendar
from django.db.models import Count
from ninja.errors import HttpError
from io import BytesIO
from django.http import HttpResponse
from openpyxl import Workbook, load_workbook
from datetime import date
from typing import Literal
import httpx
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from ninja import Schema, Form, File
from ninja.files import UploadedFile

from .models import Warehouse, Booking



class OrganizationOut(Schema):
    id: int
    name: str


class WarehouseOut(Schema):
    id: int
    name: str
    daily_limit: int
    distributor: OrganizationOut


class AvailabilityOut(Schema):
    date: date
    daily_limit: int
    booked: int
    free: int
    can_book: bool


class CalendarDayOut(Schema):
    date: date
    booked: int
    free: int
    can_book: bool


class CalendarOut(Schema):
    warehouse: WarehouseOut
    month: str
    days: list[CalendarDayOut]


class ErrorOut(Schema):
    code: str
    message: str
    rows_count: int | None = None


class BookingRowOut(Schema):
    row: int
    vin: str
    status: Literal[
        "booked",
        "invalid_vin",
        "duplicate_in_file",
        "not_found",
        "already_booked",
        "no_slots"
    ]
    message: str
    duplicate_of_row: int | None = None


class BookingResultOut(Schema):
    warehouse: WarehouseOut
    date: date
    total: int
    booked_count: int
    daily_limit: int
    free_after: int
    rows: list[BookingRowOut]


api = NinjaAPI(title="Запись машин на приёмку склада", auth=JWTAuth())

@api.get("/warehouses", response=list[WarehouseOut])
def list_warehouses(request):
    result = []
    warehouses = Warehouse.objects.all()
    if request.auth["org_type"] == "distributor":
        warehouses = Warehouse.objects.filter(distributor_id=request.auth["org_id"])
    for warehouse in warehouses:
            card = {
                "id": warehouse.id,
                "name": warehouse.name,
                "daily_limit": warehouse.daily_limit,
                "distributor": {
                    "id": warehouse.distributor_id,
                    "name": warehouse.distributor.name,
                }
            }
            result.append(card)
    return result


@api.get("/warehouses/{warehouse_id}/availability", response=AvailabilityOut)
def availability(request, warehouse_id: int, date: date):
    warehouses = Warehouse.objects.all()

    if request.auth["org_type"] == "distributor":
        warehouses = Warehouse.objects.filter(
            distributor_id=request.auth["org_id"]
        )
    warehouse = get_object_or_404(warehouses, id=warehouse_id)
    booked = Booking.objects.filter(warehouse=warehouse, date=date, is_active=True).count()
    free = warehouse.daily_limit - booked
    can_book = free > 0 and date >= timezone.localdate()
    result = {
        "date": date,
        "daily_limit": warehouse.daily_limit,
        "booked": booked,
        "free": free,
        "can_book": can_book
    }
    return result


@api.get("/warehouses/{warehouse_id}/calendar", response=CalendarOut)
def warehouse_calendar(request, warehouse_id: int, month: str):
    warehouses = Warehouse.objects.all()

    if request.auth["org_type"] == "distributor":
        warehouses = Warehouse.objects.filter(
            distributor_id=request.auth["org_id"]
        )
    warehouse = get_object_or_404(warehouses, id=warehouse_id)
    if (
        len(month) != 7
        or month[4] != "-"
        or not month[:4].isdigit()
        or not month[5:].isdigit()
    ):
        raise HttpError(400, "Укажите месяц в формате YYYY-MM")
    year = int(month[:4])
    month_number = int(month[5:])
    if year < 1 or month_number < 1 or month_number > 12:
        raise HttpError(400, "Неправильный год или месяц")
    days_count = calendar.monthrange(year, month_number)[1]
    bookings = Booking.objects.filter(
        warehouse=warehouse,
        date__year=year,
        date__month=month_number,
        is_active=True
    ).values("date").annotate(booked=Count("id"))
    booked_by_date = {}

    for booking in bookings:
        booked_by_date[booking["date"]] = booking["booked"]

    days = []
    today = timezone.localdate()

    for day_number in range(1, days_count + 1):
        day_date = date(year, month_number, day_number)
        booked = booked_by_date.get(day_date, 0)
        free = warehouse.daily_limit - booked
        can_book = free > 0 and day_date >= today

        day = {
            "date": day_date,
            "booked": booked,
            "free": free,
            "can_book": can_book
        }
        days.append(day)

    warehouse_card = {
        "id": warehouse.id,
        "name": warehouse.name,
        "daily_limit": warehouse.daily_limit,
        "distributor": {
            "id": warehouse.distributor_id,
            "name": warehouse.distributor.name
        }
    }

    result = {
        "warehouse": warehouse_card,
        "month": month,
        "days": days
    }

    return result


@api.get("/bookings/template")
def download_template(request):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["VIN"])
    buffer = BytesIO()
    workbook.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="template.xlsx"'
    return response


@api.post(
    "/warehouses/{warehouse_id}/bookings",
    response={
        200: BookingResultOut,
        400: ErrorOut,
        409: ErrorOut,
        503: ErrorOut
    },
    exclude_none=True
)


def create_bookings(request, warehouse_id: int, date: date = Form(...), file: UploadedFile = File(...)):
    warehouses = Warehouse.objects.all()

    if request.auth["org_type"] == "distributor":
        warehouses = Warehouse.objects.filter(
            distributor_id=request.auth["org_id"]
        )

    warehouse = get_object_or_404(warehouses, id=warehouse_id)

    # проверка даты на допуск
    if date < timezone.localdate():
        return 400, {
            "code": "past_date",
            "message": "Нельзя записать на прошедшую дату"
        }

    # считаем свободные места
    booked = Booking.objects.filter(
        warehouse=warehouse,
        date=date,
        is_active=True
    ).count()

    free = warehouse.daily_limit - booked

    if free <= 0:
        return 409, {
            "code": "no_free_slots",
            "message": "На эту дату нет свободных мест"
        }

    # считаем строки эксельки
    workbook = load_workbook(file, read_only=True, data_only=True)
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    workbook.close()

    # ищем вин
    vin_column = None

    if len(rows) > 0:
        for index, value in enumerate(rows[0]):
            if str(value).strip().upper() == "VIN":
                vin_column = index
                break

    if vin_column is None:
        return 400, {
            "code": "no_vin_column",
            "message": "В файле нет колонки VIN"
        }

    # собираем все вин из екзельки
    vin_rows = []

    for row_number, row in enumerate(rows[1:], start=2):
        if vin_column >= len(row):
            continue

        value = row[vin_column]

        if value is None:
            continue

        vin = str(value).strip().upper()

        if vin == "":
            continue

        vin_rows.append({
            "row": row_number,
            "vin": vin,
            "status": None,
            "message": None,
            "duplicate_of_row": None
        })

    # проверка строк с вином
    rows_count = len(vin_rows)

    if rows_count > 200:
        return 400, {
            "code": "too_many_rows",
            "message": f"В файле {rows_count} строк, максимум 200",
            "rows_count": rows_count
        }

    # проверка длину вин
    for row in vin_rows:
        vin = row["vin"]

        if len(vin) != 17:
            row["status"] = "invalid_vin"
            row["message"] = f"Неверный VIN — {len(vin)} символов"

    # ищем повторы
    first_row_by_vin = {}

    for row in vin_rows:
        if row["status"] is not None:
            continue

        vin = row["vin"]

        if vin in first_row_by_vin:
            first_row = first_row_by_vin[vin]

            row["status"] = "duplicate_in_file"
            row["message"] = f"Повторяется в файле (строка {first_row})"
            row["duplicate_of_row"] = first_row
        else:
            first_row_by_vin[vin] = row["row"]

    # собираем все вин которые прошли
    vins = []

    for row in vin_rows:
        if row["status"] is None:
            vins.append(row["vin"])

    # разом все отправляем
    found = set()

    if len(vins) > 0:
        try:
            response = httpx.post(
                f"{settings.ACCOUNTING_URL}/api/v1/vehicles/lookup",
                json={"vins": vins},
                timeout=5
            )

            response.raise_for_status()
            data = response.json()

            if not isinstance(data, dict):
                raise ValueError("Неверный ответ сервиса учёта")

            if not isinstance(data["found"], list):
                raise ValueError("Неверный список VIN")

            for vin in data["found"]:
                if not isinstance(vin, str):
                    raise ValueError("VIN должен быть строкой")

            found = set(data["found"])

        except (httpx.HTTPError, ValueError, KeyError):
            return 503, {
                "code": "accounting_unavailable",
                "message": "Сервис учёта недоступен, попробуйте позже"
            }

    # помечаем машины которых нет в учёте
    for row in vin_rows:
        if row["status"] is None:
            if row["vin"] not in found:
                row["status"] = "not_found"
                row["message"] = "Нет в учёте"

    # собираем вин которые ещё нужно проверить
    vins = []

    for row in vin_rows:
        if row["status"] is None:
            vins.append(row["vin"])

    # ищем вин у еоторых активная
    booked_vins = Booking.objects.filter(
        vin__in=vins,
        is_active=True
    ).values_list("vin", flat=True)

    booked_vins = set(booked_vins)

    for row in vin_rows:
        if row["status"] is None:
            if row["vin"] in booked_vins:
                row["status"] = "already_booked"
                row["message"] = "Уже есть активная запись"

    # блокаем склад и сохраняем прошедшие
    with transaction.atomic():
        warehouse = Warehouse.objects.select_for_update().get(
            id=warehouse.id
        )

        # еще раз считаем
        booked = Booking.objects.filter(
            warehouse=warehouse,
            date=date,
            is_active=True
        ).count()

        free = warehouse.daily_limit - booked
        to_create = []

        # места по порядку как на екзельке
        for row in vin_rows:
            if row["status"] is not None:
                continue

            if len(to_create) < free:
                row["status"] = "booked"
                row["message"] = "Записана"

                booking = Booking(
                    warehouse=warehouse,
                    date=date,
                    vin=row["vin"],
                    is_active=True,
                    created_by=request.auth["sub"],
                    created_by_org_id=request.auth["org_id"]
                )

                to_create.append(booking)
            else:
                row["status"] = "no_slots"
                row["message"] = "Не хватило мест"

        # сохраняем записи которые прошли
        Booking.objects.bulk_create(to_create)

        booked_count = len(to_create)
        free_after = free - booked_count

    # карточка склада
    warehouse_card = {
        "id": warehouse.id,
        "name": warehouse.name,
        "daily_limit": warehouse.daily_limit,
        "distributor": {
            "id": warehouse.distributor_id,
            "name": warehouse.distributor.name
        }
    }

    # сборка итогово ответа
    result = {
        "warehouse": warehouse_card,
        "date": date,
        "total": rows_count,
        "booked_count": booked_count,
        "daily_limit": warehouse.daily_limit,
        "free_after": free_after,
        "rows": vin_rows
    }

    return 200, result