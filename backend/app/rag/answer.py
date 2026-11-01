"""
Answer generation layer. The LLM/extractive layer NEVER makes PASS/FAIL
decisions, never does waiting-period or financial arithmetic, and never
sees anything except the user's question plus the retrieved chunk texts
(never the whole database).

Provider abstraction: if no LLM API key is configured in the environment,
this falls back to a purely EXTRACTIVE answer (the retrieved passage
itself, verbatim, with a short deterministic lead-in) -- this requires no
network call and cannot hallucinate, since it never generates new text,
only surfaces real retrieved text. Extractive mode is the default.

If ANTHROPIC_API_KEY or OPENAI_API_KEY is present in the environment (and
the matching `anthropic` or `openai` package is installed), that provider
produces a natural-language summary of the SAME retrieved passages. The
system prompt forbids outside knowledge and inferring missing terms. Any
provider error, including a missing package, falls back to extractive mode.
"""
import os
from abc import ABC, abstractmethod
from typing import List

from app.rag.retrieve import RetrievedChunk

SYSTEM_PROMPT = (
    "You are a policy-document explanation assistant.\n"
    "Answer only from the supplied source passages.\n"
    "Do not use outside knowledge.\n"
    "Do not infer missing policy terms.\n"
    "If the answer is not supported by the passages, say: "
    "'Not found in the selected policy source.'\n"
    "Cite every factual claim with document and page."
)


class LLMProvider(ABC):
    @abstractmethod
    def generate_answer(self, question: str, chunks: List[RetrievedChunk]) -> str:
        ...


class ExtractiveProvider(LLMProvider):
    """No LLM call at all. Returns the single most relevant retrieved
    passage verbatim, prefixed with a fixed, non-generative lead-in. This
    is the safest possible option: it is architecturally incapable of
    inventing policy facts, because it never generates new text."""

    def generate_answer(self, question: str, chunks: List[RetrievedChunk]) -> str:
        if not chunks:
            return "Not found in the selected policy source."
        parts = [f"Based on {c.document_id} (page {c.page}): {c.text}" for c in chunks]
        return "\n\n".join(parts)


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.api_key = api_key
        self.model = model

    def generate_answer(self, question: str, chunks: List[RetrievedChunk]) -> str:
        if not chunks:
            return "Not found in the selected policy source."
        try:
            from openai import OpenAI

            passages = "\n\n".join(
                f"[{c.document_id}, page {c.page}]: {c.text}" for c in chunks
            )
            client = OpenAI(api_key=self.api_key)
            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Question: {question}\n\nSource passages:\n{passages}"},
                ],
            )
            return resp.choices[0].message.content
        except Exception:
            return ExtractiveProvider().generate_answer(question, chunks)


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001"):
        self.api_key = api_key
        self.model = model

    def generate_answer(self, question: str, chunks: List[RetrievedChunk]) -> str:
        if not chunks:
            return "Not found in the selected policy source."
        try:
            import anthropic

            passages = "\n\n".join(
                f"[{c.document_id}, page {c.page}]: {c.text}" for c in chunks
            )
            client = anthropic.Anthropic(api_key=self.api_key)
            resp = client.messages.create(
                model=self.model,
                max_tokens=500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"Question: {question}\n\nSource passages:\n{passages}"}],
            )
            return resp.content[0].text
        except Exception:
            return ExtractiveProvider().generate_answer(question, chunks)


def get_provider() -> LLMProvider:
    """Selects a provider based on environment variables ONLY -- never a
    hardcoded key. Falls back to the extractive (no-API-key-needed)
    provider if nothing is configured."""
    openai_key = os.environ.get("OPENAI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        return AnthropicProvider(anthropic_key)
    if openai_key:
        return OpenAIProvider(openai_key)
    return ExtractiveProvider()