import time
import threading
import pytest
from PyQt6.QtCore import QCoreApplication
from net_generator import GeneratorThread
from net_scope import ScopeThread
from tools.fake_esp32 import FakeGenerator, FakeScope, run_udp_discovery_server

def test_integration_fake_esp32():
    # Start fake servers on custom ports for isolated testing
    gen_srv = FakeGenerator(port=5002)
    scope_srv = FakeScope(generator=gen_srv, port=5003)

    t1 = threading.Thread(target=gen_srv.run_server, daemon=True)
    t2 = threading.Thread(target=scope_srv.run_server, daemon=True)
    t1.start()
    t2.start()

    time.sleep(0.2)

    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])

    gen_connected = False
    gen_status_received = False
    scope_connected = False
    frames_received = []

    def on_gen_conn(conn, ep):
        nonlocal gen_connected
        gen_connected = conn

    def on_gen_status(running, wave, freq, vpp, offset, duty, scale):
        nonlocal gen_status_received
        gen_status_received = True

    def on_scope_conn(conn, ep):
        nonlocal scope_connected
        scope_connected = conn

    def on_scope_frame(frame):
        frames_received.append(frame)

    gen = GeneratorThread("127.0.0.1", 5002)
    gen.connected_signal.connect(on_gen_conn)
    gen.status_reply_signal.connect(on_gen_status)

    scope = ScopeThread("127.0.0.1", 5003)
    scope.connected_signal.connect(on_scope_conn)
    scope.frame_received_signal.connect(on_scope_frame)

    gen.start()
    scope.start()

    start_t = time.time()
    while time.time() - start_t < 1.5:
        app.processEvents()
        time.sleep(0.05)

    assert gen_connected is True
    assert scope_connected is True
    assert gen_status_received is True
    assert len(frames_received) > 0

    gen.stop()
    scope.stop()
