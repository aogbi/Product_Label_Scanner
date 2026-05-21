# Product Label Scanner

Simple Django project for scanning and processing product labels using OCR.

## Requirements

- Python 3.8+ (Linux)
- Git

All Python dependencies are listed in `requirements.txt`.

## Quick setup (development)

1. Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Apply database migrations (this project uses SQLite by default):

```bash
python manage.py migrate
```

4. (Optional) Create a superuser to access the Django admin:

```bash
python manage.py createsuperuser
```

5. Run the development server:

```bash
python manage.py runserver
```

Open your browser at http://127.0.0.1:8000/ to view the app.

## Notes

- The SQLite database file is `db.sqlite3` in the project root.
- Uploaded label images are stored in the `media/labels/` directory.
- The scanner app code lives in the `scanner/` directory (views, models, OCR integration).
- Templates are under `scanner/templates/scanner/` and static assets under `static/`.

## Running in production

This README covers development only. For production, configure a proper WSGI server (e.g. Gunicorn), set `DEBUG = False`, configure `ALLOWED_HOSTS`, and serve static/media files via a web server.

Example (basic Gunicorn run):

```bash
pip install gunicorn
gunicorn label_scanner.wsgi:application --bind 0.0.0.0:8000
```

Remember to collect static files before deploying:

```bash
python manage.py collectstatic --noinput
```

## Troubleshooting

- If you see import errors, ensure the virtualenv is activated and `requirements.txt` was installed.
- If ports are in use, run the server on another port: `python manage.py runserver 0.0.0.0:8001`.

---

If you want, I can also update `requirements.txt`, add a Dockerfile, or create a `README` section describing the OCR workflow in more detail.
