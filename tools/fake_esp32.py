import socket
import struct
import time
import math
import threading
import logging
import numpy as np

logging.basicConfig(level=logging.INFO, format="[FAKE ESP32] %(asctime)s - %(message)s")

MAGIC = 0xA55A

class FakeGenerator:
    def __init__(self, port=5000):
        self.port = port
        self.waveform = "SINE"
        self.freq = 1000.0
        self.vpp = 1.00
        self.offset = 0.00
        self.duty = 50
        self.running = True
        self.scale = 1.0

        self.update_scale()

    def update_scale(self):
        v_max = self.offset + (self.vpp / 2.0)
        v_min = self.offset - (self.vpp / 2.0)
        peak = max(abs(v_max), abs(v_min))
        if peak <= 1.35:
            self.scale = 1.0
        else:
            self.scale = 1.35 / peak

    def process_command(self, cmd_line: str) -> str:
        cmd_line = cmd_line.strip()
        if not cmd_line:
            return ""
        parts = cmd_line.split(",")
        cmd = parts[0].upper()

        if cmd == "PING":
            return "PONG,GEN\n"

        elif cmd == "SET_WAVEFORM":
            if len(parts) >= 6:
                self.waveform = parts[1].upper()
                self.freq = float(parts[2])
                self.vpp = float(parts[3])
                self.offset = float(parts[4])
                self.duty = int(parts[5])
                self.update_scale()
                pin_vpp = self.vpp * self.scale
                pin_center_v = 1.65 + self.offset * self.scale
                return f"ACK,SET_WAVEFORM,{self.scale:.6f},{pin_vpp:.3f},{pin_center_v:.3f}\n"
            return "ERR,INVALID_SET_WAVEFORM_PARAMS\n"

        elif cmd == "START":
            self.running = True
            return "ACK,START\n"

        elif cmd == "STOP":
            self.running = False
            return "ACK,STOP\n"

        elif cmd == "STATUS":
            run_flag = 1 if self.running else 0
            return f"STATUS,{run_flag},{self.waveform},{self.freq:.1f},{self.vpp:.2f},{self.offset:.2f},{self.duty},{self.scale:.6f}\n"

        return "ERR,UNKNOWN_COMMAND\n"

    def run_server(self):
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("0.0.0.0", self.port))
        server_sock.listen(5)
        logging.info(f"Generator TCP server listening on port {self.port}")

        while True:
            client_sock, addr = server_sock.accept()
            logging.info(f"Generator client connected from {addr}")
            buffer = ""
            client_sock.settimeout(0.5)

            while True:
                try:
                    chunk = client_sock.recv(1024).decode("ascii", errors="replace")
                    if not chunk:
                        break
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        reply = self.process_command(line)
                        if reply:
                            client_sock.sendall(reply.encode("ascii"))
                except socket.timeout:
                    continue
                except Exception as e:
                    logging.info(f"Generator client disconnect: {e}")
                    break
            client_sock.close()


class FakeScope:
    def __init__(self, generator: FakeGenerator, port=5001):
        self.generator = generator
        self.port = port
        self.sample_rate_hz = 200000
        self.n_samples = 2000
        self.trig_mode = "AUTO"
        self.trig_edge = "RISING"
        self.trig_level_pin_mv = 1650
        self.streaming = True
        self.frame_id = 0

    def pack_text_msg(self, text: str) -> bytes:
        payload = text.encode("ascii")
        header = struct.pack("<HBI", MAGIC, 0x01, len(payload))
        return header + payload

    def pack_frame_msg(self, frame_id: int, sample_rate_hz: int, n_samples: int,
                       flags: int, trigger_index: int, pin_mv_array: np.ndarray) -> bytes:
        payload_header = struct.pack("<IIHHH", frame_id, sample_rate_hz, n_samples, flags, trigger_index)
        samples_bytes = pin_mv_array.astype(np.uint16).tobytes()
        payload = payload_header + samples_bytes
        header = struct.pack("<HBI", MAGIC, 0x02, len(payload))
        return header + payload

    def generate_waveform(self) -> np.ndarray:
        # Generates array of pin voltages in millivolts
        n = self.n_samples
        sr = self.sample_rate_hz
        dt = 1.0 / sr
        t = np.arange(n, dtype=np.float64) * dt

        if not self.generator.running:
            # Output flat DC at pin_center
            pin_center_v = 1.65 + self.generator.offset * self.generator.scale
            pin_mv = np.full(n, pin_center_v * 1000.0, dtype=np.float64)
        else:
            freq = self.generator.freq
            vpp = self.generator.vpp
            offset = self.generator.offset
            scale = self.generator.scale
            wtype = self.generator.waveform
            duty = self.generator.duty / 100.0

            phase = 2.0 * np.pi * freq * t

            if wtype == "SINE":
                virt_v = (vpp / 2.0) * np.sin(phase) + offset
            elif wtype == "SQUARE":
                # Duty cycle square wave
                norm_phase = (phase / (2.0 * np.pi)) % 1.0
                square_val = np.where(norm_phase < duty, 1.0, -1.0)
                virt_v = (vpp / 2.0) * square_val + offset
            elif wtype == "TRIANGLE":
                # Symmetric triangle wave
                norm_phase = (phase / (2.0 * np.pi)) % 1.0
                tri_val = np.where(norm_phase < 0.5, 4.0 * norm_phase - 1.0, 3.0 - 4.0 * norm_phase)
                virt_v = (vpp / 2.0) * tri_val + offset
            elif wtype == "SAW":
                # Sawtooth wave (-1 to +1)
                norm_phase = (phase / (2.0 * np.pi)) % 1.0
                saw_val = 2.0 * norm_phase - 1.0
                virt_v = (vpp / 2.0) * saw_val + offset
            else:
                virt_v = (vpp / 2.0) * np.sin(phase) + offset

            pin_v = 1.65 + scale * virt_v
            pin_mv = pin_v * 1000.0

        # Add ~5 mV noise
        noise = np.random.normal(0, 5.0, n)
        pin_mv_noisy = np.clip(np.round(pin_mv + noise), 0, 3300).astype(np.uint16)
        return pin_mv_noisy

    def run_server(self):
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("0.0.0.0", self.port))
        server_sock.listen(5)
        logging.info(f"Scope TCP server listening on port {self.port}")

        while True:
            client_sock, addr = server_sock.accept()
            logging.info(f"Scope client connected from {addr}")
            client_sock.settimeout(0.01)
            buffer = ""

            last_frame_time = time.perf_counter()

            while True:
                # 1. Check commands from client
                try:
                    chunk = client_sock.recv(1024).decode("ascii", errors="replace")
                    if not chunk:
                        break
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        reply_bytes = self.process_command(line.strip())
                        if reply_bytes:
                            client_sock.sendall(reply_bytes)
                except socket.timeout:
                    pass
                except Exception as e:
                    logging.info(f"Scope client disconnect: {e}")
                    break

                # 2. Send frame if streaming and ~50ms elapsed (20 FPS)
                now = time.perf_counter()
                if self.streaming and (now - last_frame_time >= 0.05):
                    last_frame_time = now
                    self.frame_id += 1
                    
                    pin_mv = self.generate_waveform()
                    
                    # Perform trigger search
                    trig_idx = 0
                    triggered = False
                    trig_level = self.trig_level_pin_mv
                    
                    if self.trig_edge == "RISING":
                        # Find first index where pin_mv[i] <= trig_level and pin_mv[i+1] > trig_level
                        diffs = np.diff(pin_mv.astype(np.int32))
                        cond = (pin_mv[:-1] <= trig_level) & (pin_mv[1:] > trig_level)
                        matches = np.where(cond)[0]
                        if len(matches) > 0:
                            trig_idx = int(matches[0])
                            triggered = True
                    else:  # FALLING
                        cond = (pin_mv[:-1] >= trig_level) & (pin_mv[1:] < trig_level)
                        matches = np.where(cond)[0]
                        if len(matches) > 0:
                            trig_idx = int(matches[0])
                            triggered = True

                    flags = 0
                    if triggered:
                        flags |= 0x01
                    else:
                        if self.trig_mode == "NORMAL" or self.trig_mode == "SINGLE":
                            # In NORMAL or SINGLE mode, don't send frame if not triggered
                            continue
                        else:  # AUTO mode
                            flags |= 0x02  # auto timeout bit
                            trig_idx = int(round(self.n_samples * 0.1))

                    frame_bytes = self.pack_frame_msg(
                        self.frame_id,
                        self.sample_rate_hz,
                        self.n_samples,
                        flags,
                        trig_idx,
                        pin_mv
                    )

                    try:
                        client_sock.sendall(frame_bytes)
                    except Exception as e:
                        logging.info(f"Scope frame send error: {e}")
                        break

            client_sock.close()

    def process_command(self, cmd_line: str) -> bytes:
        if not cmd_line:
            return b""
        parts = cmd_line.split(",")
        cmd = parts[0].upper()

        if cmd == "PING":
            return self.pack_text_msg("PONG,SCOPE")

        elif cmd == "SET_CAPTURE":
            if len(parts) >= 3:
                self.sample_rate_hz = int(parts[1])
                self.n_samples = int(parts[2])
                return self.pack_text_msg(f"ACK,SET_CAPTURE,{self.sample_rate_hz},{self.n_samples}")
            return self.pack_text_msg("ERR,INVALID_SET_CAPTURE")

        elif cmd == "SET_TRIGGER":
            if len(parts) >= 4:
                self.trig_mode = parts[1].upper()
                self.trig_edge = parts[2].upper()
                self.trig_level_pin_mv = int(parts[3])
                return self.pack_text_msg("ACK,SET_TRIGGER")
            return self.pack_text_msg("ERR,INVALID_SET_TRIGGER")

        elif cmd == "RUN":
            self.streaming = True
            return self.pack_text_msg("ACK,RUN")

        elif cmd == "STOP":
            self.streaming = False
            return self.pack_text_msg("ACK,STOP")

        return self.pack_text_msg("ERR,UNKNOWN_COMMAND")


def run_udp_discovery_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 4210))
    logging.info("UDP Discovery server listening on port 4210")

    while True:
        try:
            data, addr = sock.recvfrom(1024)
            msg = data.decode("ascii", errors="replace").strip()
            if msg == "DISCOVER_ESP32":
                logging.info(f"Discovery request from {addr}")
                # Reply with GEN and SCOPE
                sock.sendto("GEN,127.0.0.1,5000".encode("ascii"), addr)
                sock.sendto("SCOPE,127.0.0.1,5001".encode("ascii"), addr)
        except Exception as e:
            logging.error(f"UDP Discovery error: {e}")


def main():
    gen = FakeGenerator(port=5000)
    scope = FakeScope(generator=gen, port=5001)

    t_gen = threading.Thread(target=gen.run_server, daemon=True)
    t_scope = threading.Thread(target=scope.run_server, daemon=True)
    t_udp = threading.Thread(target=run_udp_discovery_server, daemon=True)

    t_gen.start()
    t_scope.start()
    t_udp.start()

    logging.info("Fake ESP32 Test Servers running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutting down fake ESP32 servers.")

if __name__ == "__main__":
    main()
