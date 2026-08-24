import os
import base64
from email.mime.text import MIMEText
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
DIR_PATH = os.path.dirname(os.path.abspath(__file__))

def get_gmail_service():
    creds = None
    token_path = os.path.join(DIR_PATH, "token.json")
    credentials_path = os.path.join(DIR_PATH, "credentials.json")

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        
        with open(token_path, "w") as token:
            token.write(creds.to_json())
    
    return build("gmail", "v1", credentials=creds)

def list_unread(max_results=15):
    service = get_gmail_service()
    results = service.users().messages().list(userId="me", labelIds=["INBOX", "UNREAD"], q="category:primary", maxResults=max_results).execute()
    messages = results.get("messages", [])
    
    emails = []
    for msg in messages:
        msg_data = service.users().messages().get(userId="me", id=msg["id"], format="metadata", metadataHeaders=["From", "Subject"]).execute()
        
        headers = msg_data.get("payload", {}).get("headers", [])
        from_hdr = next((h["value"] for h in headers if h["name"] == "From"), "Unknown")
        subject_hdr = next((h["value"] for h in headers if h["name"] == "Subject"), "No Subject")
        
        emails.append({
            "id": msg["id"],
            "from": from_hdr,
            "subject": subject_hdr,
            "snippet": msg_data.get("snippet", "")
        })
    return emails

def send_email(to: str, subject: str, body: str):
    service = get_gmail_service()
    
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode()
    create_message = {"raw": encoded}
    
    service.users().messages().send(userId="me", body=create_message).execute()

def mark_as_read(message_id: str):
    service = get_gmail_service()
    service.users().messages().modify(
        userId="me", id=message_id,
        body={"removeLabelIds": ["UNREAD"]}
    ).execute()
