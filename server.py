import os
import time
import asyncio
import bcrypt
import json
import mysql.connector # type: ignore
from dotenv import load_dotenv
from quart import Quart, request, jsonify, websocket, send_from_directory # type: ignore
from quart_cors import cors # type: ignore
from werkzeug.utils import secure_filename

# =============================
# Environment & Config
# =============================
load_dotenv()

app = cors(Quart(__name__), allow_origin=[
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "https://securechat-frontend-chi.vercel.app"
], allow_credentials=True)

app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
app.config['PORT'] = int(os.getenv("PORT", 8080))

MYSQL_CONFIG = {
    'host': os.getenv("MYSQL_HOST"),
    'port': int(os.getenv("MYSQL_PORT")),
    'user': os.getenv("MYSQL_USER"),
    'password': os.getenv("MYSQL_PASSWORD"),
    'database': os.getenv("MYSQL_DB")
}

# =============================
# State
# =============================
connected = set()
usernames = {}
rooms = {}
client_msg_freq = {}
RATE_LIMIT_INTERVAL = 5
RATE_LIMIT_THRESHOLD = 5
HEARTBEAT_FREQ = 10

# =============================
# Utility
# =============================
def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def authenticate_user(username, password):
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM users WHERE username = %s", (username,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return False

        stored_hash = row[0]
        if isinstance(stored_hash, str):
            stored_hash = stored_hash.encode('utf-8')

        # ONLY FOR TESTING -- REMOVE WHEN DONE TESTING
        print(f"[DEBUG] Authenticating user: {username}", flush=True)
        print(f"[DEBUG] Provided password: {password}", flush=True)
        print(f"[DEBUG] Stored hash: {stored_hash}", flush=True)
        print(f"[DEBUG] Password match: {bcrypt.checkpw(password.encode('utf-8'), stored_hash)}", flush=True)

        return bcrypt.checkpw(password.encode('utf-8'), stored_hash)

    except mysql.connector.Error as err:
        print(f"[MySQL ERROR - AUTH] {err}")
        return False

# =============================
# WebSocket Endpoint
# =============================
@app.websocket("/ws")
async def ws_handler():
    ws = websocket._get_current_object()
    query = websocket.args
    username = query.get("user")

    if not username or not authenticate_user(username, ""):
        await ws.close()
        return

    usernames[ws] = username
    connected.add(ws)
    rooms[ws] = "main"

    print(f"[WebSocket] {username} connected.")  # ONLY FOR TESTING -- REMOVE WHEN DONE TESTING

    await notify_online_status(username, "online", exclude=ws)
    await send_existing_users(ws)
    await send_message_history(ws, "main")

    client_id = id(ws)
    client_msg_freq[client_id] = (time.time(), 0)
    asyncio.create_task(heartbeat(ws, client_id))

    try:
        while True:
            raw = await ws.receive()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "heartbeat":
                continue

            if msg_type == "typing":
                await broadcast({
                    "type": "typing",
                    "user": username
                }, exclude=ws)

            elif msg_type == "message":
                message = data.get("message", "")
                room = data.get("room", "main")
                rooms[ws] = room

                if is_rate_limited(ws):
                    await ws.send(json.dumps({
                        "type": "status",
                        "user": username,
                        "status": "sending messages too fast. Slow down!"
                    }))
                    continue

                await save_message(username, room, message)
                await broadcast({
                    "type": "message",
                    "user": username,
                    "room": room,
                    "message": message
                }, room=room)

            elif msg_type == "switch-room":
                new_room = data.get("room", "main")
                rooms[ws] = new_room
                await send_message_history(ws, new_room)

    except Exception as e:
        print(f"[WebSocket] {username} disconnected.")  # ONLY FOR TESTING -- REMOVE WHEN DONE TESTING
        connected.discard(ws)
        del usernames[ws]
        rooms.pop(ws, None)
        await notify_online_status(username, "offline")

# =============================
# Helpers
# =============================
async def notify_online_status(user, status, exclude=None):
    payload = json.dumps({"type": "status", "user": user, "status": status})
    for client in connected:
        if client != exclude:
            await client.send(payload)

async def broadcast(message_dict, exclude=None, room=None):
    message = json.dumps(message_dict)
    for client in connected:
        if client != exclude and (room is None or rooms.get(client) == room):
            await client.send(message)

def is_rate_limited(ws):
    client_id = id(ws)
    now = time.time()
    last_time, count = client_msg_freq.get(client_id, (now, 0))
    if now - last_time < RATE_LIMIT_INTERVAL:
        count += 1
    else:
        count = 1
        last_time = now
    client_msg_freq[client_id] = (last_time, count)
    return count > RATE_LIMIT_THRESHOLD

async def send_existing_users(ws):
    for other_ws, user in usernames.items():
        if other_ws != ws:
            await ws.send(json.dumps({
                "type": "status",
                "user": user,
                "status": "online"
            }))

async def send_message_history(ws, room):
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT username, message FROM messages WHERE room = %s ORDER BY timestamp ASC", (room,))
        rows = cursor.fetchall()
        for row in rows:
            await ws.send(json.dumps({
                "type": "message",
                "user": row["username"],
                "room": room,
                "message": row["message"]
            }))
    except mysql.connector.Error as err:
        print(f"[MySQL ERROR - HISTORY] {err}")
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

async def save_message(username, room, message):
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO messages (room, username, message) VALUES (%s, %s, %s)",
                       (room, username, message))
        conn.commit()
    except mysql.connector.Error as err:
        print(f"[MySQL ERROR - INSERT] {err}")
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

async def heartbeat(ws, client_id):
    while True:
        try:
            await ws.send(json.dumps({"type": "heartbeat"}))
            await asyncio.sleep(HEARTBEAT_FREQ)
        except:
            client_msg_freq.pop(client_id, None)
            connected.discard(ws)
            break

# =============================
# HTTP API Routes
# =============================

@app.route('/login', methods=['POST'])
async def login():
    data = await request.get_json()
    username = data.get("username")
    password = data.get("password")

    print(f"[DEBUG] /login route hit with username: {username}", flush=True)  # ONLY FOR TESTING -- REMOVE WHEN DONE TESTING

    if not username or not password:
        return jsonify({'success': False, 'message': 'Missing credentials'}), 400

    if authenticate_user(username, password):
        return jsonify({'success': True, 'message': 'Login successful'}), 200

    return jsonify({'success': False, 'message': 'Invalid credentials'}), 401

@app.route('/register', methods=['POST'])
async def register():
    data = await request.get_json()
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({'success': False, 'message': 'Missing fields'}), 400

    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': 'Username exists'}), 409

        hashed = hash_password(password)
        cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, hashed))
        conn.commit()
        return jsonify({'success': True, 'message': 'Registered'}), 201

    except mysql.connector.Error as err:
        print(f"[MySQL ERROR - REGISTER] {err}")
        return jsonify({'success': False, 'message': 'DB error'}), 500
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/upload', methods=['POST'])
async def upload_file():
    file = (await request.files).get('file')
    if not file:
        return jsonify({'success': False, 'message': 'No file'}), 400

    filename = secure_filename(file.filename)
    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    await file.save(path)
    url = f"/uploads/{filename}"
    return jsonify({'success': True, 'url': url}), 200

@app.route('/uploads/<filename>')
async def uploaded_file(filename):
    return await send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ONLY FOR TESTING -- REMOVE WHEN DONE TESTING
@app.route('/hash/<password>', methods=['GET'])
async def generate_hash(password):
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    return jsonify({'hash': hashed})