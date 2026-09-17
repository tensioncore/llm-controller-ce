from flask_socketio import SocketIO

SOCKET_MAX_HTTP_BUFFER_BYTES = 10 * 1024 * 1024

socketio = SocketIO(
    async_mode='gevent',
    max_http_buffer_size=SOCKET_MAX_HTTP_BUFFER_BYTES,
    logger=False,
    engineio_logger=False
)
