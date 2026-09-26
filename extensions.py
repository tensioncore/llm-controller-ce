from flask_socketio import SocketIO

SOCKET_MAX_HTTP_BUFFER_BYTES = 64 * 1024 * 1024
ATTACHMENT_MAX_FILE_BYTES = 10 * 1024 * 1024
ATTACHMENT_MAX_TOTAL_BYTES = 40 * 1024 * 1024

socketio = SocketIO(
    async_mode='gevent',
    max_http_buffer_size=SOCKET_MAX_HTTP_BUFFER_BYTES,
    logger=False,
    engineio_logger=False
)
