# backend/main.py

import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from models import (AnswerUpload, BaselineUpload, ConsentRequest,
                    FollowupResponse, Question, SessionConfig, SessionSummary,
                    SurveyResponse)
from services.openai_llm import generate_followup, pick_three_questions
from services.tts_openai import AUDIO_DIR, synthesize_tts
from starlette.background import BackgroundTask

app = FastAPI()
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = Path("data/sessions")
DATA_DIR.mkdir(parents=True, exist_ok=True)


SESSIONS: Dict[str, Dict[str, Any]] = {}
WORK_SUFFIX = ".work.json"

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def _work_path(session_id: str) -> Path:
    return DATA_DIR / f"{session_id}{WORK_SUFFIX}"


def _get_session(session_id: str) -> Dict[str, Any] | None:
    sess = SESSIONS.get(session_id)
    if sess:
        return sess
    wp = _work_path(session_id)
    if wp.exists():
        try:
            with wp.open("r", encoding="utf-8") as f:
                data = json.load(f)
            SESSIONS[session_id] = data
            return data
        except Exception as e:
            print(f"[SESSION] Failed to load work file {wp}: {e!r}")
    return None


def _save_session(session_id: str, sess: Dict[str, Any]) -> None:
    try:
        with _work_path(session_id).open("w", encoding="utf-8") as f:
            json.dump(sess, f, indent=2)
    except Exception as e:
        print(f"[SESSION] Failed to persist session {session_id}: {e!r}")




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
        "audio_files": [],
    }
    _save_session(session_id, SESSIONS[session_id])
    return {"session_id": session_id}



@app.post("/session/{session_id}/config")
def set_config(session_id: str, config: SessionConfig):
    sess = _get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    sess["config"] = config.dict()
    _save_session(session_id, sess)
    return {"ok": True}



@app.post("/session/{session_id}/baseline")
def upload_baseline(session_id: str, baseline: BaselineUpload):

    sess = _get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    sess["baseline"] = baseline.voice_features.dict()
    _save_session(session_id, sess)
    return {"ok": True}


@app.get("/session/{session_id}/next_question", response_model=Question)
def get_next_question(session_id: str):
    sess = _get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    idx = sess["current_q_index"]
    if idx >= len(sess["questions"]):
        raise HTTPException(status_code=400, detail="No more questions")

    q = sess["questions"][idx]

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



    return Question(
        id=q["id"],
        text=q["text"],
        audio_url=audio_url,
    )



@app.post("/session/{session_id}/answer", response_model=FollowupResponse)
def submit_answer(session_id: str, payload: AnswerUpload):
    sess = _get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    if sess["config"] is None:
        raise HTTPException(status_code=400, detail="Config not set")

    idx = sess["current_q_index"]
    if idx >= len(sess["questions"]):
        raise HTTPException(status_code=400, detail="No more questions")

    q = sess["questions"][idx]


    answer_record = {
      "question_id": payload.question_id,
      "question_text": q["text"],
      "transcript": payload.transcript,
      "voice_features": payload.voice_features.dict(),
    }
    sess["answers"].append(answer_record)
    sess["current_q_index"] += 1
    _save_session(session_id, sess)

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


@app.post("/session/{session_id}/finish", response_model=SessionSummary)
def finish_session(session_id: str):
    sess = _get_session(session_id)
    if not sess:

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

    out_path = DATA_DIR / f"{session_id}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(summary.dict(), f, indent=2)
    print(f"[SESSION] Saved summary to {out_path}")


    audio_files = sess.get("audio_files") or []
    for url in audio_files:
        if not url:
            continue

        if "/audio/" in url:
            fname = url.split("/audio/")[-1]
        else:
            fname = url.rsplit("/", 1)[-1]

        fpath = AUDIO_DIR / fname
        try:
            fpath.unlink()
            print(f"[TTS] Deleted audio file: {fpath}")
        except FileNotFoundError:

            pass
        except Exception as e:
            print(f"[TTS] Failed to delete {fpath}: {e!r}")

    del SESSIONS[session_id]
    try:
        _work_path(session_id).unlink()
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[SESSION] Failed to delete work file for {session_id}: {e!r}")

    return summary

@app.post("/session/{session_id}/survey")
def submit_survey(session_id: str, survey: SurveyResponse):



    summary_path = DATA_DIR / f"{session_id}.json"
    if not summary_path.exists():

        raise HTTPException(status_code=404, detail="Session summary not found")

    try:
        with summary_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[SURVEY] Failed to read summary file {summary_path}: {e!r}")
        raise HTTPException(status_code=500, detail="Failed to read summary file")


    data["survey"] = survey.dict()

    try:
        with summary_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[SURVEY] Updated summary file with survey: {summary_path}")
    except Exception as e:
        print(f"[SURVEY] Failed to update summary file {summary_path}: {e!r}")
        raise HTTPException(status_code=500, detail="Failed to update summary file")

    sess = SESSIONS.get(session_id)
    if sess is not None:
        sess["survey"] = survey.dict()
        print(f"[SURVEY] Also updated in-memory session {session_id}")

    return {"ok": True}



@app.get("/sessions")
def list_sessions():
    files = sorted(
        p for p in DATA_DIR.glob("*.json") if not p.name.endswith(WORK_SUFFIX)
    )
    return {"count": len(files), "sessions": [p.stem for p in files]}


@app.get("/sessions/{session_id}/download")
def download_session(session_id: str):
    path = DATA_DIR / f"{session_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Session summary not found")
    return FileResponse(path, media_type="application/json", filename=path.name)


@app.get("/sessions/archive")
def download_archive():
    files = [p for p in DATA_DIR.glob("*.json") if not p.name.endswith(WORK_SUFFIX)]
    if not files:
        raise HTTPException(status_code=404, detail="No session files")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        zip_path = Path(tmp.name)

    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, arcname=p.name)

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename="sessions.zip",
        background=BackgroundTask(zip_path.unlink, missing_ok=True),
    )


@app.api_route("/", methods=["GET", "HEAD"])
def root(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)
    return RedirectResponse(url="/app/")

app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
