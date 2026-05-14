from websocket import EufySecurityWebSocket
import aiohttp
import asyncio
import json
import socket
import select
import threading
import time
import sys
import signal
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from queue import Empty, Queue
from typing import Any, Optional, Tuple

RECV_CHUNK_SIZE = 8192  # Increased for better throughput
SOCKET_BUFFER_SIZE = 262144  # 256KB buffer for faster data transfer

video_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
audio_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
backchannel_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)


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
    "buffer": None
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

TALKBACK_RESULT_MESSAGE = {"messageId": "talkback_audio_data", "errorCode": "device_talkback_not_running"}

DRIVER_CONNECT_MESSAGE = {"messageId": "driver_connect", "command": "driver.connect"}

run_event = threading.Event()

def exit_handler(signum, frame):
    print(f'Signal handler called with signal {signum}')
    run_event.set()

# Install signal handler
signal.signal(signal.SIGINT, exit_handler)
signal.signal(signal.SIGTERM, exit_handler)

class ClientAcceptThread(threading.Thread):
    def __init__(self, socket, run_event, name, connector, serialno):
        threading.Thread.__init__(self)
        self.socket = socket
        self.queues = []
        self.run_event = run_event
        self.name = name
        self.connector = connector
        self.serialno = serialno
        self.my_threads = []
        self.last_cleanup_time = 0
        self.ready_to_accept = threading.Event()  # Don't accept until data is ready
        self.skip_non_idr = self.name == "Video"
        print(f"[{self.name}] ClientAcceptThread initialized, skip_non_idr={self.skip_non_idr}")
        sys.stdout.flush()

    def update_threads(self):
        my_threads_before = len(self.my_threads)
        for thread in self.my_threads:
            if not thread.is_alive():
                self.queues.remove(thread.queue)
        self.my_threads = [t for t in self.my_threads if t.is_alive()]
        
        # Debounce the stop command - go2rtc/ffmpeg may probe and reconnect; avoid thrash
        current_time = time.time()
        if my_threads_before > 0 and len(self.my_threads) == 0:
            if self.last_cleanup_time == 0:
                self.last_cleanup_time = current_time
            elif current_time - self.last_cleanup_time >= 15.0:
                if self.name == "BackChannel":
                    print("All clients died (BackChannel): ", self.name)
                    sys.stdout.flush()
                else:
                    # Only stop from the Video side to avoid audio-probe disconnects killing the stream
                    if self.name == "Video":
                        print("All video clients gone. Stopping Stream (grace elapsed): ", self.name)
                        sys.stdout.flush()
                        # Stop the stream to save battery/bandwidth
                        self.connector.schedule_stop_livestream()
                self.last_cleanup_time = 0
        elif len(self.my_threads) > 0:
            self.last_cleanup_time = 0

    def run(self):
        print("Accepting connection for ", self.name)
        
        while not self.run_event.is_set():
            self.update_threads()

            sys.stdout.flush()
            try:
                readable, _, _ = select.select([self.socket], [], [], 1.0)
                if not readable:
                    continue

                client_sock, client_addr = self.socket.accept()
                print("New connection added: ", client_addr, " for ", self.name)
                sys.stdout.flush()

                if self.name == "BackChannel":
                    # Only manage talkback lifecycle when a client actually connects
                    self.connector.schedule_stop_talkback()
                    client_sock.setblocking(True)
                    # Optimize socket buffers for audio streaming
                    try:
                        client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_SIZE)
                        client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    except OSError:
                        pass
                    print("[BACKCHANNEL] Starting BackChannel thread")
                    sys.stdout.flush()
                    thread = ClientRecvThread(client_sock, run_event, self.name, self.connector, self.serialno)
                    thread.start()
                else:
                    client_sock.setblocking(False)
                    # Optimize socket buffers for faster streaming
                    try:
                        client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUFFER_SIZE)
                        client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    except OSError:
                        pass
                    thread = ClientSendThread(client_sock, run_event, self.name, self.connector, self.serialno)
                    self.queues.append(thread.queue)
                    if self.name == "Video":
                        thread.skip_non_idr = True
                        # Re-enable accept thread's flag so we can detect IDR for this new thread
                        self.skip_non_idr = True
                        print(f"[Video] New client thread created with skip_non_idr=True, accept thread flag reset")
                        sys.stdout.flush()
                        self.connector.prime_video_queue(thread.queue)
                    # Ensure livestream is running (idempotent due to debouncing)
                    self.connector.schedule_start_livestream()
                    self.my_threads.append(thread)
                    thread.start()
            except (socket.timeout, OSError):
                continue

class ClientSendThread(threading.Thread):
    def __init__(self, client_sock, run_event, name, connector, serialno):
        threading.Thread.__init__(self)
        self.client_sock = client_sock
        self.queue = Queue(30)  # Smaller queue for real-time streaming
        self.run_event = run_event
        self.name = name
        self.connector = connector
        self.serialno = serialno
        self._last_send_error_log = 0.0
        self.skip_non_idr = False  # Will be set by connector if needed
        print(f"[{self.name}] ClientSendThread created, skip_non_idr={self.skip_non_idr}")
        sys.stdout.flush()

    def run(self):
        print("Thread running: ", self.name)
        sys.stdout.flush()

        try:
            pending_item = None
            while not self.run_event.is_set():
                if pending_item is None:
                    try:
                        pending_item = self.queue.get(timeout=1.0)
                    except Empty:
                        continue

                if self.run_event.is_set():
                    break

                ready_to_read, ready_to_write, in_error = \
                    select.select([], [self.client_sock], [self.client_sock], 1.0)
                if len(in_error):
                    print("Exception in socket", self.name)
                    sys.stdout.flush()
                    break
                if len(ready_to_write):
                    try:
                        if isinstance(pending_item, dict) and "data" in pending_item:
                            payload = pending_item["data"]
                        else:
                            payload = pending_item
                        self.client_sock.sendall(bytearray(payload))
                        pending_item = None
                    except (BrokenPipeError, ConnectionResetError, OSError) as e:
                        now = time.time()
                        # go2rtc/ffmpeg often connects, probes, and disconnects quickly; avoid log spam
                        if now - self._last_send_error_log >= 5.0:
                            print(f"Send error on {self.name}: {e}")
                            sys.stdout.flush()
                            self._last_send_error_log = now
                        break
                else:
                    time.sleep(0.05)
        except socket.error as e:
            print("Connection lost", self.name, e)
            pass
        except socket.timeout:
            print("Timeout on socket for ", self.name)
            pass
        
        self._cleanup()

    def _cleanup(self):
        try:
            self.client_sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.client_sock.close()
        except OSError:
            pass
        print("Thread stopping: ", self.name)
        sys.stdout.flush()

class ClientRecvThread(threading.Thread):
    def __init__(self, client_sock, run_event, name, connector, serialno):
        threading.Thread.__init__(self)
        self.client_sock = client_sock
        self.run_event = run_event
        self.name = name
        self.connector = connector
        self.serialno = serialno

    def run(self):
        print("[BACKCHANNEL] Thread started, attempting to start talkback")
        sys.stdout.flush()
        # Schedule start in event loop
        self.connector.schedule_start_talkback()
        
        try:
            curr_packet = bytearray() 
            no_data = 0
            last_send_time = time.time()
            total_bytes_received = 0
            packets_sent = 0
            
            while not self.run_event.is_set():
                try:
                    ready_to_read, ready_to_write, in_error = \
                        select.select([self.client_sock,], [], [self.client_sock], 1)
                    if len(in_error):
                        print("[BACKCHANNEL] Exception in socket")
                        sys.stdout.flush()
                        break
                    if len(ready_to_read):
                        data = self.client_sock.recv(RECV_CHUNK_SIZE)
                        if len(data) > 0:
                            curr_packet += bytearray(data)
                            total_bytes_received += len(data)
                            no_data = 0
                            
                            if packets_sent == 0:
                                print(f"[BACKCHANNEL] First data received! {len(data)} bytes")
                                sys.stdout.flush()
                            
                            # Send packets at regular intervals or when buffer is large enough
                            current_time = time.time()
                            if len(curr_packet) >= 1600 or (current_time - last_send_time >= 0.1 and len(curr_packet) > 0):
                                print(f"[BACKCHANNEL] Sending {len(curr_packet)} bytes to camera (packet #{packets_sent + 1}, total received: {total_bytes_received})")
                                sys.stdout.flush()
                                self.connector.schedule_send_talkback_data(list(bytes(curr_packet)))
                                curr_packet = bytearray()
                                last_send_time = current_time
                                packets_sent += 1
                        else:
                            # Connection closed
                            print(f"[BACKCHANNEL] Connection closed by peer (total: {total_bytes_received} bytes, {packets_sent} packets)")
                            sys.stdout.flush()
                            break
                    else:
                        no_data += 1
                        # Send any remaining data if idle for a bit
                        if len(curr_packet) > 0 and time.time() - last_send_time >= 0.2:
                            print(f"[BACKCHANNEL] Sending remaining {len(curr_packet)} bytes (timeout)")
                            sys.stdout.flush()
                            self.connector.schedule_send_talkback_data(list(bytes(curr_packet)))
                            curr_packet = bytearray()
                            last_send_time = time.time()
                            packets_sent += 1
                    
                    if no_data >= 30:  # 30 seconds with no data
                        print(f"[BACKCHANNEL] 30 seconds idle (total: {total_bytes_received} bytes, {packets_sent} packets)")
                        sys.stdout.flush()
                        # Don't break - keep connection alive for backchannel
                        no_data = 0
                except BlockingIOError:
                    pass
        except (socket.error, select.error) as e:
            print(f"[BACKCHANNEL] Connection error: {e}")
            sys.stdout.flush()
            pass
        except socket.timeout:
            print("[BACKCHANNEL] Socket timeout")
            sys.stdout.flush()
            pass
        
        print(f"[BACKCHANNEL] Thread stopping (total: {total_bytes_received} bytes, {packets_sent} packets)")
        sys.stdout.flush()
        self._cleanup()
        # Schedule stop in event loop
        self.connector.schedule_stop_talkback()

    def _cleanup(self):
        sys.stdout.flush()
        try:
            self.client_sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.client_sock.close()
        except OSError:
            pass
        sys.stdout.flush()

class Connector:
    def __init__(self, run_event):
        video_sock.bind(("0.0.0.0", 63336))
        video_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        video_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        video_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_SIZE)
        video_sock.settimeout(0.5)  # Faster accept response
        video_sock.listen(5)  # Increased backlog
        
        audio_sock.bind(("0.0.0.0", 63337))
        audio_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        audio_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        audio_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_SIZE)
        audio_sock.settimeout(0.5)  # Faster accept response
        audio_sock.listen(5)  # Increased backlog
        
        backchannel_sock.bind(("0.0.0.0", 63338))
        backchannel_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        backchannel_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        backchannel_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        backchannel_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_SIZE)
        backchannel_sock.settimeout(0.5)  # Faster accept response
        backchannel_sock.listen(5)  # Increased backlog
        
        self.ws = None
        self.run_event = run_event
        self.serialno = ""
        self.loop = None
        self.ws_closed_event: Optional[asyncio.Event] = None
        self.livestream_active = False
        self.talkback_active = False
        # IMPORTANT: create asyncio primitives only once we have a running loop (Py3.9 safety)
        self.livestream_lock: Optional[asyncio.Lock] = None
        self.talkback_lock: Optional[asyncio.Lock] = None
        self.last_livestream_start = 0
        self.last_talkback_start = 0

        # Codec/cache for improving first-frame decode on new TCP clients
        self.video_codec: Optional[str] = None
        self.audio_codec: Optional[str] = None
        self._video_buffer_shape: Optional[str] = None  # "dict" or "list"
        self._video_parse_buffer = bytearray()
        self._last_vps: Optional[bytes] = None
        self._last_sps: Optional[bytes] = None
        self._last_pps: Optional[bytes] = None
        self._last_idr: Optional[bytes] = None
        
        # Command queues for thread-safe async communication
        self.command_queue = Queue()
        
        # WebSocket event timeout detection (detect stale connections)
        self.last_event_time = time.time()
        self.ws_event_timeout_seconds = 30  # Reconnect if no events for 30 seconds
        self.ws_monitor_task: Optional[asyncio.Task] = None

    def stop(self):
        try:
            video_sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            video_sock.close()
        except OSError:
            pass
        try:
            audio_sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            audio_sock.close()
        except OSError:
            pass
        try:
            backchannel_sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            backchannel_sock.close()
        except OSError:
            pass

    def setWs(self, ws: EufySecurityWebSocket):
        self.ws = ws

    def set_loop(self, loop):
        self.loop = loop
        self._ensure_async_primitives()

    def _ensure_async_primitives(self) -> None:
        if self.ws_closed_event is None:
            self.ws_closed_event = asyncio.Event()
        if self.livestream_lock is None:
            self.livestream_lock = asyncio.Lock()
        if self.talkback_lock is None:
            self.talkback_lock = asyncio.Lock()

    # Thread-safe scheduling methods
    def schedule_start_livestream(self):
        if self.loop and not self.loop.is_closed() and not self.run_event.is_set():
            asyncio.run_coroutine_threadsafe(self._start_livestream(), self.loop)

    def schedule_stop_livestream(self):
        if self.loop and not self.loop.is_closed() and not self.run_event.is_set():
            asyncio.run_coroutine_threadsafe(self._stop_livestream(), self.loop)

    def schedule_start_talkback(self):
        if self.loop and not self.loop.is_closed() and not self.run_event.is_set():
            asyncio.run_coroutine_threadsafe(self._start_talkback(), self.loop)

    def schedule_stop_talkback(self):
        if self.loop and not self.loop.is_closed() and not self.run_event.is_set():
            asyncio.run_coroutine_threadsafe(self._stop_talkback(), self.loop)

    def schedule_send_talkback_data(self, data):
        if self.loop and not self.loop.is_closed() and not self.run_event.is_set():
            asyncio.run_coroutine_threadsafe(self._send_talkback_data(data), self.loop)

    def prime_video_queue(self, queue: Queue) -> None:
        """Best-effort: send VPS/SPS/PPS (+ last IDR) to a new client first.

        This reduces ffmpeg/go2rtc start failures when the connection begins mid-stream
        without parameter sets or a keyframe.
        """
        if self.video_codec is None:
            return
        parts: list[bytes] = []
        codec = self.video_codec.lower()
        if "h265" in codec or "hevc" in codec:
            if self._last_vps:
                parts.append(self._last_vps)
            if self._last_sps:
                parts.append(self._last_sps)
            if self._last_pps:
                parts.append(self._last_pps)
        else:  # default to H.264 style
            if self._last_sps:
                parts.append(self._last_sps)
            if self._last_pps:
                parts.append(self._last_pps)

        if self._last_idr:
            parts.append(self._last_idr)

        if not parts:
            return

        # Keep this small to avoid introducing latency
        preamble = b"".join(parts)
        try:
            if self._video_buffer_shape == "list":
                queue.put_nowait(list(preamble))
            else:
                queue.put_nowait({"data": list(preamble)})
        except Exception:
            pass

    def _extract_buffer_bytes(self, event_value: Any) -> Tuple[Optional[bytes], Optional[str]]:
        if isinstance(event_value, dict) and "data" in event_value and isinstance(event_value["data"], list):
            return bytes(event_value["data"]), "dict"
        if isinstance(event_value, list):
            return bytes(event_value), "list"
        return None, None

    @staticmethod
    def _find_start_codes(data: bytes) -> list[Tuple[int, int]]:
        """Return list of (index, length) for Annex-B start codes."""
        out: list[Tuple[int, int]] = []
        i = 0
        n = len(data)
        while i + 3 < n:
            if data[i] == 0 and data[i + 1] == 0:
                if data[i + 2] == 1:
                    out.append((i, 3))
                    i += 3
                    continue
                if i + 3 < n and data[i + 2] == 0 and data[i + 3] == 1:
                    out.append((i, 4))
                    i += 4
                    continue
            i += 1
        return out

    def _is_idr_frame(self, chunk: bytes) -> bool:
        """Check if chunk contains any IDR (keyframe) NAL unit."""
        if not self.video_codec:
            return False
        codec_l = self.video_codec.lower()
        starts = self._find_start_codes(chunk)
        if len(starts) < 1:
            return False
        
        # Check ALL NAL units in the chunk, not just the first one
        for (idx, sc_len), next_start in zip(starts, starts[1:] + [(len(chunk), 0)]):
            if idx + sc_len + 1 > len(chunk):
                continue
            header = chunk[idx + sc_len]
            if "h265" in codec_l or "hevc" in codec_l:
                nal_type = (header >> 1) & 0x3F
                if nal_type in (19, 20):
                    print(f"[IDR] HEVC IDR frame found (NAL type {nal_type})")
                    sys.stdout.flush()
                    return True
            else:
                nal_type = header & 0x1F
                if nal_type == 5:
                    print(f"[IDR] H.264 IDR frame found (NAL type 5)")
                    sys.stdout.flush()
                    return True
        
        # No IDR found, log what we saw
        nal_types = []
        for (idx, sc_len) in starts[:3]:  # Log first 3 NALs
            if idx + sc_len + 1 <= len(chunk):
                header = chunk[idx + sc_len]
                if "h265" in codec_l or "hevc" in codec_l:
                    nal_type = (header >> 1) & 0x3F
                else:
                    nal_type = header & 0x1F
                nal_types.append(nal_type)
        return False

    def _update_video_codec_cache(self, chunk: bytes, codec: Optional[str]) -> None:
        if codec and not self.video_codec:
            self.video_codec = codec
            print(f"Video codec detected: {codec}")
            sys.stdout.flush()

        # Keep a rolling buffer so we can detect SPS/PPS/VPS even if split across frames
        if self.video_codec:
            codec_l = self.video_codec.lower()
            if ("h265" in codec_l or "hevc" in codec_l) and self._last_vps and self._last_sps and self._last_pps and self._last_idr:
                return
            if not ("h265" in codec_l or "hevc" in codec_l) and self._last_sps and self._last_pps and self._last_idr:
                return

        self._video_parse_buffer.extend(chunk)
        if len(self._video_parse_buffer) > 512 * 1024:
            # Keep the tail; parameter sets repeat periodically and are small
            self._video_parse_buffer = self._video_parse_buffer[-128 * 1024 :]

        buf = bytes(self._video_parse_buffer)
        starts = self._find_start_codes(buf)
        if len(starts) < 2:
            return

        # Parse complete NAL units (between two start codes). Leave remainder for next time.
        for (idx, sc_len), (next_idx, _next_len) in zip(starts, starts[1:]):
            nal = buf[idx:next_idx]
            if len(nal) <= sc_len:
                continue
            header = nal[sc_len]
            codec_l = (self.video_codec or "").lower()
            if "h265" in codec_l or "hevc" in codec_l:
                nal_type = (header >> 1) & 0x3F
                if nal_type == 32:
                    self._last_vps = nal
                elif nal_type == 33:
                    self._last_sps = nal
                elif nal_type == 34:
                    self._last_pps = nal
                elif nal_type in (19, 20):
                    self._last_idr = nal
            else:
                nal_type = header & 0x1F
                if nal_type == 7:
                    self._last_sps = nal
                elif nal_type == 8:
                    self._last_pps = nal
                elif nal_type == 5:
                    self._last_idr = nal

        # Keep unparsed remainder starting at the last start code
        last_start_idx, _ = starts[-1]
        self._video_parse_buffer = bytearray(buf[last_start_idx:])

    # Async methods with state management
    async def _start_livestream(self):
        if not self.ws or not self.serialno:
            print(f"[LIVESTREAM] Cannot start: ws={self.ws is not None}, serial={self.serialno}")
            sys.stdout.flush()
            return
        self._ensure_async_primitives()
        assert self.livestream_lock is not None

        async with self.livestream_lock:
            current_time = time.time()
            # Debounce - don't start if we recently started
            if self.livestream_active or (current_time - self.last_livestream_start) < 2.0:  # Reduced from 3s to 2s
                print(f"[LIVESTREAM] Start debounced (active={self.livestream_active})")
                sys.stdout.flush()
                return
            
            self.livestream_active = True
            self.last_livestream_start = current_time
            msg = START_P2P_LIVESTREAM_MESSAGE.copy()
            msg["serialNumber"] = self.serialno
            try:
                print(f"[LIVESTREAM] Sending START command for {self.serialno}")
                sys.stdout.flush()
                await self.ws.send_message(json.dumps(msg))
                print(f"[LIVESTREAM] START command sent successfully")
                sys.stdout.flush()
            except Exception as e:
                print(f"[LIVESTREAM] Error starting livestream: {e}")
                sys.stdout.flush()
                self.livestream_active = False

    async def _stop_livestream(self):
        if not self.ws or not self.serialno:
            print(f"[LIVESTREAM] Cannot stop: ws={self.ws is not None}, serial={self.serialno}")
            sys.stdout.flush()
            return
        self._ensure_async_primitives()
        assert self.livestream_lock is not None

        async with self.livestream_lock:
            if not self.livestream_active:
                print(f"[LIVESTREAM] Stop skipped (not active)")
                sys.stdout.flush()
                return
            
            self.livestream_active = False
            msg = STOP_P2P_LIVESTREAM_MESSAGE.copy()
            msg["serialNumber"] = self.serialno
            try:
                print(f"[LIVESTREAM] Sending STOP command for {self.serialno}")
                sys.stdout.flush()
                await self.ws.send_message(json.dumps(msg))
                print(f"[LIVESTREAM] STOP command sent successfully")
                sys.stdout.flush()
            except Exception as e:
                print(f"[LIVESTREAM] Error stopping livestream: {e}")
                sys.stdout.flush()

    async def _start_talkback(self):
        if not self.ws or not self.serialno:
            print("[BACKCHANNEL] Cannot start talkback - no websocket or serial number")
            sys.stdout.flush()
            return
        
        self._ensure_async_primitives()
        assert self.talkback_lock is not None

        async with self.talkback_lock:
            current_time = time.time()
            if self.talkback_active:
                print("[BACKCHANNEL] Talkback already active")
                sys.stdout.flush()
                return
            if (current_time - self.last_talkback_start) < 1.0:
                print(f"[BACKCHANNEL] Talkback debounced (too soon: {current_time - self.last_talkback_start:.2f}s ago)")
                sys.stdout.flush()
                return
            
            self.talkback_active = True
            self.last_talkback_start = current_time
            msg = START_TALKBACK.copy()
            msg["serialNumber"] = self.serialno
            try:
                print(f"[BACKCHANNEL] Sending START_TALKBACK command to {self.serialno}")
                sys.stdout.flush()
                await self.ws.send_message(json.dumps(msg))
                print("[BACKCHANNEL] Talkback start command sent successfully")
                sys.stdout.flush()
            except Exception as e:
                print(f"[BACKCHANNEL] Error starting talkback: {e}")
                sys.stdout.flush()
                self.talkback_active = False

    async def _stop_talkback(self):
        if not self.ws or not self.serialno:
            print("[BACKCHANNEL] Cannot stop talkback - no websocket or serial number")
            sys.stdout.flush()
            return
        
        self._ensure_async_primitives()
        assert self.talkback_lock is not None

        async with self.talkback_lock:
            if not self.talkback_active:
                print("[BACKCHANNEL] Talkback not active, skip stop")
                sys.stdout.flush()
                return
            
            self.talkback_active = False
            msg = STOP_TALKBACK.copy()
            msg["serialNumber"] = self.serialno
            try:
                print(f"[BACKCHANNEL] Sending STOP_TALKBACK command to {self.serialno}")
                sys.stdout.flush()
                await self.ws.send_message(json.dumps(msg))
                print("[BACKCHANNEL] Talkback stop command sent successfully")
                sys.stdout.flush()
            except Exception as e:
                print(f"[BACKCHANNEL] Error stopping talkback: {e}")
                sys.stdout.flush()

    async def _send_talkback_data(self, data):
        if not self.ws or not self.serialno:
            print("[BACKCHANNEL] Cannot send data - no websocket or serial number")
            sys.stdout.flush()
            return
        
        if not self.talkback_active:
            print("[BACKCHANNEL] Cannot send data - talkback not active")
            sys.stdout.flush()
            return
        
        msg = SEND_TALKBACK_AUDIO_DATA.copy()
        msg["serialNumber"] = self.serialno
        msg["buffer"] = data
        try:
            await self.ws.send_message(json.dumps(msg))
        except Exception as e:
            print(f"[BACKCHANNEL] Error sending talkback data ({len(data)} bytes): {e}")
            sys.stdout.flush()

    async def on_open(self):
        print(f"[WS_LIFECYCLE] WebSocket connection opened")
        sys.stdout.flush()
        if self.ws_closed_event is not None:
            self.ws_closed_event.clear()

    async def on_close(self):
        print(f"[WS_LIFECYCLE] WebSocket connection closed")
        sys.stdout.flush()
        # Do not terminate the whole add-on; allow reconnect loop to recover.
        self.ws = None
        self._ensure_async_primitives()
        assert self.livestream_lock is not None
        assert self.talkback_lock is not None
        async with self.livestream_lock:
            self.livestream_active = False
        async with self.talkback_lock:
            self.talkback_active = False
        try:
            if hasattr(self, "video_thread"):
                self.video_thread.ready_to_accept.clear()
            if hasattr(self, "audio_thread"):
                self.audio_thread.ready_to_accept.clear()
        except Exception:
            pass
        if self.ws_closed_event is not None:
            self.ws_closed_event.set()

    async def on_error(self, message):
        print(f" on_error - executed - {message}")

    def _update_event_timestamp(self):
        """Track that we received an event from the websocket."""
        self.last_event_time = time.time()

    async def _monitor_websocket_health(self) -> None:
        """Monitor websocket event flow and reconnect if stale while streaming.
        
        Only enforce the timeout when there are active clients. If no clients are 
        connected, it's normal to have no events.
        """
        print("[WS_MONITOR] Event monitor started")
        sys.stdout.flush()
        while not self.run_event.is_set():
            try:
                await asyncio.sleep(5)  # Check every 5 seconds
                
                # Only check for stale events if there are active video/audio clients
                has_video_clients = hasattr(self, "video_thread") and self.video_thread and len(self.video_thread.queues) > 0
                has_audio_clients = hasattr(self, "audio_thread") and self.audio_thread and len(self.audio_thread.queues) > 0
                
                if has_video_clients or has_audio_clients:
                    time_since_event = time.time() - self.last_event_time
                    if time_since_event > self.ws_event_timeout_seconds:
                        print(f"[WS_MONITOR] ALERT: No events for {time_since_event:.1f}s with active clients (video:{has_video_clients}, audio:{has_audio_clients})!")
                        sys.stdout.flush()
                        if self.ws and not self.ws.ws.closed:
                            print("[WS_MONITOR] Forcing websocket close to trigger reconnect...")
                            sys.stdout.flush()
                            try:
                                await self.ws.ws.close()
                            except Exception as e:
                                print(f"[WS_MONITOR] Error closing stale websocket: {e}")
                                sys.stdout.flush()
                        self.last_event_time = time.time()  # Reset timer after action
                    else:
                        print(f"[WS_MONITOR] Stream active: events flowing ({time_since_event:.1f}s since last event)")
                        sys.stdout.flush()
                else:
                    # No active clients - events are not expected
                    print("[WS_MONITOR] No active clients - event monitoring idle")
                    sys.stdout.flush()
            except asyncio.CancelledError:
                print("[WS_MONITOR] Monitor task cancelled")
                sys.stdout.flush()
                break
            except Exception as e:
                print(f"[WS_MONITOR] Monitor error: {e}")
                sys.stdout.flush()

    async def on_error(self, message):
        print(f" on_error - executed - {message}")
        sys.stdout.flush()
        self._update_event_timestamp()

    async def on_message(self, message):
        self._update_event_timestamp()  # Track that we received an event
        payload = message.json()
        message_type: str = payload["type"]
        
        if message_type == "result":
            message_id = payload["messageId"]
            if message_id != SEND_TALKBACK_AUDIO_DATA["messageId"]:
                print(f"[WS_RX] Result message: {message_id}")
                sys.stdout.flush()
            
            if message_id == START_LISTENING_MESSAGE["messageId"]:
                message_result = payload[message_type]
                states = message_result["state"]
                # `devices` can be an array of strings (serial numbers) or full device objects
                devices = states.get("devices") or []
                for dev in devices:
                    if isinstance(dev, str):
                        self.serialno = dev
                    elif isinstance(dev, dict) and "serialNumber" in dev:
                        self.serialno = dev["serialNumber"]

                # Create the TCP accept threads once. On websocket reconnect, just refresh serialno.
                if not hasattr(self, "video_thread") or not self.video_thread.is_alive():
                    self.video_thread = ClientAcceptThread(video_sock, run_event, "Video", self, self.serialno)
                    self.audio_thread = ClientAcceptThread(audio_sock, run_event, "Audio", self, self.serialno)
                    self.backchannel_thread = ClientAcceptThread(backchannel_sock, run_event, "BackChannel", self, self.serialno)
                    self.audio_thread.start()
                    self.video_thread.start()
                    self.backchannel_thread.start()
                else:
                    try:
                        self.video_thread.serialno = self.serialno
                        self.audio_thread.serialno = self.serialno
                        self.backchannel_thread.serialno = self.serialno
                    except Exception:
                        pass

                # If the serial becomes available after a client already connected,
                # trigger livestream start so the connection can receive video data.
                if self.serialno and hasattr(self, "video_thread") and len(self.video_thread.queues) > 0:
                    self.schedule_start_livestream()
            
            # Update state based on responses
            if message_id == "start_livestream":
                if payload.get("success"):
                    self._ensure_async_primitives()
                    assert self.livestream_lock is not None
                    async with self.livestream_lock:
                        self.livestream_active = True
            elif message_id == "stop_livestream":
                if payload.get("success"):
                    self._ensure_async_primitives()
                    assert self.livestream_lock is not None
                    async with self.livestream_lock:
                        self.livestream_active = False
            elif message_id == "start_talkback":
                if payload.get("success"):
                    self._ensure_async_primitives()
                    assert self.talkback_lock is not None
                    async with self.talkback_lock:
                        self.talkback_active = True
                    print("[BACKCHANNEL] Camera confirmed talkback started")
                    sys.stdout.flush()
                else:
                    print(f"[BACKCHANNEL] Camera rejected talkback start: {payload}")
                    sys.stdout.flush()
            elif message_id == "stop_talkback":
                if payload.get("success"):
                    self._ensure_async_primitives()
                    assert self.talkback_lock is not None
                    async with self.talkback_lock:
                        self.talkback_active = False
                    print("[BACKCHANNEL] Camera confirmed talkback stopped")
                    sys.stdout.flush()
            
            if message_id == TALKBACK_RESULT_MESSAGE["messageId"] and "errorCode" in payload:
                error_code = payload["errorCode"]
                if error_code == "device_talkback_not_running":
                    await self._start_talkback()

        if message_type == "event":
            message = payload[message_type]
            event_type = message["event"]
            print(f"[WS_RX] Event: {event_type}")
            sys.stdout.flush()
            
            if message["event"] == "livestream audio data":
                # Signal that audio data is flowing - ready to accept connections
                if not self.audio_thread.ready_to_accept.is_set():
                    self.audio_thread.ready_to_accept.set()
                    print("Audio data flowing - accepting connections")

                # Log codec once (useful for go2rtc/ffmpeg config)
                try:
                    meta = message.get("metadata") or {}
                    codec = meta.get("audioCodec") if isinstance(meta, dict) else None
                    if codec and not self.audio_codec:
                        self.audio_codec = codec
                        print(f"Audio codec detected: {codec}")
                        sys.stdout.flush()
                except Exception:
                    pass
                
                event_value = message[EVENT_CONFIGURATION[event_type]["value"]]
                event_data_type = EVENT_CONFIGURATION[event_type]["type"]
                if event_data_type == "event":
                    for queue in self.audio_thread.queues:
                        # Aggressive dropping for real-time: keep queue small
                        while queue.qsize() > 5:  # Keep only ~0.5s of audio buffered
                            try:
                                queue.get(False)
                            except:
                                break
                        try:
                            queue.put(event_value, block=False)
                        except:
                            pass
            
            if message["event"] == "livestream video data":
                # Signal that video data is flowing - ready to accept connections
                if not self.video_thread.ready_to_accept.is_set():
                    self.video_thread.ready_to_accept.set()
                    print("Video data flowing - accepting connections")

                # Cache codec parameter sets / keyframe to help new consumers start
                try:
                    meta = message.get("metadata") or {}
                    codec = meta.get("videoCodec") if isinstance(meta, dict) else None
                    event_value = message[EVENT_CONFIGURATION[event_type]["value"]]
                    chunk, shape = self._extract_buffer_bytes(event_value)
                    is_idr = False
                    if shape and not self._video_buffer_shape:
                        self._video_buffer_shape = shape
                    if chunk:
                        self._update_video_codec_cache(chunk, codec)
                        is_idr = self._is_idr_frame(chunk)
                        if is_idr:
                            # Check if any thread is still waiting for an IDR
                            any_waiting = any(thread.skip_non_idr for thread in self.video_thread.my_threads)
                            if any_waiting:
                                print(f"[VIDEO] First IDR detected, enabling frame distribution for all threads")
                                sys.stdout.flush()
                                # Update all threads' skip flags
                                for thread in self.video_thread.my_threads:
                                    thread.skip_non_idr = False
                            # Also reset the accept thread's flag so new connections don't skip
                            self.video_thread.skip_non_idr = False
                except Exception as e:
                    print(f"[VIDEO] Error in video frame processing: {e}")
                    import traceback
                    traceback.print_exc()
                    sys.stdout.flush()
                
                event_value = message[EVENT_CONFIGURATION[event_type]["value"]]
                event_data_type = EVENT_CONFIGURATION[event_type]["type"]
                if event_data_type == "event":
                    video_threads_count = len(self.video_thread.my_threads)
                    skipped_count = 0
                    sent_count = 0
                    for thread in self.video_thread.my_threads:
                        if thread.skip_non_idr and not is_idr:
                            skipped_count += 1
                            continue
                        # Aggressive dropping for real-time: keep only latest frames
                        while thread.queue.qsize() > 3:  # Keep only ~0.2s of video buffered
                            try:
                                thread.queue.get(False)
                            except:
                                break
                        try:
                            thread.queue.put(event_value, block=False)
                            sent_count += 1
                        except:
                            pass
            
            if message["event"] == "livestream error":
                print("Livestream Error - attempting restart!")
                sys.stdout.flush()
                self._ensure_async_primitives()
                assert self.livestream_lock is not None
                async with self.livestream_lock:
                    self.livestream_active = False
                    # Reset ready flags so threads wait for new data
                    try:
                        if hasattr(self, "video_thread") and self.video_thread:
                            self.video_thread.ready_to_accept.clear()
                    except Exception as e:
                        print(f"Error clearing video_thread flag: {e}")
                        sys.stdout.flush()
                    try:
                        if hasattr(self, "audio_thread") and self.audio_thread:
                            self.audio_thread.ready_to_accept.clear()
                    except Exception as e:
                        print(f"Error clearing audio_thread flag: {e}")
                        sys.stdout.flush()
                # Wait a bit before restarting
                await asyncio.sleep(1.5)
                # Only restart if clients are still connected
                try:
                    if self.ws and hasattr(self, "video_thread") and self.video_thread and len(self.video_thread.queues) > 0:
                        await self._start_livestream()
                except Exception as e:
                    print(f"Error restarting livestream after error: {e}")
                    sys.stdout.flush()

# Websocket connector
c = Connector(run_event)

async def init_websocket() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Missing argument: expected websocket access token / API key as argv[1]")

    c.set_loop(asyncio.get_running_loop())

    # Reconnect loop: keeps TCP ports open and recovers from transient ws/server restarts
    backoff_s = 1.0
    async with aiohttp.ClientSession() as session:
        while not run_event.is_set():
            print(f"[WS_INIT] Attempting WebSocket connection (backoff: {backoff_s:.1f}s)...")
            sys.stdout.flush()
            ws: EufySecurityWebSocket = EufySecurityWebSocket(
                "402f1039-eufy-security-ws",
                sys.argv[1],
                session,
                c.on_open,
                c.on_message,
                c.on_close,
                c.on_error,
            )
            c.setWs(ws)
            if c.ws_closed_event is not None:
                c.ws_closed_event.clear()

            try:
                await ws.connect()
                print("[WS_INIT] WebSocket connected successfully")
                sys.stdout.flush()
                
                # Reset event timestamp on successful connection
                c.last_event_time = time.time()
                
                # Start the event monitor (will detect stale connections)
                if c.ws_monitor_task is None or c.ws_monitor_task.done():
                    c.ws_monitor_task = asyncio.create_task(c._monitor_websocket_health())
                    print("[WS_INIT] Event monitor task started")
                    sys.stdout.flush()

                # Prefer setting schema before listening so server formats events accordingly
                await ws.send_message(json.dumps(SET_API_SCHEMA))
                await ws.send_message(json.dumps(START_LISTENING_MESSAGE))
                await ws.send_message(json.dumps(DRIVER_CONNECT_MESSAGE))
                print("[WS_INIT] Initialization messages sent")
                sys.stdout.flush()

                backoff_s = 1.0

                # Wait until websocket closes (callback sets the event) or we are asked to stop
                if c.ws_closed_event is not None:
                    await c.ws_closed_event.wait()
                else:
                    # Fallback; should not normally happen
                    while not run_event.is_set():
                        await asyncio.sleep(1)
            except Exception as ex:
                print(f"[WS_INIT] WebSocket error: {ex}")
                sys.stdout.flush()
            finally:
                # Cancel monitor task
                if c.ws_monitor_task and not c.ws_monitor_task.done():
                    c.ws_monitor_task.cancel()
                    try:
                        await c.ws_monitor_task
                    except asyncio.CancelledError:
                        pass
                    c.ws_monitor_task = None
                
                c.setWs(None)

            if run_event.is_set():
                break
            print(f"[WS_INIT] Reconnecting in {backoff_s:.1f}s...")
            sys.stdout.flush()
            await asyncio.sleep(backoff_s)
            backoff_s = min(backoff_s * 2.0, 30.0)

    print("Cleaning up...")
    sys.stdout.flush()
    c.stop()


if __name__ == "__main__":
    try:
        asyncio.run(init_websocket())
    except KeyboardInterrupt:
        print("Interrupted by user")
