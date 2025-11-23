# backend/models.py
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel

InterviewerStyle = Literal["neutral", "challenging"]
FeedbackMode = Literal["real", "fake", "none"]


class ConsentRequest(BaseModel):
    accepted: bool


class SessionConfig(BaseModel):
    interviewer_style: InterviewerStyle
    feedback_mode: FeedbackMode


class VoiceFeatures(BaseModel):
    # 你可以根据自己前端算的特征再加字段
    nervousness_score: float
    avg_rms: float
    silence_ratio: float
    intensity_variance: float
    speech_rate: float
    filler_count: int
    repetition_count: int   # ⭐ 新增：重复词计数
    duration_sec: float


class BaselineUpload(BaseModel):
    voice_features: VoiceFeatures


class Question(BaseModel):
    id: int
    text: str
    audio_url: Optional[str] = None


class AnswerUpload(BaseModel):
    question_id: int
    transcript: Optional[str] = None  # 以后可以接 STT 文本
    voice_features: VoiceFeatures


class FollowupResponse(BaseModel):
    followup_text: str
    audio_url: Optional[str] = None

class SurveyResponse(BaseModel):
    q1: int
    q2: int
    q3: int
    q4: int
    q5: int
    q6: int
    q7: int
    q8: int
    q9: int
    q10_text: Optional[str] = None


class SessionSummary(BaseModel):
    session_id: str
    interviewer_style: InterviewerStyle
    feedback_mode: FeedbackMode
    baseline: Optional[VoiceFeatures]
    questions: List[Question]
    answers: List[Dict]
    survey: Optional[SurveyResponse] = None

