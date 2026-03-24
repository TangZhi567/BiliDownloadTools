from __future__ import annotations

import os
import threading
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models.task import DownloadTask
from ..services.app_logger import get_log_dir, get_log_file
from ..services.link_parser import parse_links
from ..services.scheduler import DownloadScheduler


class _TaskSignalBus(QObject):
    task_updated = Signal(dict)
    parse_finished = Signal(dict)
    parse_failed = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("B站下载工具（PySide6）")
        self.resize(1180, 600)

        self._signal_bus = _TaskSignalBus()
        self._signal_bus.task_updated.connect(self._on_task_updated)
        self._signal_bus.parse_finished.connect(self._on_parse_finished)
        self._signal_bus.parse_failed.connect(self._on_parse_failed)
        self.scheduler = DownloadScheduler(max_concurrency=2, on_task_update=self._emit_task_update)

        self.parsed_videos: list[dict] = []
        self.task_rows: dict[str, int] = {}
        self.preview_row_to_video: dict[int, dict] = {}
        self.download_task_rows: dict[int, int] = {}
        self.available_qualities = ["1080p", "720p", "480p"]
        self._is_parsing = False

        self._init_ui()
        self._apply_styles()

    def _init_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        layout.setSpacing(12)
        layout.setContentsMargins(14, 14, 14, 14)

        title_bar = QFrame()
        title_bar_layout = QHBoxLayout(title_bar)
        title_bar_layout.setContentsMargins(0, 0, 0, 0)
        app_title = QLabel("B站视频下载工具")
        app_title.setObjectName("AppTitle")
        app_tip = QLabel("支持批量链接 · 并发下载 · 实时进度")
        app_tip.setObjectName("AppSubTitle")
        title_bar_layout.addWidget(app_title)
        title_bar_layout.addStretch(1)
        title_bar_layout.addWidget(app_tip)

        input_group = QGroupBox("1) 输入链接")
        input_layout = QVBoxLayout(input_group)

        self.link_input = QPlainTextEdit()
        self.link_input.setPlaceholderText("每行一个B站链接，支持批量粘贴")
        self.link_input.setMinimumHeight(100)
        self.link_input.setMaximumHeight(220)

        parse_row = QHBoxLayout()
        self.parse_btn = QPushButton("解析链接")
        self.parse_btn.clicked.connect(self._parse_links_async)
        self.parse_result = QLabel("待解析")
        self.parse_result.setObjectName("ParseResult")
        parse_row.addWidget(self.parse_btn)
        parse_row.addWidget(self.parse_result)
        parse_row.addStretch(1)

        input_layout.addWidget(self.link_input)
        input_layout.addLayout(parse_row)

        setting_group = QGroupBox("2) 下载参数")
        grid = QGridLayout(setting_group)

        self.quality_input = QComboBox()
        self.quality_input.addItems(self.available_qualities)
        self.open_log_btn = QPushButton("打开日志")
        self.open_log_btn.clicked.connect(self._open_log_dir)
        self.concurrent_input = QSpinBox()
        self.concurrent_input.setRange(1, 8)
        self.concurrent_input.setValue(2)
        self.concurrent_input.valueChanged.connect(self._on_concurrency_changed)

        grid.addWidget(QLabel("清晰度"), 0, 0)
        grid.addWidget(self.quality_input, 0, 1)
        grid.addWidget(QLabel("并发数"), 0, 2)
        grid.addWidget(self.concurrent_input, 0, 3)
        grid.addWidget(self.open_log_btn, 0, 4)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 0)
        grid.setColumnStretch(3, 0)
        grid.setColumnStretch(4, 0)

        path_group = QGroupBox("3) 路径与命名")
        path_grid = QGridLayout(path_group)

        self.save_path_input = QLineEdit(str(Path.cwd() / "downloads"))
        self.choose_dir_btn = QPushButton("选择目录")
        self.choose_dir_btn.clicked.connect(self._choose_dir)
        self.open_dir_btn = QPushButton("打开目录")
        self.open_dir_btn.clicked.connect(self._open_save_dir)
        self.naming_template_input = QLineEdit("({index})- {title}")
        self.naming_template_reset_btn = QPushButton("重置命名")
        self.naming_template_reset_btn.clicked.connect(
            lambda: self.naming_template_input.setText("({index})- {title}")
        )

        path_grid.addWidget(QLabel("保存目录"), 0, 0)
        path_grid.addWidget(self.save_path_input, 0, 1, 1, 2)
        path_grid.addWidget(self.choose_dir_btn, 0, 3)
        path_grid.addWidget(self.open_dir_btn, 0, 4)
        path_grid.addWidget(QLabel("命名规则"), 1, 0)
        path_grid.addWidget(self.naming_template_input, 1, 1, 1, 3)
        path_grid.addWidget(self.naming_template_reset_btn, 1, 4)

        path_grid.setColumnStretch(1, 1)
        path_grid.setColumnStretch(2, 0)
        path_grid.setColumnStretch(3, 0)
        path_grid.setColumnStretch(4, 0)

        self.path_hint = QLabel()
        self.path_hint.setObjectName("PathHint")
        self.path_hint.setText(f"当前下载目录：{Path(self.save_path_input.text()).resolve()}")
        path_grid.addWidget(self.path_hint, 2, 0, 1, 5)

        self.naming_hint = QLabel(
            "可用占位符：\n"
            "{index}=任务序号  {title}=视频标题  {shorttitle}/{shoutitle}=短标题(30字)\n"
            "{collection}=合集名  {shortcollection}=短合集名(30字)\n"
            "{quality}=清晰度  {bvid}=BV号  {date}=日期(YYYYMMDD)  {time}=时间(HHMMSS)"
        )
        self.naming_hint.setObjectName("PathHint")
        path_grid.addWidget(self.naming_hint, 3, 0, 1, 5)

        action_row = QHBoxLayout()
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.clicked.connect(lambda: self._set_preview_checked(True))
        self.unselect_all_btn = QPushButton("全不选")
        self.unselect_all_btn.clicked.connect(lambda: self._set_preview_checked(False))
        self.start_btn = QPushButton("开始批量下载")
        self.start_btn.clicked.connect(self._start_batch)
        action_row.addWidget(self.select_all_btn)
        action_row.addWidget(self.unselect_all_btn)
        action_row.addWidget(self.start_btn)
        action_row.addStretch(1)

        task_group = QGroupBox("4) 任务列表")
        task_layout = QVBoxLayout(task_group)
        self.task_table = QTableWidget(0, 9)
        self.task_table.setHorizontalHeaderLabels(["选择", "序号", "标题", "合集", "状态", "进度", "清晰度", "保存目录", "错误信息"])
        self.task_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.task_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.task_table.horizontalHeader().setSectionResizeMode(8, QHeaderView.Stretch)
        self.task_table.setAlternatingRowColors(True)
        self.task_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.task_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.task_table.setTextElideMode(Qt.ElideMiddle)
        self.task_table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.task_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.setContextMenuPolicy(Qt.ActionsContextMenu)
        copy_action = self.task_table.addAction("复制选中单元格")
        copy_action.triggered.connect(self._copy_selected_cells)
        copy_action.setShortcut("Ctrl+C")
        self.task_table.setMinimumHeight(360)
        task_layout.addWidget(self.task_table)

        self.batch_progress = QProgressBar()
        self.batch_progress.setValue(0)
        task_layout.addWidget(self.batch_progress)

        layout.addWidget(title_bar)
        layout.addWidget(input_group)
        config_row = QHBoxLayout()
        config_row.addWidget(setting_group, 1)
        config_row.addWidget(path_group, 2)

        layout.addLayout(config_row)
        layout.addLayout(action_row)
        layout.addWidget(task_group)

        # 强化任务列表区域占比
        layout.setStretch(0, 0)  # title
        layout.setStretch(1, 0)  # input
        layout.setStretch(2, 0)  # setting
        layout.setStretch(3, 0)  # action
        layout.setStretch(4, 1)  # task_group

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                font-family: 'Microsoft YaHei UI';
                font-size: 13px;
            }
            #AppTitle {
                font-size: 22px;
                font-weight: 700;
                color: #1f2937;
            }
            #AppSubTitle {
                color: #6b7280;
            }
            QGroupBox {
                border: 1px solid #dbe3ef;
                border-radius: 10px;
                margin-top: 12px;
                background: #f8fbff;
                font-weight: 600;
                padding: 8px 10px 10px 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #1f2937;
            }
            QPushButton {
                background-color: #3b82f6;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 7px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
            QPushButton:pressed {
                background-color: #1d4ed8;
            }
            QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 6px;
                background: #ffffff;
            }
            #ParseResult {
                color: #0f766e;
                font-weight: 600;
            }
            #PathHint {
                color: #475569;
                padding-top: 2px;
            }
            QTableWidget {
                border: 1px solid #dbe3ef;
                border-radius: 8px;
                gridline-color: #e5e7eb;
                alternate-background-color: #f8fafc;
                background: #ffffff;
            }
            QHeaderView::section {
                background: #eaf2ff;
                color: #1f2937;
                border: none;
                border-right: 1px solid #d1d5db;
                padding: 8px;
                font-weight: 700;
            }
            QProgressBar {
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                text-align: center;
                background: #eef2ff;
                min-height: 18px;
            }
            QProgressBar::chunk {
                background-color: #22c55e;
                border-radius: 8px;
            }
            """
        )

    def _parse_links_async(self) -> None:
        if self._is_parsing:
            return

        raw_text = self.link_input.toPlainText()
        if not raw_text.strip():
            QMessageBox.information(self, "提示", "请先输入链接再解析")
            return

        self._is_parsing = True
        self.parse_btn.setEnabled(False)
        self.parse_result.setText("解析中，请稍候...")

        threading.Thread(target=self._parse_worker, args=(raw_text,), daemon=True).start()

    def _parse_worker(self, raw_text: str) -> None:
        try:
            result = parse_links(raw_text)
            self._signal_bus.parse_finished.emit(result)
        except Exception as ex:
            self._signal_bus.parse_failed.emit(str(ex))

    def _on_parse_finished(self, result: dict) -> None:
        self._is_parsing = False
        self.parse_btn.setEnabled(True)

        self.parsed_videos = result["valid"]
        self.parse_result.setText(f"共 {result['total']} 条，合法 {len(result['valid'])} 条，非法 {len(result['invalid'])} 条")

        quality_set = set()
        for video in self.parsed_videos:
            for q in video.get("qualities", []):
                quality_set.add(q)
        if quality_set:
            self.available_qualities = sorted(quality_set, reverse=True)
            current = self.quality_input.currentText()
            self.quality_input.blockSignals(True)
            self.quality_input.clear()
            self.quality_input.addItems(self.available_qualities)
            if current in self.available_qualities:
                self.quality_input.setCurrentText(current)
            self.quality_input.blockSignals(False)

        if result["invalid"]:
            QMessageBox.warning(self, "存在非法链接", "检测到部分非B站链接，已自动忽略。")

        if result["total"] > 0 and not self.parsed_videos:
            QMessageBox.information(self, "解析结果", "未识别到有效B站链接，请检查输入格式。")
            self.task_table.setRowCount(0)
            self.preview_row_to_video.clear()
            return

        self._render_preview_tasks()

    def _on_parse_failed(self, error_text: str) -> None:
        self._is_parsing = False
        self.parse_btn.setEnabled(True)
        self.parse_result.setText("解析失败")
        QMessageBox.warning(self, "解析失败", error_text or "未知错误")

    def _render_preview_tasks(self) -> None:
        self.task_table.setRowCount(0)
        self.task_rows.clear()
        self.preview_row_to_video.clear()
        self.download_task_rows.clear()
        self.batch_progress.setValue(0)

        for i, video in enumerate(self.parsed_videos):
            row = self.task_table.rowCount()
            self.task_table.insertRow(row)

            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            check_item.setCheckState(Qt.Checked)
            self.task_table.setItem(row, 0, check_item)

            collection = video.get("collection", "")
            values = [
                str(i + 1),
                video.get("title", f"待下载视频 {i + 1}"),
                collection,
                "待下载",
                "0%",
                self.quality_input.currentText().strip() or "1080p",
                self.save_path_input.text().strip(),
                "",
            ]
            for col, val in enumerate(values, start=1):
                item = QTableWidgetItem(val)
                if col in (1, 5):
                    item.setTextAlignment(Qt.AlignCenter)
                self.task_table.setItem(row, col, item)

            self.preview_row_to_video[row] = {
                **video,
                "source_row": row,
                "collection": collection,
            }

    def _set_preview_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.task_table.rowCount()):
            item = self.task_table.item(row, 0)
            if item:
                item.setCheckState(state)

    def _collect_selected_videos(self) -> list[dict]:
        selected: list[dict] = []
        for row in range(self.task_table.rowCount()):
            check_item = self.task_table.item(row, 0)
            if not check_item or check_item.checkState() != Qt.Checked:
                continue
            if row not in self.preview_row_to_video:
                continue
            selected.append(self.preview_row_to_video[row])
        return selected

    def _choose_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择下载目录", self.save_path_input.text())
        if selected:
            path = str(Path(selected).resolve())
            self.save_path_input.setText(path)
            self.path_hint.setText(f"当前下载目录：{path}")

    def _open_save_dir(self) -> None:
        save_path = self._normalize_save_path(self.save_path_input.text().strip())
        os.makedirs(save_path, exist_ok=True)
        os.startfile(save_path)

    def _open_log_dir(self) -> None:
        log_dir = get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(log_dir))
        self.parse_result.setText(f"日志文件：{get_log_file()}")

    def _on_concurrency_changed(self, value: int) -> None:
        self.scheduler.set_concurrency(value)

    def _start_batch(self) -> None:
        if not self.parsed_videos:
            QMessageBox.information(self, "提示", "请先解析至少一个有效链接")
            return

        selected_videos = self._collect_selected_videos()
        if not selected_videos:
            QMessageBox.information(self, "提示", "请先勾选至少一个任务再开始下载")
            return

        self.task_rows.clear()
        self.download_task_rows.clear()
        self.batch_progress.setValue(0)

        save_path = self.save_path_input.text().strip()
        save_path = self._normalize_save_path(save_path)
        self.save_path_input.setText(save_path)
        self.path_hint.setText(f"当前下载目录：{save_path}")
        quality = self.quality_input.currentText().strip() or "1080p"
        naming_template = self.naming_template_input.text().strip() or "({index})- {title}"
        for row in range(self.task_table.rowCount()):
            if row not in self.preview_row_to_video:
                continue
            status = self.task_table.item(row, 4)
            progress = self.task_table.item(row, 5)
            quality_item = self.task_table.item(row, 6)
            save_item = self.task_table.item(row, 7)
            err_item = self.task_table.item(row, 8)
            if status:
                status.setText("待下载")
            if progress:
                progress.setText("0%")
            if quality_item:
                quality_item.setText(quality)
            if save_item:
                save_item.setText(save_path)
            if err_item:
                err_item.setText("")

        for v in selected_videos:
            v["naming_template"] = naming_template

        self.scheduler.start_batch(
            selected_videos,
            save_path=save_path,
            quality=quality,
            naming_template=naming_template,
        )

    def _emit_task_update(self, task: DownloadTask) -> None:
        self._signal_bus.task_updated.emit(asdict(task))

    def _on_task_updated(self, task: dict) -> None:
        task_id = task["id"]
        source_row = int(task.get("source_row", -1))
        row = self.task_rows.get(task_id)

        if row is None:
            if source_row >= 0 and source_row < self.task_table.rowCount():
                row = source_row
            else:
                row = self.task_table.rowCount()
                self.task_table.insertRow(row)
            self.task_rows[task_id] = row
            self.download_task_rows[row] = 1

        check_item = self.task_table.item(row, 0)
        if check_item:
            check_item.setCheckState(Qt.Checked)

        values = [
            str(task.get("queue_index", row + 1)),
            task.get("title", ""),
            task.get("collection", ""),
            task.get("status", ""),
            f"{task.get('progress', 0)}%",
            task.get("quality", ""),
            task.get("save_path", ""),
            task.get("error_message", ""),
        ]

        for col, val in enumerate(values, start=1):
            item = QTableWidgetItem(val)
            if col in (1, 5):
                item.setTextAlignment(Qt.AlignCenter)
            self.task_table.setItem(row, col, item)

        self._refresh_batch_progress()

    def _refresh_batch_progress(self) -> None:
        total = self.task_table.rowCount()
        if total == 0:
            self.batch_progress.setValue(0)
            return

        progress_sum = 0
        count = 0
        for row in range(total):
            if row not in self.download_task_rows:
                continue
            text = self.task_table.item(row, 5).text().replace("%", "") if self.task_table.item(row, 5) else "0"
            try:
                progress_sum += int(text)
                count += 1
            except ValueError:
                pass

        if count == 0:
            self.batch_progress.setValue(0)
        else:
            self.batch_progress.setValue(int(progress_sum / count))

    def _copy_selected_cells(self) -> None:
        ranges = self.task_table.selectedRanges()
        if not ranges:
            return

        block = ranges[0]
        lines: list[str] = []
        for row in range(block.topRow(), block.bottomRow() + 1):
            row_values: list[str] = []
            for col in range(block.leftColumn(), block.rightColumn() + 1):
                item = self.task_table.item(row, col)
                if col == 0:
                    row_values.append("√" if item and item.checkState() == Qt.Checked else "")
                else:
                    row_values.append(item.text() if item else "")
            lines.append("\t".join(row_values))

        text = "\n".join(lines)
        QApplication.clipboard().setText(text)

    @staticmethod
    def _normalize_save_path(path_text: str) -> str:
        if not path_text:
            path_text = str(Path.cwd() / "downloads")
        return str(Path(path_text).resolve())

