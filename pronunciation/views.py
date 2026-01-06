import hashlib
import json
import logging
import re
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.http import FileResponse

from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .services.pronunciation_ai import (
    analyze_pronunciation,
    generate_correction_clips_yandex,
    validate_wav_16k_mono,
)


logger = logging.getLogger(__name__)


_FILENAME_RE = re.compile(r'^[A-Za-z0-9_.\-]+\.mp3$')


def _parse_bool(value, default: bool = False) -> bool:
    """Parse a boolean from multipart/form-data.

    Accepts bools and common string representations.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {'1', 'true', 'yes', 'y', 'on'}:
        return True
    if s in {'0', 'false', 'no', 'n', 'off', ''}:
        return False
    raise ValueError('invalid boolean')


def _tasks_root() -> Path:
    return Path(settings.MEDIA_ROOT) / 'audio_tasks'


def _tmp_root() -> Path:
    return Path(settings.MEDIA_ROOT) / 'audio_tmp'


def _analysis_path(task_dir: Path) -> Path:
    return task_dir / 'analysis.json'


def _clips_dir(task_dir: Path) -> Path:
    return task_dir / 'correction_audio'


def _build_clip_url(task_id: str, filename: str) -> str:
    # Must match the OpenAPI contract: /api/audio/corrections/{task_id}/{filename}
    return f"/api/audio/corrections/{task_id}/{filename}"


class ProcessAudioAPIView(APIView):
    """POST /api/audio/process/

    Implements the required contract:
      - multipart/form-data fields: text (required), audio (required), enable_tts (optional, default false)
      - validates audio via the built-in wave module (mono, 16000 Hz)
      - runs local STT + comparison
      - optional paid TTS runs ONLY when enable_tts=true
      - caches analysis by sha256(audio_bytes + reference_text)
    """

    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        text = (request.data.get('text') or '').strip()
        if not text:
            return Response({'error': 'text is required'}, status=status.HTTP_400_BAD_REQUEST)

        audio_file = request.FILES.get('audio')
        if audio_file is None:
            return Response({'error': 'audio is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            enable_tts = _parse_bool(request.data.get('enable_tts'), default=False)
        except ValueError:
            return Response({'error': 'enable_tts must be a boolean'}, status=status.HTTP_400_BAD_REQUEST)

        # Cost / secrets safety: paid TTS must never run by default.
        if enable_tts:
            api_key = (getattr(settings, 'YANDEX_API_KEY', '') or '').strip()
            folder_id = (getattr(settings, 'YANDEX_FOLDER_ID', '') or '').strip()
            if not api_key or not folder_id:
                return Response(
                    {'error': 'YANDEX_API_KEY/YANDEX_FOLDER_ID not configured'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            api_key = ''
            folder_id = ''

        # Prepare temp storage for validation + analysis.
        tmp_id = uuid.uuid4().hex
        tmp_dir = _tmp_root() / tmp_id
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_wav_path = tmp_dir / 'input.wav'

        # Cache key (recommended): sha256(audio_bytes + reference_text)
        hasher = hashlib.sha256()

        try:
            with tmp_wav_path.open('wb') as out:
                for chunk in audio_file.chunks():
                    out.write(chunk)
                    hasher.update(chunk)
            hasher.update(text.encode('utf-8', errors='ignore'))
            cache_key = hasher.hexdigest()

            # Validate WAV container + required params (must be done before AI).
            ok, info_or_err = validate_wav_16k_mono(tmp_wav_path)
            if not ok:
                logger.info("Rejected invalid WAV upload: %s", info_or_err)
                return Response(
                    {'error': 'audio must be a valid WAV (mono, 16000 Hz)'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Deterministic UUID (strictly valid) derived from the cache key.
            task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, cache_key))

            task_dir = _tasks_root() / task_id
            task_dir.mkdir(parents=True, exist_ok=True)

            analysis_file = _analysis_path(task_dir)

            # Load cached analysis or run local AI.
            if analysis_file.exists():
                try:
                    analysis = json.loads(analysis_file.read_text(encoding='utf-8'))
                except Exception:
                    analysis = {}
            else:
                try:
                    result = analyze_pronunciation(
                        audio_path=tmp_wav_path,
                        reference_text=text,
                        vosk_model_path=getattr(settings, 'VOSK_MODEL_PATH', ''),
                    )
                    analysis = {
                        'recognizedText': result.get('recognized_text', '') or '',
                        'mispronouncedWords': result.get('mispronounced_words', []) or [],
                    }

                    # Persist for caching (best-effort).
                    try:
                        analysis_file.write_text(
                            json.dumps(analysis, ensure_ascii=False, indent=2),
                            encoding='utf-8',
                        )
                    except Exception:
                        logger.exception("Failed to write analysis cache for task_id=%s", task_id)
                except Exception:
                    logger.exception("Internal error while processing audio (task_id=%s)", task_id)
                    return Response(
                        {'error': 'internal server error while processing audio'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )

            recognized_text = analysis.get('recognizedText', '') or ''
            mispronounced_words = analysis.get('mispronouncedWords', []) or []

            correction_clips = []
            if enable_tts:
                # Generate correction MP3 clips under the task folder.
                clips_dir = _clips_dir(task_dir)
                try:
                    pairs = generate_correction_clips_yandex(
                        words=mispronounced_words,
                        output_dir=clips_dir,
                        api_key=api_key,
                        folder_id=folder_id,
                        max_clips=int(getattr(settings, 'PRONUNCIATION_MAX_CLIPS', 25) or 25),
                    )
                except RuntimeError:
                    # Should not happen because we pre-validated keys, but keep it safe.
                    return Response(
                        {'error': 'YANDEX_API_KEY/YANDEX_FOLDER_ID not configured'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                except Exception:
                    logger.exception("TTS generation failed (task_id=%s)", task_id)
                    pairs = []

                correction_clips = [
                    {'word': word, 'file': filename, 'url': _build_clip_url(task_id, filename)}
                    for word, filename in pairs
                ]

            # Optionally keep the uploaded wav.
            if getattr(settings, 'KEEP_UPLOADED_AUDIO', False):
                try:
                    dest = task_dir / 'input.wav'
                    if not dest.exists():
                        shutil.copyfile(tmp_wav_path, dest)
                except Exception:
                    logger.exception("Failed to persist input.wav for task_id=%s", task_id)

            return Response(
                {
                    'taskId': task_id,
                    'recognizedText': recognized_text,
                    'mispronouncedWords': mispronounced_words,
                    'correctionClips': correction_clips if enable_tts else [],
                },
                status=status.HTTP_200_OK,
            )
        finally:
            # Cleanup temp upload files.
            try:
                if tmp_wav_path.exists():
                    tmp_wav_path.unlink()
            except Exception:
                logger.exception("Failed to remove temp wav: %s", tmp_wav_path)
            try:
                if tmp_dir.exists():
                    tmp_dir.rmdir()
            except Exception:
                # Directory may not be empty or may already be removed.
                pass


class CorrectionClipDownloadAPIView(APIView):
    """GET /api/audio/corrections/{task_id}/{filename}

    Streams an MP3 clip generated for a given task.
    Strictly validates parameters to prevent path traversal.
    """

    def get(self, request, task_id: str, filename: str):
        # Validate task_id is a strict UUID (canonical form).
        try:
            parsed = uuid.UUID(task_id)
            if str(parsed) != task_id:
                raise ValueError('non-canonical uuid')
        except Exception:
            return Response({'error': 'invalid file name'}, status=status.HTTP_400_BAD_REQUEST)

        # Validate filename is safe and ends with .mp3
        if not filename or not _FILENAME_RE.fullmatch(filename):
            return Response({'error': 'invalid file name'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            base_dir = (_tasks_root() / task_id / 'correction_audio').resolve()
            file_path = (base_dir / filename).resolve()

            # Extra defense: ensure resolved path stays within base_dir.
            try:
                file_path.relative_to(base_dir)
            except Exception:
                return Response({'error': 'invalid file name'}, status=status.HTTP_400_BAD_REQUEST)

            if not file_path.exists() or not file_path.is_file():
                return Response({'error': 'file not found'}, status=status.HTTP_404_NOT_FOUND)

            return FileResponse(
                open(file_path, 'rb'),
                content_type='audio/mpeg',
            )
        except Exception:
            logger.exception(
                "Error while serving correction clip (task_id=%s, filename=%s)", task_id, filename
            )
            return Response({'error': 'internal server error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
