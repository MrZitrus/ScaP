# StreamScraper Fix Guide

## 🚨 Issues Fixed

### 1. ✅ Port Binding Error (WinError 10048)
**Problem**: "OSError: [WinError 10048] Only one usage of each socket address is permitted"

**Root Cause**: Server starting automatically on import instead of only when run directly.

**Fixed**:
- Added `use_reloader=False` to prevent Flask debug reloader from starting server twice
- Added proper `if __name__ == "__main__":` guards
- Disabled debug mode in websocket_server.py

**To run the application**:
```bash
# Run main Flask app with WebSocket support
python app.py

# OR run separate WebSocket server (if needed)
python websocket_server.py
```

### 2. ✅ Database Corruption Fix
**Problem**: "database disk image is malformed"

**Fixed**:
- Added robust SQLite connection settings:
  - WAL mode for better crash recovery
  - Busy timeout (5000ms) for concurrent access
  - Synchronous=NORMAL for better performance
  - check_same_thread=False for threading support

**To repair corrupted database**:
```bash
# Run the repair script
python repair_database.py

# Or manually (if repair script fails):
# 1. Stop your application
# 2. Backup media.db to media.db.backup
# 3. Delete media.db
# 4. Restart application (tables will be recreated)
```

### 3. ✅ Import-Time Side Effects Fixed
**Problem**: Modules starting servers when imported

**Fixed**:
- All server startup code moved behind `if __name__ == "__main__":`
- No more automatic server startup on import

## 🔧 How to Use

### Quick Fix (Recommended)
1. **Stop all running Python processes**
2. **Run the database repair**:
   ```bash
   python repair_database.py
   ```
3. **Start the application**:
   ```bash
   python app.py
   ```

### Manual Fix (If repair script doesn't work)
1. **Stop all Python processes**
2. **Backup your database**:
   ```bash
   copy media.db media.db.backup
   ```
3. **Delete corrupted database**:
   ```bash
   del media.db
   ```
4. **Start application** (will recreate tables):
   ```bash
   python app.py
   ```

### Port Configuration
If you still get port conflicts, change ports in `config.json`:
```json
{
  "server": {
    "port": 5001,
    "host": "127.0.0.1"
  }
}
```

## 🛠️ Technical Details

### Database Robustness Features Added:
- **WAL Mode**: Write-Ahead Logging for crash recovery
- **Busy Timeout**: 5-second timeout for locked databases
- **Thread Safety**: Support for multi-threaded access
- **Connection Pooling**: Proper connection management

### Server Startup Protection:
- **Import Guards**: Servers only start when run directly
- **Reloader Disabled**: Prevents double server startup
- **Debug Mode**: Controlled via configuration

## 📋 Testing the Fix

1. **Start the application**:
   ```bash
   python app.py
   ```

2. **Check logs** for successful startup:
   ```
   Starting server on 127.0.0.1:5000 (debug=False)
   ```

3. **Test database operations**:
   - Try accessing `/api/media/list`
   - Check if database queries work without errors

4. **Verify WebSocket connection**:
   - Open browser to `http://localhost:5000`
   - Check browser console for connection status

## 🚨 If Issues Persist

### Check for Running Processes:
```cmd
netstat -ano | findstr :5000
taskkill /PID <PID> /F
```

### Clear Python Cache:
```bash
# Delete __pycache__ directories
for /d /r . %d in (__pycache__) do @if exist "%d" rd /s /q "%d"
```

### Check Antivirus:
- Some antivirus software blocks SQLite WAL files
- Add exception for your project directory

## 📞 Support

If you continue to experience issues:
1. Check the logs in the `logs/` directory
2. Verify all Python packages are installed: `pip install -r requirements.txt`
3. Try running with a fresh database (delete `media.db`)

The fixes implemented should resolve the most common Windows-specific issues with SQLite databases and Flask server startup.