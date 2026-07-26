import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from app import theme, widgets
import main as M

app = QApplication.instance() or QApplication([])
widgets.set_mode("dark")
app.setStyleSheet(theme.qss("dark"))
win = M.MainWindow()
win.resize(1280, 820)
win.show()
app.processEvents()
idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
win.stack.setCurrentIndex(idx)
win.nav_group.button(idx).setChecked(True)
app.processEvents(); app.processEvents()
out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shot.png"
win.grab().save(out)
print("saved", out)
