import os
import sqlite3

# Test database access
from portal.config import DB_PATH

print(f"DB_PATH: {DB_PATH}")
print(f"DB_PATH exists: {os.path.exists(DB_PATH)}")

# Try to connect to the database
try:
    conn = sqlite3.connect(DB_PATH)
    print("Successfully connected to database")
    
    # Try to execute a simple query
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = c.fetchall()
    print(f"Tables in database: {tables}")
    
    conn.close()
    print("Successfully closed database connection")
except Exception as e:
    print(f"Error with database: {e}")