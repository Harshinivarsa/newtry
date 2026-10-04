import socket
import time
import queue
import logging
from PyQt6.QtCore import QThread, pyqtSignal
from protocol import parse_scope_buffer, build_scope_ping

class ScopeThread(QThread):
    connected_signal = pyqtSignal(bool, str)        # connected, endpoint
    status_msg_signal = pyqtSignal(str, bool)       # msg, is_error
    ping_signal = pyqtSignal(float)                  # rtt_ms
    frame_received_signal = pyqtSignal(dict)         # parsed frame dict
    text_reply_signal = pyqtSignal(str)              # text ACK/PONG message
    diag_signal = pyqtSignal(dict)                   # diagnostics update dict

    def __init__(self, ip: str = "127.0.0.1", port: int = 5001, parent=None):
        super().__init__(parent)
        self.ip = ip
        self.port = port
        self.cmd_queue = queue.Queue()
        self._running = False
        self._connected = False
        self._socket = None
        
        # Diagnostics stats
        self.frames_rx = 0
        self.samples_rx = 0
        self.actual_sample_rate = 0
        self.bad_frames = 0
        self.last_ping_ms = -1.0
        self.stream_status = "STOPPED"
        self.ping_start_time = None

    def set_endpoint(self, ip: str, port: int):
        self.ip = ip
        self.port = port

    def send_command(self, cmd: str):
        """Pushes command string to FIFO queue."""
        if not cmd.endswith("\n"):
            cmd += "\n"
        self.cmd_queue.put(cmd)

    def stop(self):
        self._running = False
        self.cmd_queue.put("QUIT\n")
        self.wait(1000)

    def run(self):
        self._running = True
        buffer = bytearray()
        endpoint = f"{self.ip}:{self.port}"

        # Attempt TCP Connection
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(3.0)
            self._socket.connect((self.ip, self.port))
            self._socket.settimeout(0.02) # non-blocking read loop
            self._connected = True
            self.connected_signal.emit(True, endpoint)
            self.status_msg_signal.emit(f"ESP32 #2 Connected to {endpoint}", False)
            logging.info(f"ESP32 #2 connected to {endpoint}")
        except Exception as e:
            self._connected = False
            self.connected_signal.emit(False, endpoint)
            err_msg = f"ESP32 #2 Connection failed ({endpoint}): {e}"
            self.status_msg_signal.emit(err_msg, True)
            logging.error(err_msg)
            self.stream_status = "DISCONNECTED"
            self.emit_diag()
            return

        last_diag_time = time.perf_counter()

        while self._running and self._connected:
            # 1. Send pending commands if any
            try:
                while not self.cmd_queue.empty():
                    cmd_str = self.cmd_queue.get_nowait()
                    if cmd_str.strip() == "QUIT":
                        self._running = False
                        break
                    
                    if cmd_str.strip() == "PING":
                        self.ping_start_time = time.perf_counter()

                    self._socket.sendall(cmd_str.encode("ascii"))
            except Exception as e:
                self._connected = False
                self.connected_signal.emit(False, endpoint)
                self.status_msg_signal.emit(f"ESP32 #2 Send error: {e}", True)
                break

            if not self._running:
                break

            # 2. Read incoming binary socket data
            try:
                chunk = self._socket.recv(4096)
                if not chunk:
                    # Connection closed by server
                    self._connected = False
                    self.connected_signal.emit(False, endpoint)
                    self.status_msg_signal.emit("ESP32 #2 Server closed connection", True)
                    break
                buffer.extend(chunk)
            except socket.timeout:
                pass
            except Exception as e:
                self._connected = False
                self.connected_signal.emit(False, endpoint)
                self.status_msg_signal.emit(f"ESP32 #2 Socket read error: {e}", True)
                break

            # 3. Parse binary messages from buffer
            if len(buffer) > 0:
                messages, buffer, bad_count = parse_scope_buffer(buffer)
                self.bad_frames += bad_count

                for msg_type, content in messages:
                    if msg_type == "text":
                        self.text_reply_signal.emit(content)
                        if content.startswith("PONG"):
                            if self.ping_start_time:
                                rtt_ms = (time.perf_counter() - self.ping_start_time) * 1000.0
                                self.last_ping_ms = rtt_ms
                                self.ping_signal.emit(rtt_ms)
                                self.status_msg_signal.emit(f"ESP32 #2 PONG: {rtt_ms:.1f} ms", False)
                        elif content.startswith("ACK,SET_CAPTURE"):
                            # Device reports what it can REALLY do,
                            # e.g. ESP8266: ACK,SET_CAPTURE,10000,150
                            parts = content.split(",")
                            try:
                                self.actual_sample_rate = int(float(parts[2]))
                                self.status_msg_signal.emit(
                                    f"ESP32 #2 capture: {parts[2]} S/s x {parts[3]} samples", False)
                            except (IndexError, ValueError):
                                self.status_msg_signal.emit(f"ESP32 #2: {content}", False)
                        elif content.startswith("ACK,RUN"):
                            self.stream_status = "STREAMING"
                            self.status_msg_signal.emit("ESP32 #2 Oscilloscope RUNNING", False)
                        elif content.startswith("ACK,STOP"):
                            self.stream_status = "STOPPED"
                            self.status_msg_signal.emit("ESP32 #2 Oscilloscope STOPPED", False)
                        elif content.startswith("ERR"):
                            self.status_msg_signal.emit(f"ESP32 #2 {content}", True)
                        else:
                            self.status_msg_signal.emit(f"ESP32 #2: {content}", False)

                    elif msg_type == "frame":
                        self.frames_rx += 1
                        self.samples_rx += content["n_samples"]
                        self.actual_sample_rate = content["sample_rate_hz"]
                        self.stream_status = "STREAMING"
                        self.frame_received_signal.emit(content)

            # 4. Periodic diagnostics update (every 0.5s)
            now = time.perf_counter()
            if now - last_diag_time >= 0.5:
                self.emit_diag()
                last_diag_time = now

        # Cleanup socket
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        self._connected = False
        self.stream_status = "DISCONNECTED"
        self.connected_signal.emit(False, endpoint)
        self.emit_diag()

    def emit_diag(self):
        self.diag_signal.emit({
            "endpoint": f"{self.ip}:{self.port}",
            "connected": self._connected,
            "frames_rx": self.frames_rx,
            "samples_rx": self.samples_rx,
            "actual_sample_rate": self.actual_sample_rate,
            "stream_status": self.stream_status,
            "bad_frames": self.bad_frames,
            "ping_ms": self.last_ping_ms
        })