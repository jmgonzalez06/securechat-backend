import mysql.connector  # type: ignore MySQL client library
import bcrypt           # For secure password hashing
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# MySQL connection settings loaded from environment
MYSQL_CONFIG = {
    'host': os.getenv("MYSQL_HOST"),
    'port': int(os.getenv("MYSQL_PORT")),
    'user': os.getenv("MYSQL_USER"),
    'password': os.getenv("MYSQL_PASSWORD"),
    'database': os.getenv("MYSQL_DB")
}

# ============================
# Authenticate a user
# ============================
def authenticate_user(username, password):
    """
    Verifies a user's credentials by checking the MySQL users table.

    Args:
        username (str): The username provided by the client.
        password (str): The plaintext password provided by the client.

    Returns:
        bool: True if authentication succeeds, False otherwise.
    """
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM users WHERE username = %s", (username,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return False  # user not found

        stored_hash = row[0]
        if isinstance(stored_hash, str):
            stored_hash = stored_hash.encode('utf-8')
            
        # TEMP DEBUGGING
        print(f"[DEBUG] Authenticating user: {username}", flush=True)
        print(f"[DEBUG] Provided password: {password}", flush=True)
        print(f"[DEBUG] Stored hash: {stored_hash}", flush=True)
        print(f"[DEBUG] Password match: {bcrypt.checkpw(password.encode('utf-8'), stored_hash)}", flush=True)    
            
        return bcrypt.checkpw(password.encode('utf-8'), stored_hash)

    except mysql.connector.Error as err:
        print(f"[MySQL ERROR - AUTH] {err}")
        return False

# ============================
# Hash a password securely
# ============================
def hash_password(password):
    """
    Hashes a plaintext password using bcrypt.

    Args:
        password (str): The plaintext password to hash.

    Returns:
        str: A bcrypt-hashed password (UTF-8 decoded).
    """
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')