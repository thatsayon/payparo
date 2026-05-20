from django.contrib import admin
from .models import Escrow, EscrowImage, EscrowDocument, EscrowInstallment, EscrowRating


class EscrowImageInline(admin.TabularInline):
    model  = EscrowImage
    extra  = 0
    readonly_fields = ("uploaded_at",)


class EscrowDocumentInline(admin.TabularInline):
    model  = EscrowDocument
    extra  = 0
    readonly_fields = ("uploaded_at",)


class EscrowInstallmentInline(admin.TabularInline):
    model  = EscrowInstallment
    extra  = 0
    ordering = ("order",)


@admin.register(Escrow)
class EscrowAdmin(admin.ModelAdmin):
    list_display  = (
        "id", "product_name", "role", "item_type",
        "payment_option", "price", "currency", "status",
        "created_by", "created_at",
    )
    list_filter   = ("role", "item_type", "payment_option", "status")
    search_fields = ("product_name", "created_by__email", "receiver__email")
    inlines       = [EscrowImageInline, EscrowDocumentInline, EscrowInstallmentInline]
    readonly_fields = ("created_at", "updated_at")

@admin.register(EscrowRating)
class EscrowRatingAdmin(admin.ModelAdmin):
    list_display = ("id", "rated_by", "rated_user", "stars", "escrow_order_id", "created_at")
    list_filter = ("stars",)
    search_fields = ("rated_by__email", "rated_user__email", "escrow__order_id")

    def escrow_order_id(self, obj):
        return obj.escrow.order_id
    escrow_order_id.short_description = "Escrow Order ID"
