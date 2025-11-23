from rest_framework import serializers
from .models import Text

class TextSerializer(serializers.ModelSerializer):
    class Meta:
        model = Text
        fields = ['id', 'title', 'body', 'difficulty', 'created_at']
        read_only_fields = ['id', 'created_at']
