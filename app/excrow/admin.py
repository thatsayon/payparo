from django.contrib import admin
from .models import Escrow, EscrowImage, EscrowDocument, EscrowInstallment


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
