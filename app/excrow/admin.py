from django.contrib import admin
from .models import Escrow, EscrowImage


class EscrowImageInline(admin.TabularInline):
    model = EscrowImage
    extra = 0
    readonly_fields = ("uploaded_at",)


@admin.register(Escrow)
class EscrowAdmin(admin.ModelAdmin):
    list_display  = ("id", "product_name", "role", "item_type", "price", "currency", "status", "created_by", "created_at")
    list_filter   = ("role", "item_type", "status")
    search_fields = ("product_name", "created_by__email", "receiver__email")
    inlines       = [EscrowImageInline]
    readonly_fields = ("created_at", "updated_at")
