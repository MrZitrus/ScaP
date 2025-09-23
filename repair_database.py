#!/usr/bin/env python3
"""
Database repair script for StreamScraper.
This script helps fix corrupted SQLite databases.
"""

import sqlite3
import os
import sys
from pathlib import Path

def repair_database(db_path="media.db"):
    """
    Attempt to repair a corrupted SQLite database.

    Args:
        db_path (str): Path to the database file

    Returns:
        bool: True if repair was successful, False otherwise
    """
    if not os.path.exists(db_path):
        print(f"❌ Database file {db_path} does not exist.")
        return False

    print(f"🔧 Attempting to repair database: {db_path}")

    # Create backup
    backup_path = f"{db_path}.backup"
    try:
        import shutil
        shutil.copy2(db_path, backup_path)
        print(f"✅ Backup created: {backup_path}")
    except Exception as e:
        print(f"⚠️ Could not create backup: {e}")

    # Try to check database integrity
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        print("🔍 Checking database integrity...")
        cursor.execute("PRAGMA integrity_check;")
        result = cursor.fetchone()

        if result[0] == "ok":
            print("✅ Database integrity check passed - no corruption detected.")
            conn.close()
            return True
        else:
            print(f"❌ Database corruption detected: {result[0]}")

        conn.close()

    except Exception as e:
        print(f"❌ Could not open database: {e}")
        return False

    # Try to dump the database
    try:
        print("📤 Attempting to dump database contents...")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get all table names
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()

        if not tables:
            print("❌ No tables found in database.")
            conn.close()
            return False

        print(f"📋 Found tables: {[table[0] for table in tables]}")

        # Create new database
        new_db_path = f"{db_path}.repaired"
        new_conn = sqlite3.connect(new_db_path)
        new_cursor = new_conn.cursor()

        # Copy schema and data
        for table_name, in tables:
            if table_name == 'sqlite_sequence':
                continue  # Skip internal table

            print(f"📋 Processing table: {table_name}")

            # Get schema
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = cursor.fetchall()
            column_names = [col[1] for col in columns]

            try:
                # Try to copy data
                cursor.execute(f"SELECT * FROM {table_name}")
                rows = cursor.fetchall()

                if rows:
                    placeholders = ','.join(['?' for _ in column_names])
                    new_cursor.executemany(
                        f"INSERT OR REPLACE INTO {table_name} ({','.join(column_names)}) VALUES ({placeholders})",
                        rows
                    )
                    print(f"✅ Copied {len(rows)} rows from {table_name}")
                else:
                    print(f"⚠️ No data in {table_name}")

            except Exception as e:
                print(f"⚠️ Could not copy data from {table_name}: {e}")
                continue

        new_conn.commit()
        new_conn.close()
        conn.close()

        # Replace original database
        os.replace(new_db_path, db_path)
        print(f"✅ Database repaired successfully: {db_path}")
        return True

    except Exception as e:
        print(f"❌ Failed to repair database: {e}")
        return False

def main():
    """Main function"""
    print("🗃️ StreamScraper Database Repair Tool")
    print("=" * 40)

    db_path = "media.db"

    if len(sys.argv) > 1:
        db_path = sys.argv[1]

    print(f"📁 Target database: {db_path}")

    if not repair_database(db_path):
        print("\n💡 Manual repair suggestions:")
        print("1. Delete the corrupted database file")
        print("2. Restart your application to recreate tables")
        print("3. Or restore from backup if available")
        return 1

    print("\n✅ Database repair completed!")
    return 0

if __name__ == "__main__":
    sys.exit(main())