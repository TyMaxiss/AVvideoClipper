"""Main application window."""
from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from PySide6.QtCore import QSettings, QSize, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QRadioButton, QSizePolicy, QSlider, QSplitter, QStackedWidget,
    QStyle, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..analysis import AnalysisResult
from ..export import (
    LAYOUT_JOINED, LAYOUT_SEPARATE, MODE_AUTO, MODE_COPY, MODE_REENCODE, ExportSettings, plan_export, uses_copy,
)
from ..media import FFmpegNotFound, find_ffmpeg
from ..segments import DetectionSettings, Segment, find_segments, kept_duration
from ..timefmt import format_time, parse_time
from . import theme
from .timeline import TimelineWidget
from .workers import AnalyzeJob, ExportJob, PreviewLoader

VIDEO_EXTENSIONS = {
    ".avi", ".mp4", ".m4v", ".mov", ".mkv", ".mpg", ".mpeg", ".ts", ".m2ts", ".mts", ".wmv", ".asf",
    ".flv", ".3gp", ".vob", ".dv", ".webm", ".ogv", ".mod", ".tod", ".divx", ".xvid", ".h264", ".264",
}

QUEUED, ANALYZING, READY, EXPORTING, DONE, ERROR, STOPPED = (
    "queued", "analyzing", "ready", "exporting", "done", "error", "stopped")

HELP_TEXT = """
<h3>Как это работает</h3>
<p>Программа покадрово просматривает запись и оценивает «качество сигнала» каждого кадра.
Белый шум («снег») случаен: соседние кадры шума совсем не похожи друг на друга.
Настоящее изображение, наоборот, от кадра к кадру меняется плавно, даже при быстром
движении камеры, плохом приёме или сильном сжатии.</p>
<p>Участки, где оценка выше <b>порога</b> (красная пунктирная линия на шкале), считаются видео
и сохраняются. Остальное вырезается. Неподвижные элементы, одинаковые во всех кадрах
(чёрные поля по краям, надписи OSD приёмника или видеорегистратора), учитываются автоматически.</p>
<h3>Порядок работы</h3>
<ol>
<li>Перетащите видеофайлы в окно или нажмите «Добавить файлы». Анализ начнётся сам.</li>
<li>Проверьте найденные фрагменты: наведите курсор на шкалу, чтобы увидеть кадр.
Зелёным отмечено то, что останется.</li>
<li>При необходимости подвиньте порог (перетащите линию на шкале), снимите галочки у лишних
фрагментов или поправьте время начала и конца в таблице двойным щелчком.</li>
<li>Нажмите «Сохранить результат» или «Обработать все».</li>
</ol>
<h3>Настройки</h3>
<ul>
<li><b>Порог сигнала</b>: чем ниже, тем больше «шумного» видео останется.</li>
<li><b>Мин. фрагмент</b>: более короткие вспышки изображения среди шума отбрасываются.</li>
<li><b>Не резать разрывы до</b>: если сигнал пропал ненадолго посреди видео, этот кусок шума
остаётся, чтобы видео не распадалось на части.</li>
<li><b>Запас по краям</b>: сколько секунд добавить до и после каждого фрагмента.</li>
<li><b>Режим «Авто»</b>: файлы MJPEG/DV (типичные для AV-захвата) режутся без перекодирования,
без потери качества и точно по кадрам. Остальные перекодируются в H.264 (MP4) в высоком качестве.</li>
</ul>
<p>Колесо мыши над шкалой меняет масштаб, Shift+колесо прокручивает, двойной щелчок сбрасывает масштаб.
Стрелки ←/→ двигают позицию на 1 с (с Shift — на 0,1 с).</p>
"""


@dataclass
class FileItem:
    id: int
    path: str
    state: str = QUEUED
    progress: float = 0.0
    result: Optional[AnalysisResult] = None
    segments: List[Segment] = field(default_factory=list)
    manual: bool = False
    outputs: List[str] = field(default_factory=list)
    error: str = ""
    export_after: bool = False
    playhead: Optional[float] = None

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _overlap(a: Segment, b: Segment) -> float:
    return max(0.0, min(a.end, b.end) - max(a.start, b.start))


class MainWindow(QMainWindow):
    def __init__(self, files=(), store: Optional[QSettings] = None):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — вырезание белого шума из AV-видео")
        self.setWindowIcon(theme.app_icon())
        self.setAcceptDrops(True)

        self.items: Dict[int, FileItem] = {}
        self._next_id = 1
        self.queue: Deque[Tuple[str, int]] = deque()
        self.running = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)

        self.store = store if store is not None else QSettings("AVvideoClipper", "AVvideoClipper")
        self.detection = DetectionSettings()
        self.export_settings = ExportSettings()
        self.auto_export = False
        self._load_settings()

        self.preview = PreviewLoader(self)
        self.preview.frameReady.connect(self._on_frame)
        self._wanted_frame: Optional[Tuple[str, int]] = None
        self._frame_image: Optional[QImage] = None
        self._hover_t: Optional[float] = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(60)
        self._hover_timer.timeout.connect(self._request_hover_preview)
        self._filling_table = False
        self._table_item: Optional[int] = None
        self._shown_item: Optional[int] = None

        self._build_ui()
        self._settings_to_widgets()
        self._restore_window()
        self._show_current()
        self._update_buttons()
        QTimer.singleShot(0, self._check_ffmpeg)
        if files:
            self.add_paths(list(files))

    # ================================================================ UI setup
    def _build_ui(self) -> None:
        self._build_menu()
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 960])
        splitter.setChildrenCollapsible(False)
        self.splitter = splitter
        self.setCentralWidget(splitter)
        self.status_label = QLabel()
        self.status_label.setObjectName("muted")
        self.statusBar().addPermanentWidget(self.status_label)

    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("Файл")
        act = QAction("Добавить файлы…", self)
        act.setShortcut(QKeySequence.Open)
        act.triggered.connect(self.choose_files)
        m.addAction(act)
        act = QAction("Добавить папку…", self)
        act.triggered.connect(self.choose_folder)
        m.addAction(act)
        m.addSeparator()
        act = QAction("Выход", self)
        act.setShortcut(QKeySequence.Quit)
        act.triggered.connect(self.close)
        m.addAction(act)
        h = self.menuBar().addMenu("Справка")
        act = QAction("Как пользоваться", self)
        act.setShortcut(QKeySequence.HelpContents)
        act.triggered.connect(self.show_help)
        h.addAction(act)
        act = QAction("О программе", self)
        act.triggered.connect(self.show_about)
        h.addAction(act)

    def _build_left(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 6, 10)
        style = self.style()

        row = QHBoxLayout()
        self.btn_add = QPushButton(style.standardIcon(QStyle.SP_FileIcon), "Добавить файлы…")
        self.btn_add.clicked.connect(self.choose_files)
        self.btn_add_dir = QPushButton(style.standardIcon(QStyle.SP_DirIcon), "Папку…")
        self.btn_add_dir.clicked.connect(self.choose_folder)
        row.addWidget(self.btn_add, 1)
        row.addWidget(self.btn_add_dir)
        v.addLayout(row)

        self.file_list = QListWidget()
        self.file_list.setMinimumHeight(140)
        self.file_list.setIconSize(QSize(14, 14))
        self.file_list.setWordWrap(True)
        self.file_list.currentItemChanged.connect(lambda *_: self._show_current())
        delete = QShortcut(QKeySequence.Delete, self.file_list)
        delete.setContext(Qt.WidgetShortcut)
        delete.activated.connect(self.remove_current)
        v.addWidget(self.file_list, 1)

        row = QHBoxLayout()
        self.btn_remove = QPushButton("Убрать из списка")
        self.btn_remove.clicked.connect(self.remove_current)
        self.btn_clear = QPushButton("Очистить")
        self.btn_clear.clicked.connect(self.clear_all)
        row.addWidget(self.btn_remove, 1)
        row.addWidget(self.btn_clear)
        v.addLayout(row)

        v.addWidget(self._build_detection_box())
        v.addWidget(self._build_export_box())

        self.btn_run_all = QPushButton("▶  Обработать все файлы")
        self.btn_run_all.setObjectName("primary")
        self.btn_run_all.setMinimumHeight(36)
        self.btn_run_all.setToolTip("Проанализировать все файлы из списка и сохранить результаты")
        self.btn_run_all.clicked.connect(self.process_all)
        self.btn_stop = QPushButton("Остановить")
        self.btn_stop.clicked.connect(self.stop_all)
        row = QHBoxLayout()
        row.addWidget(self.btn_run_all, 1)
        row.addWidget(self.btn_stop)
        v.addLayout(row)
        w.setMinimumWidth(330)
        return w

    def _spin(self, lo: float, hi: float, step: float, suffix: str = " с") -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setDecimals(1)
        s.setSuffix(suffix)
        s.setKeyboardTracking(False)
        s.setMaximumWidth(110)
        return s

    def _build_detection_box(self) -> QGroupBox:
        box = QGroupBox("Распознавание шума")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setRange(2, 98)
        self.threshold_label = QLabel()
        self.threshold_label.setMinimumWidth(38)
        row = QHBoxLayout()
        row.addWidget(self.threshold_slider, 1)
        row.addWidget(self.threshold_label)
        tip = ("Кадры с оценкой сигнала выше порога считаются видео.\n"
               "Ниже порог: больше «шумного» видео останется. Выше: резать строже.\n"
               "Порог можно менять, перетаскивая красную линию на шкале.")
        self.threshold_slider.setToolTip(tip)
        form.addRow(self._label("Порог сигнала", tip), row)
        self.threshold_slider.valueChanged.connect(self._on_threshold_slider)

        self.min_segment_spin = self._spin(0.0, 600.0, 0.5)
        tip = "Фрагменты изображения короче этого значения (случайные вспышки среди шума) отбрасываются."
        self.min_segment_spin.setToolTip(tip)
        form.addRow(self._label("Мин. фрагмент", tip), self.min_segment_spin)

        self.merge_gap_spin = self._spin(0.0, 600.0, 0.5)
        tip = ("Если посреди видео сигнал пропал ненадолго (короче этого значения),\n"
               "этот кусок шума не вырезается и видео не разрывается на части.")
        self.merge_gap_spin.setToolTip(tip)
        form.addRow(self._label("Не резать разрывы до", tip), self.merge_gap_spin)

        self.pad_spin = self._spin(0.0, 10.0, 0.1)
        tip = "Сколько секунд добавить до и после каждого фрагмента."
        self.pad_spin.setToolTip(tip)
        form.addRow(self._label("Запас по краям", tip), self.pad_spin)

        self.blank_check = QCheckBox("Вырезать однотонный экран\n(чёрный/синий «нет сигнала»)")
        self.blank_check.setToolTip("Кадры почти одного цвета (заставка «нет сигнала») тоже считаются шумом.")
        form.addRow(self.blank_check)

        self.btn_defaults = QPushButton("Сбросить настройки")
        self.btn_defaults.clicked.connect(self._reset_detection)
        form.addRow(self.btn_defaults)

        for spin in (self.min_segment_spin, self.merge_gap_spin, self.pad_spin):
            spin.valueChanged.connect(self._on_detection_widgets)
        self.blank_check.toggled.connect(self._on_detection_widgets)
        return box

    def _build_export_box(self) -> QGroupBox:
        box = QGroupBox("Сохранение")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.layout_combo = QComboBox()
        self.layout_combo.addItem("Одним файлом (склеить)", LAYOUT_JOINED)
        self.layout_combo.addItem("Каждый фрагмент отдельно", LAYOUT_SEPARATE)
        form.addRow("Результат", self.layout_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Авто (рекомендуется)", MODE_AUTO)
        self.mode_combo.addItem("Точно: перекодировать в H.264", MODE_REENCODE)
        self.mode_combo.addItem("Быстро: без перекодирования", MODE_COPY)
        self.mode_combo.setToolTip(
            "Авто: MJPEG/DV и другие «покадровые» форматы режутся без перекодирования (без потерь, точно),\n"
            "остальные перекодируются в H.264 (MP4) в высоком качестве.\n"
            "Точно: всегда перекодировать, границы точны до кадра.\n"
            "Быстро: без перекодирования и потерь, но для H.264/MPEG границы сдвигаются\n"
            "к ближайшим ключевым кадрам (может остаться немного шума).")
        form.addRow("Режим", self.mode_combo)

        self.dir_same = QRadioButton("Рядом с исходным файлом")
        self.dir_custom = QRadioButton("В папку:")
        self.dir_group = QButtonGroup(self)
        self.dir_group.addButton(self.dir_same)
        self.dir_group.addButton(self.dir_custom)
        self.dir_button = QPushButton("Выбрать…")
        self.dir_button.clicked.connect(self._choose_output_dir)
        self.dir_label = QLabel()
        self.dir_label.setObjectName("muted")
        self.dir_label.setWordWrap(True)
        form.addRow(self.dir_same)
        row = QHBoxLayout()
        row.addWidget(self.dir_custom)
        row.addWidget(self.dir_button)
        row.addStretch(1)
        form.addRow(row)
        form.addRow(self.dir_label)

        self.auto_check = QCheckBox("Сохранять сразу после анализа")
        self.auto_check.setToolTip("Полностью автоматический режим: результат сохраняется без проверки.")
        form.addRow(self.auto_check)

        self.layout_combo.currentIndexChanged.connect(self._on_export_widgets)
        self.mode_combo.currentIndexChanged.connect(self._on_export_widgets)
        self.dir_same.toggled.connect(self._on_export_widgets)
        self.auto_check.toggled.connect(self._on_export_widgets)
        return box

    @staticmethod
    def _label(text: str, tip: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setToolTip(tip)
        return lbl

    def _build_right(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 10, 10, 10)
        self.title_label = QLabel()
        self.title_label.setObjectName("title")
        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("muted")
        self.subtitle_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.title_label)
        v.addWidget(self.subtitle_label)

        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)

        # page 0: nothing selected
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch(1)
        hint = QLabel("Перетащите сюда видеофайлы\nили нажмите «Добавить файлы…»")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("font-size: 18px; color: #8b9098;")
        ev.addWidget(hint)
        sub = QLabel("Программа сама найдёт участки с белым шумом («снегом») и вырежет их,\n"
                     "оставив только настоящее видео.")
        sub.setAlignment(Qt.AlignCenter)
        sub.setObjectName("muted")
        ev.addWidget(sub)
        btn = QPushButton("Добавить файлы…")
        btn.setObjectName("primary")
        btn.clicked.connect(self.choose_files)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        row.addStretch(1)
        ev.addSpacing(10)
        ev.addLayout(row)
        ev.addStretch(2)
        self.stack.addWidget(empty)

        # page 1: file view
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 6, 0, 0)
        top = QHBoxLayout()

        left = QVBoxLayout()
        self.preview_label = QLabel("Нет кадра")
        self.preview_label.setObjectName("preview")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(360, 250)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_caption = QLabel()
        self.preview_caption.setObjectName("muted")
        left.addWidget(self.preview_label, 1)
        left.addWidget(self.preview_caption)
        top.addLayout(left, 3)

        right = QVBoxLayout()
        right.addWidget(QLabel("Фрагменты с видео"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Оставить", "Начало", "Конец", "Длительность"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Stretch)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._on_table_changed)
        self.table.currentCellChanged.connect(self._on_table_cell)
        self.table.setToolTip("Снимите галочку, чтобы не сохранять фрагмент.\n"
                              "Двойной щелчок по времени: изменить начало или конец.")
        right.addWidget(self.table, 1)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        right.addWidget(self.summary_label)
        row = QHBoxLayout()
        self.manual_label = QLabel("Фрагменты изменены вручную")
        self.manual_label.setStyleSheet("color: #e3b341;")
        self.btn_recompute = QPushButton("Пересчитать")
        self.btn_recompute.setToolTip("Отменить ручные правки и заново найти фрагменты по текущим настройкам")
        self.btn_recompute.clicked.connect(self._recompute_current)
        row.addWidget(self.manual_label)
        row.addStretch(1)
        row.addWidget(self.btn_recompute)
        right.addLayout(row)
        top.addLayout(right, 2)
        pv.addLayout(top, 1)

        self.timeline = TimelineWidget()
        self.timeline.setMinimumHeight(170)
        self.timeline.thresholdChanged.connect(self._on_timeline_threshold)
        self.timeline.hoverChanged.connect(self._on_timeline_hover)
        self.timeline.positionClicked.connect(self._on_timeline_click)
        pv.addWidget(self.timeline)
        legend = QLabel(
            "<span style='color:#3fb950'>■</span> останется &nbsp; "
            "<span style='color:#8cbeff'>▲</span> качество сигнала &nbsp; "
            "<span style='color:#ff5c5c'>- -</span> порог (можно перетащить) &nbsp; "
            "<span style='color:#4c6ef5'>▬</span> однотонный экран &nbsp; "
            "<span style='color:#ffd166'>|</span> позиция")
        legend.setObjectName("muted")
        pv.addWidget(legend)

        row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximumWidth(260)
        self.state_label = QLabel()
        self.state_label.setWordWrap(True)
        self.state_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.progress_bar)
        row.addWidget(self.state_label, 1)
        self.btn_open_result = QPushButton("Открыть результат")
        self.btn_open_result.clicked.connect(self._open_result)
        self.btn_open_folder = QPushButton("Показать папку")
        self.btn_open_folder.clicked.connect(self._open_folder)
        self.btn_export = QPushButton("Сохранить результат")
        self.btn_export.setObjectName("primary")
        self.btn_export.setMinimumHeight(34)
        self.btn_export.clicked.connect(self.export_current)
        row.addWidget(self.btn_open_result)
        row.addWidget(self.btn_open_folder)
        row.addWidget(self.btn_export)
        pv.addLayout(row)
        self.stack.addWidget(page)
        return w

    # ============================================================ settings
    def _load_settings(self) -> None:
        try:
            det = json.loads(self.store.value("detection", "{}") or "{}")
            self.detection = DetectionSettings.from_dict(det)
        except (ValueError, TypeError):
            self.detection = DetectionSettings()
        try:
            exp = json.loads(self.store.value("export", "{}") or "{}")
            self.export_settings = ExportSettings.from_dict(exp)
        except (ValueError, TypeError):
            self.export_settings = ExportSettings()
        self.auto_export = str(self.store.value("auto_export", "false")).lower() == "true"

    def _save_settings(self) -> None:
        self.store.setValue("detection", json.dumps(self.detection.to_dict()))
        self.store.setValue("export", json.dumps(self.export_settings.to_dict()))
        self.store.setValue("auto_export", "true" if self.auto_export else "false")

    def _restore_window(self) -> None:
        geo = self.store.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            self.resize(1360, 860)
        state = self.store.value("splitter")
        if state is not None:
            self.splitter.restoreState(state)

    def _settings_to_widgets(self) -> None:
        d = self.detection
        widgets = (self.threshold_slider, self.min_segment_spin, self.merge_gap_spin, self.pad_spin,
                   self.blank_check, self.layout_combo, self.mode_combo, self.dir_same, self.auto_check)
        for wdg in widgets:
            wdg.blockSignals(True)
        self.threshold_slider.setValue(int(round(d.threshold * 100)))
        self.threshold_label.setText(f"{int(round(d.threshold * 100))}%")
        self.min_segment_spin.setValue(d.min_segment)
        self.merge_gap_spin.setValue(d.merge_gap)
        self.pad_spin.setValue(d.pad_before)
        self.blank_check.setChecked(d.blank_is_noise)
        e = self.export_settings
        self.layout_combo.setCurrentIndex(max(0, self.layout_combo.findData(e.layout)))
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(e.mode)))
        custom = bool(e.output_dir)
        self.dir_same.setChecked(not custom)
        self.dir_custom.setChecked(custom)
        self.dir_label.setText(e.output_dir if custom else "")
        self.auto_check.setChecked(self.auto_export)
        for wdg in widgets:
            wdg.blockSignals(False)
        self.timeline.set_threshold(d.threshold)

    def _on_threshold_slider(self, value: int) -> None:
        self.detection.threshold = value / 100.0
        self.threshold_label.setText(f"{value}%")
        self.timeline.set_threshold(self.detection.threshold)
        self._detection_changed()

    def _on_timeline_threshold(self, value: float) -> None:
        self.threshold_slider.blockSignals(True)
        self.threshold_slider.setValue(int(round(value * 100)))
        self.threshold_slider.blockSignals(False)
        self.threshold_label.setText(f"{int(round(value * 100))}%")
        self.detection.threshold = value
        self._detection_changed()

    def _on_detection_widgets(self, *_):
        self.detection.min_segment = self.min_segment_spin.value()
        self.detection.merge_gap = self.merge_gap_spin.value()
        self.detection.pad_before = self.detection.pad_after = self.pad_spin.value()
        self.detection.blank_is_noise = self.blank_check.isChecked()
        self._detection_changed()

    def _reset_detection(self) -> None:
        self.detection = DetectionSettings()
        self._settings_to_widgets()
        self._detection_changed()

    def _detection_changed(self) -> None:
        for item in self.items.values():
            if item.result is not None and not item.manual:
                self._recompute(item)
                self._refresh_list_item(item)
        self._save_settings()
        self._show_current(keep_preview=True)

    def _on_export_widgets(self, *_):
        e = self.export_settings
        e.layout = self.layout_combo.currentData()
        e.mode = self.mode_combo.currentData()
        if self.dir_same.isChecked():
            e.output_dir = ""
            self.dir_label.setText("")
        elif not e.output_dir:
            self._choose_output_dir()
        self.auto_export = self.auto_check.isChecked()
        self._save_settings()
        self._show_current(keep_preview=True)

    def _choose_output_dir(self) -> None:
        start = self.export_settings.output_dir or str(self.store.value("last_dir", ""))
        path = QFileDialog.getExistingDirectory(self, "Папка для результатов", start)
        if path:
            self.export_settings.output_dir = path
            self.dir_custom.setChecked(True)
            self.dir_label.setText(path)
        elif not self.export_settings.output_dir:
            self.dir_same.setChecked(True)
        self._save_settings()
        self._show_current(keep_preview=True)

    # ============================================================ files
    def choose_files(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Выберите видеозаписи", str(self.store.value("last_dir", "")),
            f"Видео ({exts});;Все файлы (*)")
        if paths:
            self.store.setValue("last_dir", os.path.dirname(paths[0]))
            self.add_paths(paths)

    def choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Папка с видеозаписями", str(self.store.value("last_dir", "")))
        if path:
            self.store.setValue("last_dir", path)
            self.add_paths([path])

    @staticmethod
    def _expand(paths: List[str]) -> List[str]:
        out = []
        for p in paths:
            if os.path.isdir(p):
                for root, dirs, files in os.walk(p):
                    dirs.sort()
                    for f in sorted(files):
                        if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS:
                            out.append(os.path.join(root, f))
            elif os.path.isfile(p):
                out.append(p)
        return out

    def add_paths(self, paths: List[str]) -> None:
        known = {_norm(i.path) for i in self.items.values()}
        added = []
        for path in self._expand(paths):
            if _norm(path) in known:
                continue
            known.add(_norm(path))
            item = FileItem(self._next_id, os.path.abspath(path))
            self._next_id += 1
            self.items[item.id] = item
            lw = QListWidgetItem()
            lw.setData(Qt.UserRole, item.id)
            self.file_list.addItem(lw)
            self._refresh_list_item(item)
            self.queue.append(("analyze", item.id))
            added.append(item)
        if not added:
            if paths:
                self.statusBar().showMessage("Новых видеофайлов не найдено", 5000)
            return
        if self.file_list.currentItem() is None:
            self.file_list.setCurrentRow(self._row_of(added[0].id))
        self.statusBar().showMessage(f"Добавлено файлов: {len(added)}", 4000)
        self._pump()

    def _row_of(self, item_id: int) -> int:
        for row in range(self.file_list.count()):
            if self.file_list.item(row).data(Qt.UserRole) == item_id:
                return row
        return -1

    def current_item(self) -> Optional[FileItem]:
        lw = self.file_list.currentItem()
        if lw is None:
            return None
        return self.items.get(lw.data(Qt.UserRole))

    def remove_current(self) -> None:
        item = self.current_item()
        if item is None:
            return
        self._drop_item(item.id)
        self._show_current()
        self._update_buttons()

    def clear_all(self) -> None:
        for item_id in list(self.items):
            self._drop_item(item_id)
        self._show_current()
        self._update_buttons()

    def _drop_item(self, item_id: int) -> None:
        self.queue = deque(q for q in self.queue if q[1] != item_id)
        if self.running is not None and self.running.item_id == item_id:
            self.running.cancel.set()
        row = self._row_of(item_id)
        if row >= 0:
            self.file_list.takeItem(row)
        self.items.pop(item_id, None)

    # ============================================================ jobs
    def _pump(self) -> None:
        while self.running is None and self.queue:
            kind, item_id = self.queue.popleft()
            item = self.items.get(item_id)
            if item is None:
                continue
            if kind == "analyze":
                job = AnalyzeJob(item_id, item.path)
                item.state = ANALYZING
            else:
                if item.result is None or not any(s.enabled for s in item.segments):
                    item.state = READY if item.result is not None else item.state
                    self._refresh_list_item(item)
                    continue
                job = ExportJob(item_id, item.result.info, item.segments, self.export_settings)
                item.state = EXPORTING
            item.progress = 0.0
            item.error = ""
            job.setAutoDelete(False)
            job.signals.progress.connect(self._on_job_progress)
            job.signals.finished.connect(self._on_job_finished)
            job.signals.failed.connect(self._on_job_failed)
            job.signals.cancelled.connect(self._on_job_cancelled)
            self.running = job
            self._refresh_list_item(item)
            self.pool.start(job)
        self._update_buttons()
        self._show_state()

    def _job_done(self, item_id: int):
        job = self.running
        if job is not None and job.item_id == item_id:
            self.running = None
        return job

    def _on_job_progress(self, item_id: int, fraction: float) -> None:
        item = self.items.get(item_id)
        if item is None:
            return
        item.progress = fraction
        self._refresh_list_item(item)
        if item is self.current_item():
            self._show_state()

    def _on_job_finished(self, item_id: int, result) -> None:
        job = self._job_done(item_id)
        item = self.items.get(item_id)
        if item is not None and job is not None:
            if job.kind == "analyze":
                item.result = result
                item.manual = False
                item.segments = find_segments(result, self.detection)
                item.state = READY
                if item.segments and (item.export_after or self.auto_export):
                    self.queue.appendleft(("export", item.id))
                item.export_after = False
                item.playhead = self._initial_playhead(item)
            else:
                item.outputs = list(result)
                item.state = DONE
                if result:
                    self.statusBar().showMessage("Сохранено: " + ", ".join(os.path.basename(p) for p in result), 8000)
            self._refresh_list_item(item)
            if item is self.current_item():
                self._show_current(keep_preview=job.kind != "analyze")
        self._pump()

    def _on_job_failed(self, item_id: int, message: str) -> None:
        job = self._job_done(item_id)
        item = self.items.get(item_id)
        if item is not None:
            item.state = ERROR
            item.error = message
            item.export_after = False
            self._refresh_list_item(item)
            what = "анализа" if job is not None and job.kind == "analyze" else "сохранения"
            self.statusBar().showMessage(f"{item.name}: ошибка {what}", 8000)
            if item is self.current_item():
                self._show_current(keep_preview=True)
        self._pump()

    def _on_job_cancelled(self, item_id: int) -> None:
        self._job_done(item_id)
        item = self.items.get(item_id)
        if item is not None:
            item.state = READY if item.result is not None else STOPPED
            item.export_after = False
            self._refresh_list_item(item)
            if item is self.current_item():
                self._show_current(keep_preview=True)
        self._pump()

    def process_all(self) -> None:
        queued = {q[1] for q in self.queue}
        for row in range(self.file_list.count()):
            item = self.items.get(self.file_list.item(row).data(Qt.UserRole))
            if item is None or item.state == DONE:
                continue
            running = self.running is not None and self.running.item_id == item.id
            if item.result is None:
                item.export_after = True
                if item.id not in queued and not running:
                    self.queue.append(("analyze", item.id))
                    item.state = QUEUED
            elif item.state in (READY, ERROR) and not running and item.id not in queued:
                self.queue.append(("export", item.id))
                item.state = QUEUED
            self._refresh_list_item(item)
        self._pump()

    def export_current(self) -> None:
        item = self.current_item()
        if item is None or item.result is None:
            return
        if not any(s.enabled for s in item.segments):
            QMessageBox.information(self, APP_NAME, "Нет отмеченных фрагментов для сохранения.")
            return
        if any(q == ("export", item.id) for q in self.queue):
            return
        # an explicit request goes ahead of the queued analyses
        self.queue.appendleft(("export", item.id))
        item.state = QUEUED
        self._refresh_list_item(item)
        self._pump()
        self._show_state()

    def stop_all(self) -> None:
        for kind, item_id in self.queue:
            item = self.items.get(item_id)
            if item is not None:
                item.state = READY if item.result is not None else STOPPED
                item.export_after = False
                self._refresh_list_item(item)
        self.queue.clear()
        if self.running is not None:
            self.running.cancel.set()
        self._update_buttons()
        self._show_state()

    # ============================================================ segments
    def _recompute(self, item: FileItem) -> None:
        old = item.segments
        new = find_segments(item.result, self.detection)
        for seg in new:
            for o in old:
                if not o.enabled and _overlap(seg, o) > 0.5 * min(seg.duration, o.duration):
                    seg.enabled = False
        item.segments = new

    @staticmethod
    def _initial_playhead(item: FileItem) -> float:
        """A frame well inside the first fragment (its edges are the noisy margins)."""
        if not item.segments:
            return 0.0
        seg = item.segments[0]
        return seg.start + min(1.0, seg.duration / 2)

    def _recompute_current(self) -> None:
        item = self.current_item()
        if item is None or item.result is None:
            return
        item.manual = False
        item.segments = find_segments(item.result, self.detection)
        self._refresh_list_item(item)
        self._show_current(keep_preview=True)

    def _fill_table(self, item: Optional[FileItem]) -> None:
        self._filling_table = True
        keep_row = self.table.currentRow() if item is not None and item.id == self._table_item else -1
        self._table_item = item.id if item is not None else None
        self.table.setRowCount(0)
        segs = item.segments if item is not None else []
        self.table.setRowCount(len(segs))
        for row, seg in enumerate(segs):
            check = QTableWidgetItem(f"{row + 1}")
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Checked if seg.enabled else Qt.Unchecked)
            self.table.setItem(row, 0, check)
            for col, value in ((1, seg.start), (2, seg.end)):
                cell = QTableWidgetItem(format_time(value))
                cell.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                self.table.setItem(row, col, cell)
            dur = QTableWidgetItem(format_time(seg.duration))
            dur.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, 3, dur)
        if 0 <= keep_row < len(segs):
            self.table.setCurrentCell(keep_row, 0)
        self._filling_table = False

    def _on_table_changed(self, cell: QTableWidgetItem) -> None:
        if self._filling_table:
            return
        item = self.current_item()
        if item is None or cell.row() >= len(item.segments):
            return
        seg = item.segments[cell.row()]
        if cell.column() == 0:
            seg.enabled = cell.checkState() == Qt.Checked
        elif cell.column() in (1, 2):
            try:
                value = parse_time(cell.text())
            except ValueError:
                value = None
            duration = item.result.duration
            if value is not None:
                value = min(max(value, 0.0), duration)
                start, end = (value, seg.end) if cell.column() == 1 else (seg.start, value)
                if end - start >= 0.1:
                    seg.start, seg.end = start, end
                    item.manual = True
                    self._normalize(item)
                    item.playhead = value if cell.column() == 1 else max(0.0, value - 0.1)
                else:
                    self.statusBar().showMessage("Начало фрагмента должно быть раньше конца", 5000)
            else:
                self.statusBar().showMessage("Не понял время. Пример: 1:23.4 или 83.4", 5000)
        self._refresh_list_item(item)
        self._show_current(keep_preview=cell.column() == 0)

    @staticmethod
    def _normalize(item: FileItem) -> None:
        segs = sorted(item.segments, key=lambda s: s.start)
        out: List[Segment] = []
        for s in segs:
            if out and s.start <= out[-1].end:
                out[-1].end = max(out[-1].end, s.end)
                out[-1].enabled = out[-1].enabled or s.enabled
            else:
                out.append(s)
        item.segments = out

    def _on_table_cell(self, row: int, col: int, *_):
        if self._filling_table:
            return
        item = self.current_item()
        if item is None or not (0 <= row < len(item.segments)):
            return
        seg = item.segments[row]
        t = max(seg.start, seg.end - 0.1) if col == 2 else seg.start
        self._set_playhead(item, t)

    # ============================================================ view
    def _item_status(self, item: FileItem) -> str:
        if item.state == QUEUED:
            return "в очереди"
        if item.state == ANALYZING:
            return f"анализ… {int(item.progress * 100)}%"
        if item.state == EXPORTING:
            return f"сохранение… {int(item.progress * 100)}%"
        if item.state == ERROR:
            return "ошибка: " + (item.error.splitlines()[-1] if item.error else "неизвестная")
        if item.state == STOPPED:
            return "остановлено"
        if item.result is not None:
            enabled = [s for s in item.segments if s.enabled]
            if not item.segments:
                text = "видео не найдено"
            else:
                text = (f"фрагментов: {len(enabled)} · оставить {format_time(kept_duration(item.segments), 0)}"
                        f" из {format_time(item.result.duration, 0)}")
            if item.state == DONE:
                text = "✔ сохранено · " + text
            return text
        return ""

    def _refresh_list_item(self, item: FileItem) -> None:
        row = self._row_of(item.id)
        if row < 0:
            return
        lw = self.file_list.item(row)
        lw.setText(f"{item.name}\n{self._item_status(item)}")
        lw.setToolTip(item.path + (f"\n\n{item.error}" if item.error else ""))
        style = self.style()
        icon = {
            QUEUED: QStyle.SP_BrowserReload, ANALYZING: QStyle.SP_BrowserReload,
            EXPORTING: QStyle.SP_DialogSaveButton, READY: QStyle.SP_MediaPlay,
            DONE: QStyle.SP_DialogApplyButton, ERROR: QStyle.SP_MessageBoxCritical,
            STOPPED: QStyle.SP_MediaStop,
        }.get(item.state, QStyle.SP_FileIcon)
        lw.setIcon(style.standardIcon(icon))
        if item.state == DONE:
            lw.setForeground(theme.OK)
        elif item.state == ERROR:
            lw.setForeground(theme.ERROR)
        else:
            lw.setForeground(theme.TEXT)

    def _show_current(self, keep_preview: bool = False) -> None:
        item = self.current_item()
        if item is None:
            self.stack.setCurrentIndex(0)
            self.title_label.setText(APP_NAME)
            self.subtitle_label.setText("Вырезает белый шум («снег») из AV-видеозаписей")
            self.timeline.set_result(None)
            self._update_buttons()
            return
        self.stack.setCurrentIndex(1)
        if item.id != self._shown_item:
            # another file: never keep showing a frame of the previous one
            self._shown_item = item.id
            self._frame_image = None
            self._wanted_frame = None
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("")
            keep_preview = False
        self.title_label.setText(item.name)
        info = item.result.info if item.result is not None else None
        if info is not None:
            parts = [f"{info.width}×{info.height}" if info.width else "",
                     f"{info.fps:.2f} к/с".replace(".00 ", " ") if info.fps else "",
                     format_time(item.result.duration, 0), info.video_codec,
                     "со звуком" if info.has_audio else "без звука"]
            if item.result.static_share > 0.02:
                parts.append(f"статичные поля/OSD: {int(item.result.static_share * 100)}% кадра")
            self.subtitle_label.setText(" · ".join(p for p in parts if p) + f"\n{item.path}")
        else:
            self.subtitle_label.setText(item.path)
        self.timeline.set_result(item.result)
        self.timeline.set_segments(item.segments)
        self.timeline.set_threshold(self.detection.threshold)
        self._fill_table(item)
        if item.result is not None:
            total = item.result.duration
            kept = kept_duration(item.segments)
            if item.segments:
                pct = int(round(100 * kept / total)) if total > 0 else 0
                self.summary_label.setText(
                    f"Останется <b>{format_time(kept)}</b> из {format_time(total)} ({pct}%), "
                    f"будет вырезано {format_time(max(total - kept, 0))}.")
            else:
                self.summary_label.setText(
                    "Видео не найдено: вся запись похожа на шум. Попробуйте понизить порог.")
        else:
            self.summary_label.setText("")
        self.manual_label.setVisible(item.manual)
        self.btn_recompute.setVisible(item.manual)
        if item.result is None:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Идёт анализ…" if item.state in (ANALYZING, QUEUED) else "Нет кадра")
            self.preview_caption.setText("")
        elif not keep_preview or self._frame_image is None:
            if item.playhead is None:
                item.playhead = self._initial_playhead(item)
            self._set_playhead(item, item.playhead)
        self.timeline.set_playhead(item.playhead)
        self._show_state()
        self._update_buttons()

    def _show_state(self) -> None:
        item = self.current_item()
        if item is None:
            return
        busy = item.state in (ANALYZING, EXPORTING)
        self.progress_bar.setVisible(busy)
        self.progress_bar.setValue(int(item.progress * 1000))
        if item.state == ERROR:
            self.state_label.setText(f"<span style='color:#f85149'>Ошибка:</span> {item.error}")
        elif item.state == DONE and item.outputs:
            names = ", ".join(os.path.basename(p) for p in item.outputs)
            self.state_label.setText(f"<span style='color:#3fb950'>Сохранено:</span> {names}")
        elif item.state in (ANALYZING, EXPORTING, QUEUED, STOPPED):
            self.state_label.setText(self._item_status(item).capitalize())
        elif item.result is not None:
            self.state_label.setText(self._output_hint(item))
        else:
            self.state_label.setText("")
        self._update_buttons()

    def _output_hint(self, item: FileItem) -> str:
        jobs = plan_export(item.result.info, item.segments, self.export_settings)
        if not jobs:
            return ""
        how = "без перекодирования" if uses_copy(item.result.info, self.export_settings) else "H.264"
        target = os.path.basename(jobs[0].output)
        if len(jobs) > 1:
            target += f" … ({len(jobs)} файла)" if len(jobs) < 5 else f" … ({len(jobs)} файлов)"
        return f"Будет сохранено ({how}): {target}"

    def _update_buttons(self) -> None:
        item = self.current_item()
        has_items = bool(self.items)
        busy = self.running is not None or bool(self.queue)
        self.btn_run_all.setEnabled(has_items)
        self.btn_stop.setEnabled(busy)
        self.btn_remove.setEnabled(item is not None)
        self.btn_clear.setEnabled(has_items)
        ready = item is not None and item.result is not None and any(s.enabled for s in item.segments)
        self.btn_export.setEnabled(ready and item.state not in (EXPORTING, QUEUED))
        outputs = item is not None and any(os.path.exists(p) for p in item.outputs)
        self.btn_open_result.setEnabled(outputs)
        self.btn_open_folder.setEnabled(outputs)
        states = [i.state for i in self.items.values()]
        running = sum(1 for s in states if s in (ANALYZING, EXPORTING, QUEUED))
        self.status_label.setText(f"Файлов: {len(states)}" + (f" · в работе: {running}" if running else ""))

    # ============================================================ preview
    def _set_playhead(self, item: FileItem, t: float) -> None:
        if item.result is None:
            return
        t = min(max(t, 0.0), max(item.result.duration - 0.05, 0.0))
        item.playhead = t
        self.timeline.set_playhead(t)
        self._request_preview(item, t)

    def _request_preview(self, item: FileItem, t: float) -> None:
        info = item.result.info if item.result is not None else None
        if info is None:
            return
        t = min(max(t, 0.0), max(item.result.duration - 0.05, 0.0))
        self._wanted_frame = (info.path, PreviewLoader.key(t))
        self._update_caption(item, t)
        self.preview.request(info, t)

    def _update_caption(self, item: FileItem, t: float) -> None:
        res = item.result
        i = int(min(max(t * res.rate, 0), res.frame_count - 1))
        score = float(res.score[i])
        inside = any(s.start <= t < s.end for s in item.segments if s.enabled)
        kind = "<span style='color:#3fb950'>останется</span>" if inside else \
            "<span style='color:#f85149'>будет вырезано</span>"
        extra = " · однотонный экран" if res.blank[i] else ""
        self.preview_caption.setText(f"{format_time(t)} · сигнал {int(round(score * 100))}%{extra} · {kind}")

    def _on_timeline_hover(self, t: float) -> None:
        item = self.current_item()
        if item is None or item.result is None:
            return
        if t < 0:
            self._hover_t = None
            self._hover_timer.stop()
            if item.playhead is not None:
                self._request_preview(item, item.playhead)
            return
        self._hover_t = t
        self._update_caption(item, t)
        self._hover_timer.start()

    def _request_hover_preview(self) -> None:
        item = self.current_item()
        if item is not None and self._hover_t is not None:
            self._request_preview(item, self._hover_t)

    def _on_timeline_click(self, t: float) -> None:
        item = self.current_item()
        if item is not None:
            self._set_playhead(item, t)

    def _on_frame(self, path: str, key: int, image: QImage) -> None:
        if self._wanted_frame != (path, key):
            return
        self._frame_image = image
        self._render_preview()

    def _render_preview(self) -> None:
        if self._frame_image is None:
            return
        pm = QPixmap.fromImage(self._frame_image)
        self.preview_label.setPixmap(pm.scaled(self.preview_label.size() - QSize(4, 4),
                                               Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        QTimer.singleShot(0, self._render_preview)

    # ============================================================ misc actions
    def _open_result(self) -> None:
        item = self.current_item()
        if item is not None:
            for p in item.outputs:
                if os.path.exists(p):
                    QDesktopServices.openUrl(QUrl.fromLocalFile(p))
                    return

    def _open_folder(self) -> None:
        item = self.current_item()
        if item is not None and item.outputs:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(item.outputs[0])))

    def _check_ffmpeg(self) -> None:
        try:
            path = find_ffmpeg()
            self.statusBar().showMessage(f"ffmpeg: {path}", 6000)
        except FFmpegNotFound as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))

    def show_help(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Как пользоваться")
        box.setTextFormat(Qt.RichText)
        box.setText(HELP_TEXT)
        box.exec()

    def show_about(self) -> None:
        try:
            ff = find_ffmpeg()
        except FFmpegNotFound:
            ff = "не найден"
        QMessageBox.about(
            self, "О программе",
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>Автоматически вырезает белый шум («снег») из AV-видеозаписей "
            "(аналоговый захват, FPV-видеорегистраторы, оцифровка VHS) и оставляет только видео.</p>"
            f"<p>ffmpeg: {ff}</p>")

    # ============================================================ events
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)
            e.acceptProposedAction()

    def closeEvent(self, e) -> None:
        if self.running is not None or self.queue:
            answer = QMessageBox.question(self, APP_NAME, "Идёт обработка. Прервать и выйти?")
            if answer != QMessageBox.Yes:
                e.ignore()
                return
            self.queue.clear()
            if self.running is not None:
                self.running.cancel.set()
            self.pool.waitForDone(5000)
        self.store.setValue("geometry", self.saveGeometry())
        self.store.setValue("splitter", self.splitter.saveState())
        self._save_settings()
        self.preview.stop()
        super().closeEvent(e)
