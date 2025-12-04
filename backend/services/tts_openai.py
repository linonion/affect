# backend/services/tts_openai.py
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set")

OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts") 
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")

client = OpenAI(api_key=OPENAI_API_KEY)

AUDIO_DIR = Path("data/audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def synthesize_tts(text: str, session_id: str, q_index: int, kind: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""

    filename = f"{session_id}_q{q_index}_{kind}.mp3"
    out_path = AUDIO_DIR / filename

    try:

        from contextlib import nullcontext

        ctx = (
            client.audio.speech.with_streaming_response.create(
                model=OPENAI_TTS_MODEL,
                voice=OPENAI_TTS_VOICE,
                input=text,
            )
            if hasattr(client.audio.speech, "with_streaming_response")
            else nullcontext(
                client.audio.speech.create(
                    model=OPENAI_TTS_MODEL,
                    voice=OPENAI_TTS_VOICE,
                    input=text,
                )
            )
        )

        with ctx as response:
            if hasattr(response, "stream_to_file"):
                response.stream_to_file(out_path)
            else:

                with out_path.open("wb") as f:
                    f.write(response.read())

        url = f"/audio/{filename}"
        print(f"[TTS] OK {kind} q{q_index}: {url}")
        return url

    except Exception as e:
        print(
            f"[TTS] Error for session={session_id} q={q_index} kind={kind}: {repr(e)}"
        )
        return ""
