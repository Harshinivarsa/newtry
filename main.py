import sys
import os
import logging
from PyQt6.QtWidgets import QApplication
from ui_main import MainWindow

def setup_logging():
    log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.info("Starting ESP32 Function Generator + Digital Oscilloscope System...")

def main():
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("ESP32 Function Generator + Digital Oscilloscope System")
    
    window = MainWindow()
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
