# gui/main_window.py

import sys
import logging
import time
import os
from datetime import date, time as time_obj, datetime, timezone, timedelta
from typing import Optional

# YENİ: Gerekli modüller eklendi
import webbrowser
import requests

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QWidget, QAction, QMenu,
    QMessageBox, QTextEdit, QPushButton, QHBoxLayout, QLabel,
    QStatusBar, QComboBox, QSizePolicy, QSpacerItem, QGroupBox, QTabWidget, QActionGroup
)
from PyQt5.QtCore import Qt, pyqtSlot, pyqtSignal, QObject, QTimer
from PyQt5.QtGui import QFont, QIcon

# --- Gerekli Sınıfların Import Edilmesi ---
try:
    from core.logger import setup_logger
    from gui.dashboard_widget import DashboardWidget as ImportedDashboardWidget
    from gui.user_management_dialog import UserManagementDialog as ImportedUserMgmtDialog
    from config.config_manager import ConfigManager
    from config.user_config_manager import UserConfigManager
    from core.bot_core import BotCore
    from core.database_manager import DatabaseManager
    from config.language_manager import LanguageManager
    CORE_COMPONENTS_LOADED = True
    logger = setup_logger('main_window', 'main_window.log')
except ImportError as e:
    CORE_COMPONENTS_LOADED = False
    INITIALIZATION_ERROR_MESSAGE = f"Temel modüller yüklenemedi:\n{e}"
    logging.basicConfig(level=logging.CRITICAL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('main_window_fallback')
    logger.critical(INITIALIZATION_ERROR_MESSAGE, exc_info=True)

# YENİ: Güncelleme kontrolü için URL sabiti
VERSION_URL = "https://gist.githubusercontent.com/iduymac/6bcbf07d562014e867d7f453a062ee5b/raw/e1b5e0a7fffa9961be1a28c3d302ccc0e484c45c/gistfile1.txt"


class QTextEditLogger(logging.Handler, QObject):
    log_received = pyqtSignal(str)
    def __init__(self, text_edit_widget):
        logging.Handler.__init__(self)
        QObject.__init__(self)
        
        if not isinstance(text_edit_widget, QTextEdit):
            logger.error("QTextEditLogger başlatma hatası: Geçersiz QTextEdit widget'ı sağlandı.")
            self.widget = None
            return
            
        self.widget = text_edit_widget
        self.widget.setReadOnly(True)
        self.log_received.connect(self.widget.append)
    def emit(self, record):
        if not self.widget: return
        msg = self.format(record)
        self.log_received.emit(msg)


class MainWindow(QMainWindow):
    def __init__(self, logged_in_user: str,
                 lang_manager: LanguageManager,
                 config_manager_instance: Optional[ConfigManager] = None,
                 user_config_manager_instance: Optional[UserConfigManager] = None,
                 database_manager_instance: Optional[DatabaseManager] = None):
        super().__init__()
        
        self.logged_in_user = logged_in_user
        self.lang_manager = lang_manager
        self.license_is_valid = False
        self.remaining_license_days = -1

        logger.info(f"Ana Pencere (MainWindow) '{self.logged_in_user}' kullanıcısı için başlatılıyor...")
        
        self.setGeometry(100, 100, 1280, 800)
        self.setMinimumSize(1000, 700)

        if not CORE_COMPONENTS_LOADED:
            QMessageBox.critical(self, "Kritik Başlatma Hatası", INITIALIZATION_ERROR_MESSAGE)
            QTimer.singleShot(0, self.close)
            return

        try:
            self.config_manager = config_manager_instance or ConfigManager()
            self.user_manager = user_config_manager_instance or UserConfigManager()
            self.db_manager = database_manager_instance or DatabaseManager(db_path=self.config_manager.get_setting('database_settings', 'path', os.path.join('data', 'trades.db')))

            self.license_is_valid = self._check_license()
            if not self.license_is_valid:
                return

            self.bot_core = BotCore(
                config_manager=self.config_manager,
                user_manager=self.user_manager,
                database_manager=self.db_manager
            )
        except Exception as e:
            logger.critical(f"Yöneticiler veya BotCore başlatılırken hata: {e}", exc_info=True)
            QMessageBox.critical(self, "Kritik Hata", f"Uygulama bileşenleri başlatılamadı:\n{e}")
            QTimer.singleShot(0, self.close)
            return

        self.current_mode = self.config_manager.get_setting('gui_settings', 'default_mode', 'real').lower()
        self.log_widget_ref = None
        self.dashboard_widget_ref = None
        self.is_dashboard_real = False
        
        self._init_ui()
        self._setup_gui_logging()
        self._connect_signals_and_buttons()
        self._update_button_enabled_state()
        
        # YENİ: Başlatma sonunda güncelleme kontrolünü çağır
        self._check_for_updates()

        logger.info("MainWindow başarıyla başlatıldı ve kullanıma hazır.")

    def _retranslate_ui(self):
        self.setWindowTitle(f"{self.lang_manager.get_string('app_title')} - [{self.logged_in_user}]")
        self.user_label.setText(self.lang_manager.get_string('active_user_label'))
        self.mode_label.setText(self.lang_manager.get_string('mode_label'))
        
        self.user_menu.setTitle(self.lang_manager.get_string('menu_user'))
        self.user_mgmt_action.setText(self.lang_manager.get_string('menu_user_manage'))
        self.language_menu.setTitle(self.lang_manager.get_string('menu_language'))
        self.test_menu.setTitle(self.lang_manager.get_string('menu_test'))
        self.run_manual_test_action.setText(self.lang_manager.get_string('menu_test_manual'))
        self.help_menu.setTitle(self.lang_manager.get_string('menu_help'))
        self.about_action.setText(self.lang_manager.get_string('menu_help_about'))
        
        self.update_status_bar()
        if self.dashboard_widget_ref:
            self.dashboard_widget_ref._retranslate_ui()
            
    def update_status_bar(self):
        if self.license_is_valid and self.remaining_license_days >= 0:
            status_text = self.lang_manager.get_string('status_welcome', user=self.logged_in_user, days=self.remaining_license_days)
            self.statusBar().showMessage(status_text, 0)

    def _check_license(self) -> bool:
        self.remaining_license_days = -1 
        user_data = self.user_manager.get_user(self.logged_in_user)
        if not user_data:
            QMessageBox.critical(self, "Kullanıcı Hatası", "Kullanıcı verileri okunamadı!")
            QTimer.singleShot(0, self.close)
            return False
        expires_str = user_data.get("expires")
        if not expires_str:
            QMessageBox.critical(self, "Lisans Hatası", "Kullanıcı için lisans bitiş tarihi tanımlanmamış!")
            QTimer.singleShot(0, self.close)
            return False
        try:
            expires_date = datetime.strptime(expires_str, "%Y-%m-%d").date()
            today = date.today()
            self.remaining_license_days = (expires_date - today).days
            if self.remaining_license_days < 0:
                QMessageBox.critical(self, "Lisans Süresi Doldu", f"Lisansınız {-self.remaining_license_days} gün önce sona erdi.")
                QTimer.singleShot(0, self.close)
                return False
            else:
                if self.remaining_license_days <= 15:
                     QMessageBox.warning(self, "Lisans Uyarısı", f"Lisansınızın dolmasına {self.remaining_license_days} gün kaldı!")
                return True
        except ValueError:
            QMessageBox.critical(self, "Lisans Format Hatası", "Lisans bitiş tarihi formatı geçersiz.")
            QTimer.singleShot(0, self.close)
            return False

    def _init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        top_bar_layout = QHBoxLayout()
        top_bar_layout.setSpacing(10)
        self.user_label = QLabel()
        top_bar_layout.addWidget(self.user_label)
        self.user_combo_box = QComboBox()
        self.user_combo_box.addItem(self.logged_in_user)
        self.user_combo_box.setEnabled(False)
        self.user_combo_box.setMinimumWidth(200)
        top_bar_layout.addWidget(self.user_combo_box)
        top_bar_layout.addStretch()
        self.mode_label = QLabel()
        top_bar_layout.addWidget(self.mode_label)
        self.mode_combo_box = QComboBox()
        self.mode_combo_box.addItems(["Real", "Demo"])
        self.mode_combo_box.setCurrentText(self.current_mode.capitalize())
        self.mode_combo_box.setMinimumWidth(100)
        top_bar_layout.addWidget(self.mode_combo_box)
        main_layout.addLayout(top_bar_layout)

        if ImportedDashboardWidget:
            self.dashboard_widget_ref = ImportedDashboardWidget(bot_core_instance=self.bot_core, lang_manager=self.lang_manager, parent=self)
            self.log_widget_ref = getattr(self.dashboard_widget_ref, 'log_output', None)
            main_layout.addWidget(self.dashboard_widget_ref, 1)
        
        if not self.log_widget_ref:
            self.log_widget_ref = QTextEdit()
            main_layout.addWidget(self.log_widget_ref)

        self._create_menus()
        self.setStatusBar(QStatusBar(self))
        self._retranslate_ui()

    def _create_menus(self):
        menubar = self.menuBar()
        self.user_menu = menubar.addMenu("")
        self.user_mgmt_action = QAction(self)
        self.user_mgmt_action.triggered.connect(self._show_user_mgmt_dialog)
        self.user_menu.addAction(self.user_mgmt_action)

        self.language_menu = menubar.addMenu("")
        lang_group = QActionGroup(self)
        lang_group.setExclusive(True)
        for lang_code in sorted(self.lang_manager.available_languages.keys()):
            action = QAction(lang_code.upper(), self, checkable=True)
            action.setData(lang_code)
            if lang_code == self.lang_manager.current_lang_code:
                action.setChecked(True)
            self.language_menu.addAction(action)
            lang_group.addAction(action)
        lang_group.triggered.connect(self._change_language)
        
        self.test_menu = menubar.addMenu("")
        self.run_manual_test_action = QAction(self)
        self.run_manual_test_action.triggered.connect(self._run_manual_core_test_wrapper)
        self.test_menu.addAction(self.run_manual_test_action)
        
        self.help_menu = menubar.addMenu("")
        self.about_action = QAction(self)
        self.about_action.triggered.connect(self._show_about_dialog)
        self.help_menu.addAction(self.about_action)

    def _change_language(self, action: QAction):
        lang_code = action.data()
        if lang_code and lang_code != self.lang_manager.current_lang_code:
            logger.info(f"Dil değiştiriliyor: {lang_code}")
            self.lang_manager.load_language(lang_code)
            self.config_manager.set_setting('general', 'language', lang_code)
            self.config_manager.save_settings()
            self._retranslate_ui()
    
    # Orijinal metotlarınız
    def _on_main_user_selection_changed(self, selected_username: str):
        pass
    def _apply_default_stylesheet(self):
        self.setStyleSheet("QWidget { font-size: 10pt; }")
    def _setup_gui_logging(self):
        if not self.log_widget_ref: return
        log_handler = QTextEditLogger(self.log_widget_ref)
        formatter = logging.Formatter('%(asctime)s [%(levelname)-7s] %(name)s: %(message)s', datefmt='%H:%M:%S')
        log_handler.setFormatter(formatter)
        log_handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(log_handler)
    def _populate_user_combo(self):
        pass
    def _update_button_enabled_state(self):
        is_bot_running = self.bot_core and self.bot_core.is_running()
        can_start = self.license_is_valid and not is_bot_running
        self.mode_combo_box.setEnabled(can_start)
        if self.dashboard_widget_ref:
            start_button = getattr(self.dashboard_widget_ref, 'start_button', None)
            stop_button = getattr(self.dashboard_widget_ref, 'stop_button', None)
            if start_button: start_button.setEnabled(can_start)
            if stop_button: stop_button.setEnabled(is_bot_running)
    def _disconnect_signals(self):
        if hasattr(self, 'bot_core') and self.bot_core:
            try: self.bot_core.status_changed_signal.disconnect()
            except (TypeError, RuntimeError): pass
    def _connect_signals_and_buttons(self):
        if not self.bot_core: return
        self.bot_core.status_changed_signal.connect(self._on_bot_status_changed)
        if self.dashboard_widget_ref:
            start_button = getattr(self.dashboard_widget_ref, 'start_button', None)
            if start_button: start_button.clicked.connect(self.start_bot_action)
            stop_button = getattr(self.dashboard_widget_ref, 'stop_button', None)
            if stop_button: stop_button.clicked.connect(self.stop_bot_action)
    @pyqtSlot(str)
    def _on_bot_status_changed(self, status_text: str):
        current_msg = self.statusBar().currentMessage()
        if "Lisans" in current_msg and "Durduruldu" in status_text: return
        self.statusBar().showMessage(f"Bot Durumu: {status_text}", 10000)
        if self.dashboard_widget_ref: self.dashboard_widget_ref.set_status_message(status_text)
        self._update_button_enabled_state()
    @pyqtSlot(list)
    def _on_positions_updated(self, positions_data: list):
        if self.dashboard_widget_ref: self.dashboard_widget_ref.update_positions_table(positions_data)
    @pyqtSlot(list)
    def _on_history_trades_updated(self, trades_data: list):
        if self.dashboard_widget_ref: self.dashboard_widget_ref.update_historical_trades_table(trades_data)
    @pyqtSlot(str)
    def _on_report_data_ready(self, report_text: str):
        if self.dashboard_widget_ref:
            reports_widget = getattr(self.dashboard_widget_ref, '_reports_tab_ref', None)
            if reports_widget: reports_widget.display_report(report_text)
    @pyqtSlot(str, str)
    def _handle_bot_core_log_signal(self, message: str, level_str: str): pass
    @pyqtSlot(str)
    def _handle_close_position_command_wrapper(self, order_id: str):
        if self.bot_core: self.bot_core.handle_manual_close_position(order_id)
    @pyqtSlot(str)
    def _on_mode_changed_combo(self, selected_mode_text: str):
        if self.bot_core.is_running():
            QMessageBox.warning(self, "Değişiklik Engellendi", "Bot çalışırken mod değiştirilemez.")
            self.mode_combo_box.blockSignals(True); self.mode_combo_box.setCurrentText(self.current_mode.capitalize()); self.mode_combo_box.blockSignals(False)
            return
        self.current_mode = selected_mode_text.lower()
        self.statusBar().showMessage(f"Aktif Mod: {self.current_mode.capitalize()}", 3000)
    def start_bot_action(self):
        if not self.bot_core or self.bot_core.is_running(): return
        selected_user = self.logged_in_user
        selected_mode = self.mode_combo_box.currentText().lower()
        if not selected_user: return
        if self.dashboard_widget_ref: self.dashboard_widget_ref.clear_all_data()
        self.statusBar().showMessage(f"Bot '{selected_user}' için '{selected_mode.capitalize()}' modunda başlatılıyor...", 0)
        self.bot_core.start(selected_user, selected_mode)
    def stop_bot_action(self):
        if not (self.bot_core and self.bot_core.is_running()): return
        title = self.lang_manager.get_string('confirm_stop_bot_title')
        text = self.lang_manager.get_string('confirm_stop_bot_text', user=self.bot_core.get_active_user(), mode=self.bot_core.get_current_mode().capitalize())
        reply = QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            close_title = self.lang_manager.get_string('confirm_close_positions_title')
            close_text = self.lang_manager.get_string('confirm_close_positions_text')
            close_pos_reply = QMessageBox.question(self, close_title, close_text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            self.bot_core.stop(close_positions_decision=(close_pos_reply == QMessageBox.Yes))
    def _show_user_mgmt_dialog(self):
        if not ImportedUserMgmtDialog or not self.user_manager: return
        dialog = ImportedUserMgmtDialog(user_manager=self.user_manager, parent=self)
        dialog.exec_()
    def _show_about_dialog(self):
        version = self.config_manager.get_setting('app_info', 'version', '1.0.0-dev')
        QMessageBox.about(self, "Hakkında", f"Versiyon: {version}")
    def _run_manual_core_test_wrapper(self):
        if not self.bot_core: return
        active_user = self.logged_in_user
        if not active_user: return
        self.bot_core.fetch_and_send_report_data(active_user, "...", "...")
    def closeEvent(self, event):
        if self.bot_core and self.bot_core.is_running():
            title = self.lang_manager.get_string('exit_confirmation_title')
            text = self.lang_manager.get_string('exit_confirmation_text', user=self.bot_core.get_active_user(), mode=self.bot_core.get_current_mode().capitalize())
            reply = QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                event.ignore()
                return
            self.stop_bot_action()
        if self.db_manager: self.db_manager.close()
        event.accept()

    # --- YENİ: Güncelleme Kontrolü İçin Metotlar ---
    def _check_for_updates(self):
        """Uzak sunucudan sürüm bilgilerini kontrol eder."""
        try:
            # Mevcut versiyonu config yöneticisinden al
            local_version = self.config_manager.get_setting('app_info', 'version', '0.0.0')

            # Sunucudan en son sürüm bilgisini al (3 saniye zaman aşımı ile)
            response = requests.get(VERSION_URL, timeout=3)
            response.raise_for_status()  # HTTP hata kodu varsa (4xx veya 5xx) exception fırlat
            
            data = response.json()
            remote_version = data.get("version")
            download_url = data.get("download_url")

            # Basit string karşılaştırması versiyonlar için genellikle yeterlidir (örn: "1.1.0" > "1.0.0")
            if remote_version and download_url and remote_version > local_version:
                logger.info(f"Yeni sürüm mevcut: {remote_version} (Mevcut: {local_version})")
                self._show_update_notification(download_url, remote_version)
            else:
                logger.info("Uygulama güncel.")

        except requests.exceptions.RequestException as e:
            logger.warning(f"Güncelleme sunucusuna ulaşılamadı: {e}")
            self.statusBar().showMessage("Güncelleme sunucusuna ulaşılamadı.", 5000)
        except Exception as e:
            logger.error(f"Güncelleme kontrolünde beklenmedik bir hata oluştu: {e}", exc_info=True)
            self.statusBar().showMessage("Güncelleme kontrolünde bir hata oluştu.", 5000)

    def _show_update_notification(self, url: str, new_version: str):
        """Durum çubuğunda tıklanabilir bir güncelleme butonu gösterir."""
        # Eğer daha önceden bir buton eklenmişse tekrar ekleme
        if hasattr(self, 'update_button') and self.update_button.isVisible():
            return
            
        self.update_button = QPushButton(f"🚀 Yeni Sürüm ({new_version}) Mevcut! İndirmek için tıklayın.")
        self.update_button.setToolTip(f"İndirme sayfasına git: {url}")
        self.update_button.clicked.connect(lambda: self._open_download_page(url))
        
        # Butonu durum çubuğunun sağ tarafına kalıcı olarak ekle
        self.statusBar().addPermanentWidget(self.update_button)

    def _open_download_page(self, url: str):
        """Verilen URL'yi kullanıcının varsayılan web tarayıcısında açar."""
        try:
            webbrowser.open(url)
        except Exception as e:
            logger.error(f"İndirme sayfası açılamadı: {url}, Hata: {e}")
            QMessageBox.warning(self, "Hata", f"Tarayıcı açılamadı. Lütfen şu adresi manuel olarak ziyaret edin:\n{url}")