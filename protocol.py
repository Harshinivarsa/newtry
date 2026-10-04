import struct
import numpy as np

# Waveform type strings mapping
WAVEFORM_TYPE_MAP = {
    "Sine": "SINE",
    "Square": "SQUARE",
    "Triangle": "TRIANGLE",
    "Sawtooth": "SAW",
    "SINE": "SINE",
    "SQUARE": "SQUARE",
    "TRIANGLE": "TRIANGLE",
    "SAW": "SAW"
}

REVERSE_WAVEFORM_MAP = {
    "SINE": "Sine",
    "SQUARE": "Square",
    "TRIANGLE": "Triangle",
    "SAW": "Sawtooth"
}

# --- ESP32 #1 Command Builders ---

def build_gen_ping() -> str:
    return "PING\n"

def build_gen_set_waveform(waveform_type: str, freq_hz: float, vpp: float, offset_v: float, duty_pct: int) -> str:
    type_str = WAVEFORM_TYPE_MAP.get(waveform_type, "SINE")
    return f"SET_WAVEFORM,{type_str},{freq_hz:.1f},{vpp:.2f},{offset_v:.2f},{duty_pct}\n"

def build_gen_start() -> str:
    return "START\n"

def build_gen_stop() -> str:
    return "STOP\n"

def build_gen_status() -> str:
    return "STATUS\n"

def parse_generator_reply(line: str) -> dict:
    """Parses a single line ASCII reply from ESP32 #1."""
    line = line.strip()
    if not line:
        return {"type": "EMPTY"}
    parts = line.split(",")
    cmd = parts[0].upper()

    if cmd == "PONG":
        return {"type": "PONG", "device": parts[1] if len(parts) > 1 else "GEN"}

    elif cmd == "ACK":
        if len(parts) > 1:
            sub = parts[1].upper()
            if sub == "SET_WAVEFORM":
                scale = float(parts[2]) if len(parts) > 2 else 1.0
                pin_vpp = float(parts[3]) if len(parts) > 3 else 1.0
                pin_center_v = float(parts[4]) if len(parts) > 4 else 1.65
                return {
                    "type": "ACK_SET_WAVEFORM",
                    "scale": scale,
                    "pin_vpp": pin_vpp,
                    "pin_center_v": pin_center_v
                }
            elif sub == "START":
                return {"type": "ACK_START"}
            elif sub == "STOP":
                return {"type": "ACK_STOP"}
            else:
                return {"type": "ACK", "sub": sub}
        return {"type": "ACK"}

    elif cmd == "STATUS":
        if len(parts) >= 8:
            return {
                "type": "STATUS",
                "running": parts[1] == "1" or parts[1].lower() == "true",
                "waveform": REVERSE_WAVEFORM_MAP.get(parts[2].upper(), parts[2]),
                "freq": float(parts[3]),
                "vpp": float(parts[4]),
                "offset": float(parts[5]),
                "duty": int(parts[6]),
                "scale": float(parts[7])
            }
        return {"type": "STATUS_RAW", "raw": line}

    elif cmd == "ERR":
        return {"type": "ERR", "message": ",".join(parts[1:])}

    return {"type": "UNKNOWN", "raw": line}


# --- Scaling Functions ---

def calculate_scale(vpp: float, offset_v: float) -> float:
    """Calculates DAC scaling factor for requested signal peak voltage."""
    v_max = offset_v + (vpp / 2.0)
    v_min = offset_v - (vpp / 2.0)
    peak = max(abs(v_max), abs(v_min))
    if peak <= 1.35:
        return 1.0
    else:
        return 1.35 / peak

def virtual_to_pin_v(virtual_v: float, scale: float) -> float:
    return 1.65 + scale * virtual_v

def pin_mv_to_virtual_v(pin_mv: float, scale: float) -> float:
    scale_effective = scale if scale > 0.00001 else 1.0
    return (pin_mv / 1000.0 - 1.65) / scale_effective

def pin_v_to_virtual_v(pin_v: float, scale: float) -> float:
    scale_effective = scale if scale > 0.00001 else 1.0
    return (pin_v - 1.65) / scale_effective

def calculate_trigger_level_pin_mv(level_v: float, scale: float) -> int:
    pin_v = virtual_to_pin_v(level_v, scale)
    pin_mv = round(pin_v * 1000.0)
    return max(0, min(3300, pin_mv))


# --- ESP32 #2 Scope Commands ---

def build_scope_ping() -> str:
    return "PING\n"

def build_scope_set_capture(sample_rate_hz: int, n_samples: int) -> str:
    return f"SET_CAPTURE,{int(sample_rate_hz)},{int(n_samples)}\n"

def build_scope_set_trigger(mode: str, edge: str, level_pin_mv: int) -> str:
    mode_str = mode.upper()
    edge_str = edge.upper()
    return f"SET_TRIGGER,{mode_str},{edge_str},{int(level_pin_mv)}\n"

def build_scope_run() -> str:
    return "RUN\n"

def build_scope_stop() -> str:
    return "STOP\n"


# --- Binary Frame Protocol Parser (ESP32 #2) ---

# Every message from the scope starts with the bytes A5 5A on the wire.
# Read as a little-endian uint16 (byte0 | byte1 << 8) that is 0x5AA5.
# (struct.pack("<H", MAGIC) also produces A5 5A, so fakes/tests stay consistent.)
MAGIC = 0x5AA5
MAGIC_BYTES = b"\xA5\x5A"

def parse_scope_buffer(buffer: bytearray):
    """
    Parses complete binary frames from buffer.
    Returns list of parsed messages:
      ('text', text_string)
      ('frame', frame_dict)
    Returns updated buffer with unused bytes, and count of bad frames found.
    A "bad frame" is counted once per resynchronisation, not once per byte.
    """
    messages = []
    bad_frames = 0

    while len(buffer) >= 7:
        # Find the start marker A5 5A
        idx = buffer.find(MAGIC_BYTES)
        if idx < 0:
            # No marker at all: drop everything except the last byte
            # (it may be the first half of a marker split across reads)
            bad_frames += 1
            del buffer[:-1]
            break
        if idx > 0:
            # Garbage before the marker: skip it in one go
            bad_frames += 1
            del buffer[:idx]
            continue

        msg_type = buffer[2]
        payload_len = struct.unpack("<I", buffer[3:7])[0]

        if payload_len > 200000:
            # Impossible payload size: not a real marker, skip it
            bad_frames += 1
            del buffer[:2]
            continue

        total_msg_len = 7 + payload_len
        if len(buffer) < total_msg_len:
            # Incomplete message, wait for more data
            break

        # Complete header + payload available
        payload = buffer[7:total_msg_len]
        del buffer[:total_msg_len]

        if msg_type == 0x01:
            # Text reply
            try:
                text = payload.decode("ascii", errors="replace").strip()
                messages.append(("text", text))
            except Exception:
                bad_frames += 1

        elif msg_type == 0x02:
            # Waveform frame
            # Header payload: frame_id(I), sample_rate_hz(I), n_samples(H), flags(H), trigger_index(H) -> 14 bytes
            if payload_len < 14:
                bad_frames += 1
                continue

            frame_id, sample_rate_hz, n_samples, flags, trigger_index = struct.unpack("<IIHHH", payload[:14])
            expected_payload_len = 14 + n_samples * 2

            if payload_len != expected_payload_len:
                bad_frames += 1
                continue

            # Read n_samples uint16
            samples_bytes = payload[14:expected_payload_len]
            pin_mv = np.frombuffer(samples_bytes, dtype=np.uint16)

            frame_dict = {
                "frame_id": frame_id,
                "sample_rate_hz": sample_rate_hz,
                "n_samples": n_samples,
                "flags": flags,
                "triggered": bool(flags & 0x01),
                "auto_timeout": bool(flags & 0x02),
                "trigger_index": trigger_index,
                "pin_mv": pin_mv
            }
            messages.append(("frame", frame_dict))

        else:
            # Unknown message type
            bad_frames += 1

    return messages, buffer, bad_frames


# --- Helper Units Converters ---

TIME_DIV_VALUES = {
    "10us": 10e-6,
    "20us": 20e-6,
    "50us": 50e-6,
    "100us": 100e-6,
    "200us": 200e-6,
    "500us": 500e-6,
    "1ms": 1e-3,
    "2ms": 2e-3,
    "5ms": 5e-3,
    "10ms": 10e-3,
    "20ms": 20e-3,
    "50ms": 50e-3,
    "100ms": 100e-3
}

VOLT_DIV_VALUES = {
    "10mV": 0.010,
    "20mV": 0.020,
    "50mV": 0.050,
    "100mV": 0.100,
    "200mV": 0.200,
    "500mV": 0.500,
    "1V": 1.0,
    "2V": 2.0,
    "5V": 5.0,
    "10V": 10.0,
    "20V": 20.0
}

def time_str_to_seconds(time_str: str) -> float:
    return TIME_DIV_VALUES.get(time_str, 1e-3)

def volt_str_to_volts(volt_str: str) -> float:
    return VOLT_DIV_VALUES.get(volt_str, 0.5)

def calc_capture_params(time_per_div: float) -> tuple[int, int]:
    """
    Computes sample_rate_hz and n_samples from time_per_div.
      window_s = 10 * time_per_div
      N = 2000
      sample_rate = N / window_s
      if sample_rate > 500000: sample_rate = 500000, N = round(500000 * window_s)
      N clamped to [50, 4000]
    """
    window_s = 10.0 * time_per_div
    N = 2000
    sample_rate = N / window_s
    if sample_rate > 500000:
        sample_rate = 500000.0
        N = round(sample_rate * window_s)
    
    N = max(50, min(4000, int(N)))
    sample_rate_hz = int(round(N / window_s))
    if sample_rate_hz > 500000:
        sample_rate_hz = 500000
    return sample_rate_hz, N

def calc_time_axis(n_samples: int, sample_rate_hz: int, trigger_index: int, time_per_div: float) -> np.ndarray:
    """
    Time axis: t[i] = (i - trigger_index) / sample_rate_hz + (trigger position offset),
    with the trigger point placed at 10% of screen width (0.1 * 10 * time_per_div = 1 * time_per_div).
    """
    if sample_rate_hz <= 0:
        sample_rate_hz = 1000
    window_s = 10.0 * time_per_div
    trigger_pos_offset = 0.1 * window_s
    indices = np.arange(n_samples, dtype=np.float64)
    t = (indices - trigger_index) / float(sample_rate_hz) + trigger_pos_offset
    return t