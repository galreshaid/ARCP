from django.db import models

class Procedure(models.Model):
    code = models.CharField(
        max_length=20,
        unique=True,
        db_index=True
    )

    name = models.CharField(
        max_length=255
    )

    body_region = models.CharField(
        max_length=100,
        help_text="Head, Chest, Abdomen, Spine, etc."
    )

    modality = models.ForeignKey(
        "core.Modality",
        on_delete=models.PROTECT,
        related_name="procedures"
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["modality__code", "body_region", "code"]

    def __str__(self):
        return f"{self.code} - {self.name}"
