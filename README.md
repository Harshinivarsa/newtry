# ESP32 Function Generator + Digital Oscilloscope System

A modern Python 3.10+ desktop bench-instrument application built with **PyQt6**, **pyqtgraph**, and **numpy** for controlling ESP32 DevKit hardware over WiFi TCP/UDP protocols.

---

## Hardware Architecture

- **ESP32 #1 (Function Generator)**: DAC output on GPIO25, TCP server on port 5000 (ASCII line protocol).
- **ESP32 #2 (Digital Oscilloscope)**: ADC input on GPIO34, TCP server on port 5001 (Binary frame protocol + ASCII control).
- **UDP Discovery**: Port 4210 listener replying to broadcast `"DISCOVER_ESP32"`.

---

## Features

- **Dark Bench-Instrument Theme**: Navy `#0f1724` background, cyan trace `#00f0ff`, monospace real-time measurement displays.
- **Precise DAC Scaling (Try 1 Formula)**:
  - Peak squeeze factor calculation: $peak = \max(|OFFSET + VPP/2|, |OFFSET - VPP/2|)$
  - $SCALE = 1.0$ if $peak \le 1.35$ else $1.35 / peak$.
  - Real pin conversion: $virtual\_V = (pin\_mV / 1000.0 - 1.65) / SCALE$.
- **High-Performance Scope Plot**: >= 20 FPS rendering via `setData()` on `pyqtgraph.PlotWidget`.
- **View Modes**: Switch dynamically between **Scaled View** (requested virtual signal) and **Pin View** (real 0-3.3V on GPIO34).
- **Auto Scale & Auto Discover**:
  - Auto Scale computes optimal TIME/DIV and VOLT/DIV for 3 signal cycles and 6 vertical div fill.
  - Auto Discover broadcasts over UDP port 4210 to populate device IP/ports automatically.
- **Comprehensive Measurements**: Real-time linear interpolation mean-crossing algorithm for Frequency, Period, Vpp, Vmax, Vmin, Vrms, and Mean/DC.
- **Collapsible Diagnostics Panel**: Detailed metrics on socket endpoints, buffer status, actual sample rates, ping round-trip times, and FPS.

---

## Installation & Running

### Option 1: Automatic Script (Windows)
Double-click `run.bat` or run in command prompt:
```cmd
run.bat
```
This automatically creates a virtual environment `.venv`, installs all dependencies from `requirements.txt`, and launches `main.py`.

### Option 2: Manual Setup
```bash
python -m venv .venv
# Activate virtual environment:
# Windows:
.venv\Scripts\activate

pip install -r requirements.txt
python main.py
```

---

## Testing with Fake Hardware (Test Server)

You can test the full system without hardware using the included test server:

1. In a separate terminal, launch the fake ESP32 server:
   ```bash
   python tools/fake_esp32.py
   ```
2. Launch the desktop GUI (`python main.py`).
3. Click **AUTO-DISCOVER IPs** or click **CONNECT BOTH** (defaults to `127.0.0.1:5000` and `127.0.0.1:5001`).
4. Modify generator waveform, frequency, amplitude, offset, trigger mode, or view mode and observe real-time waveform updates and measured metrics!

---

## Unit Testing

Run the unit test suite covering binary parser integrity, split buffers, corrupted headers, scaling formulas, pin conversions, and frequency analysis:

```bash
pytest tests/test_protocol.py
```
