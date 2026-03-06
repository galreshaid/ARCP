class Exam(models.Model):
    accession_number = models.CharField(max_length=64, unique=True)
    order_id = models.CharField(max_length=64)

    procedure = models.ForeignKey(
        "core.Procedure",
        on_delete=models.PROTECT,
        null=True,
        blank=True
    )

    modality = models.ForeignKey("core.Modality", on_delete=models.PROTECT)

    procedure_name = models.CharField(max_length=255)
    body_region = models.CharField(max_length=100, blank=True)

    # باقي الحقول...
