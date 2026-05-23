from django.contrib import admin
from .models import UserAccount, OTP, KYCSubmission, UserSubscription

admin.site.register(UserAccount)
admin.site.register(OTP)
admin.site.register(KYCSubmission)
admin.site.register(UserSubscription)
