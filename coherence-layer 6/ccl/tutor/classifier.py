"""Request classifier (section 8, pipeline step 3).

A deterministic, inspectable classifier for the milestone. A model-based
classifier can slot in later behind the same function signature; keeping it
rule-based here means the tutor's routing is itself testable and free.
"""

from __future__ import annotations

from enum import Enum

from .injection import INJECTION_MARKERS


class RequestType(str, Enum):
    CONCEPT_HELP = "concept_help"
    SOLUTION_REQUEST = "solution_request"
    CLARIFICATION = "clarification"
    OFF_TOPIC = "off_topic"
    UNSAFE = "unsafe"
    PROMPT_INJECTION = "prompt_injection"


_UNSAFE_MARKERS = ("hurt myself", "kill myself", "want to die", "self-harm", "suicide")
_SOLUTION_MARKERS = (
    "just give me the answer", "what's the answer", "what is the answer",
    "give me the answer", "just the answer", "solve it for me", "final answer",
    "do it for me", "just tell me",
)
_CLARIFY_MARKERS = ("what do you mean", "can you rephrase", "i don't understand the question")


def classify(student_message: str, injected_source_text: str = "") -> RequestType:
    text = student_message.lower()
    blob = f"{text}\n{injected_source_text.lower()}"

    if any(m in blob for m in INJECTION_MARKERS):
        return RequestType.PROMPT_INJECTION
    if any(m in text for m in _UNSAFE_MARKERS):
        return RequestType.UNSAFE
    if any(m in text for m in _SOLUTION_MARKERS):
        return RequestType.SOLUTION_REQUEST
    if any(m in text for m in _CLARIFY_MARKERS):
        return RequestType.CLARIFICATION
    return RequestType.CONCEPT_HELP
