import os
import csv
import time
import socket
import logging
import numpy as np

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QRectF
from PyQt6.QtGui import QFont, QColor, QIcon, QKeySequence, QAction, QPixmap
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QPushButton, QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox,
    QCheckBox, QFileDialog, QMessageBox, QFrame, QSplitter, QStatusBar,
    QStyleOption, QStyle, QApplication, QDialog, QFormLayout
)

import pyqtgraph as pg

from config import load_config, save_config
from protocol import (
    build_gen_set_waveform, build_gen_start, build_gen_stop, build_gen_ping, build_gen_status,
    build_scope_set_capture, build_scope_set_trigger, build_scope_run, build_scope_stop, build_scope_ping,
    calculate_scale, virtual_to_pin_v, pin_mv_to_virtual_v, calculate_trigger_level_pin_mv,
    time_str_to_seconds, volt_str_to_volts, calc_capture_params, calc_time_axis,
    TIME_DIV_VALUES, VOLT_DIV_VALUES
)
from measurements import calculate_measurements
from net_generator import GeneratorThread
from net_scope import ScopeThread

# Set PyQtGraph configuration
pg.setConfigOption('background', '#0a0f1a')
pg.setConfigOption('foreground', '#94a3b8')
pg.setConfigOption('antialias', True)


class AutoDiscoverThread(QThread):
    discovered_signal = pyqtSignal(dict)  # {'gen': (ip, port), 'scope': (ip, port)}
    status_signal = pyqtSignal(str, bool)

    def run(self):
        found = {}
        self.status_signal.emit("Searching for ESP32 devices on network...", False)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(2.0)
            
            # Send broadcast to 255.255.255.255 port 4210
            sock.sendto(b"DISCOVER_ESP32", ("255.255.255.255", 4210))
            
            start_t = time.time()
            while time.time() - start_t < 2.0:
                try:
                    data, addr = sock.recvfrom(1024)
                    text = data.decode("ascii", errors="ignore").strip()
                    parts = text.split(",")
                    if parts[0] == "GEN" and len(parts) >= 3:
                        found["gen"] = (parts[1], int(parts[2]))
                    elif parts[0] == "SCOPE" and len(parts) >= 3:
                        found["scope"] = (parts[1], int(parts[2]))
                except socket.timeout:
                    break
                except Exception:
                    break
            sock.close()
        except Exception as e:
            self.status_signal.emit(f"Auto-discover error: {e}", True)
            return

        self.discovered_signal.emit(found)


class CollapsibleBox(QWidget):
    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.toggle_button = QPushButton(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #38bdf8;
                font-weight: bold;
                text-align: left;
                padding: 6px 10px;
                border: 1px solid #334155;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #334155;
            }
        """)
        self.toggle_button.clicked.connect(self.on_toggle)

        self.content_area = QWidget()
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(2)
        self.main_layout.addWidget(self.toggle_button)
        self.main_layout.addWidget(self.content_area)

    def setContentLayout(self, layout):
        self.content_area.setLayout(layout)

    def on_toggle(self, checked):
        self.content_area.setVisible(checked)
        title = self.toggle_button.text()
        if "(CLICK TO COLLAPSE)" in title and not checked:
            self.toggle_button.setText(title.replace("(CLICK TO COLLAPSE)", "(CLICK TO EXPAND)"))
        elif "(CLICK TO EXPAND)" in title and checked:
            self.toggle_button.setText(title.replace("(CLICK TO EXPAND)", "(CLICK TO COLLAPSE)"))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESP32 FUNCTION GENERATOR + DIGITAL OSCILLOSCOPE SYSTEM")
        self.resize(1350, 920)
        self.setMinimumSize(1100, 750)

        self.config = load_config()
        self.gen_thread = None
        self.scope_thread = None
        self.discover_thread = None

        # Data & State variables
        self.current_scale = 1.0
        self.scale_confirmed_by_hw = False
        self.view_mode = self.config.get("view_mode", "Scaled")
        self.simulation_mode = self.config.get("simulation_mode", False)
        self.last_frame_data = None
        self.last_time_axis = None
        self.last_voltage_axis = None

        # FPS calculation
        self.frame_count = 0
        self.last_fps_calc_t = time.perf_counter()
        self.current_fps = 0.0

        # Simulation generator parameters
        self.sim_sim_t = 0.0

        # UI Setup
        self.init_stylesheet()
        self.init_menu_bar()
        self.init_ui()

        # Frame refresh timer (60 FPS max limit to trigger plot redrawing if buffer ready)
        self.gui_timer = QTimer(self)
        self.gui_timer.setInterval(16) # ~60 FPS
        self.gui_timer.timeout.connect(self.on_gui_timer_tick)
        self.gui_timer.start()

        # Connect network clients based on config or initial state
        self.update_generator_scale_info()

    def init_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0f1724;
            }
            QWidget {
                color: #e2e8f0;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 13px;
            }
            QGroupBox {
                background-color: #162032;
                border: 1px solid #1e293b;
                border-radius: 6px;
                margin-top: 12px;
                font-weight: bold;
                color: #00f0ff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 2px 8px;
                color: #38bdf8;
            }
            QLabel {
                color: #cbd5e1;
            }
            QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {
                background-color: #0b1320;
                color: #00f0ff;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 4px 6px;
                selection-background-color: #0284c7;
            }
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {
                border: 1px solid #00f0ff;
            }
            QPushButton {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #334155;
                border-color: #00f0ff;
            }
            QPushButton:pressed {
                background-color: #0f172a;
            }
            QPushButton#btnConnect {
                background-color: #059669;
                border-color: #10b981;
            }
            QPushButton#btnConnect:hover {
                background-color: #10b981;
            }
            QPushButton#btnDisconnect {
                background-color: #dc2626;
                border-color: #ef4444;
            }
            QPushButton#btnDisconnect:hover {
                background-color: #ef4444;
            }
            QPushButton#btnApply {
                background-color: #0284c7;
                border-color: #38bdf8;
            }
            QPushButton#btnApply:hover {
                background-color: #38bdf8;
                color: #0f1724;
            }
            QPushButton#btnStart {
                background-color: #16a34a;
                border-color: #22c55e;
            }
            QPushButton#btnStart:hover {
                background-color: #22c55e;
                color: #0f1724;
            }
            QPushButton#btnStop {
                background-color: #b91c1c;
                border-color: #f87171;
            }
            QPushButton#btnStop:hover {
                background-color: #ef4444;
            }
            QPushButton#btnDiscover {
                background-color: #7c3aed;
                border-color: #a78bfa;
            }
            QPushButton#btnDiscover:hover {
                background-color: #8b5cf6;
            }
            QStatusBar {
                background-color: #0b1320;
                color: #94a3b8;
                border-top: 1px solid #1e293b;
            }
            QMenuBar {
                background-color: #0b1320;
                color: #e2e8f0;
                border-bottom: 1px solid #1e293b;
            }
            QMenuBar::item:selected {
                background-color: #1e293b;
                color: #00f0ff;
            }
            QMenu {
                background-color: #162032;
                color: #e2e8f0;
                border: 1px solid #334155;
            }
            QMenu::item:selected {
                background-color: #0284c7;
                color: #ffffff;
            }
        """)

    def init_menu_bar(self):
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("&File")

        act_save_cfg = QAction("&Save Config", self)
        act_save_cfg.setShortcut("Ctrl+S")
        act_save_cfg.triggered.connect(self.on_save_config)
        file_menu.addAction(act_save_cfg)

        act_load_cfg = QAction("&Load Config", self)
        act_load_cfg.setShortcut("Ctrl+O")
        act_load_cfg.triggered.connect(self.on_load_config)
        file_menu.addAction(act_load_cfg)

        file_menu.addSeparator()

        act_export_csv = QAction("Export &CSV...", self)
        act_export_csv.setShortcut("Ctrl+E")
        act_export_csv.triggered.connect(self.on_export_csv)
        file_menu.addAction(act_export_csv)

        act_screenshot = QAction("Save &Screenshot PNG...", self)
        act_screenshot.setShortcut("Ctrl+P")
        act_screenshot.triggered.connect(self.on_save_screenshot)
        file_menu.addAction(act_screenshot)

        file_menu.addSeparator()

        act_exit = QAction("E&xit", self)
        act_exit.setShortcut("Alt+F4")
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        # View Menu
        view_menu = menubar.addMenu("&View")

        self.act_diag = QAction("Toggle &Diagnostics", self, checkable=True)
        self.act_diag.setChecked(not self.config.get("diag_collapsed", False))
        self.act_diag.triggered.connect(self.on_toggle_diagnostics_menu)
        view_menu.addAction(self.act_diag)

        self.act_sim_mode = QAction("&Simulation Mode", self, checkable=True)
        self.act_sim_mode.setChecked(self.simulation_mode)
        self.act_sim_mode.triggered.connect(self.on_toggle_sim_mode)
        view_menu.addAction(self.act_sim_mode)

        view_menu.addSeparator()

        self.act_scaled_view = QAction("Scaled View (Virtual V)", self, checkable=True)
        self.act_pin_view = QAction("Pin View (Real 0-3.3V)", self, checkable=True)
        
        if self.view_mode == "Pin":
            self.act_pin_view.setChecked(True)
        else:
            self.act_scaled_view.setChecked(True)

        self.act_scaled_view.triggered.connect(lambda: self.set_view_mode("Scaled"))
        self.act_pin_view.triggered.connect(lambda: self.set_view_mode("Pin"))

        view_menu.addAction(self.act_scaled_view)
        view_menu.addAction(self.act_pin_view)

        # Settings Menu
        settings_menu = menubar.addMenu("&Settings")
        act_network = QAction("&Network Settings...", self)
        act_network.triggered.connect(self.on_network_settings_dialog)
        settings_menu.addAction(act_network)

        # Help Menu
        help_menu = menubar.addMenu("&Help")
        act_about = QAction("&About", self)
        act_about.triggered.connect(self.on_about_dialog)
        help_menu.addAction(act_about)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # --- TOP ROW: Connection Panels ---
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        # ESP32 #1 Connection Panel
        group_gen_conn = QGroupBox("ESP32 #1 - FUNCTION GENERATOR")
        layout_gen_c = QHBoxLayout(group_gen_conn)
        layout_gen_c.setContentsMargins(8, 8, 8, 8)
        
        layout_gen_c.addWidget(QLabel("IP:"))
        self.txt_gen_ip = QLineEdit(self.config.get("gen_ip", "127.0.0.1"))
        self.txt_gen_ip.setFixedWidth(110)
        layout_gen_c.addWidget(self.txt_gen_ip)

        layout_gen_c.addWidget(QLabel("Port:"))
        self.spn_gen_port = QSpinBox()
        self.spn_gen_port.setRange(1, 65535)
        self.spn_gen_port.setValue(self.config.get("gen_port", 5000))
        self.spn_gen_port.setFixedWidth(70)
        layout_gen_c.addWidget(self.spn_gen_port)

        self.btn_gen_connect = QPushButton("CONNECT")
        self.btn_gen_connect.setObjectName("btnConnect")
        self.btn_gen_connect.clicked.connect(self.on_connect_gen)
        layout_gen_c.addWidget(self.btn_gen_connect)

        self.btn_gen_disconnect = QPushButton("DISCONNECT")
        self.btn_gen_disconnect.setObjectName("btnDisconnect")
        self.btn_gen_disconnect.clicked.connect(self.on_disconnect_gen)
        layout_gen_c.addWidget(self.btn_gen_disconnect)

        self.btn_gen_ping = QPushButton("PING")
        self.btn_gen_ping.clicked.connect(self.on_ping_gen)
        layout_gen_c.addWidget(self.btn_gen_ping)

        self.lbl_gen_status = QLabel("DISCONNECTED")
        self.lbl_gen_status.setStyleSheet("color: #94a3b8; font-weight: bold;")
        layout_gen_c.addWidget(self.lbl_gen_status)

        top_row.addWidget(group_gen_conn, 4)

        # ESP32 #2 Connection Panel
        group_scope_conn = QGroupBox("ESP32 #2 - OSCILLOSCOPE")
        layout_scope_c = QHBoxLayout(group_scope_conn)
        layout_scope_c.setContentsMargins(8, 8, 8, 8)

        layout_scope_c.addWidget(QLabel("IP:"))
        self.txt_scope_ip = QLineEdit(self.config.get("scope_ip", "127.0.0.1"))
        self.txt_scope_ip.setFixedWidth(110)
        layout_scope_c.addWidget(self.txt_scope_ip)

        layout_scope_c.addWidget(QLabel("Port:"))
        self.spn_scope_port = QSpinBox()
        self.spn_scope_port.setRange(1, 65535)
        self.spn_scope_port.setValue(self.config.get("scope_port", 5001))
        self.spn_scope_port.setFixedWidth(70)
        layout_scope_c.addWidget(self.spn_scope_port)

        self.btn_scope_connect = QPushButton("CONNECT")
        self.btn_scope_connect.setObjectName("btnConnect")
        self.btn_scope_connect.clicked.connect(self.on_connect_scope)
        layout_scope_c.addWidget(self.btn_scope_connect)

        self.btn_scope_disconnect = QPushButton("DISCONNECT")
        self.btn_scope_disconnect.setObjectName("btnDisconnect")
        self.btn_scope_disconnect.clicked.connect(self.on_disconnect_scope)
        layout_scope_c.addWidget(self.btn_scope_disconnect)

        self.btn_scope_ping = QPushButton("PING")
        self.btn_scope_ping.clicked.connect(self.on_ping_scope)
        layout_scope_c.addWidget(self.btn_scope_ping)

        self.lbl_scope_status = QLabel("DISCONNECTED")
        self.lbl_scope_status.setStyleSheet("color: #94a3b8; font-weight: bold;")
        layout_scope_c.addWidget(self.lbl_scope_status)

        top_row.addWidget(group_scope_conn, 4)

        # Dual Connection Control stacked buttons
        layout_both = QVBoxLayout()
        layout_both.setSpacing(4)
        
        self.btn_both_connect = QPushButton("CONNECT BOTH")
        self.btn_both_connect.setObjectName("btnConnect")
        self.btn_both_connect.clicked.connect(self.on_connect_both)
        layout_both.addWidget(self.btn_both_connect)

        self.btn_both_disconnect = QPushButton("DISCONNECT BOTH")
        self.btn_both_disconnect.setObjectName("btnDisconnect")
        self.btn_both_disconnect.clicked.connect(self.on_disconnect_both)
        layout_both.addWidget(self.btn_both_disconnect)

        self.btn_discover = QPushButton("AUTO-DISCOVER IPs")
        self.btn_discover.setObjectName("btnDiscover")
        self.btn_discover.clicked.connect(self.on_auto_discover)
        layout_both.addWidget(self.btn_discover)

        top_row.addLayout(layout_both, 2)
        main_layout.addLayout(top_row)

        # --- MAIN BODY: LEFT (Generator) & RIGHT (Oscilloscope) ---
        body_layout = QHBoxLayout()
        body_layout.setSpacing(10)

        # LEFT PANEL: Function Generator
        group_gen_ctrl = QGroupBox("FUNCTION GENERATOR (ESP32 #1)")
        layout_gen_f = QVBoxLayout(group_gen_ctrl)
        layout_gen_f.setSpacing(12)

        form_gen = QFormLayout()
        form_gen.setSpacing(10)

        self.cmb_waveform = QComboBox()
        self.cmb_waveform.addItems(["Sine", "Square", "Triangle", "Sawtooth"])
        self.cmb_waveform.setCurrentText(self.config.get("waveform", "Sine"))
        self.cmb_waveform.currentTextChanged.connect(self.on_waveform_changed)
        form_gen.addRow("WAVEFORM:", self.cmb_waveform)

        self.spn_freq = QDoubleSpinBox()
        self.spn_freq.setRange(1.0, 10000.0)
        self.spn_freq.setDecimals(1)
        self.spn_freq.setSingleStep(10.0)
        self.spn_freq.setSuffix(" Hz")
        self.spn_freq.setValue(float(self.config.get("frequency", 1000.0)))
        self.spn_freq.valueChanged.connect(self.update_generator_scale_info)
        form_gen.addRow("FREQUENCY (Hz):", self.spn_freq)

        self.spn_vpp = QDoubleSpinBox()
        self.spn_vpp.setRange(0.00, 100.00)
        self.spn_vpp.setDecimals(2)
        self.spn_vpp.setSingleStep(0.1)
        self.spn_vpp.setSuffix(" Vpp")
        self.spn_vpp.setValue(float(self.config.get("vpp", 1.00)))
        self.spn_vpp.valueChanged.connect(self.update_generator_scale_info)
        form_gen.addRow("AMPLITUDE (Vpp):", self.spn_vpp)

        self.spn_offset = QDoubleSpinBox()
        self.spn_offset.setRange(-50.00, 50.00)
        self.spn_offset.setDecimals(2)
        self.spn_offset.setSingleStep(0.1)
        self.spn_offset.setSuffix(" V")
        self.spn_offset.setValue(float(self.config.get("offset", 0.00)))
        self.spn_offset.valueChanged.connect(self.update_generator_scale_info)
        form_gen.addRow("OFFSET (V):", self.spn_offset)

        self.spn_duty = QSpinBox()
        self.spn_duty.setRange(1, 99)
        self.spn_duty.setSuffix(" %")
        self.spn_duty.setValue(int(self.config.get("duty", 50)))
        self.spn_duty.setEnabled(self.cmb_waveform.currentText() == "Square")
        form_gen.addRow("DUTY CYCLE (%):", self.spn_duty)

        layout_gen_f.addLayout(form_gen)

        # Scale Info Label
        self.lbl_gen_info = QLabel("Scale: x1.000 | Pin: 1.00 Vpp @ 1.65 V")
        self.lbl_gen_info.setStyleSheet("color: #38bdf8; font-family: 'Consolas', monospace; font-size: 11px;")
        self.lbl_gen_info.setWordWrap(True)
        layout_gen_f.addWidget(self.lbl_gen_info)

        self.lbl_scale_warning = QLabel("Scale not confirmed by ESP32 #1")
        self.lbl_scale_warning.setStyleSheet("color: #f59e0b; font-size: 11px; font-style: italic;")
        layout_gen_f.addWidget(self.lbl_scale_warning)

        layout_gen_f.addStretch()

        # Bottom Generator Action Buttons
        btn_gen_layout = QHBoxLayout()
        self.btn_gen_apply = QPushButton("APPLY")
        self.btn_gen_apply.setObjectName("btnApply")
        self.btn_gen_apply.clicked.connect(self.on_apply_generator)
        btn_gen_layout.addWidget(self.btn_gen_apply)

        self.btn_gen_start = QPushButton("START")
        self.btn_gen_start.setObjectName("btnStart")
        self.btn_gen_start.clicked.connect(self.on_start_generator)
        btn_gen_layout.addWidget(self.btn_gen_start)

        self.btn_gen_stop = QPushButton("STOP")
        self.btn_gen_stop.setObjectName("btnStop")
        self.btn_gen_stop.clicked.connect(self.on_stop_generator)
        btn_gen_layout.addWidget(self.btn_gen_stop)

        layout_gen_f.addLayout(btn_gen_layout)
        body_layout.addWidget(group_gen_ctrl, 3)

        # RIGHT PANEL: Oscilloscope Plot and Controls
        right_panel = QVBoxLayout()
        right_panel.setSpacing(6)

        # Pyqtgraph PlotWidget
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel('bottom', 'Time', units='s')
        self.plot_widget.setLabel('left', 'Voltage', units='V')
        
        # Cyan Curve
        self.curve = self.plot_widget.plot(pen=pg.mkPen(color='#00f0ff', width=2))

        # Dashed Yellow Trigger Line
        self.trig_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen(color='#facc15', style=Qt.PenStyle.DashLine, width=1.5))
        self.plot_widget.addItem(self.trig_line)

        # Overlay Tags (View Mode Tag, Simulation Mode Tag, Single Trigger Tag)
        self.tag_view_mode = pg.TextItem(text="SCALED VIEW", color="#38bdf8", anchor=(1, 0))
        self.tag_view_mode.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.plot_widget.addItem(self.tag_view_mode)

        self.tag_sim = pg.TextItem(text="SIMULATION", color="#ef4444", anchor=(0, 0))
        self.tag_sim.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        self.tag_sim.setVisible(self.simulation_mode)
        self.plot_widget.addItem(self.tag_sim)

        self.tag_single = pg.TextItem(text="STOPPED (SINGLE)", color="#f59e0b", anchor=(0.5, 0))
        self.tag_single.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self.tag_single.setVisible(False)
        self.plot_widget.addItem(self.tag_single)

        right_panel.addWidget(self.plot_widget, 7)

        # Oscilloscope Controls / Trigger Group Box
        group_scope_ctrl = QGroupBox("OSCILLOSCOPE CONTROLS / TRIGGER")
        layout_s_ctrl = QVBoxLayout(group_scope_ctrl)
        layout_s_ctrl.setContentsMargins(8, 8, 8, 8)
        layout_s_ctrl.setSpacing(8)

        row_ctrl1 = QHBoxLayout()
        row_ctrl1.addWidget(QLabel("TIME/DIV:"))
        self.cmb_time_div = QComboBox()
        self.cmb_time_div.addItems(["10us", "20us", "50us", "100us", "200us", "500us", "1ms", "2ms", "5ms", "10ms", "20ms", "50ms", "100ms"])
        self.cmb_time_div.setCurrentText(self.config.get("time_div", "1ms"))
        self.cmb_time_div.currentTextChanged.connect(self.on_scope_params_changed)
        row_ctrl1.addWidget(self.cmb_time_div)

        row_ctrl1.addWidget(QLabel("VOLT/DIV:"))
        self.cmb_volt_div = QComboBox()
        self.cmb_volt_div.addItems(["10mV", "20mV", "50mV", "100mV", "200mV", "500mV", "1V", "2V", "5V", "10V", "20V"])
        self.cmb_volt_div.setCurrentText(self.config.get("volt_div", "500mV"))
        self.cmb_volt_div.currentTextChanged.connect(self.on_scope_params_changed)
        row_ctrl1.addWidget(self.cmb_volt_div)

        row_ctrl1.addWidget(QLabel("VERT POS (V):"))
        self.spn_vert_pos = QDoubleSpinBox()
        self.spn_vert_pos.setRange(-50.0, 50.0)
        self.spn_vert_pos.setSingleStep(0.1)
        self.spn_vert_pos.setValue(float(self.config.get("vert_pos", 0.0)))
        self.spn_vert_pos.valueChanged.connect(self.on_scope_params_changed)
        row_ctrl1.addWidget(self.spn_vert_pos)

        self.btn_grid_toggle = QPushButton("GRID ON")
        self.btn_grid_toggle.setCheckable(True)
        self.btn_grid_toggle.setChecked(self.config.get("grid_on", True))
        self.btn_grid_toggle.clicked.connect(self.on_toggle_grid)
        row_ctrl1.addWidget(self.btn_grid_toggle)

        layout_s_ctrl.addLayout(row_ctrl1)

        row_ctrl2 = QHBoxLayout()
        self.btn_scope_run = QPushButton("RUN")
        self.btn_scope_run.setObjectName("btnStart")
        self.btn_scope_run.clicked.connect(self.on_run_scope)
        row_ctrl2.addWidget(self.btn_scope_run)

        self.btn_scope_stop = QPushButton("STOP")
        self.btn_scope_stop.setObjectName("btnStop")
        self.btn_scope_stop.clicked.connect(self.on_stop_scope)
        row_ctrl2.addWidget(self.btn_scope_stop)

        self.btn_auto_scale = QPushButton("AUTO SCALE")
        self.btn_auto_scale.clicked.connect(self.on_auto_scale)
        row_ctrl2.addWidget(self.btn_auto_scale)

        self.btn_clear = QPushButton("CLEAR")
        self.btn_clear.clicked.connect(self.on_clear_plot)
        row_ctrl2.addWidget(self.btn_clear)

        row_ctrl2.addWidget(QLabel("TRIG MODE:"))
        self.cmb_trig_mode = QComboBox()
        self.cmb_trig_mode.addItems(["Auto", "Normal", "Single"])
        self.cmb_trig_mode.setCurrentText(self.config.get("trig_mode", "Auto"))
        self.cmb_trig_mode.currentTextChanged.connect(self.on_trigger_changed)
        row_ctrl2.addWidget(self.cmb_trig_mode)

        row_ctrl2.addWidget(QLabel("EDGE:"))
        self.cmb_trig_edge = QComboBox()
        self.cmb_trig_edge.addItems(["Rising", "Falling"])
        self.cmb_trig_edge.setCurrentText(self.config.get("trig_edge", "Rising"))
        self.cmb_trig_edge.currentTextChanged.connect(self.on_trigger_changed)
        row_ctrl2.addWidget(self.cmb_trig_edge)

        row_ctrl2.addWidget(QLabel("LEVEL (V):"))
        self.spn_trig_level = QDoubleSpinBox()
        self.spn_trig_level.setRange(-50.0, 50.0)
        self.spn_trig_level.setSingleStep(0.05)
        self.spn_trig_level.setValue(float(self.config.get("trig_level", 0.0)))
        self.spn_trig_level.valueChanged.connect(self.on_trigger_changed)
        row_ctrl2.addWidget(self.spn_trig_level)

        layout_s_ctrl.addLayout(row_ctrl2)
        right_panel.addWidget(group_scope_ctrl, 3)

        body_layout.addLayout(right_panel, 7)
        main_layout.addLayout(body_layout, 8)

        # --- MEASUREMENT BAR ---
        group_meas = QGroupBox("REAL-TIME MEASURED VALUES (FROM ESP32 #2 ADC)")
        layout_m = QHBoxLayout(group_meas)
        layout_m.setContentsMargins(10, 6, 10, 6)

        self.lbl_m_freq = self.create_meas_label("FREQ", "---")
        self.lbl_m_period = self.create_meas_label("PERIOD", "---")
        self.lbl_m_vpp = self.create_meas_label("VPP", "---")
        self.lbl_m_vmax = self.create_meas_label("VMAX", "---")
        self.lbl_m_vmin = self.create_meas_label("VMIN", "---")
        self.lbl_m_vrms = self.create_meas_label("VRMS", "---")
        self.lbl_m_mean = self.create_meas_label("MEAN/DC", "---")

        layout_m.addWidget(self.lbl_m_freq)
        layout_m.addWidget(self.lbl_m_period)
        layout_m.addWidget(self.lbl_m_vpp)
        layout_m.addWidget(self.lbl_m_vmax)
        layout_m.addWidget(self.lbl_m_vmin)
        layout_m.addWidget(self.lbl_m_vrms)
        layout_m.addWidget(self.lbl_m_mean)

        main_layout.addWidget(group_meas)

        # --- DIAGNOSTICS PANEL (Collapsible) ---
        self.diag_box = CollapsibleBox("DIAGNOSTICS / SYSTEM MONITOR (CLICK TO COLLAPSE)")
        layout_diag_grid = QGridLayout()
        layout_diag_grid.setContentsMargins(6, 6, 6, 6)

        # ESP32 #1 Box
        grp_d1 = QGroupBox("ESP32 #1 (Generator)")
        l_d1 = QVBoxLayout(grp_d1)
        self.lbl_d1_ep = QLabel("Endpoint: 127.0.0.1:5000")
        self.lbl_d1_conn = QLabel("Connection: Disconnected")
        self.lbl_d1_cmd = QLabel("Last Command: None")
        self.lbl_d1_reply = QLabel("Last Reply: None")
        self.lbl_d1_scale = QLabel("Current Scale: x1.000")
        self.lbl_d1_ping = QLabel("Ping: --- ms")
        l_d1.addWidget(self.lbl_d1_ep)
        l_d1.addWidget(self.lbl_d1_conn)
        l_d1.addWidget(self.lbl_d1_cmd)
        l_d1.addWidget(self.lbl_d1_reply)
        l_d1.addWidget(self.lbl_d1_scale)
        l_d1.addWidget(self.lbl_d1_ping)
        layout_diag_grid.addWidget(grp_d1, 0, 0)

        # ESP32 #2 Box
        grp_d2 = QGroupBox("ESP32 #2 (Oscilloscope)")
        l_d2 = QVBoxLayout(grp_d2)
        self.lbl_d2_ep = QLabel("Endpoint: 127.0.0.1:5001")
        self.lbl_d2_conn = QLabel("Connection: Disconnected")
        self.lbl_d2_frames = QLabel("Frames Rx: 0")
        self.lbl_d2_samples = QLabel("Samples Rx: 0")
        self.lbl_d2_rate = QLabel("Actual Sample Rate: 0 Hz")
        self.lbl_d2_stream = QLabel("Stream Status: STOPPED")
        self.lbl_d2_bad = QLabel("Bad Frames: 0")
        self.lbl_d2_ping = QLabel("Ping: --- ms")
        l_d2.addWidget(self.lbl_d2_ep)
        l_d2.addWidget(self.lbl_d2_conn)
        l_d2.addWidget(self.lbl_d2_frames)
        l_d2.addWidget(self.lbl_d2_samples)
        l_d2.addWidget(self.lbl_d2_rate)
        l_d2.addWidget(self.lbl_d2_stream)
        l_d2.addWidget(self.lbl_d2_bad)
        l_d2.addWidget(self.lbl_d2_ping)
        layout_diag_grid.addWidget(grp_d2, 0, 1)

        # System Health Box
        grp_d3 = QGroupBox("System Health")
        l_d3 = QVBoxLayout(grp_d3)
        self.lbl_d3_fps = QLabel("GUI Refresh Rate: 0.0 FPS")
        self.lbl_d3_error = QLabel("Last System Error: None")
        self.lbl_d3_error.setStyleSheet("color: #ef4444;")
        l_d3.addWidget(self.lbl_d3_fps)
        l_d3.addWidget(self.lbl_d3_error)
        l_d3.addStretch()
        layout_diag_grid.addWidget(grp_d3, 0, 2)

        self.diag_box.setContentLayout(layout_diag_grid)
        main_layout.addWidget(self.diag_box)

        # --- STATUS BAR ---
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

        # Initial plot ranges update
        self.update_plot_ranges()

    def create_meas_label(self, name: str, val: str) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(4, 2, 4, 2)
        l.setSpacing(1)
        
        lbl_name = QLabel(name)
        lbl_name.setStyleSheet("color: #94a3b8; font-size: 10px; font-weight: bold;")
        lbl_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        lbl_val = QLabel(val)
        lbl_val.setStyleSheet("color: #00f0ff; font-family: 'Consolas', monospace; font-size: 14px; font-weight: bold;")
        lbl_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_val.setObjectName(f"meas_{name}")
        
        l.addWidget(lbl_name)
        l.addWidget(lbl_val)
        return w

    # --- ACTION HANDLERS ---

    def on_waveform_changed(self, waveform_str: str):
        self.spn_duty.setEnabled(waveform_str == "Square")

    def update_generator_scale_info(self):
        vpp = self.spn_vpp.value()
        offset = self.spn_offset.value()

        if not self.scale_confirmed_by_hw:
            self.current_scale = calculate_scale(vpp, offset)
            self.lbl_scale_warning.setVisible(True)
        else:
            self.lbl_scale_warning.setVisible(False)

        pin_vpp = vpp * self.current_scale
        pin_center_v = 1.65 + offset * self.current_scale

        self.lbl_gen_info.setText(
            f"Scale: x{self.current_scale:.3f} | Pin: {pin_vpp:.2f} Vpp @ {pin_center_v:.2f} V"
        )
        self.lbl_d1_scale.setText(f"Current Scale: x{self.current_scale:.3f}")

        # Send updated trigger level to scope if connected
        self.send_scope_trigger_settings()

    def set_view_mode(self, mode: str):
        self.view_mode = mode
        self.act_scaled_view.setChecked(mode == "Scaled")
        self.act_pin_view.setChecked(mode == "Pin")
        self.tag_view_mode.setText("SCALED VIEW" if mode == "Scaled" else "PIN VIEW")
        self.show_status_message(f"Switched view mode to {mode} View", False)
        self.redraw_plot()

    def on_toggle_sim_mode(self, checked: bool):
        self.simulation_mode = checked
        self.tag_sim.setVisible(checked)
        self.show_status_message(f"Simulation Mode {'ON' if checked else 'OFF'}", False)

    def on_toggle_diagnostics_menu(self, checked: bool):
        self.diag_box.toggle_button.setChecked(checked)
        self.diag_box.on_toggle(checked)

    def on_toggle_grid(self, checked: bool):
        self.plot_widget.showGrid(x=checked, y=checked, alpha=0.3)
        self.btn_grid_toggle.setText("GRID ON" if checked else "GRID OFF")

    def on_connect_gen(self):
        ip = self.txt_gen_ip.text().strip()
        port = self.spn_gen_port.value()
        
        if self.gen_thread and self.gen_thread.isRunning():
            self.gen_thread.stop()

        self.gen_thread = GeneratorThread(ip, port, self)
        self.gen_thread.connected_signal.connect(self.on_gen_connected)
        self.gen_thread.status_msg_signal.connect(self.show_status_message)
        self.gen_thread.ack_waveform_signal.connect(self.on_gen_ack_waveform)
        self.gen_thread.status_reply_signal.connect(self.on_gen_status_reply)
        self.gen_thread.diag_signal.connect(self.on_gen_diag_update)
        self.gen_thread.start()

    def on_disconnect_gen(self):
        if self.gen_thread and self.gen_thread.isRunning():
            self.gen_thread.stop()
            self.show_status_message("ESP32 #1 Disconnected by user", False)
        self.scale_confirmed_by_hw = False
        self.update_generator_scale_info()

    def on_ping_gen(self):
        if self.gen_thread and self.gen_thread.isRunning():
            self.gen_thread.send_command(build_gen_ping())

    def on_connect_scope(self):
        ip = self.txt_scope_ip.text().strip()
        port = self.spn_scope_port.value()

        if self.scope_thread and self.scope_thread.isRunning():
            self.scope_thread.stop()

        self.scope_thread = ScopeThread(ip, port, self)
        self.scope_thread.connected_signal.connect(self.on_scope_connected)
        self.scope_thread.status_msg_signal.connect(self.show_status_message)
        self.scope_thread.frame_received_signal.connect(self.on_scope_frame_received)
        self.scope_thread.diag_signal.connect(self.on_scope_diag_update)
        self.scope_thread.start()

    def on_disconnect_scope(self):
        if self.scope_thread and self.scope_thread.isRunning():
            self.scope_thread.stop()
            self.show_status_message("ESP32 #2 Disconnected by user", False)

    def on_ping_scope(self):
        if self.scope_thread and self.scope_thread.isRunning():
            self.scope_thread.send_command(build_scope_ping())

    def on_connect_both(self):
        self.on_connect_gen()
        self.on_connect_scope()

    def on_disconnect_both(self):
        self.on_disconnect_gen()
        self.on_disconnect_scope()

    def on_auto_discover(self):
        if self.discover_thread and self.discover_thread.isRunning():
            return
        self.discover_thread = AutoDiscoverThread(self)
        self.discover_thread.discovered_signal.connect(self.on_discovered_devices)
        self.discover_thread.status_signal.connect(self.show_status_message)
        self.discover_thread.start()

    def on_discovered_devices(self, found: dict):
        msg_parts = []
        if "gen" in found:
            ip, port = found["gen"]
            self.txt_gen_ip.setText(ip)
            self.spn_gen_port.setValue(port)
            msg_parts.append(f"ESP32 #1 @ {ip}:{port}")

        if "scope" in found:
            ip, port = found["scope"]
            self.txt_scope_ip.setText(ip)
            self.spn_scope_port.setValue(port)
            msg_parts.append(f"ESP32 #2 @ {ip}:{port}")

        if msg_parts:
            self.show_status_message("Auto-Discovered: " + ", ".join(msg_parts), False)
        else:
            self.show_status_message("Auto-Discover: No devices responded within 2 seconds", True)

    def on_apply_generator(self):
        wtype = self.cmb_waveform.currentText()
        freq = self.spn_freq.value()
        vpp = self.spn_vpp.value()
        offset = self.spn_offset.value()
        duty = self.spn_duty.value()

        cmd = build_gen_set_waveform(wtype, freq, vpp, offset, duty)
        if self.gen_thread and self.gen_thread.isRunning():
            self.gen_thread.send_command(cmd)
        else:
            # Local update
            self.current_scale = calculate_scale(vpp, offset)
            self.update_generator_scale_info()
            self.show_status_message(f"Generator local settings updated (scale x{self.current_scale:.3f})", False)

    def on_start_generator(self):
        if self.gen_thread and self.gen_thread.isRunning():
            self.gen_thread.send_command(build_gen_start())
        else:
            self.show_status_message("Generator started locally (not connected)", False)

    def on_stop_generator(self):
        if self.gen_thread and self.gen_thread.isRunning():
            self.gen_thread.send_command(build_gen_stop())
        else:
            self.show_status_message("Generator stopped locally (not connected)", False)

    def on_run_scope(self):
        self.tag_single.setVisible(False)
        if self.scope_thread and self.scope_thread.isRunning():
            self.scope_thread.send_command(build_scope_run())
        else:
            self.show_status_message("Scope RUN (Simulation/Disconnected)", False)

    def on_stop_scope(self):
        if self.scope_thread and self.scope_thread.isRunning():
            self.scope_thread.send_command(build_scope_stop())
        else:
            self.show_status_message("Scope STOPPED", False)

    def on_clear_plot(self):
        self.curve.setData([], [])
        self.last_frame_data = None
        self.update_measurement_labels(calculate_measurements(None, None))
        self.show_status_message("Plot cleared", False)

    def on_scope_params_changed(self):
        self.update_plot_ranges()
        self.send_scope_capture_settings()

    def on_trigger_changed(self):
        self.send_scope_trigger_settings()
        self.update_trigger_line_position()

    def update_trigger_line_position(self):
        level_v = self.spn_trig_level.value()
        if self.view_mode == "Pin":
            # Display level in pin volts (0-3.3V)
            pin_level = virtual_to_pin_v(level_v, self.current_scale)
            self.trig_line.setValue(pin_level)
        else:
            # Display level in virtual volts
            self.trig_line.setValue(level_v)

    def update_plot_ranges(self):
        time_per_div = time_str_to_seconds(self.cmb_time_div.currentText())
        volt_per_div = volt_str_to_volts(self.cmb_volt_div.currentText())
        vert_pos = self.spn_vert_pos.value()

        x_max = 10.0 * time_per_div
        y_min = -4.0 * volt_per_div - vert_pos
        y_max = +4.0 * volt_per_div - vert_pos

        self.plot_widget.setXRange(0, x_max, padding=0)
        self.plot_widget.setYRange(y_min, y_max, padding=0)

        # Reposition tags
        self.tag_view_mode.setPos(x_max, y_max)
        self.tag_sim.setPos(0, y_max)
        self.tag_single.setPos(x_max / 2.0, y_max)

        self.update_trigger_line_position()

    def send_scope_capture_settings(self):
        time_per_div = time_str_to_seconds(self.cmb_time_div.currentText())
        sr_hz, n_samples = calc_capture_params(time_per_div)
        cmd = build_scope_set_capture(sr_hz, n_samples)
        if self.scope_thread and self.scope_thread.isRunning():
            self.scope_thread.send_command(cmd)

    def send_scope_trigger_settings(self):
        mode = self.cmb_trig_mode.currentText()
        edge = self.cmb_trig_edge.currentText()
        level_v = self.spn_trig_level.value()
        level_pin_mv = calculate_trigger_level_pin_mv(level_v, self.current_scale)

        cmd = build_scope_set_trigger(mode, edge, level_pin_mv)
        if self.scope_thread and self.scope_thread.isRunning():
            self.scope_thread.send_command(cmd)

    def on_auto_scale(self):
        meas = calculate_measurements(self.last_time_axis, self.last_voltage_axis)
        freq = meas.get("freq")
        vpp = meas.get("vpp")

        if freq is not None and freq > 0:
            period = 1.0 / freq
            # Choose time/div so ~3 periods fill 10 divisions -> time_div ≈ 0.3 * period
            target_tdiv = 0.3 * period
            best_tdiv_str = "1ms"
            min_tdiff = float('inf')
            for tstr, tval in TIME_DIV_VALUES.items():
                diff = abs(tval - target_tdiv)
                if diff < min_tdiff:
                    min_tdiff = diff
                    best_tdiv_str = tstr
            self.cmb_time_div.setCurrentText(best_tdiv_str)

        if vpp is not None and vpp > 0:
            # Choose volt/div so signal vpp fills ~6 vertical divisions out of 8 -> volt_div ≈ vpp / 6
            target_vdiv = vpp / 6.0
            best_vdiv_str = "500mV"
            min_vdiff = float('inf')
            for vstr, vval in VOLT_DIV_VALUES.items():
                diff = abs(vval - target_vdiv)
                if diff < min_vdiff:
                    min_vdiff = diff
                    best_vdiv_str = vstr
            self.cmb_volt_div.setCurrentText(best_vdiv_str)

        self.spn_vert_pos.setValue(0.0)
        self.show_status_message("Auto Scale applied", False)

    # --- NETWORK CALLBACKS ---

    def on_gen_connected(self, connected: bool, endpoint: str):
        if connected:
            self.lbl_gen_status.setText("CONNECTED")
            self.lbl_gen_status.setStyleSheet("color: #10b981; font-weight: bold;")
        else:
            self.lbl_gen_status.setText("DISCONNECTED")
            self.lbl_gen_status.setStyleSheet("color: #94a3b8; font-weight: bold;")
            self.scale_confirmed_by_hw = False
            self.update_generator_scale_info()

    def on_gen_ack_waveform(self, scale: float, pin_vpp: float, pin_center_v: float):
        self.current_scale = scale
        self.scale_confirmed_by_hw = True
        self.update_generator_scale_info()

    def on_gen_status_reply(self, running: bool, waveform: str, freq: float, vpp: float, offset: float, duty: int, scale: float):
        self.current_scale = scale
        self.scale_confirmed_by_hw = True
        self.cmb_waveform.setCurrentText(waveform)
        self.spn_freq.setValue(freq)
        self.spn_vpp.setValue(vpp)
        self.spn_offset.setValue(offset)
        self.spn_duty.setValue(duty)
        self.update_generator_scale_info()

    def on_scope_connected(self, connected: bool, endpoint: str):
        if connected:
            self.lbl_scope_status.setText("CONNECTED")
            self.lbl_scope_status.setStyleSheet("color: #10b981; font-weight: bold;")
            # Right after connecting, send capture and trigger settings!
            self.send_scope_capture_settings()
            self.send_scope_trigger_settings()
            self.scope_thread.send_command(build_scope_run())
        else:
            self.lbl_scope_status.setText("DISCONNECTED")
            self.lbl_scope_status.setStyleSheet("color: #94a3b8; font-weight: bold;")

    def on_scope_frame_received(self, frame_dict: dict):
        self.last_frame_data = frame_dict

        # Handle Single Trigger Mode freeze
        if self.cmb_trig_mode.currentText() == "Single":
            if frame_dict["triggered"]:
                self.tag_single.setVisible(True)
                if self.scope_thread and self.scope_thread.isRunning():
                    self.scope_thread.send_command(build_scope_stop())
                self.show_status_message("Single Trigger fired! Plot frozen.", False)

    def on_gen_diag_update(self, diag: dict):
        self.lbl_d1_ep.setText(f"Endpoint: {diag['endpoint']}")
        self.lbl_d1_conn.setText(f"Connection: {'CONNECTED' if diag['connected'] else 'DISCONNECTED'}")
        self.lbl_d1_cmd.setText(f"Last Command: {diag['last_cmd']}")
        self.lbl_d1_reply.setText(f"Last Reply: {diag['last_reply']}")
        self.lbl_d1_scale.setText(f"Current Scale: x{diag['current_scale']:.3f}")
        ping_str = f"{diag['ping_ms']:.1f}" if diag['ping_ms'] > 0 else "---"
        self.lbl_d1_ping.setText(f"Ping: {ping_str} ms")

    def on_scope_diag_update(self, diag: dict):
        self.lbl_d2_ep.setText(f"Endpoint: {diag['endpoint']}")
        self.lbl_d2_conn.setText(f"Connection: {'CONNECTED' if diag['connected'] else 'DISCONNECTED'}")
        self.lbl_d2_frames.setText(f"Frames Rx: {diag['frames_rx']}")
        self.lbl_d2_samples.setText(f"Samples Rx: {diag['samples_rx']}")
        self.lbl_d2_rate.setText(f"Actual Sample Rate: {diag['actual_sample_rate']} Hz")
        self.lbl_d2_stream.setText(f"Stream Status: {diag['stream_status']}")
        self.lbl_d2_bad.setText(f"Bad Frames: {diag['bad_frames']}")
        ping_str = f"{diag['ping_ms']:.1f}" if diag['ping_ms'] > 0 else "---"
        self.lbl_d2_ping.setText(f"Ping: {ping_str} ms")

    # --- PLOT REDRAW & SIMULATION LOOP ---

    def on_gui_timer_tick(self):
        # Calculate GUI Refresh FPS
        now = time.perf_counter()
        dt = now - self.last_fps_calc_t
        self.frame_count += 1
        if dt >= 1.0:
            self.current_fps = self.frame_count / dt
            self.lbl_d3_fps.setText(f"GUI Refresh Rate: {self.current_fps:.1f} FPS")
            self.frame_count = 0
            self.last_fps_calc_t = now

        # If Simulation Mode is active, generate fake frame data locally
        if self.simulation_mode:
            self.generate_simulated_frame()

        self.redraw_plot()

    def generate_simulated_frame(self):
        time_per_div = time_str_to_seconds(self.cmb_time_div.currentText())
        sr_hz, n_samples = calc_capture_params(time_per_div)
        dt = 1.0 / sr_hz

        freq = self.spn_freq.value()
        vpp = self.spn_vpp.value()
        offset = self.spn_offset.value()
        duty = self.spn_duty.value() / 100.0
        wtype = self.cmb_waveform.currentText()
        scale = self.current_scale

        self.sim_sim_t += 0.05
        t_samples = np.arange(n_samples, dtype=np.float64) * dt
        phase = 2.0 * np.pi * freq * t_samples

        if wtype == "Sine":
            virt_v = (vpp / 2.0) * np.sin(phase) + offset
        elif wtype == "Square":
            norm_p = (phase / (2.0 * np.pi)) % 1.0
            virt_v = (vpp / 2.0) * np.where(norm_p < duty, 1.0, -1.0) + offset
        elif wtype == "Triangle":
            norm_p = (phase / (2.0 * np.pi)) % 1.0
            tri = np.where(norm_p < 0.5, 4.0 * norm_p - 1.0, 3.0 - 4.0 * norm_p)
            virt_v = (vpp / 2.0) * tri + offset
        elif wtype == "Sawtooth":
            norm_p = (phase / (2.0 * np.pi)) % 1.0
            virt_v = (vpp / 2.0) * (2.0 * norm_p - 1.0) + offset
        else:
            virt_v = (vpp / 2.0) * np.sin(phase) + offset

        pin_v = 1.65 + scale * virt_v
        noise = np.random.normal(0, 4.0, n_samples)
        pin_mv = np.clip(np.round(pin_v * 1000.0 + noise), 0, 3300).astype(np.uint16)

        trig_idx = int(round(n_samples * 0.1))
        self.last_frame_data = {
            "frame_id": 9999,
            "sample_rate_hz": sr_hz,
            "n_samples": n_samples,
            "flags": 1,
            "triggered": True,
            "trigger_index": trig_idx,
            "pin_mv": pin_mv
        }

    def redraw_plot(self):
        if self.last_frame_data is None:
            return

        frame = self.last_frame_data
        n_samples = frame["n_samples"]
        sr_hz = frame["sample_rate_hz"]
        trig_idx = frame["trigger_index"]
        pin_mv_arr = frame["pin_mv"]

        time_per_div = time_str_to_seconds(self.cmb_time_div.currentText())

        # Generate time axis
        t_axis = calc_time_axis(n_samples, sr_hz, trig_idx, time_per_div)

        # Convert voltage based on active View Mode
        if self.view_mode == "Pin":
            # Display real pin voltage in Volts (0.0 to 3.3V)
            v_axis = pin_mv_arr.astype(np.float64) / 1000.0
        else:
            # Display virtual voltage (Scaled View)
            v_axis = pin_mv_to_virtual_v(pin_mv_arr.astype(np.float64), self.current_scale)

        self.last_time_axis = t_axis
        self.last_voltage_axis = v_axis

        # Fast plot update using existing curve item
        self.curve.setData(t_axis, v_axis)

        # Update Measurements Bar
        meas = calculate_measurements(t_axis, v_axis)
        self.update_measurement_labels(meas)

    def update_measurement_labels(self, meas: dict):
        self.lbl_m_freq.findChild(QLabel, "meas_FREQ").setText(meas["freq_str"])
        self.lbl_m_period.findChild(QLabel, "meas_PERIOD").setText(meas["period_str"])
        self.lbl_m_vpp.findChild(QLabel, "meas_VPP").setText(meas["vpp_str"])
        self.lbl_m_vmax.findChild(QLabel, "meas_VMAX").setText(meas["vmax_str"])
        self.lbl_m_vmin.findChild(QLabel, "meas_VMIN").setText(meas["vmin_str"])
        self.lbl_m_vrms.findChild(QLabel, "meas_VRMS").setText(meas["vrms_str"])
        self.lbl_m_mean.findChild(QLabel, "meas_MEAN/DC").setText(meas["mean_str"])

    def show_status_message(self, msg: str, is_error: bool = False):
        if is_error:
            self.lbl_d3_error.setText(f"Last System Error: {msg}")
            self.status_bar.setStyleSheet("QStatusBar { color: #ef4444; font-weight: bold; }")
        else:
            self.status_bar.setStyleSheet("QStatusBar { color: #94a3b8; }")
        self.status_bar.showMessage(msg)

    # --- MENU FILE ACTIONS ---

    def on_save_config(self):
        cfg = {
            "gen_ip": self.txt_gen_ip.text().strip(),
            "gen_port": self.spn_gen_port.value(),
            "scope_ip": self.txt_scope_ip.text().strip(),
            "scope_port": self.spn_scope_port.value(),
            "waveform": self.cmb_waveform.currentText(),
            "frequency": self.spn_freq.value(),
            "vpp": self.spn_vpp.value(),
            "offset": self.spn_offset.value(),
            "duty": self.spn_duty.value(),
            "time_div": self.cmb_time_div.currentText(),
            "volt_div": self.cmb_volt_div.currentText(),
            "vert_pos": self.spn_vert_pos.value(),
            "grid_on": self.btn_grid_toggle.isChecked(),
            "trig_mode": self.cmb_trig_mode.currentText(),
            "trig_edge": self.cmb_trig_edge.currentText(),
            "trig_level": self.spn_trig_level.value(),
            "view_mode": self.view_mode,
            "diag_collapsed": not self.diag_box.toggle_button.isChecked(),
            "simulation_mode": self.simulation_mode
        }
        if save_config(cfg):
            self.show_status_message("Configuration saved to config.json", False)
        else:
            self.show_status_message("Failed to save configuration", True)

    def on_load_config(self):
        self.config = load_config()
        self.txt_gen_ip.setText(self.config.get("gen_ip", "127.0.0.1"))
        self.spn_gen_port.setValue(self.config.get("gen_port", 5000))
        self.txt_scope_ip.setText(self.config.get("scope_ip", "127.0.0.1"))
        self.spn_scope_port.setValue(self.config.get("scope_port", 5001))
        self.cmb_waveform.setCurrentText(self.config.get("waveform", "Sine"))
        self.spn_freq.setValue(self.config.get("frequency", 1000.0))
        self.spn_vpp.setValue(self.config.get("vpp", 1.00))
        self.spn_offset.setValue(self.config.get("offset", 0.00))
        self.spn_duty.setValue(self.config.get("duty", 50))
        self.cmb_time_div.setCurrentText(self.config.get("time_div", "1ms"))
        self.cmb_volt_div.setCurrentText(self.config.get("volt_div", "500mV"))
        self.spn_vert_pos.setValue(self.config.get("vert_pos", 0.0))
        self.btn_grid_toggle.setChecked(self.config.get("grid_on", True))
        self.on_toggle_grid(self.config.get("grid_on", True))
        self.cmb_trig_mode.setCurrentText(self.config.get("trig_mode", "Auto"))
        self.cmb_trig_edge.setCurrentText(self.config.get("trig_edge", "Rising"))
        self.spn_trig_level.setValue(self.config.get("trig_level", 0.0))
        self.set_view_mode(self.config.get("view_mode", "Scaled"))
        self.simulation_mode = self.config.get("simulation_mode", False)
        self.act_sim_mode.setChecked(self.simulation_mode)
        self.tag_sim.setVisible(self.simulation_mode)

        self.show_status_message("Configuration loaded from config.json", False)

    def on_export_csv(self):
        if self.last_time_axis is None or self.last_voltage_axis is None:
            QMessageBox.warning(self, "Export CSV", "No waveform data available to export!")
            return

        filepath, _ = QFileDialog.getSaveFileName(self, "Export Waveform CSV", "waveform.csv", "CSV Files (*.csv)")
        if not filepath:
            return

        try:
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["time_s", f"voltage_V_{self.view_mode}"])
                for t, v in zip(self.last_time_axis, self.last_voltage_axis):
                    writer.writerow([f"{t:.8e}", f"{v:.8e}"])
            self.show_status_message(f"Exported waveform data to {os.path.basename(filepath)}", False)
        except Exception as e:
            QMessageBox.critical(self, "Export CSV Error", f"Could not write CSV file:\n{e}")

    def on_save_screenshot(self):
        filepath, _ = QFileDialog.getSaveFileName(self, "Save Screenshot", "screenshot.png", "PNG Images (*.png)")
        if not filepath:
            return
        
        pixmap = self.centralWidget().grab()
        if pixmap.save(filepath, "PNG"):
            self.show_status_message(f"Saved screenshot to {os.path.basename(filepath)}", False)
        else:
            QMessageBox.critical(self, "Screenshot Error", "Failed to save screenshot image.")

    def on_network_settings_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Network Settings")
        dlg.setFixedSize(350, 200)

        layout = QFormLayout(dlg)
        
        txt_g_ip = QLineEdit(self.txt_gen_ip.text())
        spn_g_port = QSpinBox()
        spn_g_port.setRange(1, 65535)
        spn_g_port.setValue(self.spn_gen_port.value())

        txt_s_ip = QLineEdit(self.txt_scope_ip.text())
        spn_s_port = QSpinBox()
        spn_s_port.setRange(1, 65535)
        spn_s_port.setValue(self.spn_scope_port.value())

        layout.addRow("ESP32 #1 IP:", txt_g_ip)
        layout.addRow("ESP32 #1 Port:", spn_g_port)
        layout.addRow("ESP32 #2 IP:", txt_s_ip)
        layout.addRow("ESP32 #2 Port:", spn_s_port)

        btn_box = QHBoxLayout()
        btn_ok = QPushButton("OK")
        btn_cancel = QPushButton("Cancel")
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addRow(btn_box)

        btn_cancel.clicked.connect(dlg.reject)
        
        def save_net():
            self.txt_gen_ip.setText(txt_g_ip.text().strip())
            self.spn_gen_port.setValue(spn_g_port.value())
            self.txt_scope_ip.setText(txt_s_ip.text().strip())
            self.spn_scope_port.setValue(spn_s_port.value())
            dlg.accept()

        btn_ok.clicked.connect(save_net)
        dlg.exec()

    def on_about_dialog(self):
        QMessageBox.about(
            self,
            "About System",
            "<h3>ESP32 Function Generator + Digital Oscilloscope System</h3>"
            "<p><b>Stack:</b> Python 3.10+, PyQt6, pyqtgraph, numpy</p>"
            "<p>Controls two ESP32 DevKit boards over WiFi TCP:</p>"
            "<ul>"
            "<li>ESP32 #1: Function Generator (DAC GPIO25, TCP 5000)</li>"
            "<li>ESP32 #2: Oscilloscope (ADC GPIO34, TCP 5001)</li>"
            "</ul>"
            "<p>Features precise scaling, high FPS rendering, binary frame parsing, and real-time measurement algorithms.</p>"
        )

    def closeEvent(self, event):
        self.on_save_config()
        if self.gen_thread and self.gen_thread.isRunning():
            self.gen_thread.stop()
        if self.scope_thread and self.scope_thread.isRunning():
            self.scope_thread.stop()
        event.accept()
