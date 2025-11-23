from django.contrib import admin
from .models import Text

@admin.register(Text)
class TextAdmin(admin.ModelAdmin):
    list_display = ('title', 'difficulty', 'created_at')
    search_fields = ('title', 'body')
    list_filter = ('difficulty',)
