"""
Brain service. Both the Windows and (future) Android clients talk to this
over HTTP - it's the only place LLM calls happen.

Run: uvicorn main:app --reload --port 8008
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import memory
import gmail_client
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


class SendEmailRequest(BaseModel):
    to: str
    subject: str
    body: str

class GenerateReplyRequest(BaseModel):
    original_subject: str
    original_snippet: str
    instruction: str


class ReviseEmailRequest(BaseModel):
    to: str
    subject: str
    body: str
    instruction: str


class MarkReadRequest(BaseModel):
    message_id: str


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


@app.get("/gmail/unread")
def get_unread_emails():
    try:
        emails = gmail_client.list_unread()
        return {"emails": emails}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/gmail/send")
def send_email(req: SendEmailRequest):
    try:
        gmail_client.send_email(req.to, req.subject, req.body)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/gmail/mark-read")
def mark_read(req: MarkReadRequest):
    try:
        gmail_client.mark_as_read(req.message_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-reply")
def generate_reply(req: GenerateReplyRequest):
    system_prompt = (
        "You are writing a short, natural email reply. Given the original email's subject "
        "and a snippet of its content, and the user's instruction for what to say, write "
        "ONLY the reply body - concise, 2-4 sentences unless the instruction implies more. "
        "If the instruction is something like 'write something for me' or empty, write a brief, "
        "polite, appropriate reply based on the snippet alone."
    )
    prompt = f"Subject: {req.original_subject}\nSnippet: {req.original_snippet}\nInstruction: {req.instruction}"
    try:
        from llm_providers import generate_text, ProviderError
        draft = generate_text(system_prompt, prompt).strip()
        return {"body": draft}
    except ProviderError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/revise-email")
def revise_email(req: ReviseEmailRequest):
    system_prompt = (
        "You are revising a draft email based on the user's instruction. Return ONLY "
        "the revised email in this exact format:\n"
        "TO: <email address>\n"
        "SUBJECT: <subject>\n"
        "BODY: <body>\n"
        "Keep it concise. If the instruction only concerns one field, keep the "
        "other fields UNCHANGED exactly as given - do not invent a new recipient, "
        "subject, or body unless the instruction asks for that specific field."
    )
    prompt = f"Original To: {req.to}\nOriginal Subject: {req.subject}\nOriginal Body: {req.body}\nInstruction: {req.instruction}"
    try:
        revised_text = generate_text(system_prompt, prompt).strip()
        import re
        match = re.search(
            r"TO:\s*(.*?)\nSUBJECT:\s*(.*?)\nBODY:\s*(.*)",
            revised_text,
            re.DOTALL
        )
        if match:
            to_address = match.group(1).strip() or req.to
            subject = match.group(2).strip() or req.subject
            body = match.group(3).strip() or req.body
        else:
            to_address, subject, body = req.to, req.subject, req.body
            
        return {"to": to_address, "subject": subject, "body": body}
    except ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))
