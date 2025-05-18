import sys, os
from PyQt6.QtGui import QFont, QColor, QIcon, QShortcut, QKeySequence
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QStackedWidget, QFileDialog
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QThread, QMutex, pyqtSlot, QTranslator, QCoreApplication, QTimer
sys.stdout = open(os.devnull, 'w')
from qfluentwidgets import TextBrowser, setThemeColor, ToolButton, TransparentToolButton, FluentIcon, HyperlinkCard, PushSettingCard, ComboBoxSettingCard, SubtitleLabel, OptionsSettingCard, isDarkTheme, InfoBar, InfoBarPosition, ToolTipFilter, ToolTipPosition, SettingCard, MessageBox, FluentTranslator, IndeterminateProgressBar, SwitchSettingCard, InfoBadgePosition, DotInfoBadge, ScrollArea, StrongBodyLabel
from winrt.windows.ui.viewmanagement import UISettings, UIColorType
import pyaudio
import time
import numpy as np
from resource.config import cfg
from resource.model_utils import update_model, update_device
import gc
import shutil
import traceback
import psutil
from faster_whisper import WhisperModel
import wave
import tempfile
from ctranslate2 import get_cuda_device_count

def get_nvidia_lib_paths():
    if getattr(sys, 'frozen', False):  # Running inside PyInstaller
        base_dir = os.path.join(sys.prefix)
    else:  # Running inside a virtual environment
        base_dir = os.path.join(sys.prefix, "Lib", "site-packages")

    nvidia_base_libs = os.path.join(base_dir, "nvidia")
    cuda_libs = os.path.join(nvidia_base_libs, "cuda_runtime", "bin")
    cublas_libs = os.path.join(nvidia_base_libs, "cublas", "bin")
    cudnn_libs = os.path.join(nvidia_base_libs, "cudnn", "bin")

    return [cuda_libs, cublas_libs, cudnn_libs]


for dll_path in get_nvidia_lib_paths():
    if os.path.exists(dll_path):
        os.environ["PATH"] = dll_path + os.pathsep + os.environ["PATH"]

if getattr(sys, 'frozen', False):
    # Running as a PyInstaller bundle
    base_dir = os.path.dirname(sys.executable)  # Points to build/
    res_dir = os.path.join(sys.prefix)
else:
    # Running as a script
    base_dir = os.path.dirname(os.path.abspath(__file__))
    res_dir = base_dir

if os.name == 'nt':
    import ctypes
    myappid = u'icosane.eustoma.tts.100'  # arbitrary string
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

class ErrorHandler(object):
    def __call__(self, exctype, value, tb):
        # Extract the traceback details
        tb_info = traceback.extract_tb(tb)
        # Get the last entry in the traceback (the most recent call)
        last_call = tb_info[-1] if tb_info else None

        if last_call:
            filename, line_number, function_name, text = last_call
            error_message = (f"Type: {exctype.__name__}\n"
                             f"Message: {value}\n"
                             f"File: {filename}\n"
                             f"Line: {line_number}\n"
                             f"Code: {text}")
        else:
            error_message = (f"Type: {exctype.__name__}\n"
                             f"Message: {value}")

        error_box = MessageBox("Error", error_message, parent=window)
        error_box.cancelButton.hide()
        error_box.buttonLayout.insertStretch(1)
        error_box.exec()

    def write(self, message):
        if message.startswith("Error:"):
            error_box = MessageBox("Error", message, parent=window)
            error_box.cancelButton.hide()
            error_box.buttonLayout.insertStretch(1)
            error_box.exec()
        else:
            pass

    def flush(self):
        pass


class ModelLoader(QThread):
    model_loaded = pyqtSignal(object, str)

    def __init__(self, model, device):
        super().__init__()
        self.model = model
        self.device_type = device

    def run(self):
        try:
            model = WhisperModel(
                self.model,
                device=self.device_type,
                compute_type="float32" if self.device_type == "cpu" else "float16",
                cpu_threads=psutil.cpu_count(logical=False),
                download_root="./models",
                local_files_only=True
            )
            self.model_loaded.emit(model, self.model)
        except Exception as e:
            error_box = MessageBox("Error", f"Error loading model: {str(e)}", parent=window)
            error_box.cancelButton.hide()
            error_box.buttonLayout.insertStretch(1)

class AudioStreamHandler(QObject):
    recording_finished = pyqtSignal()
    mic_status = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.p = pyaudio.PyAudio()
        self.stream = None
        self.stream_lock = QMutex()
        self.recording = False
        self.audio_source = "mic"
        self.audio_buffer = []

    def mic_connection_check(self):
        device_found = self._check_device()

        if not device_found:
            self.p.terminate()
            self.p = pyaudio.PyAudio()
            device_found = self._check_device()

        self.mic_status.emit(device_found)

    def _check_device(self):
        num_devices = self.p.get_device_count()
        device_found = False

        for i in range(num_devices):
            device_info = self.p.get_device_info_by_index(i)

            if device_info.get('name') == 'Microsoft Sound Mapper - Input':
                device_found = True
                break

        return device_found

    def open_audio_stream(self, source):
        try:
            device_index = 0 if source == "mic" else 1

            self.stream_lock.lock()
            if self.stream is not None:
                self.stream.stop_stream()
                self.stream.close()

            self.stream = self.p.open(format=pyaudio.paInt16,
                                      channels=1,
                                      rate=16000,
                                      input=True,
                                      input_device_index=device_index,
                                      frames_per_buffer=1024)
        except Exception as e:
            error_box = MessageBox("Error", f"Error opening audio stream: {e}", parent=window)
            error_box.cancelButton.hide()
            error_box.buttonLayout.insertStretch(1)
        finally:
            self.stream_lock.unlock()

    def start_recording(self):
        self.recording = True
        self.audio_buffer = []

    def stop_recording(self):
        self.recording = False
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        self.recording_finished.emit()

    def read_audio_data(self):
        if self.stream is None:
            return None
        try:
            self.stream_lock.lock()
            audio_data = self.stream.read(1024)
        except Exception as e:
            error_box = MessageBox("Error", f"Error reading audio data: {e}", parent=window)
            error_box.cancelButton.hide()
            error_box.buttonLayout.insertStretch(1)
            return None
        finally:
            self.stream_lock.unlock()
        return audio_data


class AudioThread(QThread):
    audio_data_signal = pyqtSignal(str)

    def __init__(self, audio_handler):
        super().__init__()
        self.audio_handler = audio_handler
        self.running = True

    def run(self):
        while self.running:
            if self.audio_handler.recording:
                audio_data = self.audio_handler.read_audio_data()
                if audio_data is not None:
                    self.audio_handler.audio_buffer.append(audio_data)
            self.msleep(33)

    def stop(self):
        self.running = False
        self.quit()
        self.wait()

class TranscriptionWorker(QThread):
    transcription_done = pyqtSignal(str)

    def __init__(self, model, audio_file):
        super().__init__()
        self.model = model
        self.audio_file = audio_file

    def run(self):
        try:
            segments, _ = self.model.transcribe(self.audio_file)

            if (cfg.get(cfg.lineformat) is False):
                transcription = "\n".join([segment.text for segment in segments])
            else:
                transcription = "".join([segment.text for segment in segments])

            self.transcription_done.emit(transcription)

        finally:
            try:
                if os.path.exists(self.audio_file):
                    os.remove(self.audio_file)
            except Exception as e:
                error_box = MessageBox("Error", f"Error deleting temp file: {e}", parent=window)
                error_box.cancelButton.hide()
                error_box.buttonLayout.insertStretch(1)

class MainWindow(QMainWindow):
    theme_changed = pyqtSignal()
    model_changed = pyqtSignal()
    device_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(QCoreApplication.translate("MainWindow", "Eustoma"))
        self.setWindowIcon(QIcon(os.path.join(res_dir, "resource", "assets", "icon.ico")))
        self.setGeometry(100,100,1318,720)
        self.setMinimumSize(842, 806)
        self.setup_theme()
        self.center()
        self.model = None
        self.model_mutex = QMutex()
        self.show_badge = False

        self.audio_handler = AudioStreamHandler()
        self.audio_thread = AudioThread(self.audio_handler)

        self.theme_changed.connect(self.update_theme)
        self.model_changed.connect(lambda: update_model(self))
        self.device_changed.connect(lambda: update_device(self))

        QTimer.singleShot(500, self.audio_thread.start)
        self.audio_handler.recording_finished.connect(self.save_audio)
        self.audio_handler.mic_status.connect(self.mic_check)

        self.stacked_widget = QStackedWidget()
        self.setCentralWidget(self.stacked_widget)
        self.stacked_widget.currentChanged.connect(self.update_badge_visibility)


        self.layout_main()
        self.layout_settings()

        QTimer.singleShot(100, self.init_check)

    def mic_check(self,s):
        if not s:
            InfoBar.warning(
                title=(QCoreApplication.translate("MainWindow", "Warning")),
                content=(QCoreApplication.translate("MainWindow", "<b>Microphone is not detected</b>. Please check that your microphone is connected and turned on.")),
                orient=Qt.Orientation.Horizontal,
                isClosable=False,
                position=InfoBarPosition.BOTTOM,
                duration=4000,
                parent=window
            )
            self.record_button.setDisabled(True)
            self.micstatusupd_button.show()
        elif ((cfg.get(cfg.model).value == 'None')):
            self.micstatusupd_button.hide()
        else:
            self.micstatusupd_button.hide()
            self.record_button.setEnabled(True)

    def init_check(self):
        model_path = os.path.abspath(os.path.join(base_dir, "models", f"models--Systran--faster-whisper-{cfg.get(cfg.model).value}"))
        if not os.path.exists(model_path):
            model_path = os.path.abspath(os.path.join(base_dir, "models", f"models--mobiuslabsgmbh--faster-whisper-{cfg.get(cfg.model).value}"))
        if not (os.path.exists(model_path) and (cfg.get(cfg.model).value != 'None')):
            cfg.set(cfg.model, 'None')

        if ((cfg.get(cfg.model).value == 'None')):
            InfoBar.info(
                title=(QCoreApplication.translate("MainWindow", "Information")),
                content=(QCoreApplication.translate("MainWindow", "<b>No model is currently selected</b>. Go to Settings and select the Whisper model before starting.")),
                orient=Qt.Orientation.Horizontal,
                isClosable=False,
                position=InfoBarPosition.BOTTOM,
                duration=4000,
                parent=window
            )
            self.record_button.setDisabled(True)
            self.card_deletemodel.button.setDisabled(True)
            self.settings_badge.show()
            self.show_badge = True

        if (get_cuda_device_count() == 0) and ((cfg.get(cfg.device).value == 'cuda')):
            InfoBar.info(
                title=(QCoreApplication.translate("MainWindow", "Information")),
                content=(QCoreApplication.translate("MainWindow", "<b>Your device does not have an NVIDIA graphics card</b>. Please go to Settings and switch the device to <b>cpu</b>.")),
                orient=Qt.Orientation.Horizontal,
                isClosable=False,
                position=InfoBarPosition.BOTTOM,
                duration=4000,
                parent=window
            )
            self.record_button.setDisabled(True)

        self.audio_handler.mic_connection_check()

    def layout_main(self):
        # Create the main layout
        main_layout = QVBoxLayout()

        self.text_browser = TextBrowser()
        font = QFont()
        font.setPointSize(14)
        self.text_browser.setFont(font)
        self.text_browser.setPlaceholderText(QCoreApplication.translate("MainWindow", "Waiting for input. To start press the play button."))
        main_layout.addWidget(self.text_browser)

        # Create left buttons
        self.record_button = ToolButton(FluentIcon.PLAY)
        self.record_button.setFixedSize(50, 50)
        self.settings_button = TransparentToolButton(FluentIcon.SETTING)
        self.settings_badge = DotInfoBadge.error(self, target=self.settings_button, position=InfoBadgePosition.TOP_RIGHT)
        self.copy_button = TransparentToolButton(FluentIcon.COPY)
        self.save_button = TransparentToolButton(FluentIcon.SAVE_AS)
        self.clear_button = TransparentToolButton(FluentIcon.BROOM)
        self.micstatusupd_button = TransparentToolButton(FluentIcon.UPDATE)

        #tooltips
        self.copy_button.setToolTip(QCoreApplication.translate("MainWindow", "Copy to clipboard"))
        self.copy_button.setToolTipDuration(2000)
        self.copy_button.installEventFilter(ToolTipFilter(self.copy_button, 0, ToolTipPosition.TOP))

        self.save_button.setToolTip(QCoreApplication.translate("MainWindow", "Save current text as"))
        self.save_button.setToolTipDuration(2000)
        self.save_button.installEventFilter(ToolTipFilter(self.save_button, 0, ToolTipPosition.TOP))

        self.clear_button.setToolTip(QCoreApplication.translate("MainWindow", "Clear text window"))
        self.clear_button.setToolTipDuration(2000)
        self.clear_button.installEventFilter(ToolTipFilter(self.clear_button, 0, ToolTipPosition.TOP))

        self.micstatusupd_button.setToolTip(QCoreApplication.translate("MainWindow", "Recheck microphone connection"))
        self.micstatusupd_button.setToolTipDuration(2000)
        self.micstatusupd_button.installEventFilter(ToolTipFilter(self.micstatusupd_button, 0, ToolTipPosition.TOP))

        # Connect button signals
        self.record_button.clicked.connect(self.toggle_recording)
        self.settings_button.clicked.connect(self.show_settings_page)
        self.copy_button.clicked.connect(self.copy_to_clipboard)
        self.save_button.clicked.connect(self.save_to_file)
        self.clear_button.clicked.connect(self.clear_browser)
        self.micstatusupd_button.clicked.connect(self.updmicstatus)

        # Key shortcuts
        self.record_action = QShortcut(QKeySequence("Space"), self)
        self.record_action.activated.connect(self.toggle_recording)
        self.copy_action = QShortcut(QKeySequence("Alt+C"), self)
        self.copy_action.activated.connect(self.copy_to_clipboard)
        self.save_action = QShortcut(QKeySequence("Ctrl+S"), self)
        self.save_action.activated.connect(self.save_to_file)
        self.clear_action = QShortcut(QKeySequence("Delete"), self)
        self.clear_action.activated.connect(self.clear_browser)

        # Create a layout for the left buttons
        settings_layout = QHBoxLayout()
        settings_layout.addWidget(self.settings_button, alignment=Qt.AlignmentFlag.AlignBottom)
        settings_layout.addWidget(self.copy_button, alignment=Qt.AlignmentFlag.AlignBottom)
        settings_layout.addWidget(self.save_button, alignment=Qt.AlignmentFlag.AlignBottom)
        settings_layout.addWidget(self.clear_button, alignment=Qt.AlignmentFlag.AlignBottom)
        settings_layout.addWidget(self.micstatusupd_button, alignment=Qt.AlignmentFlag.AlignBottom)

        bottom_button_layout = QHBoxLayout()
        bottom_button_layout.addLayout(settings_layout)
        bottom_button_layout.addStretch()
        bottom_button_layout.addWidget(self.record_button)
        bottom_button_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.addLayout(bottom_button_layout)

        # Set the main layout
        main_widget = QWidget()
        main_widget.setLayout(main_layout)
        self.stacked_widget.addWidget(main_widget)

    def layout_settings(self):
        # Create the settings layout
        settings_layout = QVBoxLayout()

        # Create a horizontal layout for the back button
        back_button_layout = QHBoxLayout()

        # Create a back button
        back_button = TransparentToolButton(FluentIcon.LEFT_ARROW)
        back_button.clicked.connect(self.show_main_page)

        back_button_layout.addWidget(back_button, alignment=Qt.AlignmentFlag.AlignTop)
        back_button_layout.setContentsMargins(5, 5, 5, 5)

        settings_layout.addLayout(back_button_layout)

        self.settings_title = SubtitleLabel(QCoreApplication.translate("MainWindow", "Settings"))
        self.settings_title.setTextColor(QColor(0, 0, 0), QColor(255, 255, 255))

        back_button_layout.addWidget(self.settings_title, alignment=Qt.AlignmentFlag.AlignTop)

        card_layout = QVBoxLayout()
        self.devices_title = StrongBodyLabel(QCoreApplication.translate("MainWindow", "Devices"))
        self.devices_title.setTextColor(QColor(0, 0, 0), QColor(255, 255, 255))
        card_layout.addWidget(self.devices_title, alignment=Qt.AlignmentFlag.AlignTop)

        self.card_setdevice = ComboBoxSettingCard(
            configItem=cfg.device,
            icon=FluentIcon.IOT,
            title=QCoreApplication.translate("MainWindow","Device"),
            content=QCoreApplication.translate("MainWindow", "Select device. cpu will utilize your CPU, cuda will only work on NVIDIA graphics card."),
            texts=['cpu', 'cuda']
        )

        card_layout.addWidget(self.card_setdevice, alignment=Qt.AlignmentFlag.AlignTop)

        if get_cuda_device_count() == 0:
            self.card_setdevice.hide()
            self.devices_title.hide()
            if cfg.get(cfg.device).value == 'cuda':
                cfg.set(cfg.device, 'cpu')
        
        cfg.device.valueChanged.connect(self.device_changed.emit)

        self.modelsins_title = StrongBodyLabel(QCoreApplication.translate("MainWindow", "Model management"))
        self.modelsins_title.setTextColor(QColor(0, 0, 0), QColor(255, 255, 255))
        card_layout.addSpacing(20)
        card_layout.addWidget(self.modelsins_title, alignment=Qt.AlignmentFlag.AlignTop)

        self.card_setmodel = ComboBoxSettingCard(
            configItem=cfg.model,
            icon=FluentIcon.CLOUD_DOWNLOAD,
            title=QCoreApplication.translate("MainWindow","Model"),
            content=QCoreApplication.translate("MainWindow", "Change whisper model"),
            texts=['None', 'tiny.en', 'tiny', 'base.en', 'base', 'small.en', 'small', 'medium.en', 'medium', 'large-v1', 'large-v2', 'large-v3', 'large', 'large-v3-turbo']
        )

        card_layout.addWidget(self.card_setmodel, alignment=Qt.AlignmentFlag.AlignTop)
        cfg.model.valueChanged.connect(self.model_changed.emit)

        self.card_deletemodel = PushSettingCard(
            text=QCoreApplication.translate("MainWindow","Remove"),
            icon=FluentIcon.BROOM,
            title=QCoreApplication.translate("MainWindow","Remove model"),
            content=QCoreApplication.translate("MainWindow", "Delete currently selected model. Currently selected: <b>{}</b>").format(cfg.get(cfg.model).value),
        )

        card_layout.addWidget(self.card_deletemodel, alignment=Qt.AlignmentFlag.AlignTop)
        self.card_deletemodel.clicked.connect(self.modelremover)

        self.switches_title = StrongBodyLabel(QCoreApplication.translate("MainWindow", "Text options"))
        self.switches_title.setTextColor(QColor(0, 0, 0), QColor(255, 255, 255))
        card_layout.addSpacing(20)
        card_layout.addWidget(self.switches_title, alignment=Qt.AlignmentFlag.AlignTop)

        self.card_switch_line_format = SwitchSettingCard(
            icon=FluentIcon.FONT_SIZE,
            title=QCoreApplication.translate("MainWindow","Continuous text"),
            content=QCoreApplication.translate("MainWindow","Click to switch between separate lines for each sentence and a continuous flow of text."),
            configItem=cfg.lineformat
        )

        card_layout.addWidget(self.card_switch_line_format, alignment=Qt.AlignmentFlag.AlignTop)

        self.card_keep_output = SwitchSettingCard(
            icon=FluentIcon.FONT,
            title=QCoreApplication.translate("MainWindow","Keep previous text"),
            content=QCoreApplication.translate("MainWindow","Do not clear the previous transcription"),
            configItem=cfg.saveoutput
        )

        card_layout.addWidget(self.card_keep_output, alignment=Qt.AlignmentFlag.AlignTop)

        self.miscellaneous_title = StrongBodyLabel(QCoreApplication.translate("MainWindow", "Miscellaneous"))
        self.miscellaneous_title.setTextColor(QColor(0, 0, 0), QColor(255, 255, 255))
        card_layout.addSpacing(20)
        card_layout.addWidget(self.miscellaneous_title, alignment=Qt.AlignmentFlag.AlignTop)

        self.card_setlanguage = ComboBoxSettingCard(
            configItem=cfg.language,
            icon=FluentIcon.LANGUAGE,
            title=QCoreApplication.translate("MainWindow","Language"),
            content=QCoreApplication.translate("MainWindow", "Change UI language"),
            texts=["English", "Русский"]
        )

        card_layout.addWidget(self.card_setlanguage, alignment=Qt.AlignmentFlag.AlignTop)
        cfg.language.valueChanged.connect(self.restartinfo)

        self.card_theme = OptionsSettingCard(
            cfg.themeMode,
            FluentIcon.BRUSH,
            QCoreApplication.translate("MainWindow","Application theme"),
            QCoreApplication.translate("MainWindow", "Adjust how the application looks"),
            [QCoreApplication.translate("MainWindow","Light"), QCoreApplication.translate("MainWindow","Dark"), QCoreApplication.translate("MainWindow","Follow System Settings")]
        )

        card_layout.addWidget(self.card_theme, alignment=Qt.AlignmentFlag.AlignTop)
        self.card_theme.optionChanged.connect(self.theme_changed.emit)

        self.card_zoom = OptionsSettingCard(
            cfg.dpiScale,
            FluentIcon.ZOOM,
            QCoreApplication.translate("MainWindow","Interface zoom"),
            QCoreApplication.translate("MainWindow","Change the size of widgets and fonts"),
            texts=[
                "100%", "125%", "150%", "175%", "200%",
                QCoreApplication.translate("MainWindow","Follow System Settings")
            ]
        )

        card_layout.addWidget(self.card_zoom, alignment=Qt.AlignmentFlag.AlignTop)
        cfg.dpiScale.valueChanged.connect(self.restartinfo)

        self.card_ab = HyperlinkCard(
            url="https://github.com/icosane/eustoma",
            text="Github",
            icon=FluentIcon.INFO,
            title=QCoreApplication.translate("MainWindow", "About"),
            content=QCoreApplication.translate("MainWindow", "Speech to text app, powered by SYSTRAN's faster-whisper and zhiyiYo's QFluentWidgets")
        )
        card_layout.addWidget(self.card_ab,  alignment=Qt.AlignmentFlag.AlignTop )

        self.scroll_area = ScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.card_widget = QWidget()
        self.card_widget.setLayout(card_layout)
        self.scroll_area.setWidget(self.card_widget)
        settings_layout.addWidget(self.scroll_area)

        self.download_progressbar = IndeterminateProgressBar()
        settings_layout.addWidget(self.download_progressbar )
        self.download_progressbar.hide()

        settings_widget = QWidget()
        settings_widget.setLayout(settings_layout)
        self.stacked_widget.addWidget(settings_widget)

    def show_settings_page(self):
        self.stacked_widget.setCurrentIndex(1)  # Switch to the settings page

    def show_main_page(self):
        self.stacked_widget.setCurrentIndex(0)  # Switch back to the main page

    def center(self):
        screen_geometry = self.screen().availableGeometry()
        window_geometry = self.geometry()

        x = (screen_geometry.width() - window_geometry.width()) // 2
        y = (screen_geometry.height() - window_geometry.height()) // 2

        self.move(x, y)

    def update_theme(self):
        self.setup_theme()

    def on_model_download_finished(self, status):
        if status == "start":
            self.download_progressbar.show()
            InfoBar.info(
                title=QCoreApplication.translate("MainWindow", "Information"),
                content=QCoreApplication.translate("MainWindow", "Model download started. Please wait for it to finish"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.BOTTOM,
                duration=4000,
                parent=self
            )
            self.update_remove_button(False)

        elif status == "success":
            if hasattr(self, 'model_thread') and self.model_thread.isRunning():
                self.model_thread.stop()  # Stop the thread after success
            self.download_progressbar.hide()
            InfoBar.success(
                title=QCoreApplication.translate("MainWindow", "Success"),
                content=QCoreApplication.translate("MainWindow", "Model successfully downloaded"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.BOTTOM,
                duration=4000,
                parent=self
            )
            self.update_remove_button(True)
            gc.collect()

        else:
            InfoBar.error(
                title=QCoreApplication.translate("MainWindow", "Error"),
                content=QCoreApplication.translate("MainWindow", f"Failed to download model: {status}"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.BOTTOM,
                duration=4000,
                parent=self
            )
            self.update_remove_button(False)

    def modelremover(self):
        directory = os.path.join(base_dir, "models", f"models--Systran--faster-whisper-{cfg.get(cfg.model).value}")
        if not os.path.exists(directory):
            directory = os.path.join(base_dir, "models", f"models--mobiuslabsgmbh--faster-whisper-{cfg.get(cfg.model).value}")
        if os.path.exists(directory) and os.path.isdir(directory):
            try:
                # Remove the directory and its contents
                shutil.rmtree(directory)
                cfg.set(cfg.model, 'None')


                InfoBar.success(
                    title=QCoreApplication.translate("MainWindow", "Success"),
                    content=QCoreApplication.translate("MainWindow", "Model removed"),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.BOTTOM,
                    duration=2000,
                    parent=self
                )
            except Exception as e:
                InfoBar.error(
                    title=QCoreApplication.translate("MainWindow", "Error"),
                    content=QCoreApplication.translate("MainWindow", f"Failed to remove the model: {e}"),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.BOTTOM,
                    duration=2000,
                    parent=self
                )

    def update_record_button(self, enabled):
        if hasattr(self, 'record_button'):
            self.record_button.setEnabled(enabled)
            self.record_button.repaint()
            self.settings_badge.hide()

    def update_remove_button(self, enabled):
        if hasattr(self, 'card_deletemodel'):
            self.card_deletemodel.button.setEnabled(enabled)
            self.record_button.repaint()

    def clear_browser(self):
        self.text_browser.clear()
        self.text_browser.setPlaceholderText(QCoreApplication.translate("MainWindow", "Waiting for input. To start press the play button."))

    def updmicstatus(self):
        QTimer.singleShot(1000, self.audio_handler.mic_connection_check)

    def restartinfo(self):
        InfoBar.warning(
            title=(QCoreApplication.translate("MainWindow", "Success")),
            content=(QCoreApplication.translate("MainWindow", "Setting takes effect after restart")),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=2000,
            parent=window
        )

    def setup_theme(self):
        main_color_hex = self.get_main_color_hex()
        setThemeColor(main_color_hex)
        if isDarkTheme():
            self.setStyleSheet("""
                QWidget {
                    background-color: #1e1e1e;  /* Dark background */
                    border: none;
                }
            """)
        else:
            self.setStyleSheet("""
                QWidget {
                    background-color: #f0f0f0;  /* Light background */
                    border: none;
                }
            """)

    def get_main_color_hex(self):
        color = UISettings().get_color_value(UIColorType.ACCENT)
        return f'#{int((color.r)):02x}{int((color.g)):02x}{int((color.b )):02x}'

    def update_audio_text_field(self, data):
        if (cfg.get(cfg.saveoutput) is True):
            if (cfg.get(cfg.lineformat) is True):
                text = (self.current_text + '\n' + '\n' + data) if self.current_text else data
                self.text_browser.setPlainText(text)
            else:
                text = (self.current_text + '\n' + data) if self.current_text else data
                self.text_browser.setPlainText(text)
        else:
            self.text_browser.clear()
            self.text_browser.setPlainText(data)

    def copy_to_clipboard(self):
        text = self.text_browser.toPlainText()

        if not text.strip():
            InfoBar.warning(
                title=(QCoreApplication.translate("MainWindow", "Warning")),
                content=(QCoreApplication.translate("MainWindow", "The text browser is empty!")),
                orient=Qt.Orientation.Horizontal,
                isClosable=False,
                position=InfoBarPosition.BOTTOM,
                duration=2000,
                parent=window
            )
            return

        clipboard = QApplication.clipboard()
        clipboard.setText(text)

        InfoBar.success(
            title=(QCoreApplication.translate("MainWindow", "Success")),
            content=(QCoreApplication.translate("MainWindow", "Text copied to clipboard!")),
            orient=Qt.Orientation.Horizontal,
            isClosable=False,
            position=InfoBarPosition.BOTTOM,
            duration=2000,
            parent=window
        )

    def save_to_file(self):
        text = self.text_browser.toPlainText()

        if not text.strip():
            InfoBar.warning(
                title=(QCoreApplication.translate("MainWindow", "Warning")),
                content=(QCoreApplication.translate("MainWindow", "The text browser is empty!")),
                orient=Qt.Orientation.Horizontal,
                isClosable=False,
                position=InfoBarPosition.BOTTOM,
                duration=2000,
                parent=window
            )
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            QCoreApplication.translate("MainWindow", "Save File"),
            "",
            QCoreApplication.translate("MainWindow", "Text Files (*.txt)")
        )

        if not file_path:
            return

        try:
            with open(file_path, "w", encoding='utf-8') as file:
                file.write(text)
            InfoBar.success(
                title=(QCoreApplication.translate("MainWindow", "Success")),
                content=(QCoreApplication.translate("MainWindow", "Text saved to file!")),
                orient=Qt.Orientation.Horizontal,
                isClosable=False,
                position=InfoBarPosition.BOTTOM,
                duration=2000,
                parent=window
            )
        except Exception as e:
            InfoBar.error(
                title=(QCoreApplication.translate("MainWindow", "Error")),
                content=(QCoreApplication.translate("MainWindow", f"Failed to save text to file: {str(e)}")),
                orient=Qt.Orientation.Horizontal,
                isClosable=False,
                position=InfoBarPosition.BOTTOM,
                duration=2000,
                parent=window
            )

    def closeEvent(self, event):
        self.audio_thread.stop()
        super().closeEvent(event)

    def update_badge_visibility(self, index):
        if index == 0 and self.show_badge:
            self.settings_badge.setVisible(True)
        else:
            self.settings_badge.setVisible(False)

    def toggle_recording(self):
        self.audio_handler.recording = not self.audio_handler.recording
        if self.audio_handler.recording:
            if (cfg.get(cfg.saveoutput) is True):
                self.current_text = self.text_browser.toPlainText()
            self.text_browser.clear()
            self.text_browser.setPlaceholderText(QCoreApplication.translate("MainWindow", "Recording..."))
            # When starting, ensure stream is open
            self.audio_handler.open_audio_stream(self.audio_handler.audio_source)
            self.record_button.setIcon(FluentIcon.PAUSE)
            self.audio_handler.start_recording()
        else:
            self.stop_recording()
            self.record_button.setIcon(FluentIcon.PLAY)

    def load_model(self):
        if self.model is not None:
            del self.model
            gc.collect()

        model = f"{cfg.get(cfg.model).value}"
        device = f"{cfg.get(cfg.device).value}"
        self.text_browser.setPlaceholderText(QCoreApplication.translate("MainWindow", "Loading model into memory..."))
        self.model_loader = ModelLoader(model, device)
        self.model_loader.model_loaded.connect(self.on_model_loaded)
        self.model_loader.start()

    @pyqtSlot(object, str)
    def on_model_loaded(self, model, model_name):
        self.model_mutex.lock()
        self.model = model
        self.model_mutex.unlock()
        self.transcribe_audio(self.temp_filename)

    def transcribe_audio(self, audio_file):
        self.text_browser.setPlaceholderText(QCoreApplication.translate("MainWindow", "Transcribing audio..."))
        self.model_mutex.lock()
        model = self.model
        self.model_mutex.unlock()
        self.transcription_worker = TranscriptionWorker(model, audio_file)
        self.transcription_worker.transcription_done.connect(self.update_audio_text_field)
        self.transcription_worker.start()

    def stop_recording(self):
        self.audio_handler.stop_recording()
        if self.audio_thread.isRunning():
            self.audio_thread.stop()
            self.audio_thread.wait()
        self.audio_thread = AudioThread(self.audio_handler)
        self.audio_thread.start()

    def save_audio(self):
        if self.audio_handler.audio_buffer:
            audio_data = [np.frombuffer(chunk, dtype=np.int16) for chunk in self.audio_handler.audio_buffer]
            audio_data = np.concatenate(audio_data)

            temp_dir = tempfile.gettempdir()
            self.temp_filename = os.path.join(temp_dir, f"transcription_{int(time.time())}.wav")
            with wave.open(self.temp_filename, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(audio_data.astype(np.int16).tobytes())

            self.text_browser.setPlaceholderText(QCoreApplication.translate("MainWindow", "Audio saved, starting transcription..."))
            time.sleep(0.5)
            self.load_model()



if __name__ == "__main__":
    if cfg.get(cfg.dpiScale) != "Auto":
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_SCALE_FACTOR"] = str(cfg.get(cfg.dpiScale))

    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # Set the Fusion style for QFluentWidgets
    locale = cfg.get(cfg.language).value
    fluentTranslator = FluentTranslator(locale)
    appTranslator = QTranslator()
    lang_path = os.path.join(res_dir, "resource", "lang")
    appTranslator.load(locale, "lang", ".", lang_path)

    app.installTranslator(fluentTranslator)
    app.installTranslator(appTranslator)

    window = MainWindow()
    window.show()
    sys.excepthook = ErrorHandler()
    sys.stderr = ErrorHandler()
    sys.exit(app.exec())
