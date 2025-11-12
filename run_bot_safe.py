#!/usr/bin/env python3
"""
Safe bot launcher with automatic syntax validation and backup/restore.
This prevents broken bot.py files from running and auto-restores from backup.
"""

import os
import sys
import shutil
import subprocess
from datetime import datetime

BOT_FILE = "bot.py"
BACKUP_FILE = "bot.py.backup"
LOG_PREFIX = "🛡️ SAFE LAUNCHER"

def validate_syntax(file_path):
    """Check if the Python file has valid syntax."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", file_path],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0, result.stderr
    except Exception as e:
        return False, str(e)

def create_backup():
    """Create a backup of the current bot.py if it's valid."""
    is_valid, error = validate_syntax(BOT_FILE)
    if is_valid:
        shutil.copy2(BOT_FILE, BACKUP_FILE)
        print(f"{LOG_PREFIX} ✅ Created backup of valid bot.py")
        return True
    return False

def restore_from_backup():
    """Restore bot.py from backup if backup exists and is valid."""
    if not os.path.exists(BACKUP_FILE):
        print(f"{LOG_PREFIX} ❌ No backup found to restore from")
        return False
    
    is_valid, error = validate_syntax(BACKUP_FILE)
    if is_valid:
        shutil.copy2(BACKUP_FILE, BOT_FILE)
        print(f"{LOG_PREFIX} ✅ Restored bot.py from backup")
        return True
    else:
        print(f"{LOG_PREFIX} ❌ Backup file is also corrupted!")
        return False

def main():
    print(f"\n{LOG_PREFIX} Starting validation checks...")
    print(f"{LOG_PREFIX} Checking bot.py syntax...")
    
    is_valid, error = validate_syntax(BOT_FILE)
    
    if is_valid:
        print(f"{LOG_PREFIX} ✅ bot.py syntax is valid")
        create_backup()
        print(f"{LOG_PREFIX} 🚀 Launching bot...\n")
        
        # Execute the bot with proper globals
        bot_globals = {
            "__name__": "__main__",
            "__file__": os.path.abspath("bot.py"),
            "__builtins__": __builtins__
        }
        with open("bot.py") as f:
            exec(f.read(), bot_globals)
        
    else:
        print(f"{LOG_PREFIX} ❌ SYNTAX ERROR DETECTED!")
        print(f"{LOG_PREFIX} Error details:\n{error}")
        print(f"{LOG_PREFIX} Attempting automatic restoration from backup...")
        
        if restore_from_backup():
            print(f"{LOG_PREFIX} ✅ Restoration successful!")
            print(f"{LOG_PREFIX} 🚀 Launching bot with restored file...\n")
            
            # Execute the bot with proper globals
            bot_globals = {
                "__name__": "__main__",
                "__file__": os.path.abspath("bot.py"),
                "__builtins__": __builtins__
            }
            with open("bot.py") as f:
                exec(f.read(), bot_globals)
        else:
            print(f"{LOG_PREFIX} ❌ Could not restore from backup")
            print(f"{LOG_PREFIX} ⚠️  BOT WILL NOT START until syntax errors are fixed")
            print(f"\n{LOG_PREFIX} Syntax error from bot.py:")
            print(error)
            print(f"\n{LOG_PREFIX} Please fix the syntax error in your local bot.py file")
            print(f"{LOG_PREFIX} and re-sync, or edit bot.py directly on Replit.")
            sys.exit(1)

if __name__ == "__main__":
    main()
