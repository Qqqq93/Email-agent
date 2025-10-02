import logging
import openai
from django.conf import settings
from django.shortcuts import redirect
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from .serializers import SendEmailSerializer
from .gmail_client import (
    get_credentials,
    send_message,
    list_messages,
    modify_message_labels,
    list_labels,
    create_label,
)

logger = logging.getLogger(__name__)

# Configure OpenAI if key is available
if getattr(settings, "OPENAI_API_KEY", None):
    try:
        openai.api_key = settings.OPENAI_API_KEY
    except Exception:
        # If using new OpenAI client libraries, this may differ. Fall back gracefully.
        pass


# ------------------------
# Auth endpoints
# ------------------------
@api_view(["GET"])
def start_auth(request):
    """
    Start OAuth flow (if your gmail_client.get_credentials triggers interactive auth).
    This endpoint should be used to begin auth and store token.json via your gmail_client.
    """
    try:
        creds = get_credentials()  # your gmail_client should create/save token.json after a flow
        return Response({"status": "ok", "message": "Credentials obtained/stored."})
    except Exception as e:
        logger.exception("start_auth failed")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
def oauth2callback(request):
    """
    If your oauth flow requires a callback URL to accept auth code, keep this endpoint.
    If your get_credentials handles the flow internally, this can simply acknowledge.
    """
    # If your gmail_client implements a callback-based flow, you would process request.GET here.
    return Response({"status": "ok", "message": "OAuth callback received (implement as needed)."})


# ------------------------
# Send email
# ------------------------
@api_view(["POST"])
def send_view(request):
    """
    POST /gmail/send/
    Body JSON: { "to": "recipient@example.com", "subject": "Subject", "body": "Hello" }
    Returns: { "ok": True, "message_id": "..." }  OR { "error": "..." }
    """
    serializer = SendEmailSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data
    try:
        # send_message should return a dict with info or raise on failure.
        sent_result = send_message(data["to"], data["subject"], data["body"])
        # Normalize return to minimal JSON for frontend
        # If send_message returns message id: {"id": "..."}
        result = {"ok": True}
        if isinstance(sent_result, dict):
            # copy some useful fields
            if "id" in sent_result:
                result["message_id"] = sent_result["id"]
            result.update({k: v for k, v in sent_result.items() if k not in result})
        else:
            # If it's not dict, put raw value
            result["result"] = sent_result
        return Response(result)
    except Exception as e:
        logger.exception("send_view failed")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ------------------------
# List emails
# ------------------------
@api_view(["GET"])
def list_view(request):
    """
    GET /gmail/list/?limit=5&q=optional
    Returns a JSON list of simplified messages:
    [
      {
        "id": "...",
        "threadId": "...",
        "from": "sender@example.com",
        "to": "me@example.com",
        "subject": "Subject",
        "snippet": "...",
        "body": "...",   # truncated body (first N chars)
        "time": "2025-09-25T12:34:56Z"  # if available
      },
      ...
    ]
    """
    q = request.GET.get("q", None)
    try:
        limit = int(request.GET.get("limit", 10))
    except Exception:
        limit = 10

    try:
        msgs = list_messages(query=q, max_results=limit)
        simplified = []
        for m in msgs:
            simplified.append(
                {
                    "id": m.get("id"),
                    "threadId": m.get("threadId"),
                    "from": m.get("from") or m.get("sender") or m.get("emailFrom"),
                    "to": m.get("to"),
                    "subject": m.get("subject") or m.get("title") or m.get("header_subject"),
                    "snippet": m.get("snippet"),
                    # prefer an explicit date/time field, fallback to 'date'
                    "time": m.get("time") or m.get("date") or m.get("internalDate"),
                    # the 'body' may be long; keep a truncated version for list
                    "body": (m.get("body") or "")[:2000],
                }
            )
        return Response(simplified)
    except Exception as e:
        logger.exception("list_view failed")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ------------------------
# Summary (AI)
# ------------------------
@api_view(["GET"])
def summary_view(request):
    """
    GET /gmail/summary/?limit=5
    Returns {"summary": "..."} or {"snippets": [...], "summary": "..."} when OpenAI key is present.
    """
    try:
        limit = int(request.GET.get("limit", 5))
    except Exception:
        limit = 5

    try:
        msgs = list_messages(max_results=limit)
        snippets = [ (m.get("body") or m.get("snippet") or "") for m in msgs ]

        if not getattr(settings, "OPENAI_API_KEY", None):
            # No OpenAI key — return snippets and a helpful warning
            return Response({"snippets": snippets, "summary": None, "warning": "OPENAI_API_KEY not set in settings."})

        # create a prompt with the recent snippets
        prompt = "You are an assistant who summarizes a user's recent emails. " \
                 "Summarize the main topics briefly and list any clear action items.\n\n"
        for i, s in enumerate(snippets, 1):
            prompt += f"Email {i}:\n{s}\n\n"

        model = getattr(settings, "OPENAI_MODEL", "gpt-3.5-turbo")
        # Use the ChatCompletion endpoint for gpt-3.5-turbo style models
        resp = openai.ChatCompletion.create(
            model=model,
            messages=[
                {"role": "system", "content": "Summarize emails concisely and list action items."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0.25,
        )

        # Support the usual OpenAI response structure
        summary_text = None
        try:
            summary_text = resp["choices"][0]["message"]["content"].strip()
        except Exception:
            # fallback if API returns something else
            summary_text = str(resp)

        return Response({"snippets": snippets, "summary": summary_text})

    except Exception as e:
        logger.exception("summary_view failed")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ------------------------
# Spam and Labels (existing helpers)
# ------------------------
@api_view(["POST"])
def manage_spam(request):
    """
    POST /gmail/spam/
    body: { "message_id": "...", "action": "mark_spam" / "unspam" / "unmark_spam" }
    """
    body = request.data
    message_id = body.get("message_id")
    action = body.get("action")
    if not message_id or not action:
        return Response({"error": "message_id and action required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        if action == "mark_spam":
            res = modify_message_labels(message_id, add_labels=["SPAM"])
        elif action in ("unspam", "unmark_spam"):
            res = modify_message_labels(message_id, remove_labels=["SPAM"])
        else:
            return Response({"error": "unknown action"}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"ok": True, "result": res})
    except Exception as e:
        logger.exception("manage_spam failed")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
def organize_labels(request):
    """
    POST /gmail/labels/
    body: { "message_id": "...", "label": "LabelName" }
    """
    body = request.data
    message_id = body.get("message_id")
    label_name = body.get("label")
    if not message_id or not label_name:
        return Response({"error": "message_id and label required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        labels = list_labels()
        label_map = {lab["name"]: lab["id"] for lab in labels}
        if label_name not in label_map:
            created = create_label(label_name)
            label_id = created.get("id")
        else:
            label_id = label_map[label_name]
        res = modify_message_labels(message_id, add_labels=[label_id])
        return Response({"ok": True, "result": res})
    except Exception as e:
        logger.exception("organize_labels failed")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
