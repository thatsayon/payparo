from .base import *
from .base import env

DEBUG = True

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["*"])

#
# # Email Configuration (Hostinger SMTP)
# EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
#
# EMAIL_HOST = env("EMAIL_HOST")                 # smtp.hostinger.com
# EMAIL_PORT = env.int("EMAIL_PORT", default=465)
#
# # SSL config (for port 465)
# EMAIL_USE_TLS = False
# EMAIL_USE_SSL = True
#
# EMAIL_HOST_USER = env("EMAIL_HOST_USER")       # no-reply@ovation-app.com
# EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
#
# # MUST match the authenticated domain
# DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL") # no-reply@ovation-app.com
# SERVER_EMAIL = DEFAULT_FROM_EMAIL
