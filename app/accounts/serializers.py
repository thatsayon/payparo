from rest_framework import serializers
from django.contrib.auth import get_user_model

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=4)
    password_confirm = serializers.CharField(write_only=True, min_length=4)
    full_name = serializers.CharField(required=False, default="", max_length=80)
    referral_code = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ("email", "full_name", "password", "password_confirm", "referral_code")

    def validate_email(self, value: str) -> str:
        return value.lower().strip()

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match."}
            )
        
        referral_code = attrs.get("referral_code")
        if referral_code:
            from app.refer.models import ReferralProfile
            if not ReferralProfile.objects.filter(referral_code=referral_code).exists():
                raise serializers.ValidationError(
                    {"referral_code": "Invalid referral code."}
                )
        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm")
        referral_code = validated_data.pop("referral_code", None)
        
        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            full_name=validated_data["full_name"],
        )
        
        if referral_code:
            from app.refer.models import ReferralProfile, ReferralEarning
            from django.utils import timezone
            try:
                referrer_profile = ReferralProfile.objects.get(referral_code=referral_code)
                if referrer_profile.user != user:
                    profile, _ = ReferralProfile.objects.get_or_create(user=user)
                    profile.referred_by = referrer_profile.user
                    profile.referred_at = timezone.now()
                    profile.save()
                    
                    ReferralEarning.objects.create(
                        referrer=referrer_profile.user,
                        referred_user=user,
                        amount=ReferralProfile.REFERRAL_COMMISSION_AMOUNT,
                        status=ReferralEarning.Status.PENDING
                    )
            except ReferralProfile.DoesNotExist:
                pass
                
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()


class UpdatePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField(min_length=4)
    retype_new_password = serializers.CharField(min_length=4)

    def validate(self, attrs):
        if attrs.get("new_password") != attrs.get("retype_new_password"):
            raise serializers.ValidationError(
                {"retype_new_password": "New passwords do not match."}
            )
        return attrs


class VerifyLogin2FASerializer(serializers.Serializer):
    two_factor_token = serializers.CharField()
    otp = serializers.CharField(max_length=6)


class Toggle2FASerializer(serializers.Serializer):
    enable = serializers.BooleanField()
    method = serializers.ChoiceField(choices=User.TwoFactorMethod.choices, required=False)


class IDCardUploadSerializer(serializers.Serializer):
    id_front = serializers.ImageField(required=True)
    id_back = serializers.ImageField(required=True)


class KYCIdentitySerializer(serializers.Serializer):
    id_number = serializers.CharField(max_length=50)
    full_name = serializers.CharField(max_length=120)
    father_name = serializers.CharField(max_length=120)
    mother_name = serializers.CharField(max_length=120)
    date_of_birth = serializers.DateField()
    present_address = serializers.CharField()
    permanent_address = serializers.CharField()
    gender = serializers.CharField(max_length=10)


class FaceImageUploadSerializer(serializers.Serializer):
    front_face = serializers.ImageField(required=True, help_text="Straight-on front face photo")
    left_face  = serializers.ImageField(required=True, help_text="Back/left side face photo")
    right_face = serializers.ImageField(required=True, help_text="Right side face photo")
