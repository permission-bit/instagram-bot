# Instagram Comment Webhook Bot

A Flask application for receiving Instagram comment webhooks, detecting configured keywords, and responding through the Instagram Graph API.

## Features

- Receives Instagram webhook events through Flask.
- Processes the `comments` webhook field.
- Ignores comment replies by checking `parent_id`.
- Detects the keywords `bot` and `shell`.
- Sends a public reply to matching comments.
- Sends a private reply using the comment ID.
- Stores processed comments in SQLite.
- Exports the stored comments to `comments.json`.
- Uses the Instagram comment ID as the SQLite primary key to prevent duplicate processing.
- Provides a `/test-comment` endpoint when test mode is enabled.
- Provides a `/health` endpoint.
- Supports HTTPS termination and reverse proxying with Nginx.

---

## Requirements

- Python 3
- Flask
- Requests
- python-dotenv
- An Instagram/Meta application configured for the required Graph API functionality
- A publicly reachable HTTPS endpoint for Instagram webhooks

---

## Installation

Clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
cd YOUR_REPOSITORY
```

Create a virtual environment:

```bash
python3 -m venv venv
```

Activate it:

```bash
source venv/bin/activate
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

---

## requirements.txt

```txt
requests
python-dotenv
Flask
```

---

## Configuration

Create a `.env` file in the project directory:

```env
INSTAGRAM_ACCESS_TOKEN=YOUR_ACCESS_TOKEN
INSTAGRAM_VERIFY_TOKEN=YOUR_VERIFY_TOKEN
INSTAGRAM_USER_ID=YOUR_INSTAGRAM_USER_ID

INSTAGRAM_GRAPH_API_VERSION=v23.0

INSTAGRAM_TEST_MODE=false
```

### Environment variables

| Variable                      | Description                                             | Default |
| ----------------------------- | ------------------------------------------------------- | ------- |
| `INSTAGRAM_ACCESS_TOKEN`      | Instagram Graph API access token                        | —       |
| `INSTAGRAM_VERIFY_TOKEN`      | Token used for webhook verification                     | —       |
| `INSTAGRAM_USER_ID`           | Instagram user ID used by the private messaging request | —       |
| `INSTAGRAM_GRAPH_API_VERSION` | Graph API version used by the application               | `v23.0` |
| `INSTAGRAM_TEST_MODE`         | Enables test behavior instead of sending API requests   | `false` |

### Security

Do not commit `.env` to Git.

Add it to `.gitignore`:

```gitignore
.env
venv/
__pycache__/
data/
*.db
```

If an access token has already been exposed publicly, revoke or rotate it before using the repository.

---

# Running the application

The application starts Flask on:

```text
127.0.0.1:5555
```

Start it with:

```bash
python3 app.py
```

The application will create the `data` directory automatically.

The SQLite database is stored at:

```text
data/instagram.db
```

The JSON export is stored at:

```text
data/comments.json
```

---

# Endpoints

## GET `/health`

Returns the application status and configuration state.

Example:

```bash
curl http://127.0.0.1:5555/health
```

Example response:

```json
{
  "status": "ok",
  "service": "instagram-bot",
  "test_mode": false,
  "graph_api_version": "v23.0",
  "access_token_configured": true,
  "instagram_user_id_configured": true
}
```

---

## GET `/webhook`

Used for Instagram webhook verification.

The endpoint expects the following query parameters:

```text
hub.mode
hub.verify_token
hub.challenge
```

The request is accepted when:

```text
hub.mode == subscribe
```

and the supplied verification token matches:

```env
INSTAGRAM_VERIFY_TOKEN
```

The endpoint returns the provided challenge with HTTP `200`.

Invalid verification requests return HTTP `403`.

---

## POST `/webhook`

Receives Instagram webhook events.

The application reads the JSON payload and processes entries containing:

```text
field = comments
```

Other webhook fields are ignored.

The endpoint returns:

```text
OK
```

with HTTP `200` after receiving the request.

---

# Keyword handling

The application currently supports two keywords.

## `bot`

The keyword detector searches the comment text for:

```text
bot
```

If found, the corresponding messages from `MESSAGES["bot"]` are used.

## `shell`

The keyword detector searches the comment text for:

```text
shell
```

If found, the corresponding messages from `MESSAGES["shell"]` are used.

The detection is performed using lowercase text, so matching is case-insensitive.

The detection order is:

```text
bot
shell
```

If neither keyword is found, no response is sent.

---

# Duplicate protection

The SQLite database uses:

```sql
comment_id TEXT PRIMARY KEY
```

When a webhook is received, the application attempts to insert the comment using:

```sql
INSERT OR IGNORE
```

If the comment ID already exists, the webhook is treated as a duplicate and processing stops.

This means the same comment ID is only claimed once by the application.

---

# Comment replies

The application checks:

```python
parent_id = comment.get("parent_id")
```

If `parent_id` exists, the event is treated as a comment reply and ignored.

Only comments without a `parent_id` continue through the keyword-processing logic.

---

# Instagram API

The application uses the Instagram Graph API through HTTP requests.

The Graph API base URL is constructed using:

```text
https://graph.instagram.com/{GRAPH_API_VERSION}/
```

The configured API version comes from:

```env
INSTAGRAM_GRAPH_API_VERSION
```

and defaults to:

```text
v23.0
```

The access token is sent as a Bearer token:

```http
Authorization: Bearer YOUR_ACCESS_TOKEN
```

---

## Public comment reply

For a matching comment, the application sends a POST request to:

```text
/{comment_id}/replies
```

with:

```json
{
  "message": "..."
}
```

The result is stored in SQLite as either a successful or failed public reply.

---

## Private reply

For a matching comment, the application sends a POST request to:

```text
/{INSTAGRAM_USER_ID}/messages
```

The recipient is specified using the comment ID:

```json
{
  "recipient": {
    "comment_id": "COMMENT_ID"
  },
  "message": {
    "text": "..."
  }
}
```

The private reply result is stored separately from the public reply result.

---

# Database

SQLite is used as the application's persistent storage.

The database contains a `comments` table with fields including:

```text
comment_id
user_id
username
text
matched_keyword
received_at
claimed_at
reply_sent
reply_status
reply_error
dm_sent
dm_status
dm_error
dm_message
updated_at
```

The application treats SQLite as the source of truth.

---

# JSON export

After processing comments, the application exports the database contents to:

```text
data/comments.json
```

The JSON file is intended to provide a readable representation of the stored comments.

SQLite remains the source of truth.

---

# Test mode

Test mode can be enabled with:

```env
INSTAGRAM_TEST_MODE=true
```

When enabled, the application does not send the public reply or private reply requests to Instagram.

Instead, it logs what would have been sent and stores the corresponding status as:

```text
test_mode
```

---

## Test endpoint

When test mode is enabled, a test comment can be submitted to:

```text
POST /test-comment
```

Example:

```bash
curl -X POST http://127.0.0.1:5555/test-comment \
  -H "Content-Type: application/json" \
  -d '{
    "comment_id": "TEST_COMMENT_123",
    "username": "testuser",
    "user_id": "TEST_USER_123",
    "text": "bot"
  }'
```

The endpoint returns JSON containing:

```json
{
  "success": true,
  "comment_id": "TEST_COMMENT_123",
  "text": "bot"
}
```

If test mode is disabled, the endpoint returns HTTP `403`.

---

# Nginx

The Flask application listens on:

```text
127.0.0.1:5555
```

Nginx can be used as a reverse proxy in front of Flask.

Example configuration:

```nginx
server {
    listen 80;
    listen [::]:80;

    server_name your_domain www.your_domain;

    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;

    server_name your_domain www.your_domain;

    ssl_certificate /etc/letsencrypt/live/your_domain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your_domain/privkey.pem;

    location /.well-known/discord {
        root /var/www/braxton;
        try_files $uri =404;
    }

    location / {
        proxy_pass http://127.0.0.1:5555;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Replace:

```text
your_domain
```

with your actual domain.

The Flask application itself does not provide HTTPS; the example configuration terminates HTTPS at Nginx and proxies requests to Flask on port `5555`.

---

# Webhook URL

With the Nginx configuration above, the webhook endpoint is:

```text
https://your_domain/webhook
```

The webhook verification endpoint uses the same path:

```text
https://your_domain/webhook
```

---

# Logging

The application uses Python's built-in `logging` module.

Log messages include information such as:

- webhook payloads
- received comment IDs
- usernames
- detected keywords
- duplicate comments
- Instagram API status codes
- API responses
- public reply status
- private reply status
- test-mode actions

API responses are logged by `log_api_response()`.

Be careful when sharing logs publicly because API responses or webhook payloads may contain information that should not be exposed.

---

# Error handling

HTTP requests to Instagram use a timeout of:

```text
15 seconds
```

Request exceptions are caught and stored in the database.

Public replies and private replies have separate status and error fields.

For example:

```text
reply_status
reply_error

dm_status
dm_error
```

This allows the two operations to be tracked independently.

---

# Customizing messages

Messages are defined in the `MESSAGES` dictionary:

```python
MESSAGES = {
    "shell": {
        "comment": "...",
        "dm": "..."
    },

    "bot": {
        "comment": "...",
        "dm": "..."
    }
}
```

You can modify these strings to change the responses for each keyword.

---

# Adding another keyword

Add another entry to `MESSAGES`:

```python
MESSAGES = {
    "shell": {
        "comment": "...",
        "dm": "..."
    },

    "bot": {
        "comment": "...",
        "dm": "..."
    },

    "example": {
        "comment": "Public response",
        "dm": "Private response"
    }
}
```

Then extend `detect_keyword()`:

```python
def detect_keyword(text):
    text = (text or "").lower()

    if "bot" in text:
        return "bot"

    if "shell" in text:
        return "shell"

    if "example" in text:
        return "example"

    return None
```

---

# License

Add the license you want to use for this project.

For example:

```text
MIT License
```

---

# Disclaimer

This project is provided as-is.

Instagram and Meta API behavior, permissions, endpoints, and requirements can change independently of this application. Make sure your Meta/Instagram application has the permissions and configuration required for the API functionality you intend to use.
