import asyncio
import websockets
import os
import json
import time
import socket
import http.server
import socketserver
import threading
import ssl
import mysql.connector   # type: ignore
import bcrypt

from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask import send_from_directory
from db import authenticate_user, hash_password
from urllib.parse import urlparse, parse_qs # This will let us extract the user query parameter from the WebSocket URL
from werkzeug.utils import secure_filename # This imports a function from the Werkzeug library (used internally by Flask) that sanitizes file names for safe usage on the filesystem and in URLs. 

# =============================
# Server Configuration
# =============================
load_dotenv()
HOST = HOST = "0.0.0.0" # Possible temp fix, maybe full fix
PORT = int(os.getenv("PORT", 8080))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SSL_CERTFILE = os.path.join(BASE_DIR, "cert.pem")
SSL_KEYFILE = os.path.join(BASE_DIR, "key.pem")

# MySQL config
MYSQL_CONFIG = {
    'host': os.getenv("MYSQL_HOST"),
    'port': int(os.getenv("MYSQL_PORT")),
    'user': os.getenv("MYSQL_USER"),
    'password': os.getenv("MYSQL_PASSWORD"),
    'database': os.getenv("MYSQL_DB")
}

connected_clients = set()
usernames = {} # Track usernames in a global dict
rooms = {}  # Track which room each websocket is in
client_msg_freq = {}
chat_history = []  # Store recent chat messages in memory
RATE_LIMIT_INTERVAL = 5
RATE_LIMIT_THRESHOLD = 5
HEARTBEAT_FREQ = 10
PORT_HTTP = 8081

# =============================
# HTTP Static File Server
# =============================
web_dir = os.path.join(BASE_DIR, '../frontend')
os.chdir(web_dir)
httpd = socketserver.TCPServer(("", PORT_HTTP), http.server.SimpleHTTPRequestHandler)
print(f"[INFO] HTTP static server running on port {PORT_HTTP}")
threading.Thread(target=httpd.serve_forever, daemon=True).start()

# =============================
# WebSocket Server
# =============================
async def handle_connection(websocket, path):
    connected_clients.add(websocket)
    print(f"[WebSocket] New client connected. Total: {len(connected_clients)}")
    print(f"[WebSocket] Incoming connection path: {path}")

        # Parse the username from the WebSocket URL query string
    parsed = urlparse(path)
    query = parse_qs(parsed.query)
    username = query.get("user", [None])[0]

    # Validate that the user exists in the MySQL database
    # This protects against someone guessing ?user=admin in the WebSocket URL
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cursor.fetchone() is None:
            print(f"[WebSocket] Unauthorized username: {username}")
            await websocket.close()
            return
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()
    if not username:
        print("[WebSocket] Connection rejected: no username provided.")
        await websocket.close()
        return

    usernames[websocket] = username
    # Broadcast to others that new user is online
    status_broadcast = json.dumps({
        "type": "status",
        "user": username,
        "status": "online"
    })
    for client in connected_clients:
        if client != websocket and client.open:
            await client.send(status_broadcast)
    # Notify user about everyone else already online
    for user_ws, user_name in usernames.items():
        if user_ws != websocket:
            await websocket.send(json.dumps({
                "type": "status",
                "user": user_name,
                "status": "online"
            }))
    rooms[websocket] = "main"  # default room assignment
    print(f"[WebSocket] User '{username}' connected. Total: {len(connected_clients)+1}")
    
    # Load recent message history from MySQL for the default room
    room = rooms[websocket]
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT username, message, timestamp FROM messages WHERE room = %s ORDER BY timestamp ASC",
            (room,)
        )
        rows = cursor.fetchall()
        for row in rows:
            await websocket.send(json.dumps({
                "type": "message",
                "user": row["username"],
                "room": room,
                "message": row["message"]
            }))
    except mysql.connector.Error as err:
        print(f"[MySQL ERROR - LOAD HISTORY] {err}")
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()
    client_id = id(websocket)
    client_msg_freq[client_id] = (time.time(), 0)
    asyncio.create_task(heartbeat(websocket, client_id))

    try:
        async for raw_message in websocket:
            sender = usernames.get(websocket, "unknown")

            try:
                # Attempt to parse incoming message as JSON
                data = json.loads(raw_message)
                msg_type = data.get("type")
                
                if msg_type == "heartbeat":
                    continue  # Ignore heartbeats (no broadcast, no fallback)

                if msg_type == "typing":
                    typing_notice = json.dumps({
                        "type": "typing",
                        "user": sender
                    })
                    for client in connected_clients:
                        if client != websocket and client.open:
                            await client.send(typing_notice)

                elif msg_type == "message":
                    message_content = data.get("message", "")
                    client_id = id(websocket)
                    now = time.time()
                    last_time, count = client_msg_freq.get(client_id, (now, 0))

                    if now - last_time < RATE_LIMIT_INTERVAL:
                        count += 1
                    else:
                        count = 1
                        last_time = now

                    client_msg_freq[client_id] = (last_time, count)

                    if count > RATE_LIMIT_THRESHOLD:
                        warning_msg = json.dumps({
                            "type": "status",
                            "user": sender,
                            "status": "sending messages too fast. Slow down!"
                        })
                        await websocket.send(warning_msg)
                        continue
                    print(f"[Message] {message_content}")
                    
                    # Handle command to clear chat history
                    if message_content.strip() == "/clear-h":
                        try:
                            conn = mysql.connector.connect(**MYSQL_CONFIG)
                            cursor = conn.cursor()
                            cursor.execute("DELETE FROM messages WHERE room = %s", (msg_room,))
                            conn.commit()
                        except mysql.connector.Error as err:
                            print(f"[MySQL ERROR - DELETE] {err}")
                        finally:
                            if 'conn' in locals() and conn.is_connected():
                                cursor.close()
                                conn.close()
                        clear_notice = json.dumps({
                            "type": "status",
                            "user": sender,
                            "status": "cleared the chat history"
                        })
                        for client in connected_clients:
                            if client.open:
                                await client.send(clear_notice)
                        continue  
                    msg_room = data.get("room", "main")
                    rooms[websocket] = msg_room
                    formatted_message = json.dumps({
                        "type": "message",
                        "user": sender,
                        "room": msg_room,
                        "message": message_content
                    })
                    # Save message to chat history
                    chat_history.append({
                        "type": "message",
                        "user": sender,
                        "room": msg_room,
                        "message": message_content
                    })
                    try:
                        conn = mysql.connector.connect(**MYSQL_CONFIG)
                        cursor = conn.cursor()
                        cursor.execute(
                            "INSERT INTO messages (room, username, message) VALUES (%s, %s, %s)",
                            (msg_room, sender, message_content)
                        )
                        conn.commit()
                    except mysql.connector.Error as err:
                        print(f"[MySQL ERROR - INSERT] {err}")
                    finally:
                        if 'conn' in locals() and conn.is_connected():
                            cursor.close()
                            conn.close()
                    for client in connected_clients:
                        if client != websocket and client.open and rooms.get(client) == msg_room:
                            await client.send(formatted_message)
                
                # Adding new message type for switching rooms
                elif msg_type == "switch-room":
                    new_room = data.get("room", "main")
                    rooms[websocket] = new_room

                    try:
                        conn = mysql.connector.connect(**MYSQL_CONFIG)
                        cursor = conn.cursor(dictionary=True)
                        cursor.execute(
                            "SELECT username, message, timestamp FROM messages WHERE room = %s ORDER BY timestamp ASC",
                            (new_room,)
                        )
                        rows = cursor.fetchall()
                        for row in rows:
                            await websocket.send(json.dumps({
                                "type": "message",
                                "user": row["username"],
                                "room": new_room,
                                "message": row["message"]
                            }))
                    except mysql.connector.Error as err:
                        print(f"[MySQL ERROR - SWITCH ROOM LOAD] {err}")
                    finally:
                        if 'conn' in locals() and conn.is_connected():
                            cursor.close()
                            conn.close()

            except json.JSONDecodeError:
                # Handle raw string fallback
                message_content = raw_message.strip()
                print(f"[Message] {message_content} (fallback)")

                formatted_message = json.dumps({
                    "type": "message",
                    "user": sender,
                    "message": message_content
                })
                for client in connected_clients:
                    if client != websocket and client.open:
                        await client.send(formatted_message)

    except websockets.ConnectionClosed:
        disconnecting_user = usernames.pop(websocket, "unknown")
        # Broadcast online presence
        status_message = json.dumps({
            "type": "status",
            "user": username,
            "status": "online"
        })
        for client in connected_clients:
            if client != websocket and client.open:
                await client.send(status_message)
        connected_clients.discard(websocket)
        rooms.pop(websocket, None)

        status_message = json.dumps({
            "type": "status",
            "user": disconnecting_user,
            "status": "offline"
        })
        for client in connected_clients:
            if client.open:
                await client.send(status_message)

        print(f"[WebSocket] User '{disconnecting_user}' disconnected. Total clients: {len(connected_clients)}")

        
async def heartbeat(websocket, client_id):
    while True:
        try:
            await websocket.ping()
            await asyncio.sleep(HEARTBEAT_FREQ)
        except websockets.ConnectionClosed:
            client_msg_freq.pop(client_id, None)
            connected_clients.discard(websocket)
            rooms.pop(websocket, None)
            print(f"[WebSocket] Client disconnected. Total clients: {len(connected_clients)}")
            break


# =============================
# Flask API (Login / Register)
# =============================
app = Flask(__name__)
CORS(app)

@app.route('/login', methods=['POST'])
def login():
    """Login using MySQL-backed authentication via db.py"""
    data = request.json
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'success': False, 'message': 'Missing credentials'}), 400

    if authenticate_user(username, password):
        return jsonify({'success': True, 'message': 'Login successful'}), 200

    return jsonify({'success': False, 'message': 'Invalid credentials'}), 401

@app.route('/register', methods=['POST'])
def register():
    """Register new users into MySQL-based DB"""
    data = request.json
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'success': False, 'message': 'Username and password required.'}), 400

    try:
        # Connect to MySQL
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()

        # Check if user exists
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            return jsonify({'success': False, 'message': 'Username already exists.'}), 409

        # Insert new user
        hashed = hash_password(password)
        cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, hashed))
        conn.commit()

        return jsonify({'success': True, 'message': 'User registered successfully.'}), 201

    except mysql.connector.Error as err:
        print(f"[MySQL ERROR] {err}")
        return jsonify({'success': False, 'message': 'Database error'}), 500

    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files.get('file')
    if not file:
        return jsonify({'success': False, 'message': 'No file received'}), 400

    # Sanitize the filename and save it
    filename = secure_filename(file.filename)
    file_path = os.path.join(UPLOAD_DIR, filename)
    file.save(file_path)

    # Use 127.0.0.1 for routable download
    file_url = f"http://127.0.0.1:5000/uploads/{filename}"
    return jsonify({'success': True, 'url': file_url}), 200

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)

# Start Flask API thread
threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000, debug=False), daemon=True).start()

# =============================
# WebSocket SSL (Still Placeholder for Now)
# =============================
if not os.path.exists(SSL_CERTFILE) or not os.path.exists(SSL_KEYFILE):
    raise FileNotFoundError("cert.pem or key.pem missing.")

ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ssl_context.load_cert_chain(certfile=SSL_CERTFILE, keyfile=SSL_KEYFILE)

# =============================
# Launch WebSocket Server
# =============================
# print(f"[WebSocket] Running on ws://{HOST}:{PORT}") attempt fix 
print("[WebSocket] Running on all interfaces (0.0.0.0)")
start_server = websockets.serve(handle_connection, HOST, PORT)
try:
    asyncio.get_event_loop().run_until_complete(start_server)
    asyncio.get_event_loop().run_forever()
except KeyboardInterrupt:
    print("\n[Server] Shutdown requested. Closing Down...")