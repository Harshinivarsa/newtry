"""
esp32_check.py - test the two ESP32 boards WITHOUT the GUI.

Usage (from the project folder, with the venv):
    .venv\\Scripts\\python.exe esp32_check.py --discover
    .venv\\Scripts\\python.exe esp32_check.py --gen 192.168.1.50 --scope 192.168.1.51

It sets ESP32 #1 to a 1 kHz, 10 Vpp triangle, captures frames from ESP32 #2 and
prints what the scope really measured. Only uses the Python standard library.
"""
import argparse
import math
import socket
import struct
import time

GEN_PORT, SCOPE_PORT, DISC_PORT = 5000, 5001, 4210


# ----------------------------------------------------------------- discovery
def discover(timeout=2.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.3)
    s.sendto(b"DISCOVER_ESP32", ("255.255.255.255", DISC_PORT))
    found, t_end = {}, time.time() + timeout
    while time.time() < t_end:
        try:
            data, _ = s.recvfrom(256)
        except socket.timeout:
            continue
        parts = data.decode(errors="ignore").strip().split(",")
        if len(parts) == 3:
            found[parts[0]] = parts[1]
            print(f"  found {parts[0]:5s} at {parts[1]}:{parts[2]}")
    s.close()
    return found


# ----------------------------------------------------------------- generator
def gen_cmd(sock, line):
    sock.sendall((line + "\n").encode())
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(256)
        if not chunk:
            raise ConnectionError("generator closed the connection")
        buf += chunk
    reply = buf.decode().strip()
    print(f"  GEN  > {line:45s} < {reply}")
    return reply


# ----------------------------------------------------------------- scope
class ScopeReader:
    def __init__(self, sock):
        self.sock, self.buf = sock, bytearray()

    def read_message(self, timeout=3.0):
        t_end = time.time() + timeout
        while True:
            i = self.buf.find(b"\xA5\x5A")
            if i > 0:
                del self.buf[:i]
            if len(self.buf) >= 7 and self.buf[0] == 0xA5:
                mtype = self.buf[2]
                (length,) = struct.unpack_from("<I", self.buf, 3)
                if len(self.buf) >= 7 + length:
                    payload = bytes(self.buf[7:7 + length])
                    del self.buf[:7 + length]
                    return mtype, payload
            if time.time() > t_end:
                return None, None
            self.sock.settimeout(max(0.05, t_end - time.time()))
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError("scope closed the connection")
            self.buf.extend(chunk)

    def cmd(self, line):
        self.sock.sendall((line + "\n").encode())
        while True:
            mtype, payload = self.read_message()
            if mtype is None:
                print(f"  SCOPE> {line:45s} < (no reply!)")
                return None
            if mtype == 0x01:
                reply = payload.decode(errors="ignore")
                print(f"  SCOPE> {line:45s} < {reply}")
                return reply
            # a waveform frame arrived before the reply - ignore it


def parse_frame(payload):
    frame_id, rate, n, flags, trig = struct.unpack_from("<IIHHH", payload, 0)
    samples = struct.unpack_from(f"<{n}H", payload, 14)
    return frame_id, rate, flags, trig, samples


def measure(volts, rate):
    n = len(volts)
    mean = sum(volts) / n
    vmax, vmin = max(volts), min(volts)
    rms = math.sqrt(sum(v * v for v in volts) / n)
    ups = [i + (mean - volts[i]) / (volts[i + 1] - volts[i])
           for i in range(n - 1) if volts[i] < mean <= volts[i + 1]]
    freq = (len(ups) - 1) * rate / (ups[-1] - ups[0]) if len(ups) >= 2 else float("nan")
    return freq, vmax - vmin, vmax, vmin, rms, mean


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", help="ESP32 #1 IP")
    ap.add_argument("--scope", help="ESP32 #2 IP")
    ap.add_argument("--discover", action="store_true", help="find the boards first")
    ap.add_argument("--wave", default="TRIANGLE")
    ap.add_argument("--freq", type=float, default=1000.0)
    ap.add_argument("--vpp", type=float, default=10.0)
    ap.add_argument("--offset", type=float, default=0.0)
    a = ap.parse_args()

    if a.discover or not (a.gen and a.scope):
        print("Discovering boards (UDP broadcast)...")
        found = discover()
        a.gen = a.gen or found.get("GEN")
        a.scope = a.scope or found.get("SCOPE")
        if not (a.gen and a.scope):
            print("Could not find both boards. Pass --gen and --scope IPs from the Serial Monitor.")
            return

    print(f"\n[1] ESP32 #1 generator at {a.gen}:{GEN_PORT}")
    g = socket.create_connection((a.gen, GEN_PORT), timeout=3)
    t0 = time.time(); gen_cmd(g, "PING"); print(f"      ping {1000 * (time.time() - t0):.1f} ms")
    ack = gen_cmd(g, f"SET_WAVEFORM,{a.wave},{a.freq:.1f},{a.vpp:.2f},{a.offset:.2f},50")
    gen_cmd(g, "START")
    gen_cmd(g, "STATUS")
    scale = float(ack.split(",")[2]) if ack.startswith("ACK,SET_WAVEFORM") else 1.0

    print(f"\n[2] ESP32 #2 scope at {a.scope}:{SCOPE_PORT}")
    s = socket.create_connection((a.scope, SCOPE_PORT), timeout=3)
    r = ScopeReader(s)
    t0 = time.time(); r.cmd("PING"); print(f"      ping {1000 * (time.time() - t0):.1f} ms")
    window = 3.0 / a.freq                               # show ~3 periods
    rate = int(min(500000, 2000 / window))
    n = int(max(50, min(4000, round(rate * window))))
    r.cmd(f"SET_CAPTURE,{rate},{n}")
    r.cmd("SET_TRIGGER,AUTO,RISING,1650")
    r.cmd("RUN")

    print("\n[3] Receiving frames for 3 seconds...")
    frames, t_end, last = 0, time.time() + 3, None
    while time.time() < t_end:
        mtype, payload = r.read_message(timeout=2)
        if mtype == 0x02:
            frames += 1
            last = parse_frame(payload)
    if not last:
        print("  NO FRAMES RECEIVED - check the scope Serial Monitor")
        return
    fid, frate, flags, trig, mv = last
    print(f"  frames: {frames} ({frames / 3:.1f}/s), last id {fid}, rate {frate} S/s, "
          f"n {len(mv)}, trigger idx {trig}, {'TRIGGERED' if flags & 1 else 'AUTO (no trigger)'}")

    pin = [m / 1000.0 for m in mv]
    f, vpp, vmax, vmin, rms, mean = measure(pin, frate)
    print(f"\n  PIN voltage (real, on GPIO34): Vmax {vmax:.3f} V  Vmin {vmin:.3f} V  "
          f"Vpp {vpp:.3f} V  mean {mean:.3f} V")
    virt = [(v - 1.65) / scale for v in pin]
    f, vpp, vmax, vmin, rms, mean = measure(virt, frate)
    print(f"  SCALED (scale {scale:.4f}):     freq {f:.2f} Hz  Vpp {vpp:.3f} V  "
          f"Vmax {vmax:.3f}  Vmin {vmin:.3f}  Vrms {rms:.3f}  mean {mean:.3f}")
    print(f"\n  Expected: freq {a.freq:.1f} Hz, Vpp {a.vpp:.2f} V")
    g.close(); s.close()


if __name__ == "__main__":
    main()
