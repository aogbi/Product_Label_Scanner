web: python manage.py migrate && python manage.py collectstatic --noinput && gunicorn label_scanner.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120
