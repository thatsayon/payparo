from django.contrib import admin
from .models import UserAccount, OTP, KYCSubmission

admin.site.register(UserAccount)
admin.site.register(OTP)
admin.site.register(KYCSubmission)
