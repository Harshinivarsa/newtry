import numpy as np

def format_freq(freq_hz: float) -> str:
    if freq_hz is None or np.isnan(freq_hz) or freq_hz <= 0:
        return "---"
    if freq_hz >= 1e6:
        return f"{freq_hz / 1e6:.3f} MHz"
    elif freq_hz >= 1e3:
        return f"{freq_hz / 1e3:.3f} kHz"
    else:
        return f"{freq_hz:.2f} Hz"

def format_time(time_s: float) -> str:
    if time_s is None or np.isnan(time_s) or time_s <= 0:
        return "---"
    if time_s >= 1.0:
        return f"{time_s:.3f} s"
    elif time_s >= 1e-3:
        return f"{time_s * 1e3:.3f} ms"
    elif time_s >= 1e-6:
        return f"{time_s * 1e6:.2f} us"
    else:
        return f"{time_s * 1e9:.1f} ns"

def format_voltage(v: float) -> str:
    if v is None or np.isnan(v):
        return "---"
    abs_v = abs(v)
    if abs_v >= 1.0 or abs_v == 0.0:
        return f"{v:.3f} V"
    else:
        return f"{v * 1000.0:.1f} mV"

def calculate_measurements(time_s: np.ndarray, voltage_v: np.ndarray) -> dict:
    """
    Calculates Frequency, Period, Vpp, Vmax, Vmin, Vrms, Mean/DC from latest frame data.
    Returns dictionary with raw values and formatted strings.
    """
    empty_result = {
        "vmax": None, "vmax_str": "---",
        "vmin": None, "vmin_str": "---",
        "vpp": None, "vpp_str": "---",
        "vrms": None, "vrms_str": "---",
        "mean": None, "mean_str": "---",
        "period": None, "period_str": "---",
        "freq": None, "freq_str": "---"
    }

    if time_s is None or voltage_v is None or len(voltage_v) < 2:
        return empty_result

    vmax = float(np.max(voltage_v))
    vmin = float(np.min(voltage_v))
    vpp = vmax - vmin
    mean = float(np.mean(voltage_v))
    vrms = float(np.sqrt(np.mean(voltage_v ** 2)))

    res = {
        "vmax": vmax, "vmax_str": format_voltage(vmax),
        "vmin": vmin, "vmin_str": format_voltage(vmin),
        "vpp": vpp, "vpp_str": format_voltage(vpp),
        "vrms": vrms, "vrms_str": format_voltage(vrms),
        "mean": mean, "mean_str": format_voltage(mean),
        "period": None, "period_str": "---",
        "freq": None, "freq_str": "---"
    }

    # Signal peak-to-peak must be reasonable to compute frequency
    if vpp < 0.005:  # Noise threshold 5mV
        return res

    # Frequency: rising crossings of the frame mean, with linear interpolation
    crossings = []
    for i in range(len(voltage_v) - 1):
        v1 = voltage_v[i]
        v2 = voltage_v[i + 1]
        if v1 <= mean and v2 > mean:
            dy = v2 - v1
            if abs(dy) > 1e-12:
                frac = (mean - v1) / dy
                t_cross = time_s[i] + frac * (time_s[i + 1] - time_s[i])
                crossings.append(t_cross)

    if len(crossings) >= 2:
        periods = np.diff(crossings)
        avg_period = float(np.mean(periods))
        if avg_period > 0:
            freq = 1.0 / avg_period
            res["period"] = avg_period
            res["period_str"] = format_time(avg_period)
            res["freq"] = freq
            res["freq_str"] = format_freq(freq)

    return res
