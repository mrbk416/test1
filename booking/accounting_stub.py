from ninja import NinjaAPI, Schema
from ninja.errors import HttpError

from booking.management.commands.seed import NOT_IN_ACCOUNTING


class LookupIn(Schema):
    vins: list[str]


class LookupOut(Schema):
    found: list[str]


stub_api = NinjaAPI(
    title="Заглушка сервиса учёта",
    urls_namespace="accounting_stub"
)


@stub_api.post("/api/v1/vehicles/lookup", response=LookupOut)
def lookup(request, payload: LookupIn):
    # За один раз можно передать не больше 200 VIN
    if len(payload.vins) > 200:
        raise HttpError(400, "Можно передать не больше 200 VIN")

    found = []

    for vin in payload.vins:
        if vin not in NOT_IN_ACCOUNTING:
            found.append(vin)

    result = {
        "found": found
    }

    return result