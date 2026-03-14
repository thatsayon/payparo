from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Escrow, EscrowInstallment, EscrowImage, EscrowDocument, EscrowStatusHistory
from app.administration.models import FeeConfiguration

User = get_user_model()

MIN_IMAGES = 3


# ──────────────────────────────────────────────
# Nested read serializers
# ──────────────────────────────────────────────

class EscrowInstallmentSerializer(serializers.ModelSerializer):
    class Meta:
        model  = EscrowInstallment
        fields = ["id", "order", "amount"]


class EscrowImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model  = EscrowImage
        fields = ["id", "url", "uploaded_at"]

    def get_url(self, obj):
        return obj.image.url if obj.image else None


class EscrowDocumentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model  = EscrowDocument
        fields = ["id", "url", "uploaded_at"]

    def get_url(self, obj):
        return obj.file.url if obj.file else None


class EscrowStatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = EscrowStatusHistory
        fields = ["id", "status", "created_at"]


class ReceiverSerializer(serializers.ModelSerializer):
    class Meta:
        model  = User
        fields = ["id", "email", "username", "full_name"]


# ──────────────────────────────────────────────
# Create serializer (write-only)
# ──────────────────────────────────────────────

class EscrowCreateSerializer(serializers.Serializer):
    receiver_username = serializers.CharField(
        help_text="Email or username of the receiver."
    )
    role = serializers.ChoiceField(
        choices=Escrow.Role.choices,
        default=Escrow.Role.SELLER,
    )
    item_type = serializers.ChoiceField(
        choices=Escrow.ItemType.choices,
        default=Escrow.ItemType.PRODUCT,
    )
    product_name = serializers.CharField(max_length=255)
    description  = serializers.CharField()

    payment_option = serializers.ChoiceField(
        choices=Escrow.PaymentOption.choices,
        default=Escrow.PaymentOption.SINGLE,
    )

    # Required only for single payment
    price = serializers.DecimalField(
        max_digits=12, decimal_places=2,
        required=False, allow_null=True,
    )
    currency = serializers.CharField(max_length=10, default="USD", required=False)

    # Required only for installment payment (list of amounts)
    installments = serializers.ListField(
        child=serializers.DecimalField(max_digits=12, decimal_places=2),
        required=False,
        allow_empty=False,
        help_text="List of installment amounts. Required when payment_option=installment.",
    )

    # Required: minimum 3 product images
    images = serializers.ListField(
        child=serializers.ImageField(),
        min_length=MIN_IMAGES,
        help_text=f"Minimum {MIN_IMAGES} product images required.",
    )

    # Optional: multiple documents
    documents = serializers.ListField(
        child=serializers.FileField(),
        required=False,
        allow_empty=True,
        help_text="Optional supporting documents (PDF, DOC, DOCX, JPG, PNG).",
    )

    def validate(self, data):
        request = self.context["request"]
        creator = request.user

        # Resolve receiver by username
        receiver_username = data["receiver_username"].strip()
        if receiver_username.startswith("@"):
            receiver_username = receiver_username[1:]
            
        try:
            receiver = User.objects.get(username=receiver_username, is_active=True)
        except User.DoesNotExist:
            raise serializers.ValidationError(
                {"receiver_username": "No active user found with this username."}
            )

        if receiver == creator:
            raise serializers.ValidationError(
                {"receiver_username": "You cannot create an escrow with yourself."}
            )

        data["receiver"] = receiver

        # Payment option cross-validation
        payment_option = data.get("payment_option")

        if payment_option == Escrow.PaymentOption.SINGLE:
            if not data.get("price"):
                raise serializers.ValidationError(
                    {"price": "Price is required for Single Payment."}
                )
            data.pop("installments", None)

        elif payment_option == Escrow.PaymentOption.INSTALLMENT:
            if not data.get("installments"):
                raise serializers.ValidationError(
                    {"installments": "At least one installment amount is required for Custom Installments."}
                )
            data["price"] = None  # no single price

        return data

    @transaction.atomic
    def create(self, validated_data):
        creator       = self.context["request"].user
        receiver      = validated_data.pop("receiver")
        images        = validated_data.pop("images")
        documents     = validated_data.pop("documents", [])
        installments  = validated_data.pop("installments", [])
        validated_data.pop("receiver_username")

        price = validated_data.get("price")
        payment_option = validated_data["payment_option"]

        # Calculate Fee and Total Amount
        fee_config = FeeConfiguration.objects.first()
        escrow_fee = fee_config.escrow_fee if fee_config else 0.00
        
        # NOTE: escrow_fee is a fixed amount (or assumed as such based on model DecimalField max_digits=6, decimal_places=2)
        fee_amount = escrow_fee

        if payment_option == Escrow.PaymentOption.SINGLE:
            total_amount = price + fee_amount if price else None
        else:
            # Installment payment
            installments_total = sum(installments) if installments else 0
            total_amount = installments_total + fee_amount

        escrow = Escrow.objects.create(
            created_by=creator,
            receiver=receiver,
            role=validated_data["role"],
            item_type=validated_data["item_type"],
            product_name=validated_data["product_name"],
            description=validated_data["description"],
            payment_option=payment_option,
            price=price,
            fee_amount=fee_amount,
            total_amount=total_amount,
            currency=validated_data.get("currency", "USD"),
        )

        # Bulk-create product images
        EscrowImage.objects.bulk_create([
            EscrowImage(escrow=escrow, image=img)
            for img in images
        ])

        # Bulk-create documents (optional)
        if documents:
            EscrowDocument.objects.bulk_create([
                EscrowDocument(escrow=escrow, file=doc)
                for doc in documents
            ])

        # Bulk-create installments (if custom installment plan)
        if installments:
            EscrowInstallment.objects.bulk_create([
                EscrowInstallment(escrow=escrow, amount=amount, order=i + 1)
                for i, amount in enumerate(installments)
            ])

        return escrow


# ──────────────────────────────────────────────
# Read serializers
# ──────────────────────────────────────────────

class EscrowListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views."""
    created_by = ReceiverSerializer(read_only=True)
    receiver   = ReceiverSerializer(read_only=True)
    cover_image = serializers.SerializerMethodField()

    class Meta:
        model  = Escrow
        fields = [
            "id", "order_id", "product_name", "role", "item_type",
            "payment_option", "price", "fee_amount", "total_amount", "currency", "status",
            "created_by", "receiver", "cover_image", "created_at",
        ]

    def get_cover_image(self, obj):
        first = obj.images.first()
        return first.image.url if first else None


class EscrowDetailSerializer(serializers.ModelSerializer):
    """Full serializer for detail view including nested data."""
    created_by   = ReceiverSerializer(read_only=True)
    receiver     = ReceiverSerializer(read_only=True)
    images       = EscrowImageSerializer(many=True, read_only=True)
    documents    = EscrowDocumentSerializer(many=True, read_only=True)
    installments = EscrowInstallmentSerializer(many=True, read_only=True)
    status_history = EscrowStatusHistorySerializer(many=True, read_only=True)

    class Meta:
        model  = Escrow
        fields = [
            "id", "order_id", "product_name", "role", "item_type",
            "payment_option", "price", "fee_amount", "total_amount", "currency", "status",
            "description", "created_by", "receiver",
            "images", "documents", "installments", "status_history",
            "created_at", "updated_at",
        ]


class OrderHistorySerializer(serializers.ModelSerializer):

    class Meta:
        model = Escrow
        fields = [
            "id", "order_id", "product_name", "status", "created_at"
        ]


class OrderHistoryDetailSerializer(serializers.ModelSerializer):
    created_by = ReceiverSerializer(read_only=True)
    receiver   = ReceiverSerializer(read_only=True)
    cover_image = serializers.SerializerMethodField()
    status_history = EscrowStatusHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Escrow
        fields = [
            "id", "order_id", "product_name", "price", 
            "created_by", "receiver", "cover_image", 
            "status", "status_history", "created_at"
        ]

    def get_cover_image(self, obj):
        first = obj.images.first()
        return first.image.url if first else None
