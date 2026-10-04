import json
import os
import logging

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "gen_ip": "127.0.0.1",
    "gen_port": 5000,
    "scope_ip": "127.0.0.1",
    "scope_port": 5001,
    "waveform": "Sine",
    "frequency": 1000.0,
    "vpp": 1.00,
    "offset": 0.00,
    "duty": 50,
    "time_div": "1ms",
    "volt_div": "500mV",
    "vert_pos": 0.0,
    "grid_on": True,
    "trig_mode": "Auto",
    "trig_edge": "Rising",
    "trig_level": 0.0,
    "view_mode": "Scaled",
    "diag_collapsed": False,
    "simulation_mode": False
}


def load_config() -> dict:
    """Loads configuration from config.json. Returns defaults if missing or invalid."""
    if not os.path.exists(CONFIG_FILE):
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            config = DEFAULT_CONFIG.copy()
            config.update(data)
            return config
    except Exception as e:
        logging.error(f"Error loading config.json: {e}")
        return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> bool:
    """Saves dictionary to config.json."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        return True
    except Exception as e:
        logging.error(f"Error saving config.json: {e}")
        return False
