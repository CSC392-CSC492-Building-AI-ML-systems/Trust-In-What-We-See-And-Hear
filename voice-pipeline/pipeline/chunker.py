"""Groups streamed LLM text into chunks for text to speech.

Each speech call costs roughly a second however short the text is, so
speaking every tiny sentence separately ("Please.") causes audible pauses.
- The first complete sentence is sent alone, so audio starts quickly.
- Later sentences are joined until a chunk has at least `min_chars`
  characters (about 3 s of speech), so each call produces enough audio to
  cover the time the next call takes.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import re

# While streaming, a sentence only counts as finished once whitespace follows
# the punctuation; otherwise "Hey." could be cut before ".." arrives.
SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*\s+")
TAG = re.compile(r"\[[a-z ]{2,20}\]")


class SpeechChunker:
    def __init__(self, min_chars: int = 45, first_min_chars: int = 12):
        self.min_chars = min_chars
        self.first_min_chars = first_min_chars
        self.buf = ""
        self.pending = ""
        self.sent_first = False

    def feed(self, delta: str) -> list[str]:
        """Add streamed text; return any chunks that are ready to speak."""
        self.buf += delta
        out = []
        while (m := SENTENCE_END.search(self.buf)) is not None:
            sentence = self.buf[:m.end()].strip()
            self.buf = self.buf[m.end():]
            if not sentence:
                continue
            self.pending = f"{self.pending} {sentence}".strip()
            need = self.first_min_chars if not self.sent_first else self.min_chars
            if len(TAG.sub("", self.pending)) >= need:
                out.append(self.pending)
                self.pending = ""
                self.sent_first = True
        return out

    def flush(self) -> list[str]:
        rest = f"{self.pending} {self.buf}".strip()
        self.pending = self.buf = ""
        return [rest] if TAG.sub("", rest).strip() else []


def strip_tags(text: str, keep: tuple[str, ...] = ()) -> str:
    """Remove [sigh]-style tags, except the ones the voice engine can perform.

    An engine reads any other tag aloud as a word.
    """
    kept = TAG.sub(lambda m: m.group(0) if m.group(0) in keep else "", text)
    return re.sub(r"\s{2,}", " ", kept).strip()
