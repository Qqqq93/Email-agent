import streamlit as st
import requests
import time
from datetime import datetime
from email.utils import parsedate_to_datetime

# -------------------------------------------------------------------
# Page setup
# -------------------------------------------------------------------
st.set_page_config(page_title="📧 Gmail Conversational Agent", layout="wide")
st.markdown("<h2 style='text-align:center;'>📧 Gmail Conversational Agent</h2>", unsafe_allow_html=True)

# Backend endpoints
BASE_URL = "http://127.0.0.1:8000/gmail/"
SEND_URL = BASE_URL + "send/"
LIST_URL = BASE_URL + "list/"
SUMMARY_URL = BASE_URL + "summary/"

# -------------------------------------------------------------------
# Multiple chat threads
# -------------------------------------------------------------------
if "chats" not in st.session_state:
    st.session_state["chats"] = {
        "Chat 1": [
            {"role": "assistant", "content": "Hi 👋, I’m your Gmail AI assistant.\n\nHow can I help you today?"}
        ]
    }
    st.session_state["current_chat"] = "Chat 1"

def current_messages():
    return st.session_state["chats"][st.session_state["current_chat"]]

# -------------------------------------------------------------------
# API Helpers
# -------------------------------------------------------------------
def list_emails(limit=3):
    try:
        resp = requests.get(LIST_URL, params={"limit": limit}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def summarize_emails(limit=5):
    try:
        resp = requests.get(SUMMARY_URL, params={"limit": limit}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def send_email(to, subject, body):
    try:
        payload = {"to": to, "subject": subject, "body": body}
        resp = requests.post(SEND_URL, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def format_time(raw_time):
    """Convert Gmail RFC822 or epoch time to nice format"""
    if not raw_time:
        return ""
    try:
        # Try parsing Gmail RFC822 date
        dt = parsedate_to_datetime(raw_time)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        try:
            # Fallback if timestamp is epoch (ms)
            if isinstance(raw_time, (int, float)) or raw_time.isdigit():
                dt = datetime.fromtimestamp(int(raw_time) / 1000)
                return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    return str(raw_time)

# -------------------------------------------------------------------
# Chat display
# -------------------------------------------------------------------
for msg in current_messages():
    if msg["role"] == "user":
        st.markdown(f"<div style='margin:8px 0;'><b>You:</b><br>{msg['content']}</div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div style='margin:8px 0;'><b>Assistant:</b><br>{msg['content']}</div>", unsafe_allow_html=True)

# -------------------------------------------------------------------
# Chat input
# -------------------------------------------------------------------
if prompt := st.chat_input("Type your Gmail request..."):
    current_messages().append({"role": "user", "content": prompt})
    reply = None
    p = prompt.lower().strip()

    if "list" in p and "email" in p:
        with st.spinner("Fetching emails..."):
            data = list_emails(limit=3)
        if "error" in data:
            reply = f"⚠️ Failed to fetch emails: {data['error']}"
        else:
            reply = "📩 **Latest Emails:**\n\n"
            for i, e in enumerate(data, 1):
                reply += f"**{i}. {e.get('subject','(No subject)')}**\n"
                reply += f"- From: {e.get('from')}\n"
                reply += f"- Time: {format_time(e.get('time'))}\n"
                reply += f"- Body: {e.get('body')}\n\n"

    elif "summarize" in p:
        with st.spinner("Summarizing emails..."):
            data = summarize_emails(limit=5)
        if "error" in data:
            reply = f"⚠️ Failed to summarize emails: {data['error']}"
        else:
            reply = "📝 **Inbox Summary:**\n\n"
            reply += data.get("summary", str(data))

    elif "send an email to" in p:
        try:
            parts = prompt.split("send an email to")[1].strip().split("saying")
            recipient = parts[0].strip()
            body = parts[1].strip() if len(parts) > 1 else "Hello!"
        except:
            recipient, body = "unknown@example.com", "Hello!"
        subject = body[:30] or "No subject"

        with st.spinner("Sending email..."):
            data = send_email(recipient, subject, body)
        if "error" in data:
            reply = f"⚠️ Failed to send email: {data['error']}"
        else:
            reply = f"""✅ **Email Sent**

- To: {recipient}  
- Subject: {subject}  
- Body: {body}  
- Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}  
"""

    else:
        reply = "⚠️ I can:\n- `List my last 3 emails`\n- `Summarize my recent emails`\n- `Send an email to someone@example.com saying Hi`"

    with st.spinner("Assistant is typing..."):
        time.sleep(1.2)

    current_messages().append({"role": "assistant", "content": reply})
    st.rerun()

# -------------------------------------------------------------------
# Sidebar
# -------------------------------------------------------------------
st.sidebar.header("💬 Conversation Controls")

if st.sidebar.button("➕ New Chat"):
    new_name = f"Chat {len(st.session_state['chats'])+1}"
    st.session_state["chats"][new_name] = [
        {"role": "assistant", "content": "New chat started. Hi 👋, how can I help you?"}
    ]
    st.session_state["current_chat"] = new_name
    st.rerun()

chat_choice = st.sidebar.radio("Your Chats", list(st.session_state["chats"].keys()),
                               index=list(st.session_state["chats"].keys()).index(st.session_state["current_chat"]))
st.session_state["current_chat"] = chat_choice

if st.sidebar.button("🗑️ Clear Chat"):
    st.session_state["chats"][st.session_state["current_chat"]] = [
        {"role": "assistant", "content": "Chat cleared. Hi 👋, how can I help you now?"}
    ]
    st.rerun()

st.sidebar.subheader("📥 Inbox")
with st.spinner("Loading inbox..."):
    data = list_emails(limit=5)
if "error" in data:
    st.sidebar.write(f"⚠️ {data['error']}")
else:
    for email in data:
        with st.sidebar.expander(f"{email.get('subject')} ({email.get('from')})"):
            st.write(f"📧 From: {email.get('from')}")
            st.write(f"🕒 {format_time(email.get('time'))}")
            st.write(f"📝 {email.get('body')}")

