from rest_framework import serializers
from django.contrib.auth import get_user_model

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=4)
    password_confirm = serializers.CharField(write_only=True, min_length=4)
    full_name = serializers.CharField(required=False, default="", max_length=80)

    class Meta:
        model = User
        fields = ("email", "full_name", "password", "password_confirm")

    def validate_email(self, value: str) -> str:
        return value.lower().strip()

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match."}
            )
        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm")
        return User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            full_name=validated_data["full_name"],
        )


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()


class UpdatePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField(min_length=4)


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
