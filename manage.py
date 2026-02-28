#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
import environ
from pathlib import Path

def main():
    """Run administrative tasks."""
    BASE_DIR = Path(__file__).resolve().parent
    env = environ.Env()
    environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

    current_env = env('ENVIRONMENT', default='dev')

    if current_env == 'prod':
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    else:
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
