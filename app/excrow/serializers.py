from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Escrow, EscrowInstallment, EscrowImage, EscrowDocument, EscrowStatusHistory, EscrowDispute, EscrowDisputeImage
from app.administration.models import FeeConfiguration

User = get_user_model()

MIN_IMAGES = 3


# ──────────────────────────────────────────────
# Nested read serializers
# ──────────────────────────────────────────────

class EscrowInstallmentSerializer(serializers.ModelSerializer):
    class Meta:
        model  = EscrowInstallment
        fields = ["id", "order", "amount", "is_paid", "paid_at"]


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


class EscrowDisputeImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = EscrowDisputeImage
        fields = ["id", "image", "created_at"]


class EscrowDisputeSerializer(serializers.ModelSerializer):
    images = EscrowDisputeImageSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    raised_by = ReceiverSerializer(read_only=True)

    class Meta:
        model = EscrowDispute
        fields = [
            "id", "reason", "note", "status", "status_display",
            "decision_reason", "ai_decision", "ai_confidence", "ai_summary",
            "seller_response", "seller_response_deadline", "penalty_charged",
            "raised_by", "created_at", "images",
        ]



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

    # Required only for product: minimum 3 product images
    images = serializers.ListField(
        child=serializers.ImageField(),
        required=False,
        help_text=f"Minimum {MIN_IMAGES} product images required for products.",
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

        has_price = bool(data.get("price"))
        has_installments = bool(data.get("installments"))

        if has_price and has_installments:
            raise serializers.ValidationError(
                "You cannot provide both a price and installments. Please provide only one."
            )

        if payment_option == Escrow.PaymentOption.SINGLE:
            if not has_price:
                raise serializers.ValidationError(
                    {"price": "Price is required for Single Payment."}
                )
            data.pop("installments", None)

        elif payment_option == Escrow.PaymentOption.INSTALLMENT:
            if not has_installments:
                raise serializers.ValidationError(
                    {"installments": "At least one installment amount is required for Custom Installments."}
                )
            data["price"] = None  # no single price

        # Item type specific validation: Images required for Product
        item_type = data.get("item_type", Escrow.ItemType.PRODUCT)
        images = data.get("images", [])
        
        if item_type == Escrow.ItemType.PRODUCT:
            if not images or len(images) < MIN_IMAGES:
                raise serializers.ValidationError(
                    {"images": f"At least {MIN_IMAGES} images are required for product type escrows."}
                )

        return data

    @transaction.atomic
    def create(self, validated_data):
        creator       = self.context["request"].user
        receiver      = validated_data.pop("receiver")
        images        = validated_data.pop("images", [])
        documents     = validated_data.pop("documents", [])
        installments  = validated_data.pop("installments", [])
        validated_data.pop("receiver_username")

        price = validated_data.get("price")
        payment_option = validated_data["payment_option"]

        # Calculate Fee and Total Amount
        from decimal import Decimal
        fee_config = FeeConfiguration.objects.first()
        escrow_fee = fee_config.escrow_fee if fee_config and fee_config.escrow_fee is not None else Decimal("0.00")
        
        # NOTE: escrow_fee is a fixed amount (or assumed as such based on model DecimalField max_digits=6, decimal_places=2)
        fee_amount = Decimal(str(escrow_fee))

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
    user_role = serializers.SerializerMethodField()
    is_creator = serializers.SerializerMethodField()

    class Meta:
        model  = Escrow
        fields = [
            "id", "order_id", "product_name", "role", "user_role", "is_creator", "item_type",
            "payment_option", "price", "fee_amount", "total_amount", "currency", "status",
            "created_by", "receiver", "cover_image", "created_at",
        ]

    def get_cover_image(self, obj):
        first = obj.images.first()
        return first.image.url if first else None

    def get_user_role(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        
        user = request.user
        if obj.created_by_id == user.id:
            return obj.role
        elif obj.receiver_id == user.id:
            return Escrow.Role.BUYER if obj.role == Escrow.Role.SELLER else Escrow.Role.SELLER
        return None

    def get_is_creator(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.created_by_id == request.user.id


class EscrowDetailSerializer(serializers.ModelSerializer):
    """Full serializer for detail view including nested data."""
    created_by   = ReceiverSerializer(read_only=True)
    receiver     = ReceiverSerializer(read_only=True)
    images       = EscrowImageSerializer(many=True, read_only=True)
    documents    = EscrowDocumentSerializer(many=True, read_only=True)
    installments = EscrowInstallmentSerializer(many=True, read_only=True)
    status_history = EscrowStatusHistorySerializer(many=True, read_only=True)
    dispute = EscrowDisputeSerializer(read_only=True)
    user_role = serializers.SerializerMethodField()
    is_creator = serializers.SerializerMethodField()

    class Meta:
        model  = Escrow
        fields = [
            "id", "order_id", "product_name", "role", "user_role", "is_creator", "item_type",
            "payment_option", "price", "fee_amount", "total_amount", "currency", "status",
            "description", "created_by", "receiver",
            "images", "documents", "installments", "status_history", "dispute",
            "created_at", "updated_at",
        ]

    def get_user_role(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        
        user = request.user
        if obj.created_by_id == user.id:
            return obj.role
        elif obj.receiver_id == user.id:
            return Escrow.Role.BUYER if obj.role == Escrow.Role.SELLER else Escrow.Role.SELLER
        return None

    def get_is_creator(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.created_by_id == request.user.id




class OrderHistorySerializer(serializers.ModelSerializer):
    user_role = serializers.SerializerMethodField()
    is_creator = serializers.SerializerMethodField()

    class Meta:
        model = Escrow
        fields = [
            "id", "order_id", "product_name", "status", "created_at", "user_role", "is_creator"
        ]

    def get_user_role(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        
        user = request.user
        if obj.created_by_id == user.id:
            return obj.role
        elif obj.receiver_id == user.id:
            return Escrow.Role.BUYER if obj.role == Escrow.Role.SELLER else Escrow.Role.SELLER
        return None

    def get_is_creator(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.created_by_id == request.user.id


class OrderHistoryDetailSerializer(serializers.ModelSerializer):
    created_by = ReceiverSerializer(read_only=True)
    receiver   = ReceiverSerializer(read_only=True)
    images     = EscrowImageSerializer(many=True, read_only=True)
    cover_image = serializers.SerializerMethodField()
    status_history = EscrowStatusHistorySerializer(many=True, read_only=True)
    dispute = EscrowDisputeSerializer(read_only=True)
    user_role = serializers.SerializerMethodField()
    is_creator = serializers.SerializerMethodField()
    available_actions = serializers.SerializerMethodField()

    class Meta:
        model = Escrow
        fields = [
            "id", "order_id", "product_name", "price", 
            "created_by", "receiver", "images", "cover_image", 
            "status", "status_history", "dispute", "created_at",
            "user_role", "is_creator", "available_actions"
        ]

    def get_cover_image(self, obj):
        first = obj.images.first()
        return first.image.url if first else None

    def get_user_role(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        
        user = request.user
        if obj.created_by_id == user.id:
            return obj.role
        elif obj.receiver_id == user.id:
            return Escrow.Role.BUYER if obj.role == Escrow.Role.SELLER else Escrow.Role.SELLER
        return None

    def get_is_creator(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.created_by_id == request.user.id

    def get_available_actions(self, obj):
        """
        Always returns a list of action objects for the requesting user so the
        frontend knows which button to render and whether to enable or disable it.

        Each action object:
          {
            "action":  str,          # API action key
            "label":   str,          # Human-readable button label
            "enabled": bool,         # Whether the button should be active
            "message": str | null    # Reason shown when disabled (null when enabled)
          }

        Party resolution:
          escrow.role == 'seller'  →  created_by is seller,  receiver is buyer
          escrow.role == 'buyer'   →  created_by is buyer,   receiver is seller
        """
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return []

        user = request.user
        s = obj.status

        # Resolve parties
        if obj.role == Escrow.Role.SELLER:
            seller_user_id = obj.created_by_id
            buyer_user_id  = obj.receiver_id
        else:
            seller_user_id = obj.receiver_id
            buyer_user_id  = obj.created_by_id

        actions = []

        # ── RECEIVER (invited party) ───────────────────────────────
        # The receiver always sees Accept first at CREATED status.
        # This is true regardless of whether the receiver is the seller or buyer,
        # because the creator initiates the escrow and the other party must accept.
        if user.id == obj.receiver_id:
            if s == Escrow.Status.CREATED:
                actions.append({
                    "action":  "accept",
                    "label":   "Accept Order",
                    "enabled": True,
                    "message": None,
                })

        # ── SELLER PARTY ───────────────────────────────────────────
        # The seller sends the product after the escrow is accepted.
        # If the seller is also the receiver (creator is buyer), they see Accept
        # above at CREATED, so we only show send_product from ACCEPTED onward.
        if user.id == seller_user_id:
            if s == Escrow.Status.CREATED and user.id != obj.receiver_id:
                # Creator is the seller — show disabled send_product while waiting for buyer to accept
                actions.append({
                    "action":  "send_product",
                    "label":   "Send Product",
                    "enabled": False,
                    "message": "Waiting for the buyer to accept the order first.",
                })
            elif s == Escrow.Status.ACCEPTED:
                actions.append({
                    "action":  "send_product",
                    "label":   "Send Product",
                    "enabled": True,
                    "message": None,
                })

        # ── BUYER PARTY ────────────────────────────────────────────
        if user.id == buyer_user_id:
            if s == Escrow.Status.CREATED and user.id != obj.receiver_id:
                actions.append({
                    "action":  "delivered",
                    "label":   "Mark as Delivered",
                    "enabled": False,
                    "message": "Waiting for the seller to accept and ship the order.",
                })
            elif s == Escrow.Status.ACCEPTED:
                actions.append({
                    "action":  "delivered",
                    "label":   "Mark as Delivered",
                    "enabled": False,
                    "message": "Waiting for the seller to ship the product.",
                })
            elif s == Escrow.Status.IN_PROGRESS:
                actions.append({
                    "action":  "delivered",
                    "label":   "Mark as Delivered",
                    "enabled": True,
                    "message": None,
                })
            elif s == Escrow.Status.DELIVERED:
                actions.append({
                    "action":  "dispute",
                    "label":   "Dispute",
                    "enabled": True,
                    "message": None,
                })

        return actions


class DisputeListSerializer(serializers.ModelSerializer):
    """Slim serializer for the disputes list: id, product_name, status, user_role, total_amount."""
    user_role = serializers.SerializerMethodField()

    class Meta:
        model  = Escrow
        fields = ["id", "order_id", "product_name", "status", "user_role", "total_amount"]

    def get_user_role(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        user = request.user
        if obj.created_by_id == user.id:
            return obj.role  # seller or buyer as the creator chose
        elif obj.receiver_id == user.id:
            # receiver is always the opposite party
            return Escrow.Role.BUYER if obj.role == Escrow.Role.SELLER else Escrow.Role.SELLER
        return None
