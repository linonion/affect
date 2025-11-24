# backend/main.py

import json
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from models import (AnswerUpload, BaselineUpload, ConsentRequest,
                    FollowupResponse, Question, SessionConfig, SessionSummary,
                    SurveyResponse)
from services.openai_llm import generate_followup, pick_three_questions
from services.tts_openai import AUDIO_DIR, synthesize_tts

app = FastAPI()
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")


# 允许前端 localhost 调用（开发阶段先放开所有域）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # 以后可以改成 ["http://localhost:5500"] 等更严格
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 本地保存 session 结果的目录：data/sessions/xxx.json
DATA_DIR = Path("data/sessions")
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 简单的内存内 session 存储（开发用途）
SESSIONS: Dict[str, Dict[str, Any]] = {}

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# ============ 1. 创建 session（用户点击 I Agree 之后） ============


@app.post("/session/start")
def start_session(consent: ConsentRequest):
    if not consent.accepted:
        raise HTTPException(status_code=400, detail="Consent not accepted")

    session_id = str(uuid4())
    questions = pick_three_questions()

    SESSIONS[session_id] = {
        "consent": True,
        "config": None,
        "baseline": None,
        "questions": [
            {"id": i, "text": q} for i, q in enumerate(questions, start=1)
        ],
        "answers": [],
        "current_q_index": 0,
        "audio_files": [],   # ⭐ 记录这个 session 生成过哪些 TTS 文件
    }
    return {"session_id": session_id}


# ============ 2. 设置实验条件（interviewer_style + feedback_mode） ============

@app.post("/session/{session_id}/config")
def set_config(session_id: str, config: SessionConfig):
    sess = SESSIONS.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    # 保存 interviewer_style + feedback_mode
    sess["config"] = config.dict()
    return {"ok": True}


# ============ 3. 上传 baseline voice features ============

@app.post("/session/{session_id}/baseline")
def upload_baseline(session_id: str, baseline: BaselineUpload):
    """
    Baseline 阶段结束时调用，前端会把 voiceEngine 统计出的 baseline 特征传上来。
    """
    sess = SESSIONS.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    sess["baseline"] = baseline.voice_features.dict()
    return {"ok": True}


# ============ 4. 获取下一题（共 3 题） ============

@app.get("/session/{session_id}/next_question", response_model=Question)
def get_next_question(session_id: str):
    sess = SESSIONS.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    idx = sess["current_q_index"]
    if idx >= len(sess["questions"]):
        raise HTTPException(status_code=400, detail="No more questions")

    q = sess["questions"][idx]

    # ⭐ 为这一题生成 TTS 音频
    config = sess.get("config")
    style = (config or {}).get("interviewer_style", "neutral")
    try:
        audio_url = synthesize_tts(
            text=q["text"],
            session_id=session_id,
            q_index=idx + 1,
            kind="question",
        )
    except Exception as e:
        print("[TTS] synthesize_tts error in next_question:", repr(e))
        audio_url = ""

    if audio_url:
        sess["audio_files"].append(audio_url)


    # 返回带 audio_url 的 Question
    return Question(
        id=q["id"],
        text=q["text"],
        audio_url=audio_url,
    )


# ============ 5. 提交每题回答 + 生成 follow-up ============

@app.post("/session/{session_id}/answer", response_model=FollowupResponse)
def submit_answer(session_id: str, payload: AnswerUpload):
    sess = SESSIONS.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    if sess["config"] is None:
        raise HTTPException(status_code=400, detail="Config not set")

    idx = sess["current_q_index"]
    if idx >= len(sess["questions"]):
        raise HTTPException(status_code=400, detail="No more questions")

    q = sess["questions"][idx]

    # 记录本题结果
    answer_record = {
      "question_id": payload.question_id,
      "question_text": q["text"],
      "transcript": payload.transcript,
      "voice_features": payload.voice_features.dict(),
    }
    sess["answers"].append(answer_record)
    sess["current_q_index"] += 1

    style = sess["config"]["interviewer_style"]
    followup_text = generate_followup(style, q["text"], payload.transcript)

    try:
        audio_url = synthesize_tts(
            text=followup_text,
            session_id=session_id,
            q_index=idx + 1,
            kind="followup",
        )
    except Exception as e:
        print("[TTS] synthesize_tts error in answer:", repr(e))
        audio_url = ""

    if audio_url:
        sess["audio_files"].append(audio_url)


    return FollowupResponse(
        followup_text=followup_text,
        audio_url=audio_url,
    )

# ============ 6. 结束 session + 保存本地 JSON ============

@app.post("/session/{session_id}/finish", response_model=SessionSummary)
def finish_session(session_id: str):
    sess = SESSIONS.get(session_id)
    if not sess:
        # 这里是你之前 404 的来源
        raise HTTPException(status_code=404, detail="Session not found")

    config = sess["config"]
    if config is None:
        raise HTTPException(status_code=400, detail="Config not set")

    summary = SessionSummary(
        session_id=session_id,
        interviewer_style=config["interviewer_style"],
        feedback_mode=config["feedback_mode"],
        baseline=sess["baseline"],
        questions=[Question(**q) for q in sess["questions"]],
        answers=sess["answers"],
        survey=sess.get("survey"),
    )

    # 1) 保存 JSON（你原来的逻辑）
    out_path = DATA_DIR / f"{session_id}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(summary.dict(), f, indent=2)
    print(f"[SESSION] Saved summary to {out_path}")

    # 2) 删除本 session 的 TTS audio 文件
    audio_files = sess.get("audio_files") or []
    for url in audio_files:
        if not url:
            continue
        # url 形如 "/audio/xxx.mp3" 或 "http://.../audio/xxx.mp3"
        if "/audio/" in url:
            fname = url.split("/audio/")[-1]
        else:
            fname = url.rsplit("/", 1)[-1]

        fpath = AUDIO_DIR / fname
        try:
            fpath.unlink()
            print(f"[TTS] Deleted audio file: {fpath}")
        except FileNotFoundError:
            # 文件已经不存在就算了，不要抛异常
            pass
        except Exception as e:
            print(f"[TTS] Failed to delete {fpath}: {e!r}")

    # 3) （可选）从内存中删除这个 session，防止之后再访问
    del SESSIONS[session_id]

    return summary

@app.post("/session/{session_id}/survey")
def submit_survey(session_id: str, survey: SurveyResponse):
    """
    接受前端提交的问卷结果，尽量写入到对应的 summary JSON 中。
    不再强依赖内存里的 SESSIONS 里是否还保留该 session。
    """

    # 1) 尝试更新磁盘上的 summary JSON
    summary_path = DATA_DIR / f"{session_id}.json"
    if not summary_path.exists():
        # 如果连 JSON 文件都不存在，再说 404
        raise HTTPException(status_code=404, detail="Session summary not found")

    try:
        with summary_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[SURVEY] Failed to read summary file {summary_path}: {e!r}")
        raise HTTPException(status_code=500, detail="Failed to read summary file")

    # 写入或覆盖 survey 字段
    data["survey"] = survey.dict()

    try:
        with summary_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[SURVEY] Updated summary file with survey: {summary_path}")
    except Exception as e:
        print(f"[SURVEY] Failed to update summary file {summary_path}: {e!r}")
        raise HTTPException(status_code=500, detail="Failed to update summary file")

    # 2) 如果内存里还有这个 session，就顺便更新一下（可选）
    sess = SESSIONS.get(session_id)
    if sess is not None:
        sess["survey"] = survey.dict()
        print(f"[SURVEY] Also updated in-memory session {session_id}")

    return {"ok": True}

# ============ 最后挂载前端静态资源（放在所有 API 路由之后，避免覆盖 /session/...） ============
@app.get("/")
def root():
    # 避免静态目录挂到 "/" 覆盖 API，统一放到 /app/
    return RedirectResponse(url="/app/")

# 静态文件挂载到 /app
app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
