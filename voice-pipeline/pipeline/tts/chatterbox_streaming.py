"""Streaming wrapper for Chatterbox (original and Turbo).

Chatterbox's public generate() makes all speech tokens for a chunk, decodes
them all, then returns. Here the two stages are interleaved:

1. Speech tokens are generated one at a time (same sampling loop as the
   library's T3.inference / T3.inference_turbo, rewritten as a generator).
2. Every `first_tokens` (then `chunk_tokens`) new tokens, the decoder
   (S3Gen) turns the newest tokens into audio, using `finalize=False`, the
   library's own streaming mode, which holds back 3 lookahead tokens.
   A window of earlier tokens is re-decoded for context, so cost per piece
   stays bounded.
3. Pieces are joined with a short crossfade to hide seams, watermarked
   (Perth, like generate()), and yielded immediately.

25 speech tokens = 1 s of audio; 1 token = 2 mel frames = 960 samples.
Written against chatterbox-tts as of 2026-11-02. It uses the library's
internals, which may change; chatterbox.py can fall back to the public
generate() with TTS_STREAM=0.

The voice comes from `model.conds`, which chatterbox.py sets before each call.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import time

import numpy as np
import torch
import torch.nn.functional as F

SAMPLES_PER_FRAME = 480  # 24 kHz audio, 50 Hz mels
FRAMES_PER_TOKEN = 2
VALID_TOKEN_LIMIT = 6561  # tokens >= this are special and dropped
LOOKAHEAD_TOKENS = 3  # S3Gen ignores the last 3 tokens when finalize=False
SILENCE_TOKEN = 4299  # S3GEN_SIL, appended at the end by Turbo's generate()


class StreamingChatterbox:
    def __init__(self, model, turbo: bool):
        """model: a loaded ChatterboxTTS, or ChatterboxTurboTTS if `turbo`."""
        self.m = model
        self.turbo = turbo
        self.sr = model.sr
        try:  # use the library's constant if present
            from chatterbox.models.s3gen.const import S3GEN_SIL
            self.silence = int(S3GEN_SIL)
        except Exception:
            self.silence = SILENCE_TOKEN

    # ------------------------------------------------------------ tokens
    @torch.inference_mode()
    def _tokens_original(self, text, exaggeration, cfg_weight, temperature,
                         repetition_penalty=1.2, min_p=0.05, top_p=1.0,
                         max_new_tokens=1000):
        from chatterbox.models.t3.inference.t3_hf_backend import T3HuggingfaceBackend
        from chatterbox.models.t3.modules.cond_enc import T3Cond
        from chatterbox.tts import punc_norm
        from transformers.generation.logits_process import (
            MinPLogitsWarper, RepetitionPenaltyLogitsProcessor, TopPLogitsWarper)

        m, t3 = self.m, self.m.t3
        if exaggeration != float(m.conds.t3.emotion_adv[0, 0, 0]):
            c = m.conds.t3
            m.conds.t3 = T3Cond(speaker_emb=c.speaker_emb,
                                cond_prompt_speech_tokens=c.cond_prompt_speech_tokens,
                                emotion_adv=exaggeration * torch.ones(1, 1, 1)).to(device=m.device)

        text_tokens = m.tokenizer.text_to_tokens(punc_norm(text)).to(m.device)
        text_tokens = torch.cat([text_tokens, text_tokens], dim=0)  # CFG pair
        text_tokens = F.pad(text_tokens, (1, 0), value=t3.hp.start_text_token)
        text_tokens = F.pad(text_tokens, (0, 1), value=t3.hp.stop_text_token)

        embeds, _ = t3.prepare_input_embeds(
            t3_cond=m.conds.t3, text_tokens=text_tokens,
            speech_tokens=t3.hp.start_speech_token * torch.ones_like(text_tokens[:, :1]),
            cfg_weight=cfg_weight)
        if getattr(t3, "patched_model", None) is None:
            t3.patched_model = T3HuggingfaceBackend(
                config=t3.cfg, llama=t3.tfmr, speech_enc=t3.speech_emb,
                speech_head=t3.speech_head)
        model = t3.patched_model

        bos = torch.tensor([[t3.hp.start_speech_token]], dtype=torch.long, device=embeds.device)
        bos_embed = t3.speech_emb(bos) + t3.speech_pos_emb.get_fixed_embedding(0)
        out = model(inputs_embeds=torch.cat([embeds, torch.cat([bos_embed, bos_embed])], dim=1),
                    past_key_values=None, use_cache=True, output_attentions=False,
                    output_hidden_states=True, return_dict=True)
        past = out.past_key_values
        generated = bos.clone()
        rep = RepetitionPenaltyLogitsProcessor(penalty=float(repetition_penalty))
        minp, topp = MinPLogitsWarper(min_p=min_p), TopPLogitsWarper(top_p=top_p)
        cfg = torch.as_tensor(cfg_weight, device=embeds.device)

        for i in range(max_new_tokens):
            step = out.logits[:, -1, :]
            cond, uncond = step[0:1], step[1:2]
            logits = cond + cfg.to(cond.dtype) * (cond - uncond)
            ids = generated[:1]
            logits = rep(ids, logits)
            if temperature != 1.0:
                logits = logits / temperature
            logits = topp(ids, minp(ids, logits))
            nxt = torch.multinomial(torch.softmax(logits, dim=-1), num_samples=1)
            generated = torch.cat([generated, nxt], dim=1)
            if nxt.view(-1) == t3.hp.stop_speech_token:
                return
            yield int(nxt)
            emb = t3.speech_emb(nxt) + t3.speech_pos_emb.get_fixed_embedding(i + 1)
            out = model(inputs_embeds=torch.cat([emb, emb]), past_key_values=past,
                        output_attentions=False, output_hidden_states=True, return_dict=True)
            past = out.past_key_values

    @torch.inference_mode()
    def _tokens_turbo(self, text, temperature, top_k=1000, top_p=0.95,
                      repetition_penalty=1.2, max_gen_len=1000):
        from chatterbox.tts_turbo import punc_norm
        from transformers.generation.logits_process import (
            LogitsProcessorList, RepetitionPenaltyLogitsProcessor,
            TemperatureLogitsWarper, TopKLogitsWarper, TopPLogitsWarper)

        m, t3 = self.m, self.m.t3
        procs = LogitsProcessorList()
        if temperature > 0 and temperature != 1.0:
            procs.append(TemperatureLogitsWarper(temperature))
        if top_k > 0:
            procs.append(TopKLogitsWarper(top_k))
        if top_p < 1.0:
            procs.append(TopPLogitsWarper(top_p))
        if repetition_penalty != 1.0:
            procs.append(RepetitionPenaltyLogitsProcessor(repetition_penalty))

        text_tokens = m.tokenizer(punc_norm(text), return_tensors="pt", padding=True,
                                  truncation=True).input_ids.to(m.device)
        start = t3.hp.start_speech_token * torch.ones_like(text_tokens[:, :1])
        embeds, _ = t3.prepare_input_embeds(t3_cond=m.conds.t3, text_tokens=text_tokens,
                                            speech_tokens=start, cfg_weight=0.0)
        out = t3.tfmr(inputs_embeds=embeds, use_cache=True)
        past = out.past_key_values
        logits = t3.speech_head(out[0][:, -1:])
        history = start
        nxt = torch.multinomial(F.softmax(procs(history, logits[:, -1, :]), dim=-1), 1)
        history = nxt
        for _ in range(max_gen_len):
            if torch.all(nxt == t3.hp.stop_speech_token):
                return
            yield int(nxt)
            out = t3.tfmr(inputs_embeds=t3.speech_emb(nxt), past_key_values=past, use_cache=True)
            past = out.past_key_values
            scores = procs(history, t3.speech_head(out[0])[:, -1, :])
            if torch.all(scores == -float("inf")):
                return
            nxt = torch.multinomial(F.softmax(scores, dim=-1), 1)
            history = torch.cat([history, nxt], dim=1)

    # ------------------------------------------------------------ audio
    @torch.inference_mode()
    def stream(self, text: str, first_tokens: int = 12, chunk_tokens: int = 25,
               context_tokens: int = 50, vocoder_context_frames: int = 6,
               crossfade_samples: int = 480, exaggeration: float = 0.5,
               cfg_weight: float = 0.5, temperature: float = 0.8,
               cfm_steps: int | None = None, second_tokens: int | None = None):
        """Yield (audio float32 numpy at self.sr, info dict) pieces.

        first_tokens: tokens before the first piece (12 = ~0.5 s of audio).
        chunk_tokens: tokens per later piece (25 = ~1 s).
        context_tokens: earlier tokens re-decoded for context per piece.
        """
        m = self.m
        # Decoder (CFM) steps: generate() uses 10 (original) / 2 (Turbo).
        # Fewer steps = faster decode, possibly slightly lower quality.
        n_steps = cfm_steps or (2 if self.turbo else 10)
        gen = (self._tokens_turbo(text, temperature) if self.turbo else
               self._tokens_original(text, exaggeration, cfg_weight, temperature))
        t0 = time.perf_counter()
        tokens: list[int] = []
        state = {"emitted_frames": 0, "tail": None, "first": True, "pieces": 0}

        def flush_tail():
            """At the end, emit any held-back crossfade tail on its own."""
            tail, state["tail"] = state["tail"], None
            if tail is None:
                return None
            return tail.astype(np.float32), {"piece": state["pieces"] + 1, "tokens": len(tokens),
                                              "decode_ms": 0, "at_ms": round((time.perf_counter() - t0) * 1000),
                                              "audio_s": round(len(tail) / self.sr, 3),
                                              "watermarked": False, "final": True}

        def decode(final: bool):
            ws = max(0, state["emitted_frames"] // FRAMES_PER_TOKEN - context_tokens)
            window = tokens[ws:] + ([self.silence] * 3 if final and self.turbo else [])
            if len(window) <= (0 if final else LOOKAHEAD_TOKENS):
                return flush_tail() if final else None
            t = time.perf_counter()
            # The library's own streaming mode (finalize=False) is broken in
            # chatterbox-tts as installed on 2026-11-02: it trims the features
            # but not the mask (shape error 324 vs 330). So always decode with
            # finalize=True and drop the lookahead frames ourselves: the last
            # LOOKAHEAD_TOKENS were decoded without right context, and get
            # re-decoded with context in the next piece.
            mel = m.s3gen.flow_inference(
                torch.tensor(window, dtype=torch.long, device=m.device),
                ref_dict=m.conds.gen, n_cfm_timesteps=n_steps, finalize=True)
            if not final:
                mel = mel[:, :, :(len(window) - LOOKAHEAD_TOKENS) * FRAMES_PER_TOKEN]
            mel = mel.to(dtype=m.s3gen.dtype)
            win_start = ws * FRAMES_PER_TOKEN  # absolute frame of mel[..., 0]
            end = win_start + mel.shape[-1]
            if end <= state["emitted_frames"]:
                return flush_tail() if final else None
            a = max(win_start, state["emitted_frames"] - vocoder_context_frames)
            wav, _ = m.s3gen.hift_inference(mel[:, :, a - win_start:], None)
            wav = wav.squeeze(0).float().cpu().numpy()
            new_from = (state["emitted_frames"] - a) * SAMPLES_PER_FRAME
            out = wav[new_from:]
            if state["first"]:
                fade = m.s3gen.trim_fade.float().cpu().numpy()  # library's anti-spillover fade-in
                out[:len(fade)] *= fade[:len(out)]
            tail = state["tail"]
            if tail is not None:  # crossfade the held-back tail with the re-decoded overlap
                over = wav[new_from - len(tail):new_from]
                if len(over) == len(tail):
                    ramp = np.linspace(0, 1, len(tail), dtype=np.float32)
                    out = np.concatenate([tail * (1 - ramp) + over * ramp, out])
                else:
                    out = np.concatenate([tail, out])
            if not final and len(out) > crossfade_samples:
                state["tail"], out = out[-crossfade_samples:].copy(), out[:-crossfade_samples]
            else:
                state["tail"] = None
            state["emitted_frames"] = end
            try:
                out = m.watermarker.apply_watermark(out, sample_rate=self.sr)
                wm = True
            except Exception:
                wm = False
            state["first"] = False
            state["pieces"] += 1
            return np.asarray(out, dtype=np.float32), {
                "piece": state["pieces"], "tokens": len(tokens),
                "decode_ms": round((time.perf_counter() - t) * 1000),
                "at_ms": round((time.perf_counter() - t0) * 1000),
                "audio_s": round(len(out) / self.sr, 3), "watermarked": wm,
                "final": final}

        decoded_tokens = 0
        for tok in gen:
            if tok >= VALID_TOKEN_LIMIT:
                continue
            tokens.append(tok)
            # Ramp up piece sizes: a tiny first piece starts audio fast, a
            # medium second piece arrives before the first finishes playing.
            if state["first"]:
                need = first_tokens
            elif state["pieces"] == 1 and second_tokens:
                need = second_tokens
            else:
                need = chunk_tokens
            if len(tokens) - decoded_tokens >= need + LOOKAHEAD_TOKENS:
                piece = decode(final=False)
                decoded_tokens = len(tokens) - LOOKAHEAD_TOKENS
                if piece is not None:
                    yield piece
        piece = decode(final=True)
        if piece is not None:
            yield piece
