import pytest
import numpy as np
import struct
from protocol import (
    calculate_scale,
    virtual_to_pin_v,
    pin_mv_to_virtual_v,
    parse_scope_buffer,
    MAGIC
)
from measurements import calculate_measurements

def test_scale_formula():
    # Peak <= 1.35 -> Scale = 1.0
    assert calculate_scale(1.0, 0.0) == 1.0
    assert calculate_scale(2.0, 0.0) == 1.0  # peak = 1.0 <= 1.35
    assert calculate_scale(2.7, 0.0) == 1.0  # peak = 1.35 <= 1.35

    # Peak > 1.35 -> Scale = 1.35 / peak
    # vpp = 10.0, offset = 0.0 -> peak = 5.0 -> scale = 1.35 / 5.0 = 0.27
    assert pytest.approx(calculate_scale(10.0, 0.0), 1e-6) == 0.27

    # Offset shift
    # vpp = 2.0, offset = 2.0 -> max = 3.0 -> peak = 3.0 -> scale = 1.35 / 3.0 = 0.45
    assert pytest.approx(calculate_scale(2.0, 2.0), 1e-6) == 0.45


def test_virtual_pin_conversions():
    scale = 0.5
    virt_v = 1.0
    # pin_V = 1.65 + 0.5 * 1.0 = 2.15 V
    pin_v = virtual_to_pin_v(virt_v, scale)
    assert pytest.approx(pin_v, 1e-6) == 2.15

    pin_mv = 2150.0
    # virtual_V = (2.150 - 1.65) / 0.5 = 1.0
    virt_calc = pin_mv_to_virtual_v(pin_mv, scale)
    assert pytest.approx(virt_calc, 1e-6) == 1.0


def test_scope_frame_parsing_valid():
    # Create valid frame
    frame_id = 42
    sample_rate_hz = 100000
    n_samples = 100
    flags = 1
    trig_idx = 10
    samples = np.full(n_samples, 1650, dtype=np.uint16)

    payload_hdr = struct.pack("<IIHHH", frame_id, sample_rate_hz, n_samples, flags, trig_idx)
    payload = payload_hdr + samples.tobytes()
    msg_len = len(payload)

    hdr = struct.pack("<HBI", MAGIC, 0x02, msg_len)
    full_msg = bytearray(hdr + payload)

    messages, buf_rem, bad_frames = parse_scope_buffer(full_msg)
    assert len(messages) == 1
    assert bad_frames == 0
    assert len(buf_rem) == 0

    msg_type, content = messages[0]
    assert msg_type == "frame"
    assert content["frame_id"] == 42
    assert content["sample_rate_hz"] == 100000
    assert content["n_samples"] == 100
    assert content["triggered"] is True
    assert content["trigger_index"] == 10
    assert len(content["pin_mv"]) == 100


def test_scope_frame_parsing_split_data():
    frame_id = 1
    sample_rate_hz = 1000
    n_samples = 50
    flags = 0
    trig_idx = 5
    samples = np.full(n_samples, 1000, dtype=np.uint16)

    payload = struct.pack("<IIHHH", frame_id, sample_rate_hz, n_samples, flags, trig_idx) + samples.tobytes()
    full_msg = bytearray(struct.pack("<HBI", MAGIC, 0x02, len(payload)) + payload)

    # Split into 3 chunks
    chunk1 = full_msg[:4]
    chunk2 = full_msg[4:20]
    chunk3 = full_msg[20:]

    buf = bytearray(chunk1)
    msgs1, buf, bad1 = parse_scope_buffer(buf)
    assert len(msgs1) == 0

    buf.extend(chunk2)
    msgs2, buf, bad2 = parse_scope_buffer(buf)
    assert len(msgs2) == 0

    buf.extend(chunk3)
    msgs3, buf, bad3 = parse_scope_buffer(buf)
    assert len(msgs3) == 1
    assert msgs3[0][1]["frame_id"] == 1


def test_scope_frame_parsing_corrupted_data():
    # Inject 5 garbage bytes before a valid frame
    garbage = b"\x00\x11\x22\x33\x44"
    
    frame_id = 99
    payload = struct.pack("<IIHHH", frame_id, 1000, 10, 0, 0) + np.zeros(10, dtype=np.uint16).tobytes()
    valid_frame = struct.pack("<HBI", MAGIC, 0x02, len(payload)) + payload

    buf = bytearray(garbage + valid_frame)
    messages, buf_rem, bad_frames = parse_scope_buffer(buf)

    assert len(messages) == 1
    assert messages[0][1]["frame_id"] == 99
    assert bad_frames >= 1


def test_frequency_measurement_known_sine():
    freq_target = 1000.0  # 1 kHz sine
    sr = 100000           # 100 kHz sample rate
    duration = 0.01       # 10 ms window (10 full cycles)
    
    n_samples = int(sr * duration)
    t = np.arange(n_samples, dtype=np.float64) / sr
    v = 2.5 * np.sin(2.0 * np.pi * freq_target * t) + 0.5  # 2.5V amplitude, 0.5V offset

    res = calculate_measurements(t, v)
    
    assert res["freq"] is not None
    assert pytest.approx(res["freq"], rel=1e-3) == freq_target
    assert pytest.approx(res["vmax"], rel=1e-3) == 3.0
    assert pytest.approx(res["vmin"], rel=1e-3) == -2.0
    assert pytest.approx(res["vpp"], rel=1e-3) == 5.0
