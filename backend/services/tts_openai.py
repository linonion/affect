# backend/services/tts_openai.py
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set")

# 你可以在 backend/.env 里覆盖这两个配置
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")  # 或 "tts-1"
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")

client = OpenAI(api_key=OPENAI_API_KEY)

AUDIO_DIR = Path("data/audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def synthesize_tts(text: str, session_id: str, q_index: int, kind: str) -> str:
    """
    用 OpenAI TTS 把 text 合成为 mp3，保存到 data/audio/ 下，
    返回一个可以给前端用的 URL（例如 /audio/xxx.mp3）。

    kind: "question" / "followup"

    ❗ TTS 出错时只打印日志并返回 ""，不抛异常，让问答流程继续。
    """
    text = (text or "").strip()
    if not text:
        return ""

    filename = f"{session_id}_q{q_index}_{kind}.mp3"
    out_path = AUDIO_DIR / filename

    try:
        # 官方推荐的 streaming 写法
        from contextlib import nullcontext

        # 有些版本没有 with_streaming_response，这里做个兼容
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
                # 旧版 SDK：用 read()
                with out_path.open("wb") as f:
                    f.write(response.read())

        # 返回给前端的 URL（main.py 已经 mount 了 /audio）
        url = f"/audio/{filename}"
        print(f"[TTS] OK {kind} q{q_index}: {url}")
        return url

    except Exception as e:
        print(
            f"[TTS] Error for session={session_id} q={q_index} kind={kind}: {repr(e)}"
        )
        # 不抛异常，让上层 /next_question /answer 继续返回文字
        return ""
