from django.db import models


class ProductLabel(models.Model):
    image = models.ImageField(upload_to="labels/")
    raw_text = models.TextField(blank=True)
    lot_number = models.CharField(max_length=100, blank=True)
    expiry_date = models.CharField(max_length=50, blank=True)
    expiry_date_parsed = models.DateField(null=True, blank=True)
    confidence = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]