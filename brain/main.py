"""
Brain service. Both the Windows and (future) Android clients talk to this
over HTTP - it's the only place LLM calls happen.

Run: uvicorn main:app --reload --port 8008
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import memory
from config import settings
from llm_providers import ProviderError, generate_text, parse_intent

app = FastAPI(title="Voice Assistant Brain")


class IntentRequest(BaseModel):
    text: str


class RememberRequest(BaseModel):
    contact: str
    text: str


class DraftMessageRequest(BaseModel):
    contact: str
    intent: str


class AnswerRequest(BaseModel):
    question: str


@app.post("/intent")
def get_intent(req: IntentRequest):
    """Turn a transcribed command into a structured action."""
    try:
        return parse_intent(req.text)
    except ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/remember")
def remember(req: RememberRequest):
    """Log a message the user actually sent - this is the training
    signal for style personalization. Call this from the client after
    the user approves/sends a drafted message (or edits then sends it -
    log the EDITED version, that's the real signal)."""
    memory.remember_message(req.contact, req.text)
    return {"ok": True}


@app.get("/style/{contact}")
def style_examples(contact: str, limit: int = 5):
    return {"contact": contact, "examples": memory.get_style_examples(contact, limit)}


@app.post("/draft-message")
def draft_message(req: DraftMessageRequest):
    """Draft a message in the user's own style, using their past
    messages to this contact (or globally, if none yet) as reference."""
    examples = memory.get_style_examples(req.contact)

    if examples:
        examples_block = "\n".join(f"- {e}" for e in examples)
        system_prompt = (
            "You draft short chat messages that sound exactly like the user "
            "normally writes. Match their tone, length, punctuation habits, "
            "and use of emoji/abbreviations. Reply with ONLY the message text, "
            "nothing else.\n\nExamples of how this user writes:\n" + examples_block
        )
    else:
        system_prompt = (
            "You draft short, natural chat messages. Reply with ONLY the "
            "message text, nothing else. (No style history yet for this user - "
            "use a friendly, casual default tone.)"
        )

    try:
        draft = generate_text(system_prompt, f"Write a message that: {req.intent}")
    except ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {"contact": req.contact, "draft": draft.strip(), "used_examples": len(examples)}


@app.post("/answer")
def answer_question(req: AnswerRequest):
    system_prompt = "You are a helpful voice assistant. Answer in AT MOST 2 short spoken sentences, under 40 words total. No markdown, no lists, no preamble like 'Sure!' - just the answer."
    try:
        answer = generate_text(system_prompt, req.question)
        return {"answer": answer}
    except ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok", "provider_order": settings.provider_order}
