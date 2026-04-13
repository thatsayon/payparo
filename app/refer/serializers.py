from rest_framework import serializers
from django.contrib.auth import get_user_model

User = get_user_model()

class ReferredUserSerializer(serializers.ModelSerializer):
    profile_pic_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'full_name', 'profile_pic_url', 'date_joined']

    def get_profile_pic_url(self, obj):
        if not obj.profile_pic:
            return None
        if hasattr(obj.profile_pic, "url"):
            return obj.profile_pic.url
        import cloudinary
        return cloudinary.CloudinaryImage(str(obj.profile_pic)).build_url()
