import socket
import time
import queue
import logging
from PyQt6.QtCore import QThread, pyqtSignal
from protocol import parse_generator_reply, build_gen_ping, build_gen_status

class GeneratorThread(QThread):
    connected_signal = pyqtSignal(bool, str)          # connected, endpoint
    status_msg_signal = pyqtSignal(str, bool)         # msg, is_error
    ping_signal = pyqtSignal(float)                    # rtt_ms
    ack_waveform_signal = pyqtSignal(float, float, float) # scale, pin_vpp, pin_center_v
    status_reply_signal = pyqtSignal(bool, str, float, float, float, int, float) # running, wave, freq, vpp, offset, duty, scale
    command_done_signal = pyqtSignal(str, dict)        # cmd, reply_dict
    diag_signal = pyqtSignal(dict)                     # diag update dict

    def __init__(self, ip: str = "127.0.0.1", port: int = 5000, parent=None):
        super().__init__(parent)
        self.ip = ip
        self.port = port
        self.cmd_queue = queue.Queue()
        self._running = False
        self._connected = False
        self._socket = None
        self.last_command = "None"
        self.last_reply = "None"
        self.current_scale = 1.0
        self.last_ping_ms = -1.0

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
        buffer = ""

        # Attempt TCP Connection
        endpoint = f"{self.ip}:{self.port}"
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(3.0)
            self._socket.connect((self.ip, self.port))
            self._socket.settimeout(0.5)
            self._connected = True
            self.connected_signal.emit(True, endpoint)
            self.status_msg_signal.emit(f"ESP32 #1 Connected to {endpoint}", False)
            logging.info(f"ESP32 #1 connected to {endpoint}")
            
            # Immediately request STATUS upon connecting
            self.send_command(build_gen_status())
        except Exception as e:
            self._connected = False
            self.connected_signal.emit(False, endpoint)
            err_msg = f"ESP32 #1 Connection failed ({endpoint}): {e}"
            self.status_msg_signal.emit(err_msg, True)
            logging.error(err_msg)
            self.emit_diag()
            return

        while self._running and self._connected:
            # Send next queued command if available
            try:
                cmd_str = self.cmd_queue.get(timeout=0.05)
                if cmd_str.strip() == "QUIT":
                    break
                
                start_time = time.perf_counter()
                self.last_command = cmd_str.strip()
                
                # Send command over TCP
                try:
                    self._socket.sendall(cmd_str.encode("ascii"))
                except Exception as e:
                    self._connected = False
                    self.connected_signal.emit(False, endpoint)
                    self.status_msg_signal.emit(f"ESP32 #1 Send error: {e}", True)
                    break

                # Read reply line with 2.0s overall timeout
                reply_line = ""
                recv_start = time.perf_counter()
                reply_received = False

                while time.perf_counter() - recv_start < 2.0:
                    if "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        reply_line = line.strip()
                        reply_received = True
                        break
                    try:
                        chunk = self._socket.recv(1024).decode("ascii", errors="replace")
                        if not chunk:
                            # Socket closed
                            self._connected = False
                            break
                        buffer += chunk
                    except socket.timeout:
                        continue
                    except Exception as e:
                        self._connected = False
                        break

                if not self._connected:
                    self.connected_signal.emit(False, endpoint)
                    self.status_msg_signal.emit("ESP32 #1 Disconnected unexpectedly", True)
                    break

                if not reply_received:
                    err_msg = f"ESP32 #1 Timeout waiting for reply to {self.last_command}"
                    self.last_reply = "TIMEOUT"
                    self.status_msg_signal.emit(err_msg, True)
                    self.emit_diag()
                    continue

                rtt_ms = (time.perf_counter() - start_time) * 1000.0
                self.last_reply = reply_line
                
                # Parse reply
                reply_dict = parse_generator_reply(reply_line)
                self.command_done_signal.emit(self.last_command, reply_dict)

                if reply_dict["type"] == "PONG":
                    self.last_ping_ms = rtt_ms
                    self.ping_signal.emit(rtt_ms)
                    self.status_msg_signal.emit(f"ESP32 #1 PONG: {rtt_ms:.1f} ms", False)

                elif reply_dict["type"] == "ACK_SET_WAVEFORM":
                    scale = reply_dict.get("scale", 1.0)
                    pin_vpp = reply_dict.get("pin_vpp", 1.0)
                    pin_center_v = reply_dict.get("pin_center_v", 1.65)
                    self.current_scale = scale
                    self.ack_waveform_signal.emit(scale, pin_vpp, pin_center_v)
                    self.status_msg_signal.emit(f"ESP32 #1 ACK: SET_WAVEFORM (scale x{scale:.3f})", False)

                elif reply_dict["type"] == "STATUS":
                    scale = reply_dict.get("scale", 1.0)
                    self.current_scale = scale
                    self.status_reply_signal.emit(
                        reply_dict["running"],
                        reply_dict["waveform"],
                        reply_dict["freq"],
                        reply_dict["vpp"],
                        reply_dict["offset"],
                        reply_dict["duty"],
                        scale
                    )
                    self.status_msg_signal.emit("ESP32 #1 Status updated", False)

                elif reply_dict["type"] == "ACK_START":
                    self.status_msg_signal.emit("ESP32 #1 Generator STARTED", False)

                elif reply_dict["type"] == "ACK_STOP":
                    self.status_msg_signal.emit("ESP32 #1 Generator STOPPED", False)

                elif reply_dict["type"] == "ERR":
                    self.status_msg_signal.emit(f"ESP32 #1 ERR: {reply_dict.get('message', '')}", True)

                self.emit_diag()

            except queue.Empty:
                pass

        # Cleanup socket
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        self._connected = False
        self.connected_signal.emit(False, endpoint)
        self.emit_diag()

    def emit_diag(self):
        self.diag_signal.emit({
            "endpoint": f"{self.ip}:{self.port}",
            "connected": self._connected,
            "last_cmd": self.last_command,
            "last_reply": self.last_reply,
            "current_scale": self.current_scale,
            "ping_ms": self.last_ping_ms
        })
