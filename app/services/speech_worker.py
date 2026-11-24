import io
import json
import sys

import onnxruntime as ort
import soundfile as sf
from kokoro_onnx import Kokoro


def main() -> None:
    request = json.loads(
        sys.stdin.buffer.read().decode("utf-8")
    )

    session = ort.InferenceSession(
        request["model_path"],
        providers=["CPUExecutionProvider"],
    )

    model = Kokoro.from_session(
        session,
        request["voices_path"],
    )

    samples, rate = model.create(
        request["text"],
        voice="bf_emma",
        speed=1.0,
        lang="en-gb",
    )

    if len(samples) == 0:
        raise RuntimeError("Emma generated no audio.")

    sf.write(
        request["output_path"],
        samples,
        rate,
        format="WAV",
        subtype="PCM_16",
    )


if __name__ == "__main__":
    main()