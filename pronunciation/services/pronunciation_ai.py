import json
import logging
import os
import re
import wave
import difflib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests


logger = logging.getLogger(__name__)

try:
    from vosk import Model, KaldiRecognizer
except ImportError as e:
    Model = None  # type: ignore
    KaldiRecognizer = None  # type: ignore


# -----------------------------
# WAV validation
# -----------------------------

def validate_wav_16k_mono(wav_path: Path) -> Tuple[bool, str]:
    """Validate that a WAV file is 16kHz and mono.

    Returns:
        (True, info_str) if valid
        (False, error_str) if invalid
    """
    try:
        with wave.open(str(wav_path), 'rb') as wf:
            channels = wf.getnchannels()
            framerate = wf.getframerate()
            sampwidth = wf.getsampwidth()

        if channels != 1:
            return False, f"Audio must be mono (1 channel). Got channels={channels}."
        if framerate != 16000:
            return False, f"Audio must be 16000 Hz sample rate. Got framerate={framerate}."
        if sampwidth not in (2, 3, 4):
            # Most common: 16-bit PCM -> sampwidth=2
            return False, f"Unexpected sample width (bytes per sample): {sampwidth}."

        return True, f"OK (channels={channels}, framerate={framerate}, sampwidth={sampwidth})"
    except wave.Error as e:
        return False, f"Invalid WAV file: {e}"
    except Exception as e:
        return False, f"Could not read WAV file: {e}"


# -----------------------------
# Text comparison
# -----------------------------

def _clean_text_words(text: str) -> List[str]:
    text = (text or '').lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return [w for w in text.split() if w]


def find_bad_words(reference_text: str, recognized_text: str) -> List[str]:
    """Find 'bad' words by diffing the reference text with the recognized text.

    Based on the logic in ai.zip (AI/test.py) but:
      - preserves word order
      - removes duplicates while preserving order
    """
    orig_words = _clean_text_words(reference_text)
    rec_words = _clean_text_words(recognized_text)

    matcher = difflib.SequenceMatcher(None, orig_words, rec_words)

    bad_words: List[str] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'replace':
            for i in range(i1, i2):
                if i - i1 < j2 - j1:
                    orig_word = orig_words[i]
                    rec_word = rec_words[j1 + (i - i1)]
                    if orig_word != rec_word:
                        bad_words.append(orig_word)
                else:
                    bad_words.append(orig_words[i])
        elif tag == 'delete':
            for i in range(i1, i2):
                bad_words.append(orig_words[i])

    simple_words = {
        'a', 'an', 'the', 'and', 'or', 'in', 'on', 'at', 'to', 'of', 'is', 'are', 'was', 'were'
    }

    filtered: List[str] = []
    seen = set()
    for w in bad_words:
        if w in simple_words or len(w) <= 2:
            continue
        if w in seen:
            continue
        seen.add(w)
        filtered.append(w)

    return filtered


# -----------------------------
# Local ASR (Vosk)
# -----------------------------

_MODEL_CACHE: Dict[str, "Model"] = {}


def _resolve_vosk_model_path(vosk_model_path: str) -> Path:
    """Resolve model path.

    Priority:
      1) explicit 'vosk_model_path'
      2) env VOSK_MODEL_PATH
      3) ./AI/model
      4) ../AI/model
    """
    if vosk_model_path:
        p = Path(vosk_model_path).expanduser()
        if p.exists():
            return p

    env_path = os.getenv('VOSK_MODEL_PATH', '')
    if env_path:
        p = Path(env_path).expanduser()
        if p.exists():
            return p

    # Best-effort: look near the Django project (cwd may vary, so walk from this file)
    here = Path(__file__).resolve()
    # .../pronunciation/services/pronunciation_ai.py -> project root is 3 parents up
    project_root = here.parent.parent.parent

    candidates = [
        project_root / 'AI' / 'model',
        project_root.parent / 'AI' / 'model',
    ]

    for c in candidates:
        if c.exists():
            return c

    raise FileNotFoundError(
        "Vosk model not found. Set VOSK_MODEL_PATH env var or place the model at '<project>/AI/model'."
    )


def _get_vosk_model(model_path: Path) -> "Model":
    key = str(model_path.resolve())
    if key not in _MODEL_CACHE:
        if Model is None:
            raise ImportError(
                "vosk is not installed. Install it with: pip install vosk"
            )
        _MODEL_CACHE[key] = Model(str(model_path))
    return _MODEL_CACHE[key]


def recognize_speech_vosk(audio_path: Path, model_path: Path) -> str:
    """Transcribe audio with Vosk."""
    model = _get_vosk_model(model_path)

    with wave.open(str(audio_path), 'rb') as wf:
        rec = KaldiRecognizer(model, wf.getframerate())

        full_text = ""
        while True:
            data = wf.readframes(4000)
            if not data:
                break
            if rec.AcceptWaveform(data):
                result = json.loads(rec.Result())
                if 'text' in result:
                    full_text += result['text'] + " "

        # Also include the final partial result
        final = json.loads(rec.FinalResult())
        if 'text' in final:
            full_text += final['text']

    return full_text.strip()


# -----------------------------
# (Optional) TTS clip generation
# -----------------------------


def synthesize_word_yandex(
    word: str,
    output_file: Path,
    api_key: str,
    folder_id: str,
    voice: str = 'john',
    speed: str = '0.8',
    fmt: str = 'mp3',
    timeout_s: int = 15,
) -> bool:
    """Generate a TTS audio file for a word using Yandex Cloud TTS.

    This logic is copied from ai.zip (AI/test.py). Yandex credentials are taken
    from the backend settings or env vars.
    """
    # Credentials must be provided via env vars and validated by the caller.
    if not api_key or not folder_id:
        return False

    url = 'https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize'
    headers = {"Authorization": f"Api-Key {api_key}"}

    data = {
        'text': word,
        'voice': voice,
        'folderId': folder_id,
        'format': fmt,
        'speed': speed,
    }

    try:
        resp = requests.post(url, headers=headers, data=data, timeout=timeout_s)
        if resp.status_code == 200:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_bytes(resp.content)
            return True
        logger.warning("Yandex TTS returned status=%s for word=%r", resp.status_code, word)
    except Exception:
        logger.exception("Yandex TTS request failed for word=%r", word)
        return False

    return False


def analyze_pronunciation(
    audio_path: Path,
    reference_text: str,
    vosk_model_path: str = '',
) -> Dict:
    """Run the local pronunciation pipeline (STT + comparison only).

    Paid TTS is intentionally NOT called here. TTS generation must be invoked
    explicitly by the API layer when enable_tts=true.

    Returns dict containing:
      - recognized_text: str
      - mispronounced_words: list[str]
    """

    model_path = _resolve_vosk_model_path(vosk_model_path)

    recognized = recognize_speech_vosk(audio_path=audio_path, model_path=model_path)
    if not recognized:
        raise RuntimeError('Could not recognize any speech from the audio.')

    bad_words = find_bad_words(reference_text, recognized)

    return {
        'recognized_text': recognized,
        'mispronounced_words': bad_words,
    }


def _safe_filename_part(word: str, max_len: int = 40) -> str:
    """Create a safe filename component for a word.

    Must match the download endpoint's allowed filename regex: [A-Za-z0-9_.-]
    """
    part = re.sub(r"[^A-Za-z0-9_.\-]", "_", word)
    part = re.sub(r"_+", "_", part).strip("_")
    if not part:
        part = "word"
    return part[:max_len]


def generate_correction_clips_yandex(
    words: List[str],
    output_dir: Path,
    api_key: str,
    folder_id: str,
    max_clips: int = 25,
) -> List[Tuple[str, str]]:
    """Generate correction MP3 clips for mispronounced words.

    Returns a list of (word, filename) for clips that exist (generated or already present).

    Caller must enforce cost controls (enable_tts default false) and validate credentials.
    """
    if not api_key or not folder_id:
        raise RuntimeError('YANDEX_API_KEY/YANDEX_FOLDER_ID not configured')

    output_dir.mkdir(parents=True, exist_ok=True)

    if max_clips and max_clips > 0:
        words = words[:max_clips]

    clips: List[Tuple[str, str]] = []
    for i, word in enumerate(words, start=1):
        safe_word = _safe_filename_part(word)
        filename = f"{i}_{safe_word}.mp3"
        out_file = output_dir / filename

        if not out_file.exists():
            ok = synthesize_word_yandex(
                word=word,
                output_file=out_file,
                api_key=api_key,
                folder_id=folder_id,
            )
            if not ok:
                # Best-effort: log and continue. Do not fail the entire request.
                logger.warning("Failed to synthesize correction clip for word=%r", word)
                continue

        clips.append((word, filename))

    return clips
