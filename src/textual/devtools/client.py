from __future__ import annotations

import asyncio
import base64
import datetime
import json
import pickle
from asyncio import Queue, Task, QueueFull
from io import StringIO
from typing import Type, Any

import aiohttp
from aiohttp import ClientResponseError, ClientConnectorError, ClientWebSocketResponse
from rich.console import Console
from rich.segment import Segment

DEFAULT_PORT = 8081
WEBSOCKET_CONNECT_TIMEOUT = 3
LOG_QUEUE_MAXSIZE = 512


class DevtoolsConsole(Console):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.record = True

    def export_segments(self) -> list[Segment]:
        """Return the list of Segments that have be printed using this console

        Returns:
            list[Segment]: The list of Segments that have been printed using this console
        """
        with self._record_buffer_lock:
            segments = self._record_buffer[:]
            self._record_buffer.clear()
        return segments


class DevtoolsConnectionError(Exception):
    """Raise when the devtools client is unable to connect to the server"""


class ClientShutdown:
    """Sentinel type sent to client queue(s) to indicate shutdown"""


class DevtoolsClient:
    """Client responsible for websocket communication with the devtools server.
    Communicates using a simple JSON protocol.

    Messages have the format `{"type": <str>, "payload": <json>}`.

    Valid values for `"type"` (that can be sent from client -> server) are
    `"client_log"` (for log messages) and `"client_spillover"` (for reporting
    to the server that messages were discarded due to rate limiting).

    A `"client_log"` message has a `"payload"` format as follows:
    ```
    {"timestamp": <int, unix timestamp>,
     "path": <str, path of file>,
     "line_number": <int, line number log was made from>,
     "encoded_segments": <str, pickled then b64 encoded Segments to log>}
    ```

    A `"client_spillover"` message has a `"payload"` format as follows:
    ```
    {"spillover": <int, the number of messages discarded by rate-limiting>}
    ```

    Args:
        host (str): The host the devtools server is running on, defaults to "127.0.0.1"
        port (int): The port the devtools server is accessed via, defaults to 8081
    """

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
        self.url: str = f"ws://{host}:{port}"
        self.session: aiohttp.ClientSession | None = None
        self.log_queue_task: Task | None = None
        self.update_console_task: Task | None = None
        self.console: DevtoolsConsole = DevtoolsConsole(file=StringIO())
        self.websocket: ClientWebSocketResponse | None = None
        self.log_queue: Queue[str | Type[ClientShutdown]] | None = None
        self.spillover: int = 0

    async def connect(self) -> None:
        """Connect to the devtools server.

        Raises:
            DevtoolsConnectionError: If we're unable to establish
                a connection to the server for any reason.
        """
        self.session = aiohttp.ClientSession()
        self.log_queue = Queue(maxsize=LOG_QUEUE_MAXSIZE)
        try:
            self.websocket = await self.session.ws_connect(
                f"{self.url}/textual-devtools-websocket",
                timeout=WEBSOCKET_CONNECT_TIMEOUT,
            )
        except (ClientConnectorError, ClientResponseError):
            raise DevtoolsConnectionError()

        log_queue = self.log_queue
        websocket = self.websocket

        async def update_console():
            """Coroutine function scheduled as a Task, which listens on
            the websocket for updates from the server regarding any changes
            in the server Console dimensions. When the client learns of this
            change, it will update its own Console to ensure it renders at
            the correct width for server-side display.
            """
            async for message in self.websocket:
                if message.type == aiohttp.WSMsgType.TEXT:
                    message_json = json.loads(message.data)
                    if message_json["type"] == "server_info":
                        payload = message_json["payload"]
                        self.console.width = payload["width"]
                        self.console.height = payload["height"]

        async def send_queued_logs():
            """Coroutine function which is scheduled as a Task, which consumes
            messages from the log queue and sends them to the server via websocket.
            """
            while True:
                log = await log_queue.get()
                if log is ClientShutdown:
                    log_queue.task_done()
                    break
                await websocket.send_str(log)
                log_queue.task_done()

        self.log_queue_task = asyncio.create_task(send_queued_logs())
        self.update_console_task = asyncio.create_task(update_console())

    async def _stop_log_queue_processing(self) -> None:
        """Schedule end of processing of the log queue, meaning that any messages a
        user logs will be added to the queue, but not consumed and sent to
        the server.
        """
        if self.log_queue is not None:
            await self.log_queue.put(ClientShutdown)
        if self.log_queue_task:
            await self.log_queue_task

    async def _stop_incoming_message_processing(self) -> None:
        """Schedule stop of the task which listens for incoming messages from the
        server around changes in the server console size.
        """
        if self.websocket:
            await self.websocket.close()
        if self.update_console_task:
            await self.update_console_task
        if self.session:
            await self.session.close()

    async def disconnect(self) -> None:
        """Disconnect from the devtools server by stopping tasks and
        closing connections.
        """
        await self._stop_log_queue_processing()
        await self._stop_incoming_message_processing()

    @property
    def is_connected(self) -> bool:
        """Checks connection to devtools server.

        Returns:
            bool: True if this host is connected to the server. False otherwise.
        """
        if not self.session or not self.websocket:
            return False
        return not (self.session.closed or self.websocket.closed)

    def log(self, *objects: Any, path: str = "", lineno: int = 0) -> None:
        """Queue a log to be sent to the devtools server for display.

        Args:
            *objects (Any): Objects to be logged.
            path (str): The path of the Python file that this log is associated with (and
                where the call to this method was made from).
            lineno (int): The line number this log call was made from.
        """
        self.console.print(*objects)
        segments = self.console.export_segments()

        encoded_segments = self._encode_segments(segments)
        message = json.dumps(
            {
                "type": "client_log",
                "payload": {
                    "timestamp": int(datetime.datetime.utcnow().timestamp()),
                    "path": path,
                    "line_number": lineno,
                    "encoded_segments": encoded_segments,
                },
            }
        )
        try:
            if self.log_queue:
                self.log_queue.put_nowait(message)
                if self.spillover > 0 and self.log_queue.qsize() < LOG_QUEUE_MAXSIZE:
                    # Tell the server how many messages we had to discard due
                    # to the log queue filling to capacity on the client.
                    spillover_message = json.dumps(
                        {
                            "type": "client_spillover",
                            "payload": {
                                "spillover": self.spillover,
                            },
                        }
                    )
                    self.log_queue.put_nowait(spillover_message)
                    self.spillover = 0
        except QueueFull:
            self.spillover += 1

    def _encode_segments(self, segments: list[Segment]) -> str:
        """Pickle and Base64 encode the list of Segments

        Args:
            segments (list[Segment]): A list of Segments to encode

        Returns:
             str: The Segment list pickled with pickle protocol v3, then base64 encoded
        """
        pickled = pickle.dumps(segments, protocol=3)
        encoded = base64.b64encode(pickled)
        return str(encoded, encoding="utf-8")