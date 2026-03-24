import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

try:
    from .ui.main_window import MainWindow
except ImportError:
    from app.ui.main_window import MainWindow


def _enable_windows_high_dpi() -> None:
    if not sys.platform.startswith("win"):
        return

    # 让 Qt 在多屏高分环境中保持更自然的缩放（避免整数舍入导致的布局挤压）
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

    # 提前启用进程 DPI 感知，避免被系统虚拟缩放导致模糊/尺寸异常
    try:
        import ctypes

        # PER_MONITOR_AWARE_V2（Win10/11 推荐）
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        )
    except Exception:
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                import ctypes

                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def main() -> None:
    _enable_windows_high_dpi()
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()

