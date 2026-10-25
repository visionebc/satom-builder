"""WSGI entry point: ``gunicorn satom_builder.wsgi:app`` (contract section 10)."""
from . import create_app

app = create_app()
