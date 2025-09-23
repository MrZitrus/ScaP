"""
WebSocket server for real-time updates in StreamScraper.
"""

from flask import Flask
from flask_socketio import SocketIO, emit
import logging
import json
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create Flask app and SocketIO instance
app = Flask(__name__)
app.config['SECRET_KEY'] = 'streamscraper-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Connected clients
clients = set()


@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    clients.add(request.sid)
    logger.info(f"Client connected: {request.sid}, Total clients: {len(clients)}")
    emit('connection_response', {'status': 'connected', 'timestamp': datetime.now().isoformat()})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    if request.sid in clients:
        clients.remove(request.sid)
    logger.info(f"Client disconnected: {request.sid}, Total clients: {len(clients)}")


def broadcast_status_update(status_data):
    """
    Broadcast status update to all connected clients.
    
    Args:
        status_data (dict): Status data to broadcast
    """
    try:
        status_data['timestamp'] = datetime.now().isoformat()
        socketio.emit('status_update', status_data)
        logger.debug(f"Status update broadcasted to {len(clients)} clients")
    except Exception as e:
        logger.error(f"Error broadcasting status update: {str(e)}")


def broadcast_download_complete(download_data):
    """
    Broadcast download completion to all connected clients.
    
    Args:
        download_data (dict): Download completion data
    """
    try:
        download_data['timestamp'] = datetime.now().isoformat()
        socketio.emit('download_complete', download_data)
        logger.info(f"Download complete broadcasted: {download_data.get('title', 'Unknown')}")
    except Exception as e:
        logger.error(f"Error broadcasting download complete: {str(e)}")


def broadcast_error(error_data):
    """
    Broadcast error to all connected clients.
    
    Args:
        error_data (dict): Error data to broadcast
    """
    try:
        error_data['timestamp'] = datetime.now().isoformat()
        socketio.emit('error', error_data)
        logger.warning(f"Error broadcasted: {error_data.get('message', 'Unknown error')}")
    except Exception as e:
        logger.error(f"Error broadcasting error: {str(e)}")


if __name__ == '__main__':
    port = 8082
    logger.info(f"Starting WebSocket server on port {port}")
    socketio.run(app, host='0.0.0.0', port=port, debug=False, use_reloader=False)
