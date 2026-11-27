from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services.voice import (
    MAX_AUDIO_BYTES,
    MAX_SPEECH_CHARACTERS,
    VoiceError,
    synthesize_speech,
    transcribe_audio,
)


router = APIRouter(
    prefix="/voice",
    tags=["voice"],
)


class SpeechRequest(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=MAX_SPEECH_CHARACTERS,
    )


@router.post("/transcribe")
async def transcribe(file: UploadFile, fast: bool = False) -> dict:
    try:
        content = await file.read(MAX_AUDIO_BYTES + 1)

        if len(content) > MAX_AUDIO_BYTES:
            raise HTTPException(
                status_code=413,
                detail="The recording exceeds the 12 MB limit.",
            )

        return await run_in_threadpool(
            transcribe_audio,
            content,
            fast,
        )
    except VoiceError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from None
    finally:
        await file.close()


@router.post("/speak")
def speak(request: SpeechRequest) -> Response:
    from time import perf_counter
    started = perf_counter()
    try:
        audio = synthesize_speech(request.text)
    except VoiceError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from None

    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "X-Artemis-Speech-Seconds": f"{perf_counter() - started:.3f}",
            "X-Artemis-Voice-Revision": "timing-1",
            "Content-Disposition": 'inline; filename="artemis-reply.wav"',
        },
    )