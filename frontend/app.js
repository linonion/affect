// app.js
console.log("[app.js] loaded v6");

import {
  initVoiceEngine,
  startAnswer,
  startBaselineCollect,
  stopAnswerAndGetFeatures,
  stopBaselineCollectAndGetFeatures,
} from "./voiceEngine.js";

const API_BASE = window.API_BASE || "";

let state = {
  sessionId: null,
  interviewerStyle: "neutral",
  feedbackMode: "real",
  currentQuestionIndex: 1,
  maxQuestions: 3,
  currentQuestionId: null,
  answerTimerId: null,
  answerTimeLeft: 180,
};

function $(id) {
  return document.getElementById(id);
}

function showScreen(id) {
  document.querySelectorAll("section").forEach((sec) => {
    sec.classList.toggle("active", sec.id === id);
  });
}

function updateNervUI(score) {
  const bar = $("nerv-bar-fill");
  const text = $("nerv-text");
  if (!bar || !text) return;
  const clamped = Math.max(0, Math.min(100, score));
  bar.style.width = clamped + "%";
  text.textContent = `Nervousness: ${clamped.toFixed(1)}`;
}


let currentAudio = null;

function playAudioThen(url, onEnd) {
  if (!url) {
    if (onEnd) onEnd();
    return;
  }
  try {
    if (currentAudio) {
      currentAudio.pause();
      currentAudio = null;
    }
    const audio = new Audio(url);
    currentAudio = audio;
    audio.addEventListener("ended", () => {
      currentAudio = null;
      if (onEnd) onEnd();
    });
    audio.addEventListener("error", (e) => {
      console.error("[audio] error:", e);
      currentAudio = null;
      if (onEnd) onEnd();
    });
    audio.play().catch((e) => {
      console.error("[audio] play error:", e);
      currentAudio = null;
      if (onEnd) onEnd();
    });
  } catch (e) {
    console.error("[audio] exception:", e);
    if (onEnd) onEnd();
  }
}

// ====== Screen 1: Consent ======
$("btn-consent-accept").addEventListener("click", async () => {
  try {
    const res = await fetch(`${API_BASE}/session/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accepted: true }),
    });
    const data = await res.json();
    console.log("[session/start] res =", data);
    state.sessionId = data.session_id;
    showScreen("screen-config");
  } catch (e) {
    console.error("[session/start] error:", e);
    alert("Error starting session. See console for details.");
  }
});

// ====== Screen 2: Config ======
$("btn-config-next").addEventListener("click", async () => {
  const style = document.querySelector('input[name="style"]:checked').value;
  const feedback = document.querySelector('input[name="feedback"]:checked').value;
  state.interviewerStyle = style;
  state.feedbackMode = feedback;

  try {
    const res = await fetch(`${API_BASE}/session/${state.sessionId}/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        interviewer_style: style,
        feedback_mode: feedback,
      }),
    });
    console.log("[config] status =", res.status);
  } catch (e) {
    console.error("[config] error:", e);
    alert("Error setting config. See console.");
    return;
  }

  try {
    console.log("[voiceEngine] initVoiceEngine...");
    await initVoiceEngine();
    console.log("[voiceEngine] init done");
  } catch (e) {
    console.error("initVoiceEngine error:", e);
    alert("Microphone init failed. Check permissions & console.");
    return;
  }

  showScreen("screen-baseline");
});

// ====== Screen 3: Baseline ======
$("btn-baseline-start").addEventListener("click", () => {
  console.log("[baseline] start");
  startBaselineCollect();
  $("btn-baseline-start").disabled = true;
  $("btn-baseline-stop").disabled = false;
});

$("btn-baseline-stop").addEventListener("click", async () => {
  console.log("[baseline] stop");
  const features = stopBaselineCollectAndGetFeatures();
  console.log("[baseline] features =", features);

  $("baseline-preview").textContent =
    `${features.nervousness_score.toFixed(1)} (0–100)`;

  $("btn-baseline-stop").disabled = true;
  $("btn-baseline-continue").disabled = false;

  try {
    const res = await fetch(`${API_BASE}/session/${state.sessionId}/baseline`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice_features: features }),
    });
    console.log("[baseline upload] status =", res.status);
  } catch (e) {
    console.error("[baseline upload] error:", e);
    alert("Error uploading baseline. See console.");
  }
});

$("btn-baseline-continue").addEventListener("click", () => {
  showScreen("screen-interview");
  loadNextQuestion();
});

// ====== Screen 4: Interview ======
$("btn-answer-start").addEventListener("click", () => {
  $("btn-answer-start").disabled = true;
  $("btn-answer-stop").disabled = false;

  console.log("[answer] start, feedbackMode =", state.feedbackMode);

  startAnswer(state.feedbackMode, (score) => {
    if (state.feedbackMode !== "none") {
      $("nerv-container").style.display = "block";
      updateNervUI(score);
    } else {
      $("nerv-container").style.display = "none";
    }
  });

  state.answerTimeLeft = 180;
  $("time-left").textContent = String(state.answerTimeLeft);
  state.answerTimerId = setInterval(() => {
    state.answerTimeLeft -= 1;
    $("time-left").textContent = String(state.answerTimeLeft);
    if (state.answerTimeLeft <= 0) {
      console.log("[answer] timeout");
      stopCurrentAnswer(true);
    }
  }, 1000);
});

$("btn-answer-stop").addEventListener("click", () => {
  stopCurrentAnswer(false);
});

async function loadNextQuestion() {
  try {
    const res = await fetch(`${API_BASE}/session/${state.sessionId}/next_question`);
    const rawText = await res.text();
    console.log("[next_question] status =", res.status, "raw =", rawText);

    if (!res.ok) {
      await finishSession();
      return;
    }

    const q = JSON.parse(rawText);
    state.currentQuestionId = q.id;
    $("q-index").textContent = state.currentQuestionIndex;
    $("question-text").textContent = q.text;

    $("followup-text").textContent = "(waiting for your answer...)";
    $("time-left").textContent = "180";
    updateNervUI(0);

    $("btn-answer-start").disabled = true;
    $("btn-answer-stop").disabled = true;

    if (q.audio_url) {
    const audioUrl = q.audio_url.startsWith("http")
        ? q.audio_url
        : `${API_BASE}${q.audio_url}`;

    console.log("[audio] playing question:", audioUrl);
    playAudioThen(audioUrl, () => {
        console.log("[audio] question finished, enable Start Answer");
        $("btn-answer-start").disabled = false;
        $("btn-answer-stop").disabled = true;
    });
    } else {
    $("btn-answer-start").disabled = false;
    $("btn-answer-stop").disabled = true;
    }

  } catch (e) {
    console.error("Error in loadNextQuestion:", e);
    $("followup-text").textContent =
      "Error loading next question. See console.";
  }
}

async function stopCurrentAnswer(timeout) {
  $("btn-answer-stop").disabled = true;
  if (state.answerTimerId) {
    clearInterval(state.answerTimerId);
    state.answerTimerId = null;
  }

  console.log("[answer] stop, timeout =", timeout);

  let features, transcript;
  try {
    const result = stopAnswerAndGetFeatures();
    features = result.features;
    transcript = result.transcript;
    console.log("[answer] features =", features);
    console.log("[answer] transcript =", transcript);
  } catch (e) {
    console.error("stopAnswerAndGetFeatures failed:", e);
    $("followup-text").textContent =
      "Error computing voice features. See console.";
    goToNextOrFinish();
    return;
  }

  let followupText = "";
  let followupAudioUrl = "";
  try {
    const res = await fetch(`${API_BASE}/session/${state.sessionId}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question_id: state.currentQuestionId,
        transcript: transcript || null,
        voice_features: features,
      }),
    });

    const rawText = await res.text();
    console.log("[answer] /answer status =", res.status, "raw =", rawText);

    if (!res.ok) {
      followupText = `Server error (${res.status}). See console.`;
    } else {
      try {
        const data = JSON.parse(rawText);
        followupText = data.followup_text || "(no follow-up text)";
        followupAudioUrl = data.audio_url || "";
      } catch (e2) {
        console.error("Failed to parse JSON from /answer:", e2, rawText);
        followupText = "Failed to parse server response JSON.";
      }
    }
  } catch (e) {
    console.error("Network or fetch error when calling /answer:", e);
    followupText = "Network error when submitting answer. See console.";
  }

  $("followup-text").textContent = followupText;

    if (followupAudioUrl) {
    const audioUrl = followupAudioUrl.startsWith("http")
        ? followupAudioUrl
        : `${API_BASE}${followupAudioUrl}`;

    console.log("[audio] playing followup:", audioUrl);
    playAudioThen(audioUrl, () => {
        console.log("[audio] followup finished, go next/finish");
        goToNextOrFinish();
    });
    } else {
    goToNextOrFinish();
    }

}

function goToNextOrFinish() {
  state.currentQuestionIndex += 1;

  if (state.currentQuestionIndex <= state.maxQuestions) {
    loadNextQuestion();
  } else {
    finishSession();
  }
}

// ====== Screen 5: Finish ======
async function finishSession() {
  try {
    const res = await fetch(`${API_BASE}/session/${state.sessionId}/finish`, {
      method: "POST",
    });
    const rawText = await res.text();
    console.log("[finish] status =", res.status, "raw =", rawText);

    showScreen("screen-done");

    if (!res.ok) {
      $("summary-json").textContent =
        "finish_session error. See console for details.";
      return;
    }
    const summary = JSON.parse(rawText);
    $("summary-json").textContent = JSON.stringify(summary, null, 2);
  } catch (e) {
    console.error("Error in finishSession:", e);
    showScreen("screen-done");
    $("summary-json").textContent =
      "Error finishing session. See console for details.";
  }
}

// ====== Survey submission ======
const surveyBtn = document.getElementById("submit-survey");
if (surveyBtn) {
  surveyBtn.addEventListener("click", async () => {
    // q1~q9
    const survey = {};
    for (let i = 1; i <= 9; i++) {
      const sel = document.querySelector(`input[name="q${i}"]:checked`);
      if (!sel) {
        alert(`Please answer question ${i} (select 1–7).`);
        return;
      }
      survey[`q${i}`] = parseInt(sel.value, 10);
    }
    survey["q10_text"] = document.getElementById("q10-text").value || "";

    console.log("[survey] payload =", survey);

    try {
      const res = await fetch(`${API_BASE}/session/${state.sessionId}/survey`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(survey),
      });
      if (!res.ok) {
        const txt = await res.text();
        console.error("[survey] server error:", res.status, txt);
        $("survey-status").textContent =
          "Error submitting questionnaire. Please try again.";
      } else {
        $("survey-status").textContent = "Questionnaire submitted. Thank you!";
      }
    } catch (e) {
      console.error("[survey] network error:", e);
      $("survey-status").textContent =
        "Network error submitting questionnaire. Please try again.";
    }
  });
}
