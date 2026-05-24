from django.urls import path
from .views import index, scan_label_view, label_history, label_detail, report_view

urlpatterns = [
    path("", index, name="index"),
    path("report/", report_view, name="report"),
    path("api/scan/", scan_label_view, name="scan_label"),
    path("api/history/", label_history, name="label_history"),
    path("api/labels/<int:pk>/", label_detail, name="label_detail"),
]

