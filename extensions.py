from flask_socketio import SocketIO

SOCKET_MAX_HTTP_BUFFER_BYTES = 10 * 1024 * 1024

socketio = SocketIO(
    async_mode='gevent',
    max_http_buffer_size=SOCKET_MAX_HTTP_BUFFER_BYTES,
    logger=False,        # Set to True for full CMD Verbose logging
    engineio_logger=False        # Set to True for full CMD Verbose logging
)
