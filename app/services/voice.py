import io
from pathlib import Path
from threading import Lock

import soundfile as sf
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio
from kokoro_onnx import Kokoro

from app.config import get_settings


MODEL_NAME = "base.en"
MAX_AUDIO_BYTES = 12 * 1024 * 1024
MAX_AUDIO_SECONDS = 90
MAX_SPEECH_CHARACTERS = 3000

SPEECH_VOICE = "bf_emma"
SPEECH_SPEED = 1.0
SPEECH_LANGUAGE = "en-gb"

_transcription_lock = Lock()
_speech_lock = Lock()

_model: WhisperModel | None = None
_speech_model: Kokoro | None = None


class VoiceError(Exception):
    pass


def model_directory() -> Path:
    return (
        get_settings().data_directory
        / "voice"
        / "models"
    )


def load_transcription_model() -> WhisperModel:
    """Call while holding the transcription lock."""
    global _model

    if _model is None:
        directory = model_directory()
        directory.mkdir(parents=True, exist_ok=True)

        _model = WhisperModel(
            MODEL_NAME,
            device="cpu",
            compute_type="int8",
            cpu_threads=2,
            num_workers=1,
            download_root=str(directory),
        )

    return _model


def prepare_voice_model() -> str:
    with _transcription_lock:
        load_transcription_model()

    return f"Voice model ready: {MODEL_NAME}"


def transcribe_audio(content: bytes, fast: bool = False) -> dict:
    if not content:
        raise VoiceError("The recording is empty.")

    if len(content) > MAX_AUDIO_BYTES:
        raise VoiceError("The recording exceeds the 12 MB limit.")

    if not _transcription_lock.acquire(blocking=False):
        raise VoiceError("Another recording is being transcribed.")

    try:
        try:
            audio = decode_audio(
                io.BytesIO(content),
                sampling_rate=16000,
            )
        except Exception as error:
            raise VoiceError(
                "The recording could not be decoded."
            ) from error

        duration = len(audio) / 16000

        if duration <= 0:
            raise VoiceError("The recording contains no audio.")

        if duration > MAX_AUDIO_SECONDS:
            raise VoiceError(
                "Keep recordings to 90 seconds or less."
            )

        try:
            model = load_transcription_model()

            segments, information = model.transcribe(
                audio,
                language="en",
                beam_size=1 if fast else 5,
                vad_filter=True,
                condition_on_previous_text=False,
            )

            text = " ".join(
                segment.text.strip()
                for segment in segments
                if segment.text.strip()
            ).strip()
        except Exception as error:
            raise VoiceError(
                "Local transcription failed."
            ) from error

        return {
            "text": text,
            "language": information.language,
            "duration_seconds": round(duration, 2),
            "model": MODEL_NAME,
        }
    finally:
        _transcription_lock.release()


def load_speech_model() -> Kokoro:
    """Call while holding the speech lock."""
    global _speech_model

    if _speech_model is None:
        import onnxruntime as ort

        directory = (
            get_settings().data_directory
            / "voice"
            / "kokoro"
        )

        model_path = directory / "kokoro-v1.0.onnx"
        voices_path = directory / "voices-v1.0.bin"

        if not model_path.is_file() or not voices_path.is_file():
            raise VoiceError("Kokoro speech model or voice bundle is missing.")

        session = ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )

        print(
            "Emma speech providers:",
            session.get_providers(),
            flush=True,
        )

        _speech_model = Kokoro.from_session(
            session,
            str(voices_path),
        )

    return _speech_model


def synthesize_speech(text: str) -> bytes:
    from app.services.persistent_speech import engine
    cleaned=text.strip()
    if not cleaned:raise VoiceError('Enter text to speak.')
    if len(cleaned)>MAX_SPEECH_CHARACTERS:raise VoiceError('Spoken replies are limited to 3,000 characters.')
    if not _speech_lock.acquire(timeout=10):raise VoiceError('Another spoken reply is being generated.')
    try:
        directory=get_settings().data_directory/'voice/kokoro'
        if not(directory/'kokoro-v1.0.onnx').is_file()or not(directory/'voices-v1.0.bin').is_file():raise VoiceError('Kokoro speech model or voice bundle is missing.')
        try:
            from app.services.voice_hub import settings as listener_settings
            speed=min(1.4,max(.75,float(listener_settings().get('speech_speed',1))))
            return engine.speak(cleaned,directory/'kokoro-v1.0.onnx',directory/'voices-v1.0.bin',speed=speed)
        except Exception as error:raise VoiceError('Persistent speech engine: '+str(error))from error
    finally:_speech_lock.release()
