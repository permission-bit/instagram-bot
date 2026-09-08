import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from flask import Flask, Blueprint, request, jsonify


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

os.makedirs(DATA_DIR, exist_ok=True)

COMMENTS_FILE = os.path.join(DATA_DIR, "comments.json")
DATABASE_FILE = os.path.join(DATA_DIR, "instagram.db")


VERIFY_TOKEN = os.getenv("INSTAGRAM_VERIFY_TOKEN")

ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
IG_USER_ID = os.getenv("INSTAGRAM_USER_ID")

GRAPH_API_VERSION = os.getenv(
    "INSTAGRAM_GRAPH_API_VERSION",
    "v23.0",
)

TEST_MODE = (
    os.getenv("INSTAGRAM_TEST_MODE", "false").lower()
    == "true"
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    ),
)

logger = logging.getLogger("instagram-bot")


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)

instagram_bp = Blueprint(
    "instagram",
    __name__,
)


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# DATABASE
# ============================================================

def get_db():
    """
    Open SQLite connection.
    """

    connection = sqlite3.connect(
        DATABASE_FILE,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_database():
    """
    Create database tables.

    The Instagram comment ID is unique.

    This is the important duplicate protection:
    the same Instagram webhook can arrive more than once,
    but only one request is allowed to claim the comment.
    """

    connection = get_db()

    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS comments (
                comment_id TEXT PRIMARY KEY,

                user_id TEXT,
                username TEXT,
                text TEXT,

                matched_keyword TEXT,

                received_at TEXT NOT NULL,

                claimed_at TEXT,

                reply_sent INTEGER NOT NULL DEFAULT 0,
                reply_status TEXT NOT NULL DEFAULT 'pending',
                reply_error TEXT,

                dm_sent INTEGER NOT NULL DEFAULT 0,
                dm_status TEXT NOT NULL DEFAULT 'pending',
                dm_error TEXT,

                dm_message TEXT,

                updated_at TEXT NOT NULL
            )
            """
        )

        connection.commit()

    finally:
        connection.close()


# ============================================================
# COMMENT DATABASE HELPERS
# ============================================================

def get_comment(comment_id):
    """
    Return one stored comment.
    """

    connection = get_db()

    try:
        row = connection.execute(
            """
            SELECT *
            FROM comments
            WHERE comment_id = ?
            """,
            (str(comment_id),),
        ).fetchone()

        if row is None:
            return None

        return dict(row)

    finally:
        connection.close()


def create_comment_if_missing(
    comment_id,
    user_id,
    username,
    text,
    matched_keyword,
):
    """
    Insert the comment exactly once.

    Returns:

        True
            This request created/claimed the comment.

        False
            The comment already exists.
    """

    now = utc_now()

    connection = get_db()

    try:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO comments (
                comment_id,
                user_id,
                username,
                text,
                matched_keyword,
                received_at,
                claimed_at,
                reply_sent,
                reply_status,
                reply_error,
                dm_sent,
                dm_status,
                dm_error,
                dm_message,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'pending', NULL,
                    0, 'pending', NULL, NULL, ?)
            """,
            (
                str(comment_id),
                user_id,
                username,
                text,
                matched_keyword,
                now,
                now,
                now,
            ),
        )

        connection.commit()

        created = cursor.rowcount == 1

        if created:
            logger.info(
                "CLAIMED comment=%s",
                comment_id,
            )
        else:
            logger.info(
                "DUPLICATE comment=%s - ignored",
                comment_id,
            )

        return created

    finally:
        connection.close()


def update_comment(
    comment_id,
    **fields,
):
    """
    Update selected fields of a comment.
    """

    if not fields:
        return

    fields["updated_at"] = utc_now()

    assignments = ", ".join(
        f"{key} = ?"
        for key in fields
    )

    values = list(fields.values())
    values.append(str(comment_id))

    connection = get_db()

    try:
        connection.execute(
            f"""
            UPDATE comments
            SET {assignments}
            WHERE comment_id = ?
            """,
            values,
        )

        connection.commit()

    finally:
        connection.close()


# ============================================================
# JSON EXPORT / HUMAN-READABLE LOG
# ============================================================

def export_comments_json():
    """
    Keep comments.json as a readable copy.

    SQLite remains the source of truth.
    """

    connection = get_db()

    try:
        rows = connection.execute(
            """
            SELECT *
            FROM comments
            ORDER BY received_at ASC
            """
        ).fetchall()

        comments = []

        for row in rows:
            item = dict(row)

            item["reply_sent"] = bool(
                item["reply_sent"]
            )

            item["dm_sent"] = bool(
                item["dm_sent"]
            )

            comments.append(item)

    finally:
        connection.close()

    temporary_file = f"{COMMENTS_FILE}.tmp"

    with open(
        temporary_file,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            comments,
            file,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(
        temporary_file,
        COMMENTS_FILE,
    )


# ============================================================
# KEYWORD DETECTION
# ============================================================

def detect_keyword(text):
    """
    Detect keyword.

    Order matters.
    """

    text = (text or "").lower()

    if "bot" in text:
        return "bot"

    if "shell" in text:
        return "shell"
 
    return None


# ============================================================
# MESSAGES
# ============================================================

MESSAGES = {


    "shell": {
        "comment": (
            "I've sent you a DM! "
            "Check your messages for the link to the Dev CLI setup instructions. 📩"
        ),
        "dm": (
            "Hey! 👋 Here’s your Dev CLI setup guide:\n\n"
            "🔗 https://braxton.rocks\n\n"
            "Follow the instructions to add the Dev CLI to your Zsh configuration "
            "and start using the commands right away. ⚡\n\n"
            "If you have any questions, feel free to send me another DM! 💬\n\n"
            "Happy coding! 🚀"
        ),
    },


    "bot": {
        "comment": (
            "I've sent you a DM! "
            "Check your messages for the link to setup instructions. 📩"
        ),
        "dm": (
            "Hey! 👋 Here’s your Instagram Bot code/setup:\n\n"
            "🔗 https://braxton.rocks/bot\n\n"
            "If you have any questions, feel free to send me another DM! 💬\n\n"
            "Have fun! 🚀"
        ),
    },
}


# ============================================================
# HTTP HELPERS
# ============================================================

def instagram_headers():
    return {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def log_api_response(
    action,
    response,
):
    logger.info(
        "========== INSTAGRAM API =========="
    )

    logger.info(
        "Action: %s",
        action,
    )

    logger.info(
        "HTTP: %s",
        response.status_code,
    )

    logger.info(
        "Response: %s",
        response.text,
    )

    logger.info(
        "===================================="
    )


# ============================================================
# PUBLIC COMMENT REPLY
# ============================================================

def reply_to_comment(
    comment_id,
    message,
):
    """
    Public reply to the Instagram comment.
    """

    if TEST_MODE:
        logger.info(
            "[TEST MODE] Public reply would be sent."
        )
        logger.info(
            "comment_id=%s",
            comment_id,
        )
        logger.info(
            "message=%s",
            message,
        )

        update_comment(
            comment_id,
            reply_status="test_mode",
            reply_sent=0,
            reply_error=None,
        )

        return True

    if not ACCESS_TOKEN:
        error = "INSTAGRAM_ACCESS_TOKEN is missing."

        logger.error(error)

        update_comment(
            comment_id,
            reply_status="failed",
            reply_sent=0,
            reply_error=error,
        )

        return False

    url = (
        f"https://graph.instagram.com/"
        f"{GRAPH_API_VERSION}/"
        f"{comment_id}/replies"
    )

    payload = {
        "message": message,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers=instagram_headers(),
            timeout=15,
        )

        log_api_response(
            "PUBLIC COMMENT REPLY",
            response,
        )

        if response.ok:
            update_comment(
                comment_id,
                reply_status="sent",
                reply_sent=1,
                reply_error=None,
            )

            logger.info(
                "PUBLIC COMMENT REPLY SUCCESS comment=%s",
                comment_id,
            )

            return True

        error = response.text

        update_comment(
            comment_id,
            reply_status="failed",
            reply_sent=0,
            reply_error=error,
        )

        logger.error(
            "PUBLIC COMMENT REPLY FAILED comment=%s",
            comment_id,
        )

        return False

    except requests.RequestException as exc:
        logger.exception(
            "PUBLIC COMMENT REPLY REQUEST ERROR"
        )

        update_comment(
            comment_id,
            reply_status="failed",
            reply_sent=0,
            reply_error=str(exc),
        )

        return False


# ============================================================
# PRIVATE REPLY / INSTAGRAM DM
# ============================================================

def send_private_reply(
    comment_id,
    message,
):
    """
    Send a private reply triggered by an Instagram comment.

    IMPORTANT:
    The recipient is the COMMENT ID.

    This is NOT the Instagram user's numeric ID.
    """

    if TEST_MODE:
        logger.info(
            "[TEST MODE] Private reply would be sent."
        )

        logger.info(
            "comment_id=%s",
            comment_id,
        )

        logger.info(
            "message=%s",
            message,
        )

        update_comment(
            comment_id,
            dm_status="test_mode",
            dm_sent=0,
            dm_error=None,
            dm_message=message,
        )

        return True

    if not ACCESS_TOKEN:
        error = "INSTAGRAM_ACCESS_TOKEN is missing."

        logger.error(error)

        update_comment(
            comment_id,
            dm_status="failed",
            dm_sent=0,
            dm_error=error,
            dm_message=message,
        )

        return False

    if not IG_USER_ID:
        error = "INSTAGRAM_USER_ID is missing."

        logger.error(error)

        update_comment(
            comment_id,
            dm_status="failed",
            dm_sent=0,
            dm_error=error,
            dm_message=message,
        )

        return False

    # --------------------------------------------------------
    # PRIVATE REPLY ENDPOINT
    # --------------------------------------------------------

    url = (
        f"https://graph.instagram.com/"
        f"{GRAPH_API_VERSION}/"
        f"{IG_USER_ID}/messages"
    )

    payload = {
        "recipient": {
            "comment_id": str(comment_id),
        },
        "message": {
            "text": message,
        },
    }

    logger.info(
        "========== INSTAGRAM PRIVATE REPLY =========="
    )

    logger.info(
        "Instagram user ID: %s",
        IG_USER_ID,
    )

    logger.info(
        "Comment ID: %s",
        comment_id,
    )

    logger.info(
        "Endpoint: %s",
        url,
    )

    logger.info(
        "Sending private reply..."
    )

    try:
        response = requests.post(
            url,
            json=payload,
            headers=instagram_headers(),
            timeout=15,
        )

        log_api_response(
            "PRIVATE REPLY / DM",
            response,
        )

        if response.ok:
            update_comment(
                comment_id,
                dm_status="sent",
                dm_sent=1,
                dm_error=None,
                dm_message=message,
            )

            logger.info(
                "PRIVATE REPLY SUCCESS comment=%s",
                comment_id,
            )

            return True

        error = response.text

        update_comment(
            comment_id,
            dm_status="failed",
            dm_sent=0,
            dm_error=error,
            dm_message=message,
        )

        logger.error(
            "PRIVATE REPLY FAILED comment=%s",
            comment_id,
        )

        return False

    except requests.RequestException as exc:
        logger.exception(
            "PRIVATE REPLY REQUEST ERROR"
        )

        update_comment(
            comment_id,
            dm_status="failed",
            dm_sent=0,
            dm_error=str(exc),
            dm_message=message,
        )

        return False


# ============================================================
# PROCESS COMMENT
# ============================================================

def handle_comment(comment):
    """
    Main comment processor.

    Important behavior:

    1. Detect comment.
    2. Atomically claim it.
    3. If already claimed, stop.
    4. Save it.
    5. Send public reply.
    6. Send private reply.
    7. Store BOTH results separately.

    Therefore:

        public success + DM failure
            -> public reply is NOT repeated.

        public failure + DM success
            -> DM is NOT repeated.

        duplicate webhook
            -> nothing is sent again.
    """

    comment_id = comment.get("id")
    parent_id = comment.get("parent_id")

    # --------------------------------------------------------
    # IGNORE REPLIES
    #
    # Only process top-level comments.
    # Replies must never trigger the bot.
    # --------------------------------------------------------

    if parent_id:
        logger.info(
            "Ignoring comment reply: "
            "comment=%s parent=%s",
            comment_id,
            parent_id,
        )
        return

    text = comment.get("text", "")

    user = comment.get("from") or {}

    username = user.get("username")
    user_id = user.get("id")

    logger.info("")
    logger.info(
        "=================================================="
    )
    logger.info(
        "INSTAGRAM COMMENT RECEIVED"
    )
    logger.info(
        "comment_id=%s",
        comment_id,
    )
    logger.info(
        "username=%s",
        username,
    )
    logger.info(
        "user_id=%s",
        user_id,
    )
    logger.info(
        "text=%s",
        text,
    )
    logger.info(
        "=================================================="
    )

    if not comment_id:
        logger.error(
            "Comment has no ID. Nothing will be sent."
        )
        return

    keyword = detect_keyword(text)

    logger.info(
        "Detected keyword: %s",
        keyword,
    )

    # --------------------------------------------------------
    # DUPLICATE PROTECTION
    # --------------------------------------------------------

    claimed = create_comment_if_missing(
        comment_id=comment_id,
        user_id=user_id,
        username=username,
        text=text,
        matched_keyword=keyword,
    )

    if not claimed:
        logger.info(
            "Duplicate webhook ignored. comment=%s",
            comment_id,
        )
        return

    export_comments_json()

    # --------------------------------------------------------
    # NO KEYWORD
    # --------------------------------------------------------

    if not keyword:
        logger.info(
            "No supported keyword. Nothing to send."
        )

        update_comment(
            comment_id,
            reply_status="not_required",
            dm_status="not_required",
        )

        export_comments_json()

        return

    messages = MESSAGES[keyword]

    # --------------------------------------------------------
    # PUBLIC COMMENT REPLY
    # --------------------------------------------------------

    logger.info(
        "Processing keyword=%s",
        keyword,
    )

    reply_success = reply_to_comment(
        comment_id,
        messages["comment"],
    )

    # --------------------------------------------------------
    # PRIVATE REPLY
    # --------------------------------------------------------

    dm_success = send_private_reply(
        comment_id,
        messages["dm"],
    )

    # --------------------------------------------------------
    # FINAL RESULT
    # --------------------------------------------------------

    logger.info("")
    logger.info(
        "========== COMMENT RESULT =========="
    )

    logger.info(
        "comment=%s",
        comment_id,
    )

    logger.info(
        "keyword=%s",
        keyword,
    )

    logger.info(
        "public_reply=%s",
        "SENT" if reply_success else "FAILED",
    )

    logger.info(
        "private_reply=%s",
        "SENT" if dm_success else "FAILED",
    )

    logger.info(
        "===================================="
    )

    export_comments_json()


# ============================================================
# WEBHOOK
# ============================================================

def process_webhook(data):
    """
    Process Instagram webhook payload.
    """

    logger.info(
        "========== WEBHOOK RECEIVED =========="
    )

    logger.info(
        "Payload: %s",
        data,
    )

    logger.info(
        "======================================="
    )

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):

            field = change.get("field")
            value = change.get("value") or {}

            logger.info(
                "Webhook field=%s",
                field,
            )

            if field == "comments":
                handle_comment(value)

            else:
                logger.info(
                    "Ignoring webhook field=%s",
                    field,
                )


# ============================================================
# WEBHOOK VERIFICATION
# ============================================================

@instagram_bp.route(
    "/webhook",
    methods=["GET"],
)
def verify_webhook():

    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if (
        mode == "subscribe"
        and token == VERIFY_TOKEN
    ):
        logger.info(
            "Instagram webhook verification SUCCESS."
        )

        return challenge, 200

    logger.warning(
        "Instagram webhook verification FAILED."
    )

    return "Forbidden", 403


# ============================================================
# INSTAGRAM WEBHOOK
# ============================================================

@instagram_bp.route(
    "/webhook",
    methods=["POST"],
)
def instagram_webhook():

    data = request.get_json(
        silent=True
    )

    if not data:
        logger.warning(
            "Webhook received without JSON."
        )

        return "OK", 200

    try:
        process_webhook(data)

    except Exception:
        logger.exception(
            "Unhandled webhook processing error."
        )

    # Always acknowledge webhook.
    return "OK", 200


# ============================================================
# TEST ENDPOINT
# ============================================================

@instagram_bp.route(
    "/test-comment",
    methods=["POST"],
)
def test_comment():

    if not TEST_MODE:
        return jsonify({
            "success": False,
            "error": "Test mode is disabled.",
        }), 403

    data = request.get_json(
        silent=True
    ) or {}

    text = str(
        data.get("text", "")
    ).strip()

    username = str(
        data.get("username", "testuser")
    ).strip()

    user_id = str(
        data.get("user_id", "TEST_USER_123")
    ).strip()

    comment_id = str(
        data.get("comment_id", "TEST_COMMENT_123")
    ).strip()

    if not text:
        return jsonify({
            "success": False,
            "error": "Missing text.",
        }), 400

    comment = {
        "id": comment_id,
        "text": text,
        "from": {
            "id": user_id,
            "username": username,
        },
    }

    try:
        handle_comment(comment)

    except Exception:
        logger.exception(
            "Test comment processing failed."
        )

        return jsonify({
            "success": False,
            "error": "Internal processing error.",
        }), 500

    return jsonify({
        "success": True,
        "comment_id": comment_id,
        "text": text,
    })


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route(
    "/health",
    methods=["GET"],
)
def health():

    return jsonify({
        "status": "ok",
        "service": "instagram-bot",
        "test_mode": TEST_MODE,
        "graph_api_version": GRAPH_API_VERSION,
        "access_token_configured": bool(
            ACCESS_TOKEN
        ),
        "instagram_user_id_configured": bool(
            IG_USER_ID
        ),
    })


# ============================================================
# STARTUP
# ============================================================

init_database()

app.register_blueprint(
    instagram_bp
)


logger.info(
    "=================================================="
)

logger.info(
    "Instagram Bot starting"
)

logger.info(
    "Graph API version: %s",
    GRAPH_API_VERSION,
)

logger.info(
    "Instagram User ID configured: %s",
    bool(IG_USER_ID),
)

logger.info(
    "Access token configured: %s",
    bool(ACCESS_TOKEN),
)

logger.info(
    "Test mode: %s",
    TEST_MODE,
)

logger.info(
    "Database: %s",
    DATABASE_FILE,
)

logger.info(
    "==================================================")




# ============================================================
# DEVELOPMENT SERVER
# ============================================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5555,
        debug=False,
    )

