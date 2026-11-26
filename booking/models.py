from django.db import models


class Organization(models.Model):
    class OrganizationType(models.TextChoices):
        COMPANY = "company", "Компания"
        DISTRIBUTOR = "distributor", "Дистрибьютор"
    name = models.CharField(max_length=100, unique=True)
    type = models.CharField(max_length=20, choices=OrganizationType.choices)
    def __str__(self):
        return self.name


class Warehouse(models.Model):
    name = models.CharField(max_length=100, unique=True)
    distributor = models.ForeignKey("Organization", on_delete=models.PROTECT)
    daily_limit=models.PositiveIntegerField(default=40)
    def __str__(self):
        return self.name


class Booking(models.Model):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    date = models.DateField()
    vin = models.CharField(max_length=17)
    is_active = models.BooleanField(default=True)
    created_by = models.CharField(max_length=100)
    created_by_org = models.ForeignKey("Organization", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["vin"], condition=models.Q(is_active=True), name="one_active_booking_per_vin")]
        indexes = [models.Index(fields=["warehouse", "date"])]

    def __str__(self):
        return self.vin
