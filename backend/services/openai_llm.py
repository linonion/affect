# backend/services/openai_llm.py

import os
import random
from typing import List

from dotenv import load_dotenv
from openai import OpenAI

# 读取 .env 配置
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set")

# 可以在 .env 里覆盖，比如 OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

client = OpenAI(api_key=OPENAI_API_KEY)

# ===================== System Prompts =====================

NEUTRAL_SYSTEM_PROMPT = """
You are a supportive, human-like job interviewer.

You expect candidates to structure their answers using the STAR framework:
- Situation (context)
- Task (their role / goal)
- Action (what they actually did)
- Result (outcome / impact)

When you respond, you DO NOT ask new follow-up questions.
Instead, you give short, natural feedback as if you were an interviewer reacting right after the answer.

For each answer, you should:
1) Briefly acknowledge what the candidate did well (content or structure), especially where STAR is clear.
2) Mention one or two positive personal qualities that come across (e.g., ownership, teamwork, communication, learning, resilience).
3) Gently suggest ONE concrete way they could strengthen the answer next time, for example:
   - clarify the Situation/Task,
   - be more specific about the Action,
   - quantify the Result.
4) If the answer is clearly off-topic, mostly nonsense, or does not address the question at all,
   you should kindly point this out and suggest that they refocus on the question and use STAR
   to describe a relevant real example.

Your response must:
- Be 2–3 short sentences.
- Sound like spoken feedback, not like a formal essay.
- Contain no bullet points, no lists, and no new questions.
""".strip()


CHALLENGING_SYSTEM_PROMPT = """
You are a challenging, high-pressure job interviewer.

You expect candidates to structure their answers using the STAR framework:
- Situation (context)
- Task (their role / goal)
- Action (what they actually did)
- Result (outcome / impact)

When you respond, you DO NOT ask new follow-up questions.
Instead, you give short, direct feedback as if you were an interviewer reacting right after the answer.

For each answer, you should:
1) Briefly acknowledge any part that was strong or clear.
2) Directly point out one or two weaknesses, especially missing or vague parts of STAR
   (e.g., unclear Situation/Task, generic Actions, no concrete Result).
3) Give ONE specific suggestion for how to improve the answer next time.
4) If the answer is clearly off-topic, mostly nonsense, or does not address the question at all,
   you should explicitly say that it does not really answer the question, and firmly recommend that
   they restart with a real, relevant example using STAR.

Your response must:
- Be 2–3 short sentences.
- Sound like a real human interviewer giving firm but professional feedback.
- Contain no bullet points, no lists, and no new questions.
""".strip()

# ===================== Question Pool =====================

GENERAL_QUESTIONS: List[str] = [
    # 已有问题
    "Describe a time you faced a challenge and how you handled it.",
    "Tell me about a time you worked in a team.",
    # 补充常见 BQ 问题
    "Describe a situation where you had to learn something quickly.",
    "Tell me about a time when you had to deal with a difficult teammate or stakeholder.",
    "Describe a time you made a mistake and how you handled it.",
    "Tell me about a time you had to work under pressure or a tight deadline.",
    "Describe a time you showed leadership, even if you were not the formal leader.",
    "Tell me about a time when you received critical feedback. How did you respond?",
    "Describe a time when you disagreed with someone at work or school. What did you do?",
    "Tell me about a time you had to prioritize multiple tasks or projects.",
    "Describe a situation where you went above and beyond what was expected.",
    "Tell me about a time when you had to solve a complex problem.",
    "Describe a time when you had to adapt to a major change.",
    "Tell me about a time when you helped someone else succeed.",
    "Describe a project or situation that you are particularly proud of.",
]


def pick_three_questions() -> List[str]:
    """
    从 GENERAL_QUESTIONS 中随机选 3 个问题。
    如果你想固定顺序，也可以改成 return GENERAL_QUESTIONS[:3]
    """
    if len(GENERAL_QUESTIONS) <= 3:
        return GENERAL_QUESTIONS
    return random.sample(GENERAL_QUESTIONS, k=3)


# ===================== Follow-up Generation =====================

def generate_followup(style: str, question: str, transcript: str | None) -> str:
    """
    根据面试官风格（neutral/challenging）、问题和候选人的回答 transcript，
    生成 2–3 句自然的口头反馈（不再问新问题；如果回答胡言乱语或完全跑题，也会指出来）。
    """
    if style == "neutral":
        system_prompt = NEUTRAL_SYSTEM_PROMPT
    else:
        system_prompt = CHALLENGING_SYSTEM_PROMPT

    user_text = transcript or "(no transcript; only voice features are available)"

    user_prompt = (
        f"You just asked the interview question: \"{question}\"\n"
        f"The candidate answered (ASR transcript or summary):\n"
        f"{user_text}\n\n"
        "Give your reaction as the interviewer.\n"
        "Remember:\n"
        "- You expect STAR structure (Situation, Task, Action, Result).\n"
        "- You only provide feedback, you do NOT ask new questions.\n"
        "- If the answer is clearly off-topic, nonsense, or unrelated to the question, "
        "explicitly mention this and recommend that they refocus on the question.\n"
        "- Respond in 2–3 natural spoken-style sentences."
    )

    try:
        resp = client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",  "content": user_prompt},
            ],
            temperature=0.7,
        )
        content = resp.choices[0].message.content
        return content.strip()
    except Exception as e:
        print("[OpenAI] Error generating followup:", repr(e))
        # fallback 保证接口不挂掉
        if style == "neutral":
            return (
                "Thanks for your answer. Overall it's a good start — next time, "
                "try to make the situation and your specific actions a bit clearer, "
                "and highlight the result more explicitly."
            )
        else:
            return (
                "Thanks for your answer. Right now the situation and your impact are still a bit vague — "
                "next time, be more concrete about what you did and what changed because of you."
            )
