"""
The fixed set of actions the assistant is allowed to take. Every LLM call
is forced to pick one of these via tool-calling, so the brain never has to
parse free-text responses - it gets structured JSON every time.

Defined once in OpenAI/Groq/Mistral "function" format, then converted to
Anthropic's "tool" format automatically (see to_anthropic_tools()).
"""
from typing import Any, Dict, List

TOOLS_OPENAI_FORMAT: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Open a desktop or mobile application by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "e.g. 'chrome', 'outlook', 'spotify', 'whatsapp'",
                    }
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for a general query. Do NOT use this for well-known named websites/services (gmail, youtube, whatsapp, etc.) - use open_website for those instead.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_email",
            "description": "Draft (not send) an email in the user's default mail client.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "recipient email or contact name"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_message",
            "description": (
                "Draft a chat/SMS/WhatsApp-style message to a contact, written in "
                "that user's own texting style using their past messages as reference."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact": {"type": "string"},
                    "intent": {
                        "type": "string",
                        "description": "what the user wants to say, in plain terms",
                    },
                },
                "required": ["contact", "intent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "Set a reminder/alarm for a given time and label.",
            "parameters": {
                "type": "object",
                "properties": {
                    "when": {"type": "string", "description": "natural language time, e.g. 'in 20 minutes'"},
                    "label": {"type": "string"},
                },
                "required": ["when", "label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "no_action",
            "description": "Use this when the input isn't a clear command - unclear audio, silence, or unrelated speech",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"}
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer_question",
            "description": "Use this when the user is asking a factual question or wants a spoken answer, not a device action.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_video",
            "description": "Use this when the user wants to watch/play a specific video, e.g. 'play the new Avengers trailer'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_folder",
            "description": "Create a new folder/directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "location": {
                        "type": "string",
                        "description": "desktop, documents, or downloads; defaults to desktop if not specified"
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_folder",
            "description": "Open an existing folder by name or common alias, e.g. 'downloads', 'desktop', 'documents', 'games', or a custom folder name the user has referenced before.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search the local filesystem for a file or folder by name (partial match ok). Use this when the user wants to find something on their computer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_website",
            "description": "Open a well-known website directly by name (gmail, youtube, whatsapp, instagram, maps, drive, amazon, netflix, github, etc.) - use this INSTEAD OF web_search whenever the user names a specific known site/service rather than asking to search for something.",
            "parameters": {
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                },
                "required": ["site"],
            },
        },
    },
]


# def to_anthropic_tools(tools=TOOLS_OPENAI_FORMAT) -> List[Dict[str, Any]]:
#     """Anthropic's tool format is close but flattened - convert once here
#     so schemas.py stays the single source of truth."""
#     converted = []
#     for t in tools:
#         fn = t["function"]
#         converted.append(
#             {
#                 "name": fn["name"],
#                 "description": fn["description"],
#                 "input_schema": fn["parameters"],
#             }
#         )
def to_gemini_tools(tools=TOOLS_OPENAI_FORMAT):
    """Gemini wants function declarations nested under one tools object."""
    declarations = []
    for t in tools:
        fn = t["function"]
        declarations.append(
            {
                "name": fn["name"],
                "description": fn["description"],
                "parameters": fn["parameters"],
            }
        )
    return [{"function_declarations": declarations}]
