// voiceEngine.js
//
//   - initVoiceEngine()
//   - startBaselineCollect()
//   - stopBaselineCollectAndGetFeatures() -> VoiceFeatures
//   - startAnswer(feedbackMode, onNervousnessUpdate)
//   - stopAnswerAndGetFeatures() -> { features: VoiceFeatures, transcript: string }
//
// VoiceFeatures:
// {
//   nervousness_score: number,
//   avg_rms: number,
//   silence_ratio: number,
//   intensity_variance: number,
//   speech_rate: number,
//   filler_count: number,
//   repetition_count: number,
//   duration_sec: number
// }

let audioContext = null;
let analyser = null;
let dataArray = null;

let FRAME_MS = 0;

// if the loop start
let analysisStarted = false;

// ===== baseline =====
let isBaselineCollecting = false;
let baselineReady = false;

let baselineStartTime = null;
let baselineRmsHistory = [];
let baselineTotalFrames = 0;
let baselineSilenceFrames = 0;
let baselineNervHistory = [];

// ===== 当前题回答状态 =====
let isAnswering = false;
let answerStartTime = null;
let answerRmsHistory = [];
let answerTotalFrames = 0;
let answerSilenceFrames = 0;
let answerNervHistory = [];

// ===== 通用特征 =====
let silenceFramesRun = 0;
let rmsShortWindow = []; // short RMS window

// biofeedback 模式 / 回调
let currentFeedbackMode = "real"; // "real" | "fake" | "none"
let onNervousnessCb = null;
let fakeScore = null;

// ===== Web Speech 相关 =====
let recognition = null;
let recognizing = false;
let currentTranscript = ""; // 当前这一题的累积文本

// 一点点常数
const SMALL = 1e-6;
const RMS_SILENCE_THRESHOLD = 0.005;
const FEATURE_WINDOW_FRAMES = 60;

// ========== 对外导出 ==========

export async function initVoiceEngine() {
  if (audioContext) return;

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  audioContext = new (window.AudioContext || window.webkitAudioContext)();

  if (audioContext.state === "suspended") {
    try {
      await audioContext.resume();
    } catch (e) {
      console.warn("[voiceEngine] audioContext resume failed:", e);
    }
  }

  const source = audioContext.createMediaStreamSource(stream);
  analyser = audioContext.createAnalyser();
  analyser.fftSize = 1024;
  dataArray = new Float32Array(analyser.fftSize);
  source.connect(analyser);

  FRAME_MS = (1000 * analyser.fftSize) / audioContext.sampleRate;

  console.log("[voiceEngine] init done, FRAME_MS =", FRAME_MS.toFixed(2));

  // ⭐ 启动 Web Speech 识别（如果浏览器支持）
  initSpeechRecognition();

  if (!analysisStarted) {
    analysisStarted = true;
    requestAnimationFrame(analyzeLoop);
  }
}

// ===== baseline 段 =====
export function startBaselineCollect() {
  console.log("[voiceEngine] startBaselineCollect");
  isBaselineCollecting = true;
  baselineReady = false;

  baselineStartTime = performance.now();
  baselineRmsHistory = [];
  baselineTotalFrames = 0;
  baselineSilenceFrames = 0;
  baselineNervHistory = [];
}

export function stopBaselineCollectAndGetFeatures() {
  console.log("[voiceEngine] stopBaselineCollect");
  isBaselineCollecting = false;
  baselineReady = true;

  const now = performance.now();
  const durationSec = baselineStartTime
    ? (now - baselineStartTime) / 1000
    : 0;

  const avgRms = baselineRmsHistory.length
    ? baselineRmsHistory.reduce((a, b) => a + b, 0) / baselineRmsHistory.length
    : 0;

  const silenceRatio =
    baselineTotalFrames > 0
      ? baselineSilenceFrames / baselineTotalFrames
      : 0;

  const intensityVariance = computeVariance(baselineRmsHistory);

  const nervMean = baselineNervHistory.length
    ? baselineNervHistory.reduce((a, b) => a + b, 0) / baselineNervHistory.length
    : 0;

  const features = {
    nervousness_score: nervMean || 0,
    avg_rms: avgRms || 0,
    silence_ratio: silenceRatio || 0,
    intensity_variance: intensityVariance || 0,
    speech_rate: 0,          // baseline 阶段不算
    filler_count: 0,         // baseline 阶段不算
    repetition_count: 0,     // baseline 阶段不算
    duration_sec: durationSec,
  };

  console.log("[voiceEngine] baseline features =", features);
  return features;
}

// ===== 回答阶段 =====
export function startAnswer(feedbackMode, onNervousnessUpdate) {
  console.log("[voiceEngine] startAnswer, mode =", feedbackMode);
  currentFeedbackMode = feedbackMode || "real";
  onNervousnessCb = onNervousnessUpdate || null;

  isAnswering = true;
  answerStartTime = performance.now();
  answerRmsHistory = [];
  answerTotalFrames = 0;
  answerSilenceFrames = 0;
  answerNervHistory = [];

  // ⭐ 每题开始时清空 transcript
  currentTranscript = "";

  fakeScore = null;
}

export function stopAnswerAndGetFeatures() {
  console.log("[voiceEngine] stopAnswerAndGetFeatures");

  // 非常防御性：任何地方算特征都不要抛异常
  try {
    isAnswering = false;
    onNervousnessCb = null;
    currentFeedbackMode = "real";

    const now = performance.now();
    const durationSec = answerStartTime
      ? (now - answerStartTime) / 1000
      : 0;

    const avgRms = answerRmsHistory.length
      ? answerRmsHistory.reduce((a, b) => a + b, 0) / answerRmsHistory.length
      : 0;

    const silenceRatio =
      answerTotalFrames > 0 ? answerSilenceFrames / answerTotalFrames : 0;

    const intensityVariance = computeVariance(answerRmsHistory);

    const nervMean = answerNervHistory.length
      ? answerNervHistory.reduce((a, b) => a + b, 0) / answerNervHistory.length
      : 0;

    // ⭐ 本题识别的文本
    const transcript = (currentTranscript || "").trim();

    // ⭐ 根据 transcript 计算语速 & filler & 重复词计数
    let speechRate = 0;
    let fillerCount = 0;
    let repetitionCount = 0;

    if (transcript && durationSec > 0.5) {
      const words = transcript
        .trim()
        .split(/\s+/)
        .filter(Boolean);
      const wordCount = words.length;
      speechRate = wordCount / durationSec; // words per second

      const lower = transcript.toLowerCase();

      // ---- 1) 传统 filler 计数（保留） ----
      const FILLERS = [
        "um", "uh",
        "like",
        "you know",
        "i mean",
        "sort of", "kind of",
        "maybe",
        "so",
        "well",
        "yeah",
        "oh"
      ];
      fillerCount = FILLERS.reduce((cnt, f) => {
        const pattern = `\\b${f.replace(" ", "\\s+")}\\b`;
        const regex = new RegExp(pattern, "g");
        const matches = lower.match(regex);
        return cnt + (matches ? matches.length : 0);
      }, 0);

      // ---- 2) 重复词计数（repetition_count） ----
      const tokens = lower
        .replace(/[.,!?;:"“”]/g, " ")
        .split(/\s+/)
        .filter(Boolean);

      if (tokens.length > 0) {
        const counts = {};
        for (const t of tokens) {
          counts[t] = (counts[t] || 0) + 1;
        }
        // 出现3次算2个重复；出现1次算0
        repetitionCount = Object.values(counts).reduce((acc, cnt) => {
          return acc + (cnt > 1 ? cnt - 1 : 0);
        }, 0);
      }
    }

    const features = {
      nervousness_score: nervMean || 0,
      avg_rms: avgRms || 0,
      silence_ratio: silenceRatio || 0,
      intensity_variance: intensityVariance || 0,
      speech_rate: speechRate || 0,
      filler_count: fillerCount || 0,
      repetition_count: repetitionCount || 0,  // ⭐ 新增字段
      duration_sec: durationSec,
    };

    console.log("[voiceEngine] answer features =", features);
    console.log("[voiceEngine] transcript =", transcript);

    // ⭐ 返回 features + transcript
    return { features, transcript };
  } catch (e) {
    console.error("[voiceEngine] stopAnswerAndGetFeatures error:", e);

    return {
      features: {
        nervousness_score: 0,
        avg_rms: 0,
        silence_ratio: 0,
        intensity_variance: 0,
        speech_rate: 0,
        filler_count: 0,
        repetition_count: 0,   // ⭐ 兜底也给上
        duration_sec: 0,
      },
      transcript: "",
    };
  }
}

// ========== 主分析循环（每帧） ==========

function analyzeLoop() {
  if (!analyser) {
    requestAnimationFrame(analyzeLoop);
    return;
  }

  analyser.getFloatTimeDomainData(dataArray);
  const rms = computeRMS(dataArray);

  // 判断是否静音
  const isSilence = rms < RMS_SILENCE_THRESHOLD;

  // 累积 silence 连续帧
  if (isSilence) {
    silenceFramesRun++;
  } else {
    silenceFramesRun = 0;
  }

  // 简单的 pause 特征：静音时间越长，越接近 1
  const silenceMs = silenceFramesRun * FRAME_MS;
  let pauseFeature = 0;
  const PAUSE_START_MS = 800;
  const PAUSE_MAX_MS = 3000;
  if (silenceMs > PAUSE_START_MS && silenceMs < PAUSE_MAX_MS) {
    const t =
      (silenceMs - PAUSE_START_MS) / (PAUSE_MAX_MS - PAUSE_START_MS);
    pauseFeature = clamp(t, 0, 1);
  } else if (silenceMs >= PAUSE_MAX_MS) {
    pauseFeature = 1;
  }

  // 短时 RMS 窗口（平滑）
  rmsShortWindow.push(rms);
  if (rmsShortWindow.length > FEATURE_WINDOW_FRAMES) {
    rmsShortWindow.shift();
  }
  const rmsAvg =
    rmsShortWindow.length > 0
      ? rmsShortWindow.reduce((a, b) => a + b, 0) / rmsShortWindow.length
      : rms;

  // 简单 nervousness：RMS（越大越紧张） + pauseFeature
  // 把 rms 映射到 [0, 1]，假设正常范围 0 ~ 0.1
  const normRms = clamp(rmsAvg / 0.1, 0, 1);
  const raw = 0.6 * normRms + 0.4 * pauseFeature;
  const score = clamp(raw * 100, 0, 100);

  // baseline 阶段统计
  if (isBaselineCollecting) {
    baselineTotalFrames++;
    if (isSilence) baselineSilenceFrames++;
    baselineRmsHistory.push(rms);
    baselineNervHistory.push(score);
  }

  // 回答阶段统计 + biofeedback
  if (isAnswering) {
    answerTotalFrames++;
    if (isSilence) answerSilenceFrames++;
    answerRmsHistory.push(rms);
    answerNervHistory.push(score);

    handleFrameForFeedback(score);
  }

  requestAnimationFrame(analyzeLoop);
}

// ========== 工具函数 ==========

function computeRMS(frame) {
  let sumSquares = 0;
  for (let i = 0; i < frame.length; i++) {
    const v = frame[i];
    sumSquares += v * v;
  }
  return Math.sqrt(sumSquares / frame.length);
}

function computeVariance(arr) {
  if (!arr || arr.length === 0) return 0;
  const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
  return (
    arr.reduce((acc, v) => acc + (v - mean) * (v - mean), 0) / arr.length
  );
}

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

// biofeedback 模式：real / fake / none
// biofeedback 模式：real / fake / none
function handleFrameForFeedback(realScore) {
  if (!onNervousnessCb) return;

  let displayScore = null;

  if (currentFeedbackMode === "real") {
    // 真实 biofeedback：直接显示 realScore
    displayScore = realScore;
  } else if (currentFeedbackMode === "fake") {
    // 假 biofeedback：永远显示“比较低”的 nervousness（看起来很好）
    // 初始化在 5~20 之间
    if (fakeScore == null) {
      fakeScore = 5 + Math.random() * 15; // 5–20
    }
    // 每帧轻微抖动，让它看起来“活着”
    const noise = (Math.random() - 0.5) * 4; // [-2, 2]
    fakeScore = clamp(fakeScore + noise, 0, 25); // 限制在 0–25 这个很低的区间
    displayScore = fakeScore;
  } else {
    // none：不显示条
    displayScore = null;
  }

  if (displayScore != null) {
    onNervousnessCb(displayScore);
  }
}


// ========== Web Speech：语音转文字（浏览器侧） ==========

function initSpeechRecognition() {
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    console.warn("[voiceEngine] Web Speech API not supported.");
    return;
  }

  recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = true;

  // ⭐ 如果你在实验里用英文回答，用 en-US；用中文就改成 "zh-CN"
  recognition.lang = "en-US";

  recognition.onresult = (event) => {
    let finalText = "";
    let interimText = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const res = event.results[i];
      if (res.isFinal) {
        finalText += res[0].transcript;
      } else {
        interimText += res[0].transcript;
      }
    }

    if (finalText) {
      if (isAnswering) {
        currentTranscript += finalText + " ";
      }
      console.log("[voiceEngine][ASR final]", finalText);
    }
    if (interimText && isAnswering) {
      console.log("[voiceEngine][ASR interim]", interimText);
    }
  };

  recognition.onerror = (e) => {
    console.error("[voiceEngine] Speech recognition error:", e);
  };

  recognition.onend = () => {
    console.log("[voiceEngine] ASR ended");
    if (recognizing) {
      recognition.start();
    }
  };

  recognizing = true;
  recognition.start();
  console.log("[voiceEngine] Web Speech recognition started");
}
