from rest_framework import serializers
from .models import ProductLabel


class ProductLabelSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductLabel
        fields = [
            "id",
            "image",
            "raw_text",
            "lot_number",
            "expiry_date",
            "expiry_date_parsed",
            "confidence",
            "created_at",
        ]
