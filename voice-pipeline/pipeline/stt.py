"""Speech to text that holds up in noisy rooms (faster-whisper).

In a classroom test on a laptop microphone, Whisper produced "Thank you.",
"In." and "Beautiful." for turns that were mostly noise. So:
- Silero VAD inside faster-whisper (`vad_filter`): only speech is
  transcribed, so noise-only audio gives no text instead of a made-up phrase
- beam search (5 candidates instead of 1)
- hint words: the names Whisper should expect in this call
- a confidence check: transcripts that look made up are rejected, and the
  caller asks the person to repeat (see session.py)

    STT_MODEL    default large-v3-turbo (large-v3 is slower, more robust)
    STT_DEVICE   default cuda ("cpu" if CTranslate2 lacks GPU support)
    STT_BEAM     default 5

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import logging
import os
import re
import time

from faster_whisper import WhisperModel

from .parts import Heard

log = logging.getLogger("pipeline.stt")

# Phrases Whisper is known to invent on silence or noise.
HALLUCINATIONS = {
    "thank you", "thank you very much", "thanks for watching", "thanks",
    "you", "bye", "so", "okay", "oh", "uh", "um", "hmm", "in", "beautiful",
    "thank you for watching", "please subscribe", "i'm sorry",
}


def normalise(text: str) -> str:
    return re.sub(r"[^a-z' ]", "", text.lower()).strip()


class RobustSTT:
    def __init__(self, model: str = "large-v3-turbo", device: str = "cuda",
                 compute_type: str | None = None, beam_size: int = 5, vad: bool = True,
                 min_silence_ms: int = 400, min_speech_s: float = 0.25,
                 max_no_speech_prob: float = 0.6, min_avg_logprob: float = -1.0):
        self.model = WhisperModel(
            model, device=device,
            compute_type=compute_type or ("float16" if device == "cuda" else "int8"))
        self.beam_size = beam_size
        self.vad = vad
        self.vad_parameters = {"min_silence_duration_ms": min_silence_ms, "speech_pad_ms": 200}
        self.min_speech_s = min_speech_s
        self.max_no_speech_prob = max_no_speech_prob
        self.min_avg_logprob = min_avg_logprob

    @classmethod
    def from_env(cls) -> RobustSTT:
        env = os.environ.get
        return cls(env("STT_MODEL", "large-v3-turbo"), device=env("STT_DEVICE", "cuda"),
                   beam_size=int(env("STT_BEAM", "5")))

    def transcribe(self, audio, hint_words: str | None = None) -> Heard:
        """audio: float32 mono numpy array at 16 kHz."""
        t = time.perf_counter()
        segs, info = self.model.transcribe(
            audio, language="en", beam_size=self.beam_size,
            vad_filter=self.vad, vad_parameters=self.vad_parameters if self.vad else None,
            condition_on_previous_text=False, hotwords=hint_words or None)
        segs = list(segs)
        ms = round((time.perf_counter() - t) * 1000)
        text = " ".join(s.text.strip() for s in segs).strip()
        audio_s = len(audio) / 16000
        speech_s = getattr(info, "duration_after_vad", None) or audio_s
        if segs:
            weights = [max(s.end - s.start, 0.01) for s in segs]
            avg_lp = sum(s.avg_logprob * w for s, w in zip(segs, weights)) / sum(weights)
            no_sp = max(s.no_speech_prob for s in segs)
        else:
            avg_lp = no_sp = None

        reason = ""
        if not text:
            reason = "no speech detected"
        elif self.vad and speech_s < self.min_speech_s:
            reason = "too little speech"
        elif no_sp is not None and no_sp > self.max_no_speech_prob and avg_lp < self.min_avg_logprob:
            reason = "low confidence"
        elif normalise(text) in HALLUCINATIONS and (
                avg_lp < -0.5 or no_sp > 0.3 or speech_s < 1.0):
            # Common phrases are only accepted when Whisper is clearly confident.
            reason = "likely hallucination"
        # The numbers behind the decision, for tuning. Never the text.
        log.info("stt %s ms, %.1f s in, %.1f s speech, avg_logprob %s, no_speech %s, %s",
                 ms, audio_s, speech_s, None if avg_lp is None else round(avg_lp, 3),
                 None if no_sp is None else round(no_sp, 3), reason or "accepted")
        return Heard(text=text, accepted=not reason, reason=reason)
