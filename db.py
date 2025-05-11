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
        # Connect to MySQL database using credentials from .env
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()

        # Query the hashed password for the given username
        cursor.execute("SELECT password FROM users WHERE username = %s", (username,))
        row = cursor.fetchone()
        conn.close()

        # Validate the provided password against the hashed one
        if row and bcrypt.checkpw(password.encode('utf-8'), row[0].encode('utf-8')):
            return True
        return False

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