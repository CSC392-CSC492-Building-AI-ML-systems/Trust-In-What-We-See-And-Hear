"""Groups streamed LLM text into chunks for text to speech.

Each speech call costs roughly a second however short the text is, so
speaking every tiny sentence separately ("Please.") causes audible pauses.
- The first complete sentence is sent alone, so audio starts quickly.
- Later sentences are joined until a chunk has at least `min_chars`
  characters (about 3 s of speech), so each call produces enough audio to
  cover the time the next call takes.

It also keeps a reply short and fresh, because the LLM does not reliably do
so when asked: sentences past `max_sentences` are dropped, and so is any
sentence the caller already said earlier in the call (`said`).

AI-assisted code (written with Claude Code and Codex), cited per the CSC398 AI policy.
"""

import re
import unicodedata

# While streaming, a sentence only counts as finished once whitespace follows
# the punctuation; otherwise "Hey." could be cut before ".." arrives.
SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*\s+")
TAG = re.compile(r"\[[a-z ]{2,20}\]")


def sentence_key(sentence: str) -> str:
    """Compare words without punctuation, preserving numbers and Unicode."""
    text = unicodedata.normalize("NFC", TAG.sub("", sentence)).casefold()
    return " ".join("".join(
        ch if ch.isalnum() or unicodedata.category(ch).startswith("M") else " "
        for ch in text).split())


class SpeechChunker:
    def __init__(self, min_chars: int = 45, first_min_chars: int = 12,
                 max_sentences: int | None = None, said: set[str] | None = None):
        self.min_chars = min_chars
        self.first_min_chars = first_min_chars
        self.max_sentences = max_sentences
        self.said = said if said is not None else set()  # keys of earlier sentences
        self.buf = ""
        self.pending = ""
        self.sent_first = False
        self.kept: list[str] = []  # the sentences that will be spoken
        self.repeats: list[str] = []  # dropped because they were said before

    def _keep(self, sentence: str) -> bool:
        if self.max_sentences is not None and len(self.kept) >= self.max_sentences:
            return False
        key = sentence_key(sentence)
        # Short ones ("Okay, okay.") may come back; whole lines may not.
        if len(key.split()) >= 4 and key in self.said:
            self.repeats.append(sentence)
            return False
        self.kept.append(sentence)
        return True

    def text(self) -> str:
        """The reply as it will be spoken."""
        return " ".join(self.kept)

    def feed(self, delta: str) -> list[str]:
        """Add streamed text; return any chunks that are ready to speak."""
        self.buf += delta
        out = []
        while (m := SENTENCE_END.search(self.buf)) is not None:
            sentence = self.buf[:m.end()].strip()
            self.buf = self.buf[m.end():]
            if not sentence or not self._keep(sentence):
                continue
            self.pending = f"{self.pending} {sentence}".strip()
            need = self.first_min_chars if not self.sent_first else self.min_chars
            if len(TAG.sub("", self.pending)) >= need:
                out.append(self.pending)
                self.pending = ""
                self.sent_first = True
        return out

    def flush(self) -> list[str]:
        last = self.buf.strip()
        if TAG.sub("", last).strip() and self._keep(last):
            self.pending = f"{self.pending} {last}".strip()
        if not self.kept and self.repeats:
            # Everything was a repeat. Saying one line again beats saying nothing.
            self.kept.append(self.repeats[0])
            self.pending = self.repeats[0]
        rest = self.pending
        self.pending = self.buf = ""
        self.said.update(sentence_key(s) for s in self.kept)
        return [rest] if TAG.sub("", rest).strip() else []


def strip_tags(text: str, keep: tuple[str, ...] = ()) -> str:
    """Remove [sigh]-style tags, except the ones the voice engine can perform.

    An engine reads any other tag aloud as a word.
    """
    kept = TAG.sub(lambda m: m.group(0) if m.group(0) in keep else "", text)
    return re.sub(r"\s{2,}", " ", kept).strip()
