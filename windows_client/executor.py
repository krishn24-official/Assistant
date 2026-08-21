"""
Maps a structured intent (from the brain's /intent endpoint) to a real
Windows action. This is the only file that touches the OS - keeping it
separate makes it easy to write an equivalent macOS/Linux executor later
without touching the brain at all.
"""
import os
import subprocess
import webbrowser
import difflib

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import requests

BRAIN_URL = os.getenv("BRAIN_URL", "http://localhost:8008")

# Add to this as you go - common app name -> how to launch it on Windows.
APP_LAUNCHERS = {
    "chrome": "start chrome",
    "outlook": "start outlook",
    "spotify": "start spotify",
    "notepad": "notepad",
    "word": "start winword",
    "excel": "start excel",
    "calculator": "start calc",
    "code": "start code",
    "whatsapp": "start whatsapp:",
}

ALIASES = {
    "vs code": "code", "vscode": "code", "visual studio code": "code",
    "vso": "code", "s-code": "code", "s4": "excel", "gis code": "code",
    "google chrome": "chrome", "google": "chrome", "chrome browser": "chrome",
}

SITE_ALIASES = {
    "gmail": "https://mail.google.com", "google mail": "https://mail.google.com",
    "youtube": "https://www.youtube.com",
    "google maps": "https://maps.google.com", "maps": "https://maps.google.com",
    "google drive": "https://drive.google.com", "drive": "https://drive.google.com",
    "google docs": "https://docs.google.com", "docs": "https://docs.google.com",
    "google sheets": "https://sheets.google.com", "sheets": "https://sheets.google.com",
    "whatsapp": "https://web.whatsapp.com", "whatsapp web": "https://web.whatsapp.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "twitter": "https://www.x.com", "x": "https://www.x.com",
    "linkedin": "https://www.linkedin.com",
    "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.com",
    "flipkart": "https://www.flipkart.com",
    "github": "https://www.github.com",
    "chatgpt": "https://chat.openai.com",
    "wikipedia": "https://www.wikipedia.org",
    "spotify": "https://open.spotify.com",
    "reddit": "https://www.reddit.com",
    "news": "https://news.google.com",
}

CONVERSATIONAL_SITES = {
    "gmail",
    "google mail",
    "whatsapp",
    "whatsapp web",
}

CONVERSATIONAL_APPS = {
    "whatsapp",
}


def open_app(app_name: str):
    key = app_name.lower().strip()
    
    if key in ALIASES:
        key = ALIASES[key]
        
    if key in APP_LAUNCHERS:
        cmd = APP_LAUNCHERS[key]
        subprocess.Popen(cmd, shell=True)
        if key in CONVERSATIONAL_APPS:
            return {
                "needs_followup": True,
                "message": f"Opening {app_name}. What would you like me to do?",
                "context": f"The user just opened {app_name} desktop app."
            }
        return f"Opening {app_name}."
        
    candidates = list(ALIASES.keys()) + list(APP_LAUNCHERS.keys())
    matches = difflib.get_close_matches(key, candidates, n=1, cutoff=0.6)
    
    if matches:
        matched_name = matches[0]
        if matched_name in ALIASES:
            matched_name = ALIASES[matched_name]
        return {
            "needs_confirmation": True,
            "suggested": matched_name,
            "original_action": "open_app",
            "original_params": {"app_name": matched_name}
        }
        
    # Fall back to websites if it wasn't found in apps
    if key in SITE_ALIASES:
        return open_website(key)
    site_matches = difflib.get_close_matches(key, SITE_ALIASES.keys(), n=1, cutoff=0.6)
    if site_matches:
        return open_website(site_matches[0])

    return f"I don't have '{app_name}' set up yet - add it to APP_LAUNCHERS or ALIASES in executor.py."


def web_search(query: str) -> str:
    webbrowser.open(f"https://www.google.com/search?q={requests.utils.quote(query)}")
    return f"Searching the web for {query}."


def open_website(site: str) -> str:
    key = site.lower().strip()
    url = SITE_ALIASES.get(key)
    if not url:
        import difflib
        close = difflib.get_close_matches(key, SITE_ALIASES.keys(), n=1, cutoff=0.6)
        if close:
            url = SITE_ALIASES[close[0]]
            site = close[0]
    if url:
        webbrowser.open(url)
        print(f"[debug] open_website resolved key: '{key}', in CONVERSATIONAL_SITES: {key in CONVERSATIONAL_SITES}")
        if key in CONVERSATIONAL_SITES:
            return {
                "needs_followup": True,
                "message": f"Opening {site}. What would you like me to do?",
                "context": f"The user just opened {site} website."
            }
        return f"Opening {site}."
    return web_search(site)  # fallback: just search for it


def draft_email(to: str, subject: str, body: str) -> str:
    # mailto: opens the default mail client with a pre-filled draft -
    # works everywhere without needing Outlook COM automation for v1.
    # Swap in win32com.client for richer Outlook-specific drafts later.
    mailto = (
        f"mailto:{to}?subject={requests.utils.quote(subject)}"
        f"&body={requests.utils.quote(body)}"
    )
    os.startfile(mailto)
    return f"Drafted an email to {to}."


def draft_message(contact: str, intent: str) -> str:
    """Calls the brain's personalized drafting endpoint, then just
    surfaces the draft (v1: copies to clipboard) rather than actually
    sending it - sending should always stay a deliberate user action."""
    if len(intent.strip().split()) < 4:
        return {
            "needs_clarification": True,
            "message": f"What would you like to say to {contact}?",
            "action": "draft_message",
            "contact": contact
        }
    return _draft_message_core(contact, intent)

def _draft_message_core(contact: str, intent: str) -> str:
    resp = requests.post(
        f"{BRAIN_URL}/draft-message", json={"contact": contact, "intent": intent}, timeout=20
    )
    resp.raise_for_status()
    draft = resp.json()["draft"]

    try:
        import pyperclip

        pyperclip.copy(draft)
        return f"Drafted for {contact}, copied to clipboard: {draft}"
    except ImportError:
        return f"Drafted for {contact}: {draft}"


def set_reminder(when: str, label: str) -> str:
    # v1 stub - wire up to Windows Task Scheduler or a simple background
    # timer thread later. For now, just acknowledge it.
    return f"Reminder noted: {label} ({when}). (Reminder execution not wired up yet.)"


def create_folder(name: str, location: str = "desktop") -> str:
    base_map = {
        "desktop": os.path.join(os.path.expanduser("~"), "Desktop"),
        "documents": os.path.join(os.path.expanduser("~"), "Documents"),
        "downloads": os.path.join(os.path.expanduser("~"), "Downloads"),
    }
    base = base_map.get(location.lower().strip(), base_map["desktop"])
    path = os.path.join(base, name)
    try:
        os.makedirs(path, exist_ok=False)
        return f"Created folder '{name}' in {location}."
    except FileExistsError:
        return f"A folder named '{name}' already exists there."


def _search_filesystem(query: str, only_dirs: bool = False, max_results: int = 5, max_depth: int = 4):
    import os as _os
    query_lower = query.lower()
    roots = [_os.path.expanduser("~"), "C:\\", "D:\\"]
    results = []
    seen_roots = set()
    for root in roots:
        if not _os.path.isdir(root) or root in seen_roots:
            continue
        seen_roots.add(root)
        base_depth = root.rstrip("\\/").count(_os.sep)
        for dirpath, dirnames, filenames in _os.walk(root):
            depth = dirpath.count(_os.sep) - base_depth
            if depth >= max_depth:
                dirnames[:] = []
                continue
            # skip noisy system dirs for speed
            dirnames[:] = [d for d in dirnames if d.lower() not in
                           ("windows", "$recycle.bin", "programdata", "node_modules", ".git")]
            for d in dirnames:
                if query_lower in d.lower():
                    results.append(_os.path.join(dirpath, d))
            if not only_dirs:
                for f in filenames:
                    if query_lower in f.lower():
                        results.append(_os.path.join(dirpath, f))
            if len(results) >= max_results:
                return results
    return results


def _find_folder_by_name(name: str):
    matches = _search_filesystem(name, only_dirs=True, max_results=1)
    return matches[0] if matches else None


def search_files(query: str) -> str:
    matches = _search_filesystem(query, max_results=5)
    if not matches:
        return f"Couldn't find anything matching '{query}'."
    print(f"[search_files] full matches for '{query}':")
    for m in matches:
        print(f"  {m}")
    if len(matches) == 1:
        import os as _os
        return f"Found it: {_os.path.basename(matches[0])}"
    import os as _os
    names = ", ".join(_os.path.basename(m) for m in matches[:3])
    extra = f" and {len(matches) - 3} more" if len(matches) > 3 else ""
    return f"Found {len(matches)} matches, including {names}{extra}. Full paths printed in the console."


def open_folder(name: str) -> str:
    common = {
        "desktop": os.path.join(os.path.expanduser("~"), "Desktop"),
        "documents": os.path.join(os.path.expanduser("~"), "Documents"),
        "downloads": os.path.join(os.path.expanduser("~"), "Downloads"),
        "pictures": os.path.join(os.path.expanduser("~"), "Pictures"),
    }
    key = name.lower().strip()
    if key in common:
        path = common[key]
    else:
        # fall back to searching for a folder with this name
        # across common drives (reuse search logic from search_files below)
        # Note: this is a synchronous full walk which can be slow on large drives -
        # a v2 could use Windows Search (via win32com) for instant results instead.
        path = _find_folder_by_name(key)
        if not path:
            return f"Couldn't find a folder named '{name}'."
    if os.path.isdir(path):
        os.startfile(path)
        return f"Opening {name} folder."
    return f"Couldn't find a folder named '{name}'."


def answer_question(question: str) -> str:
    resp = requests.post(f"{BRAIN_URL}/answer", json={"question": question}, timeout=20)
    resp.raise_for_status()
    return resp.json()["answer"]


def play_video(query: str) -> str:
    key = os.getenv("YOUTUBE_API_KEY", "")
    if not key:
        webbrowser.open(f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}")
        return f"No YouTube API key set - opened search results for {query} instead."
    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={"part": "snippet", "q": query, "type": "video", "maxResults": 1, "key": key},
        timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        return f"Couldn't find a video for {query}."
    video_id = items[0]["id"]["videoId"]
    webbrowser.open(f"https://www.youtube.com/watch?v={video_id}")
    return f"Playing {items[0]['snippet']['title']}."


DISPATCH = {
    "open_app": lambda p: open_app(**p),
    "web_search": lambda p: web_search(**p),
    "draft_email": lambda p: draft_email(**p),
    "draft_message": lambda p: draft_message(**p),
    "set_reminder": lambda p: set_reminder(**p),
    "create_folder": lambda p: create_folder(**p),
    "answer_question": lambda p: answer_question(**p),
    "play_video": lambda p: play_video(**p),
    "open_folder": lambda p: open_folder(**p),
    "search_files": lambda p: search_files(**p),
    "open_website": lambda p: open_website(**p),
    "no_action": lambda p: f"Didn't catch a clear command ({p.get('reason', 'unclear')}).",
}

RESOLVE_CLARIFICATION = {
    "draft_message": lambda contact, text: _draft_message_core(contact, text)
}


def execute(intent: dict) -> str:
    action = intent["action"]
    params = intent.get("params", {})
    handler = DISPATCH.get(action)
    if not handler:
        return f"Don't know how to handle action '{action}' yet."
    return handler(params)
