from .base import *
from .base import env

DEBUG = True

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['*'])

# EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Email Configuration
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = env('EMAIL_HOST', default='smtp.hostinger.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=465)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=False)
EMAIL_USE_SSL = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='no-reply@ovation-app.com')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='OvationAPP123!')

# Default "From" email
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='no-reply@dailo.app')
