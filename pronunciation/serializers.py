from rest_framework import serializers


class PronunciationCheckSerializer(serializers.Serializer):
    """Validates a pronunciation check request.

    Expected request content-type: multipart/form-data

    Accepts either:
      - text: str (reference/original text)
      - OR text_id: int (ID of a Text in DB)

    And:
      - audio: WAV file (16kHz mono)

    Optional:
      - generate_audio: bool (default True). If False, we will skip TTS clip generation.
    """

    text = serializers.CharField(required=False, allow_blank=False)
    text_id = serializers.IntegerField(required=False)
    audio = serializers.FileField(required=True)
    generate_audio = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs):
        text = attrs.get('text')
        text_id = attrs.get('text_id')
        if not text and not text_id:
            raise serializers.ValidationError(
                "Provide either 'text' (reference text) or 'text_id' (existing Text id)."
            )
        if text and text_id:
            raise serializers.ValidationError(
                "Provide only one of 'text' or 'text_id', not both."
            )

        audio = attrs.get('audio')
        if audio:
            name = getattr(audio, 'name', '') or ''
            if not name.lower().endswith('.wav'):
                raise serializers.ValidationError("Audio must be a .wav file.")

        return attrs
