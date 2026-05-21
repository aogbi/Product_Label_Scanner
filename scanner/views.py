import logging

from django.shortcuts import render
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response

from .models import ProductLabel
from .serializers import ProductLabelSerializer
from .ocr_service import scan_label

logger = logging.getLogger(__name__)


def index(request):
    """Render the main scanner UI."""
    return render(request, "scanner/index.html")  # templates/scanner/index.html


@api_view(["POST"])
@parser_classes([MultiPartParser, FormParser])
def scan_label_view(request):
    """
    POST /api/scan/
    Upload a label image, run OCR, save results, return JSON.
    """
    image_file = request.FILES.get("image")
    if not image_file:
        return Response(
            {"error": "No image file provided. Send 'image' in form-data."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Validate file type
    allowed_types = ["image/jpeg", "image/png", "image/webp", "image/bmp", "image/tiff"]
    if image_file.content_type not in allowed_types:
        return Response(
            {"error": f"Unsupported file type: {image_file.content_type}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Save the image via the model
    label = ProductLabel(image=image_file)
    label.save()

    try:
        # Run OCR pipeline
        result = scan_label(label.image.path)

        # Update model fields with extracted data
        label.raw_text = result.get("raw_text", "")
        label.lot_number = result.get("lot_number", "")
        label.expiry_date = result.get("expiry_date", "")
        label.expiry_date_parsed = result.get("expiry_date_parsed")
        label.confidence = result.get("confidence", 0.0)
        label.save()

        serializer = ProductLabelSerializer(label)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    except Exception as exc:
        logger.exception("Scan failed for label %s: %s", label.id, exc)
        return Response(
            {"error": "OCR processing failed. Please try again."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
def label_history(request):
    """
    GET /api/history/
    Return all scanned labels ordered by newest first.
    """
    labels = ProductLabel.objects.all()
    serializer = ProductLabelSerializer(labels, many=True)
    return Response(serializer.data)


@api_view(["PATCH", "PUT", "DELETE"])
def label_detail(request, pk):
    """
    PATCH/PUT /api/labels/<id>/
    Update editable fields (lot_number, expiry_date) on a scan record.

    DELETE /api/labels/<id>/
    Remove a scan record and its associated image file.
    """
    try:
        label = ProductLabel.objects.get(pk=pk)
    except ProductLabel.DoesNotExist:
        return Response({"error": "Label not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method in ("PATCH", "PUT"):
        partial = request.method == "PATCH"
        serializer = ProductLabelSerializer(label, data=request.data, partial=partial)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    # DELETE
    if label.image:
        try:
            label.image.delete(save=False)
        except Exception:
            pass
    label.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
