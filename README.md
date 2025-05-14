# SecureChat Backend

This is the backend server for SecureChat, a real-time secure messaging platform. It is built with Quart, a Flask-compatible asynchronous Python web framework.

---
## Live Demos

- Frontend: https://securechat-frontend-chi.vercel.app
- Backend: https://securechat-backend.onrender.com

---
## Why We Migrated from Flask to Quart

Initially, SecureChat was built using Flask with a separate WebSocket server (via `websockets.serve()`). This led to architectural complexity and limitations:

- Two separate servers: Flask for HTTP and websockets for real-time communication.
- Manual sync-async coordination: Difficult to share state cleanly between HTTP and WebSocket handlers.
- Scaling challenges: WebSockets need async support to scale properly.

### Quart Solved These Issues

- Unified async HTTP and WebSocket support
- `@app.websocket("/ws")` replaces `websockets.serve(...)`
- Shared state, shared context, and easier broadcasting
- No more threading or split server architecture
- Clean deployment with Hypercorn

---

## Repository Structure

```
securechat-backend/
├── server.py           # Quart server with HTTP and WebSocket support
├── main.py             # Entry point for Hypercorn
├── db.py               # Database utilities for user authentication and password hashing
├── .env                # MySQL configuration (excluded via .gitignore)
├── render.yaml         # Render deployment configuration
├── requirements.txt    # Python dependencies
├── uploads/            # Runtime folder for uploaded files (auto-created, ignored in Git)
├── .gitignore          # Ignored files (env, __pycache__, uploads)
└── LICENSE             # MIT License
```

---

## Technology Stack

- Quart: Flask-compatible async web framework
- Hypercorn: ASGI server for running Quart
- MySQL: Relational database (Hosted on Railway)
- bcrypt: Secure password hashing
- python-dotenv: Environment variable management

---

## Core Features

- Real-time WebSocket messaging
- Typing indicators and heartbeat checks
- Chat history stored in MySQL
- Secure login and registration
- File upload support via `/upload`

---

## Optional: Running Locally for Development
This section is intended for developers who want to test or modify the backend on their own machines. For most users, the hosted deployment is sufficient.

```bash
# Setup virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Create a .env file (see .env.example)

# Start the server
python main.py
```

---

## Deployment on Render

This project is ready to deploy via Render:

- See `render.yaml` for configuration
- Uses `main.py` to launch Hypercorn and Quart

---

## License

MIT License. See the `LICENSE` file for details.
