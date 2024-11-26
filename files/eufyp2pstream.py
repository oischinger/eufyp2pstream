import os
import signal
import sys
import threading
from aiohttp import ClientSession
import asyncio
import select
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from queue import Queue
from threading import Thread
import argparse
from websocket import EufySecurityWebSocket
import json

# Variables
camera_handlers = {}
run_event = threading.Event()
debug = False

# Constants
RECV_CHUNK_SIZE = 4096

EVENT_CONFIGURATION: dict = {
    "livestream video data": {
        "name": "video_data",
        "value": "buffer",
        "type": "event",
    },
    "livestream audio data": {
        "name": "audio_data",
        "value": "buffer",
        "type": "event",
    },
}

START_P2P_LIVESTREAM_MESSAGE = {
    "messageId": "start_livestream",
    "command": "device.start_livestream",
    "serialNumber": None,
}

STOP_P2P_LIVESTREAM_MESSAGE = {
    "messageId": "stop_livestream",
    "command": "device.stop_livestream",
    "serialNumber": None,
}

START_TALKBACK = {
    "messageId": "start_talkback",
    "command": "device.start_talkback",
    "serialNumber": None,
}

SEND_TALKBACK_AUDIO_DATA = {
    "messageId": "talkback_audio_data",
    "command": "device.talkback_audio_data",
    "serialNumber": None,
    "buffer": None,
}

STOP_TALKBACK = {
    "messageId": "stop_talkback",
    "command": "device.stop_talkback",
    "serialNumber": None,
}

SET_API_SCHEMA = {
    "messageId": "set_api_schema",
    "command": "set_api_schema",
    "schemaVersion": 13,
}


P2P_LIVESTREAMING_STATUS = "p2pLiveStreamingStatus"

START_LISTENING_MESSAGE = {"messageId": "start_listening", "command": "start_listening"}

TALKBACK_RESULT_MESSAGE = {
    "messageId": "talkback_audio_data",
    "errorCode": "device_talkback_not_running",
}

DRIVER_CONNECT_MESSAGE = {"messageId": "driver_connect", "command": "driver.connect"}


def exit_handler(signum, frame):
    """Signal handler to stop the script."""
    logMessage(f"Signal handler called with signal {signum}")
    run_event.set()


# Install signal handler
signal.signal(signal.SIGINT, exit_handler)


def logMessage(message, force=False):
    """Log a message to the console."""
    if debug or force:
        print(message)
        sys.stdout.flush()


class ClientAcceptThread(threading.Thread):
    """Thread to accept incoming connections from clients."""

    def __init__(self, socket, run_event, name, ws, serialno):
        """Initialize the thread."""
        threading.Thread.__init__(self)
        self.socket = socket
        self.queues = []
        self.run_event = run_event
        self.name = name
        self.ws = ws
        self.serialno = serialno
        self.my_threads = []

    def update_threads(self):
        """Update the list of active threads."""
        my_threads_before = len(self.my_threads)
        for thread in self.my_threads:
            if not thread.is_alive():
                self.queues.remove(thread.queue)
        self.my_threads = [t for t in self.my_threads if t.is_alive()]
        if self.ws and my_threads_before > 0 and len(self.my_threads) == 0:
            if self.name == "BackChannel":
                logMessage(f"All clients died (BackChannel): {self.name}")
            else:
                logMessage(f"All clients died. Stopping Stream: {self.name}")
                msg = STOP_P2P_LIVESTREAM_MESSAGE.copy()
                msg["serialNumber"] = self.serialno
                asyncio.run(self.ws.send_message(json.dumps(msg)))

    def run(self):
        """Run the thread to accept incoming connections."""
        logMessage(f"Accepting {self.name} connection for {self.serialno}")
        msg = STOP_TALKBACK.copy()
        msg["serialNumber"] = self.serialno
        asyncio.run(self.ws.send_message(json.dumps(msg)))
        logMessage(f"stop talkback sent for {self.serialno}")
        while not self.run_event.is_set():
            self.update_threads()
            sys.stdout.flush()
            try:
                client_sock, client_addr = self.socket.accept()
                if self.name == "BackChannel":
                    client_sock.setblocking(True)
                    thread = ClientRecvThread(
                        client_sock, run_event, self.name, self.ws, self.serialno
                    )
                    thread.start()
                else:
                    client_sock.setblocking(False)
                    thread = ClientSendThread(
                        client_sock, run_event, self.name, self.ws, self.serialno
                    )
                    self.queues.append(thread.queue)
                    if self.ws:
                        msg = START_P2P_LIVESTREAM_MESSAGE.copy()
                        msg["serialNumber"] = self.serialno
                        asyncio.run(self.ws.send_message(json.dumps(msg)))
                    self.my_threads.append(thread)
                    thread.start()
            except socket.timeout:
                pass
        logMessage(f"ClientAcceptThread {self.name} ended for {self.serialno}")


class ClientSendThread(threading.Thread):
    """Thread to send data to clients."""

    def __init__(self, client_sock, run_event, name, ws, serialno):
        """Initialize the thread."""
        threading.Thread.__init__(self)
        self.client_sock = client_sock
        self.queue = Queue(100)
        self.run_event = run_event
        self.name = name
        self.ws = ws
        self.serialno = serialno

    def run(self):
        """Run the thread to send data to clients."""
        logMessage(f"Thread {self.name} running for {self.serialno}")
        try:
            while not self.run_event.is_set():
                ready_to_read, ready_to_write, in_error = select.select(
                    [], [self.client_sock], [self.client_sock], 2
                )
                if len(in_error):
                    logMessage(f"Exception in socket {self.name}")

                    break
                if not len(ready_to_write):
                    logMessage(f"Socket not ready to write {self.name}")

                    break
                if not self.queue.empty():
                    self.client_sock.sendall(bytearray(self.queue.get(True)["data"]))
        except socket.error as e:
            logMessage(f"Connection lost {self.name}: {e}")
            pass
        except socket.timeout:
            logMessage(f"Timeout on socket for {self.name}")
            pass
        try:
            self.client_sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            logMessage(f"Error shutdown socket: {self.name}")
        self.client_sock.close()
        logMessage(f"Thread {self.name} stopping for {self.serialno}")


class ClientRecvThread(threading.Thread):
    """Thread to receive data from clients."""

    def __init__(self, client_sock, run_event, name, ws, serialno):
        """Initialize the thread."""
        threading.Thread.__init__(self)
        self.client_sock = client_sock
        self.run_event = run_event
        self.name = name
        self.ws = ws
        self.serialno = serialno

    def run(self):
        """Run the thread to receive data from clients."""
        msg = START_TALKBACK.copy()
        msg["serialNumber"] = self.serialno
        asyncio.run(self.ws.send_message(json.dumps(msg)))
        try:
            curr_packet = bytearray()
            no_data = 0
            while not self.run_event.is_set():
                try:
                    ready_to_read, ready_to_write, in_error = select.select(
                        [
                            self.client_sock,
                        ],
                        [],
                        [self.client_sock],
                        2,
                    )
                    if len(in_error):
                        logMessage(f"Exception in socket {self.name}")

                        break
                    if len(ready_to_read):
                        data = self.client_sock.recv(RECV_CHUNK_SIZE)
                        curr_packet += bytearray(data)
                        if len(data) > 0:  # and len(data) <= RECV_CHUNK_SIZE:
                            msg = SEND_TALKBACK_AUDIO_DATA.copy()
                            msg["serialNumber"] = self.serialno
                            msg["buffer"] = list(bytes(curr_packet))
                            asyncio.run(self.ws.send_message(json.dumps(msg)))
                            curr_packet = bytearray()
                            no_data = 0
                        else:
                            no_data += 1
                    else:
                        no_data += 1
                    if no_data >= 15:
                        logMessage(f"15x in a row no data in socket {self.name}")
                        break
                except BlockingIOError:
                    # Resource temporarily unavailable (errno EWOULDBLOCK)
                    pass
        except socket.error as e:
            logMessage(f"Connection lost {self.name}: {e}")
            pass
        except socket.timeout:
            logMessage(f"Timeout on socket for {self.name}")
            pass
        except select.error:
            logMessage(f"Select error on socket {self.name}")
            pass

        try:
            self.client_sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            logMessage(f"Error shutdown socket: {self.name}")

        self.client_sock.close()
        msg = STOP_TALKBACK.copy()
        msg["serialNumber"] = self.serialno
        asyncio.run(self.ws.send_message(json.dumps(msg)))
        logMessage(f"Thread {self.name} stopping for {self.serialno}")


class CameraStreamHandler:
    """Handler for camera streams."""

    def __init__(self, serial_number, start_port, run_event):
        """Initialize the handler."""
        logMessage(
            f" - CameraStreamHandler - __init__ - serial_number: {serial_number} - video_port: {start_port} - audio_port: {start_port + 1} - backchannel_port: {start_port + 2}"
        )
        self.serial_number = serial_number
        self.start_port = start_port
        self.run_event = run_event
        self.ws = None
        self.video_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.audio_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.backchannel_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.video_sock.bind(("0.0.0.0", self.start_port))
        self.video_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.video_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.video_sock.settimeout(1)  # timeout for listening
        self.audio_sock.bind(("0.0.0.0", self.start_port + 1))
        self.audio_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.audio_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.audio_sock.settimeout(1)  # timeout for listening
        self.backchannel_sock.bind(("0.0.0.0", self.start_port + 2))
        self.backchannel_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.backchannel_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.backchannel_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.backchannel_sock.settimeout(1)  # timeout for listening
        self.video_sock.listen()
        self.audio_sock.listen()
        self.backchannel_sock.listen()

    def start_stream(self):
        """Start the stream."""
        logMessage(f"Starting stream for camera {self.serial_number}.")
        self.video_thread = ClientAcceptThread(
            self.video_sock, self.run_event, "Video", self.ws, self.serial_number
        )
        self.audio_thread = ClientAcceptThread(
            self.audio_sock, self.run_event, "Audio", self.ws, self.serial_number
        )
        self.backchannel_thread = ClientAcceptThread(
            self.backchannel_sock,
            self.run_event,
            "BackChannel",
            self.ws,
            self.serial_number,
        )
        self.audio_thread.start()
        self.video_thread.start()
        self.backchannel_thread.start()

    def setWs(self, ws: EufySecurityWebSocket):
        """Set the websocket for the camera handler."""
        self.ws = ws

    def stop(self):
        """Stop the stream."""
        try:
            self.video_sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            logMessage("Error shutdown socket", True)
        self.video_sock.close()
        try:
            self.audio_sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            logMessage("Error shutdown socket", True)
        self.audio_sock.close()
        try:
            self.backchannel_sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            logMessage("Error shutdown socket", True)
        self.backchannel_sock.close()

    def start_talkback(self):
        """Start the talkback."""
        logMessage(f"Starting talkback for camera {self.serial_number}.")
        msg = START_TALKBACK.copy()
        msg["serialNumber"] = self.serial_number
        asyncio.run(self.ws.send_message(json.dumps(msg)))


async def on_open():
    """Callback when the websocket is opened."""
    logMessage(f" on_open - executed")


async def on_close():
    """Callback when the websocket is closed."""
    logMessage(f" on_close - executed")
    run_event.set()
    # Close all camera handlers.
    for handler in camera_handlers.values():
        handler.stop()
    os._exit(-1)


async def on_error(message):
    """Callback when an error occurs."""
    logMessage(f" on_error - executed - {message}")


async def on_message(message):
    """Callback when a message is received."""
    payload = message.json()
    message_type: str = payload["type"]
    sys.stdout.flush()
    if message_type == "result":
        message_id = payload["messageId"]
        if message_id != SEND_TALKBACK_AUDIO_DATA["messageId"]:
            # Avoid spamming of TALKBACK_AUDIO_DATA logs
            logMessage(f"on_message result: {payload}")

        if message_id == START_LISTENING_MESSAGE["messageId"]:
            message_result = payload[message_type]
            states = message_result["state"]
            for state in states["devices"]:
                serialno = state["serialNumber"]
                if serialno in camera_handlers:
                    camera_handlers[serialno].start_stream()
                    logMessage(f"Started stream for camera {serialno}.", True)
                else:
                    logMessage(
                        f"Found unknown Eufy camera with serial number {serialno}.",
                        True,
                    )
        elif (
            message_id == TALKBACK_RESULT_MESSAGE["messageId"]
            and "errorCode" in payload
        ):
            # TODO: Handle error codes with muliple cameras. This one is tricky since
            # the error code is not specific to a camera. Alternatives: 1) Send the
            # START_TALKBACK message again for all cameras. 2) Somehow determine which
            # camera caused the error and send the START_TALKBACK message for that camera.
            # Don't know if 2) is possible.
            # 1) is not ideal. It seems to break streaming completely.
            # error_code = payload["errorCode"]
            # if error_code == "device_talkback_not_running":
            #     for handler in camera_handlers.values():
            #         handler.start_talkback()
            pass
    elif message_type == "event":
        message = payload[message_type]
        event_type = message["event"]
        sys.stdout.flush()
        if message["event"] == "livestream audio data":
            event_value = message[EVENT_CONFIGURATION[event_type]["value"]]
            event_data_type = EVENT_CONFIGURATION[event_type]["type"]
            if event_data_type == "event":
                serialno = message["serialNumber"]
                if serialno in camera_handlers:
                    for queue in camera_handlers[serialno].audio_thread.queues:
                        if queue.full():
                            logMessage(f"Audio queue full.")
                            queue.get(False)
                        queue.put(event_value)
        elif message["event"] == "livestream video data":
            event_value = message[EVENT_CONFIGURATION[event_type]["value"]]
            event_data_type = EVENT_CONFIGURATION[event_type]["type"]
            if event_data_type == "event":
                serialno = message["serialNumber"]
                if serialno in camera_handlers:
                    for queue in camera_handlers[serialno].video_thread.queues:
                        if queue.full():
                            logMessage(f"Video queue full.")
                            queue.get(False)
                        queue.put(event_value)
        elif message["event"] == "livestream error":
            logMessage(f"Livestream Error! - {message}")
            # TODO: Handle error codes with muliple cameras.
            # if self.ws and len(self.video_thread.queues) > 0:
            #     msg = START_P2P_LIVESTREAM_MESSAGE.copy()
            #     msg["serialNumber"] = self.serialno
            #     await self.ws.send_message(json.dumps(msg))
        else:
            logMessage(f"Unknown event type: {message['event']}")
            logMessage(f"{message}")
    else:
        logMessage(f"Unknown message type: {message_type}")
        logMessage(f"{message}")


async def init_websocket(ws_security_port):
    """Initialize the websocket."""
    websocket = EufySecurityWebSocket(
        "402f1039-eufy-security-ws",
        ws_security_port,
        ClientSession(),
        on_open,
        on_message,
        on_close,
        on_error,
    )
    # Set the websocket for all camera handlers.
    for handler in camera_handlers.values():
        handler.setWs(websocket)

    try:
        await websocket.connect()
        await websocket.send_message(json.dumps(START_LISTENING_MESSAGE))
        await websocket.send_message(json.dumps(SET_API_SCHEMA))
        await websocket.send_message(json.dumps(DRIVER_CONNECT_MESSAGE))
        while not run_event.is_set():
            await asyncio.sleep(1000)
    except Exception as ex:
        logMessage(ex)
        logMessage(f"init_websocket failed. Exiting.")
    os._exit(-1)


if __name__ == "__main__":
    """Main entry point."""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Stream video and audio from multiple Eufy cameras."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode (default: disabled).",
    )
    parser.add_argument(
        "--camera_serials",
        nargs="+",
        required=True,
        help="List of camera serial numbers (e.g., --camera_serials CAM1_SERIAL CAM2_SERIAL).",
    )
    parser.add_argument(
        "--ws_security_port",
        type=int,
        default=3000,
        help="Base port number for streaming (default: 3000).",
    )
    args = parser.parse_args()
    debug = args.debug
    print(f"Debug: {debug}")
    sys.stdout.flush()
    logMessage(f"WS Security Port: {args.ws_security_port}")

    # Define constants.
    BASE_PORT = 63336
    # Create one Camera Stream Handler per camera.
    for i, serial in enumerate(args.camera_serials):
        if serial != "null":
            logMessage(f"Creating CameraStreamHandler for camera: {serial}")
            handler = CameraStreamHandler(serial, BASE_PORT + i * 3, run_event)
            # handler.setup_sockets()
            camera_handlers[serial] = handler

    logMessage(f"Starting websocket.")
    # Loop forever.
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_websocket(args.ws_security_port))
