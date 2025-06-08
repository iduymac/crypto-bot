# gui/dashboard_widget.py

import sys # sys.path kontrolü için (opsiyonel, hata ayıklama)
import logging
from datetime import datetime, date, time, timezone
from decimal import Decimal, InvalidOperation # utils.format_decimal_auto Decimal alabilir
from typing import Optional, Dict, Any, List, Tuple, Union, TYPE_CHECKING # Mevcut importlarınıza TYPE_CHECKING ekleyin
from config.language_manager import LanguageManager

if TYPE_CHECKING:
    from core.bot_core import BotCore
    from core.exchange_api import ExchangeAPI

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QTextEdit, QHeaderView, QSplitter, QAbstractItemView, QPushButton,
    QSpacerItem, QSizePolicy, QTabWidget, QMessageBox, QDateEdit,
    QFormLayout, QGroupBox
)
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QDate
from PyQt5.QtGui import QColor, QFont

# --- Logger ---
try:
    from core.logger import setup_logger
    logger = setup_logger('dashboard_widget')
    logger.debug("core.logger.setup_logger başarıyla import edildi ve 'dashboard_widget' logger'ı alındı.")
except ImportError as e_logger_import:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('dashboard_widget_fallback')
    # import sys as fallback_sys # Zaten yukarıda import edilmiş
    logger.warning(
        f"core.logger MODÜLÜ BULUNAMADI (DETAY: {e_logger_import}), temel fallback logger kullanılıyor.",
        exc_info=True
    )
    logger.warning(f"Fallback anındaki sys.path: {sys.path}") # sys'i doğrudan kullan
except Exception as e_logger_general:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('dashboard_widget_critical_fallback')
    logger.critical(
        f"core.logger import edilirken veya setup_logger çağrılırken beklenmedik bir HATA oluştu (DETAY: {e_logger_general}), "
        "temel fallback logger kullanılıyor.",
        exc_info=True
    )
# --- /Logger ---

# --- utils import ---
try:
    import utils
    if not hasattr(utils, 'format_decimal_autor'):
        logger.warning("utils modülünde 'format_decimal_auto' fonksiyonu bulunamadı. Sayı formatlama yapılamayacak.")
except ImportError as e_utils_import:
    logger.error(f"utils modülü import edilemedi! (DETAY: {e_utils_import}) Sayı formatlama gibi yardımcı fonksiyonlar çalışmayabilir.", exc_info=True)
    utils = None
except Exception as e_utils_general:
    logger.error(f"utils modülü import edilirken beklenmedik bir hata! (DETAY: {e_utils_general})", exc_info=True)
    utils = None
# --- /utils import ---


class ReportsWidget(QWidget):
    # <<<<<<<<<<<<<< DEĞİŞİKLİK: Sinyal tanımı (str, str, str) olarak güncellendi >>>>>>>>>>>>>>>
    generate_report_requested = pyqtSignal(str, str, str) # user, start_ms_str, end_ms_str

    def __init__(self, lang_manager: LanguageManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.lang_manager = lang_manager
        self.active_username: Optional[str] = None
        self._init_ui()

    def set_active_user(self, username: Optional[str]):
        self.active_username = username

    def _init_ui(self):
        layout = QVBoxLayout(self)
        self.date_selection_group = QGroupBox()
        date_selection_layout = QHBoxLayout(self.date_selection_group)
        form_layout_start = QFormLayout()
        
        today = QDate.currentDate()
        self.start_date_edit = QDateEdit(today.addMonths(-1))
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date_label = QLabel()
        form_layout_start.addRow(self.start_date_label, self.start_date_edit)

        form_layout_end = QFormLayout()
        self.end_date_edit = QDateEdit(today)
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_date_label = QLabel()
        form_layout_end.addRow(self.end_date_label, self.end_date_edit)

        date_selection_layout.addLayout(form_layout_start)
        date_selection_layout.addLayout(form_layout_end)
        date_selection_layout.addStretch()

        self.generate_button = QPushButton()
        self.generate_button.clicked.connect(self._request_report)
        date_selection_layout.addWidget(self.generate_button, 0, Qt.AlignBottom)
        layout.addWidget(self.date_selection_group)

        self.report_results_label = QLabel()
        layout.addWidget(self.report_results_label)
        self.report_output = QTextEdit()
        self.report_output.setReadOnly(True)
        self.report_output.setFont(QFont("Consolas", 9))
        layout.addWidget(self.report_output, 1)
        
        self._retranslate_ui()
    def _retranslate_ui(self):
        self.date_selection_group.setTitle(self.lang_manager.get_string("reports_date_range"))
        self.start_date_label.setText(self.lang_manager.get_string("reports_start_date"))
        self.end_date_label.setText(self.lang_manager.get_string("reports_end_date"))
        self.generate_button.setText(self.lang_manager.get_string("reports_generate_button"))
        self.report_results_label.setText(f"<b>{self.lang_manager.get_string('reports_results_title')}</b>")   

    @pyqtSlot()
    def _request_report(self):
        start_qdate = self.start_date_edit.date()
        end_qdate = self.end_date_edit.date()

        if start_qdate > end_qdate:
            QMessageBox.warning(self, "Geçersiz Tarih Aralığı",
                                "Başlangıç tarihi, bitiş tarihinden sonra olamaz.")
            return

        username_to_emit = self.active_username
        if not username_to_emit:
            QMessageBox.warning(self, "Kullanıcı Seçilmedi", "Lütfen rapor oluşturmak için bir kullanıcı seçin (Ana pencereden).")
            logger.warning("Rapor isteği için aktif kullanıcı adı bulunamadı.")
            self.report_output.setText("Hata: Rapor için aktif kullanıcı adı belirlenemedi.")
            return

        try:
            start_dt_local = datetime.combine(start_qdate.toPyDate(), time.min)
            start_dt_utc = start_dt_local.astimezone(timezone.utc)
            end_dt_local = datetime.combine(end_qdate.toPyDate(), time.max.replace(microsecond=999999))
            end_dt_utc = end_dt_local.astimezone(timezone.utc)
            start_ms = int(start_dt_utc.timestamp() * 1000)
            end_ms = int(end_dt_utc.timestamp() * 1000)

            start_ms_str = str(start_ms)
            end_ms_str = str(end_ms)

            self.report_output.setText("Rapor oluşturuluyor, lütfen bekleyin...")
            logger.critical(f"ReportsWidget EMITTING generate_report_requested: User='{username_to_emit}', Start_ms_str='{start_ms_str}', End_ms_str='{end_ms_str}'")
            self.generate_report_requested.emit(username_to_emit, start_ms_str, end_ms_str)

        except Exception as e:
            logger.error(f"Rapor isteği için tarih dönüşümü sırasında hata: {e}", exc_info=True)
            QMessageBox.critical(self, "Tarih Dönüştürme Hatası",
                                 f"Seçilen tarihler işlenirken bir hata oluştu:\n{type(e).__name__}: {e}")
            self.report_output.setText(f"Hata: Tarihler işlenemedi.\n{e}")

    @pyqtSlot(str)
    def display_report(self, report_text: str):
        self.report_output.setText(report_text)


class DashboardWidget(QWidget):
    close_position_requested = pyqtSignal(str)
    request_historical_trades = pyqtSignal(str, str, str)
    forward_generate_report_request = pyqtSignal(str, str, str)

    def __init__(self, bot_core_instance: Optional['BotCore'], lang_manager: LanguageManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.bot_core = bot_core_instance
        self.lang_manager = lang_manager
        
        self._active_username_for_requests: Optional[str] = None
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 5, 0, 5)
        self._create_top_bar()
        self.tab_widget = QTabWidget()
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        self.layout.addWidget(self.tab_widget, 1)

        self._create_open_positions_log_tab()
        self._create_history_tab()
        self._create_reports_tab()
        
        self._reports_tab_ref.generate_report_requested.connect(self.forward_generate_report_request)
        self._retranslate_ui()
    def _retranslate_ui(self):
        self.start_button.setText(self.lang_manager.get_string('start_bot_button'))
        self.stop_button.setText(self.lang_manager.get_string('stop_bot_button'))
        
        self.tab_widget.setTabText(self.tab_widget.indexOf(self.positions_tab), self.lang_manager.get_string('tab_positions'))
        self.tab_widget.setTabText(self.tab_widget.indexOf(self.history_tab), self.lang_manager.get_string('tab_history'))
        self.tab_widget.setTabText(self.tab_widget.indexOf(self._reports_tab_ref), self.lang_manager.get_string('tab_reports'))
        
        pos_headers = [
            self.lang_manager.get_string('header_open_time'), self.lang_manager.get_string('header_symbol'),
            self.lang_manager.get_string('header_side'), self.lang_manager.get_string('header_amount'),
            self.lang_manager.get_string('header_entry_price'), self.lang_manager.get_string('sl_price'), 
            self.lang_manager.get_string('tp_price'), self.lang_manager.get_string('tsl_status'), 
            self.lang_manager.get_string('tsl_stop'), self.lang_manager.get_string('header_current_price'), 
            self.lang_manager.get_string('header_pnl'), self.lang_manager.get_string('status')
        ]
        self.positions_table.setColumnCount(len(pos_headers))
        self.positions_table.setHorizontalHeaderLabels(pos_headers)

        hist_headers = [
            self.lang_manager.get_string('header_open_time'), self.lang_manager.get_string('close_time'), 
            self.lang_manager.get_string('header_symbol'), self.lang_manager.get_string('header_side'), 
            self.lang_manager.get_string('header_amount'), self.lang_manager.get_string('header_entry_price'), 
            self.lang_manager.get_string('exit_price'), self.lang_manager.get_string('gross_pnl'), 
            self.lang_manager.get_string('fee'), self.lang_manager.get_string('net_pnl')
        ]
        self.history_table.setColumnCount(len(hist_headers))
        self.history_table.setHorizontalHeaderLabels(hist_headers)

        if self._reports_tab_ref:
            self._reports_tab_ref._retranslate_ui()
        self.pos_col_indices = {self.lang_manager.get_string(key): i for i, key in enumerate(['header_open_time', 'header_symbol', 'header_side', 'header_amount', 'header_entry_price', 'sl_price', 'tp_price', 'tsl_status', 'tsl_stop', 'header_current_price', 'header_pnl', 'status'])}
        self.hist_col_indices = {self.lang_manager.get_string(key): i for i, key in enumerate(['header_open_time', 'close_time', 'header_symbol', 'header_side', 'header_amount', 'header_entry_price', 'exit_price', 'gross_pnl', 'fee', 'net_pnl'])}    
    # --- update_positions_table METODUNU GÜNCELLE ---
    @pyqtSlot(list)
    def update_positions_table(self, positions_data: List[Dict[str, Any]]):
        logger.info(f"SLOT (update_positions_table): {len(positions_data)} açık pozisyon verisi alınıyor (Lokalden).")
        total_pnl_dec = Decimal('0.0')
        has_valid_pnl = False # En az bir geçerli PnL hesaplanıp hesaplanmadığını takip eder
        self.positions_table.setSortingEnabled(False)
        
        current_selection_order_id = None
        selected_rows = self.positions_table.selectionModel().selectedRows()
        if selected_rows:
            try:
                # self._pos_col_indices kullandığınızdan emin olun
                id_item = self.positions_table.item(selected_rows[0].row(), self._pos_col_indices[POS_COL_OPEN_TIME]) 
                if id_item: current_selection_order_id = id_item.data(Qt.UserRole)
            except Exception as sel_err: 
                logger.warning(f"Mevcut tablo seçimi okunurken hata: {sel_err}", exc_info=False)
        
        self.positions_table.setRowCount(0) # Tabloyu temizle
        new_selection_row = -1

        # ExchangeAPI örneğine bot_core üzerinden erişim
        current_exchange_api: Optional['ExchangeAPI'] = None # Tip ipucu için Optional ve 'ExchangeAPI'
                                                            # ExchangeAPI'yi TYPE_CHECKING bloğunda import edin
        
        if self.bot_core and hasattr(self.bot_core, 'exchange_api'):
            current_exchange_api = self.bot_core.exchange_api 
            if not current_exchange_api: 
                logger.warning("DashboardWidget: BotCore.exchange_api (bot başlatıldıktan sonra) None olarak bulundu. API'den PnL/Giriş Fiyatı alınamayacak.")
        elif not self.bot_core:
            logger.warning("DashboardWidget: BotCore örneği (self.bot_core) mevcut değil. API'den PnL/Giriş Fiyatı alınamayacak.")
        
        # Yukarıdaki blok current_exchange_api'yi ayarlar veya None bırakır.
        # Loglama için ek bir kontrol:
        if not current_exchange_api:
            logger.warning("DashboardWidget uyarı: ExchangeAPI örneği alınamadı. API'den canlı veri çekilmeyecek, sadece lokal veriler kullanılacak.")

        try:
            if not isinstance(positions_data, list):
                logger.error("Pozisyon verisi beklenen formatta (liste) değil!")
                self._finalize_position_update(error=True)
                return
            if not positions_data:
                logger.debug("Güncellenecek açık pozisyon bulunmuyor.")
                self._finalize_position_update(total_pnl=Decimal('0.0'), has_pnl=False)
                return
                
            self.positions_table.setRowCount(len(positions_data))
            bold_font = QFont(); bold_font.setBold(True)

            for row_index, local_pos_data in enumerate(positions_data):
                if not isinstance(local_pos_data, dict):
                    logger.warning(f"Satır {row_index} verisi geçersiz (sözlük değil): {local_pos_data}")
                    continue
                
                order_id = local_pos_data.get('order_id')
                if order_id is None:
                    logger.warning(f"Satır {row_index} için 'order_id' bulunamadı, bu satır atlanıyor.")
                    continue # Bu satırı atla
                
                order_id_str = str(order_id)
                timestamp = self._format_timestamp(local_pos_data.get('timestamp'))
                symbol = local_pos_data.get('symbol', '?')
                side = str(local_pos_data.get('side', '?')).upper()
                side_color = QColor("#008000") if side == 'BUY' else (QColor("#DC143C") if side == 'SELL' else QColor("black"))
                amount_val = local_pos_data.get('filled_amount', local_pos_data.get('amount'))
                amount_str = utils.format_decimal_auto(amount_val, decimals=8, default_on_error='Err') if utils else str(amount_val or 'N/A')
                
                # Varsayılan olarak lokal verileri kullan, API'den gelirse güncelle
                entry_val_to_display = local_pos_data.get('entry_price') 
                pnl_val_to_display = local_pos_data.get('profit_loss')     
                current_price_val_to_display = local_pos_data.get('current_price')

                # --- API'DEN GİRİŞ FİYATI, PNL VE GÜNCEL FİYATI ALMA BÖLÜMÜ ---
                if current_exchange_api and symbol != '?': # Sadece exchange_api örneği varsa ve sembol geçerliyse API'ye git
                    logger.debug(f"'{symbol}' için API'den pozisyon detayları çekiliyor...")
                    api_position_details = current_exchange_api.get_futures_position_details(symbol) 
                    
                    # API'den geçerli bir pozisyon detayı döndü mü kontrol et
                    if api_position_details and isinstance(api_position_details.get('position_amt'), (float, int)) and api_position_details.get('position_amt', 0) != 0:
                        logger.info(f"API'den '{symbol}' için pozisyon detayları başarıyla alındı: Giriş={api_position_details.get('entry_price')}, PnL={api_position_details.get('unrealized_pnl')}, MarkPrice={api_position_details.get('mark_price')}")
                        entry_val_to_display = api_position_details.get('entry_price', entry_val_to_display) # API'den gelen giriş fiyatı
                        pnl_val_to_display = api_position_details.get('unrealized_pnl', pnl_val_to_display) # API'den gelen PnL
                        current_price_val_to_display = api_position_details.get('mark_price', current_price_val_to_display) # Güncel fiyat için mark_price
                    else:
                        # API'den veri alınamazsa veya pozisyon aktif değilse, logla ama lokal verilerle devam et
                        logger.warning(f"API'den '{symbol}' için pozisyon detayı alınamadı veya pozisyon aktif değil/bulunamadı. Lokal veriler (Giriş/PnL/Güncel Fiyat) kullanılacak. API Yanıtı: {api_position_details}")
                # --- API'DEN VERİ ALMA SONU ---

                # Değerleri formatla (API'den veya lokalden gelen)
                entry_str = utils.format_decimal_auto(entry_val_to_display, decimals=4, default_on_error='-') if utils else str(entry_val_to_display or '-')
                sl_val = local_pos_data.get('sl_price')
                sl_str = utils.format_decimal_auto(sl_val, decimals=4, default_on_error='-') if utils else str(sl_val or '-')
                tp_val = local_pos_data.get('tp_price')
                tp_str = utils.format_decimal_auto(tp_val, decimals=4, default_on_error='-') if utils else str(tp_val or '-')
                
                curr_price_str = utils.format_decimal_auto(current_price_val_to_display, decimals=4, default_on_error='-') if utils else str(current_price_val_to_display or '-')
                
                pnl_str = "-"
                pnl_color = QColor("black")
                if pnl_val_to_display is not None:
                    try:
                        pnl_dec = Decimal(str(pnl_val_to_display)) 
                        pnl_str = f"{pnl_dec:+.2f}" # PnL için genellikle 2 ondalık yeterli (USDT için)
                        total_pnl_dec += pnl_dec
                        has_valid_pnl = True
                        if pnl_dec > Decimal('0'): pnl_color = QColor("#008000") # Yeşil
                        elif pnl_dec < Decimal('0'): pnl_color = QColor("#DC143C") # Kırmızı
                    except (InvalidOperation, TypeError, ValueError) as pnl_conv_err:
                        logger.warning(f"PnL değeri ('{pnl_val_to_display}') Decimal'e çevrilemedi veya formatlanamadı: {pnl_conv_err}")
                        pnl_str = "Hata" 
                
                tsl_enabled = local_pos_data.get('tsl_enabled', False)
                tsl_activated = local_pos_data.get('tsl_activated', False)
                tsl_stop_price_val = local_pos_data.get('tsl_stop_price')
                tsl_stop_str = utils.format_decimal_auto(tsl_stop_price_val, decimals=4, default_on_error='-') if utils else str(tsl_stop_price_val or '-')
                tsl_status_str = "Pasif"
                tsl_color = QColor("gray")
                if tsl_enabled:
                    tsl_status_str = "Aktif" if tsl_activated else "Bekliyor"
                    tsl_color = QColor("#1E90FF") if tsl_activated else QColor("#FFA500")
                
                status = str(local_pos_data.get('status', '?')).capitalize()

                # Tabloya öğeleri ekle
                self._set_table_item(self.positions_table, row_index, POS_COL_OPEN_TIME, timestamp, self._pos_col_indices, user_data=order_id_str, font=bold_font)
                self._set_table_item(self.positions_table, row_index, POS_COL_SYMBOL, symbol, self._pos_col_indices, font=bold_font)
                self._set_table_item(self.positions_table, row_index, POS_COL_SIDE, side, self._pos_col_indices, alignment=Qt.AlignCenter, foreground_color=side_color, font=bold_font)
                self._set_table_item(self.positions_table, row_index, POS_COL_AMOUNT, amount_str, self._pos_col_indices, alignment=Qt.AlignRight | Qt.AlignVCenter, font=bold_font)
                self._set_table_item(self.positions_table, row_index, POS_COL_ENTRY, entry_str, self._pos_col_indices, alignment=Qt.AlignRight | Qt.AlignVCenter, font=bold_font)
                self._set_table_item(self.positions_table, row_index, POS_COL_SL, sl_str, self._pos_col_indices, alignment=Qt.AlignRight | Qt.AlignVCenter, font=bold_font)
                self._set_table_item(self.positions_table, row_index, POS_COL_TP, tp_str, self._pos_col_indices, alignment=Qt.AlignRight | Qt.AlignVCenter, font=bold_font)
                self._set_table_item(self.positions_table, row_index, POS_COL_TSL_STATUS, tsl_status_str, self._pos_col_indices, alignment=Qt.AlignCenter, foreground_color=tsl_color, font=bold_font)
                self._set_table_item(self.positions_table, row_index, POS_COL_TSL_STOP, tsl_stop_str, self._pos_col_indices, alignment=Qt.AlignRight | Qt.AlignVCenter, font=bold_font)
                self._set_table_item(self.positions_table, row_index, POS_COL_CURRENT, curr_price_str, self._pos_col_indices, alignment=Qt.AlignRight | Qt.AlignVCenter, font=bold_font)
                self._set_table_item(self.positions_table, row_index, POS_COL_PNL, pnl_str, self._pos_col_indices, alignment=Qt.AlignRight | Qt.AlignVCenter, foreground_color=pnl_color, font=bold_font)
                self._set_table_item(self.positions_table, row_index, POS_COL_STATUS, status, self._pos_col_indices, alignment=Qt.AlignCenter, font=bold_font)

                if order_id_str == current_selection_order_id: 
                    new_selection_row = row_index
            
            if new_selection_row != -1: 
                self.positions_table.selectRow(new_selection_row)
            else: 
                self.positions_table.clearSelection()

        except Exception as e:
            logger.error(f"Açık pozisyon tablosu güncellenirken beklenmedik hata: {e}", exc_info=True)
            self._finalize_position_update(error=True) # Hata durumunda sonlandır
            return # Hata sonrası fonksiyondan çıkmak iyi bir pratik
        
        self._finalize_position_update(total_pnl=total_pnl_dec, has_pnl=has_valid_pnl)
    # --- update_positions_table METODU SONU ---

    # Diğer metodlarınız (_create_top_bar, _create_open_positions_log_tab, _create_history_tab, vb.)
    # burada olduğu gibi kalır, onlarda bir değişiklik yapmıyoruz. Sadece __init__ ve update_positions_table değişti.
    # _finalize_position_update, _update_total_pnl_label, _format_timestamp, _set_table_item metodlarınızda değişiklik yapmıyoruz.
    # _request_close_selected_position, _update_close_button_state, _on_tab_changed, clear_logs, clear_all_data
    # set_status_message, update_historical_trades_table, _create_reports_tab, _handle_report_request_from_tab, set_current_user_for_requests
    # _request_filtered_historical_trades, _update_history_total_pnl_label
    # bu metodlar da aynı kalacak.

    def set_current_user_for_requests(self, username: Optional[str]):
        logger.debug(f"DashboardWidget: İstekler için aktif kullanıcı '{username}' olarak ayarlandı.")
        self._active_username_for_requests = username
        if self._reports_tab_ref:
            self._reports_tab_ref.set_active_user(username)

    @pyqtSlot(str, str, str)
    def _handle_report_request_from_tab(self, username: str, start_ms_str: str, end_ms_str: str):
        logger.debug(f"DashboardWidget: Rapor isteği ReportsWidget'tan alındı (string). User: {username} (tip: {type(username)}), Start_ms_str: {start_ms_str} (tip: {type(start_ms_str)}), End_ms_str: {end_ms_str} (tip: {type(end_ms_str)}). BotCore'a iletiliyor.")
        self.forward_generate_report_request.emit(username, start_ms_str, end_ms_str)

    def _create_top_bar(self):
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(5, 0, 5, 0)
        # <<< DEĞİŞİKLİK: Başlangıç metni de dil yöneticisinden geliyor >>>
        self.status_label = QLabel(self.lang_manager.get_string("status_initializing"))
        top_layout.addWidget(self.status_label)
        top_layout.addStretch(1)
        self.start_button = QPushButton()
        self.stop_button = QPushButton()
        top_layout.addWidget(self.start_button)
        top_layout.addWidget(self.stop_button)
        self.layout.addLayout(top_layout)

    def _create_open_positions_log_tab(self):
        self.positions_tab = QWidget()
        tab1_layout = QVBoxLayout(self.positions_tab)
        self.splitter = QSplitter(Qt.Vertical)
        
        self.positions_table = QTableWidget()
        self.positions_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.positions_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.positions_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.positions_table.setSortingEnabled(True)
        
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        
        self.splitter.addWidget(self.positions_table)
        self.splitter.addWidget(self.log_output)
        tab1_layout.addWidget(self.splitter)
        
        bottom_bar_layout = QHBoxLayout()
        self.total_pnl_label = QLabel()
        bottom_bar_layout.addWidget(self.total_pnl_label)
        bottom_bar_layout.addStretch()
        self.close_selected_button = QPushButton()
        self.close_selected_button.clicked.connect(self._request_close_selected_position)
        bottom_bar_layout.addWidget(self.close_selected_button)
        tab1_layout.addLayout(bottom_bar_layout)
        
        self.tab_widget.addTab(self.positions_tab, "") # Metin _retranslate_ui'da ayarlanacak

    def _create_history_tab(self):
        self.history_tab = QWidget()
        layout = QVBoxLayout(self.history_tab)
        
        # Orijinal kodunuzdaki tarih filtreleme bölümü burada kalabilir
        # (QGroupBox, QDateEdit, QPushButton vs.)
        
        self.history_table = QTableWidget()
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setSortingEnabled(True)
        
        layout.addWidget(self.history_table)
        
        # Orijinal kodunuzdaki özet (summary_layout) bölümü burada kalabilir
        
        self.tab_widget.addTab(self.history_tab, "") # Metin _retranslate_ui'da ayarlanacak

    def _create_reports_tab(self):
        # DOĞRUSU: lang_manager'ı parametre olarak iletiyoruz.
        self._reports_tab_ref = ReportsWidget(lang_manager=self.lang_manager, parent=self)
        self._reports_tab_ref.setObjectName("reportsTabWidget")
        self.tab_widget.addTab(self._reports_tab_ref, "") # Metni _retranslate_ui ayarlar
        logger.debug("Sekme 'Raporlar' oluşturuldu (ReportsWidget kullanılarak).")

    @pyqtSlot()
    def _request_filtered_historical_trades(self):
        # Bu metodun içeriği aynı kalabilir
        logger.info("Geçmiş İşlemler için 'Filtrele' butonuna tıklandı.")
        if not hasattr(self, 'history_start_date_edit') or not hasattr(self, 'history_end_date_edit'):
            logger.error("Tarih düzenleme widget'ları (history_start_date_edit veya history_end_date_edit) bulunamadı.")
            QMessageBox.warning(self, "Arayüz Hatası", "Tarih alanları düzgün başlatılamamış. Lütfen geliştiriciye bildirin.")
            return
        start_qdate = self.history_start_date_edit.date()
        end_qdate = self.history_end_date_edit.date()
        if start_qdate > end_qdate:
            QMessageBox.warning(self, "Geçersiz Tarih Aralığı", "Başlangıç tarihi, bitiş tarihinden sonra olamaz.")
            return
        username_to_emit = self._active_username_for_requests
        if not username_to_emit or self._active_username_for_requests == "<Kullanıcı Yok>":
             QMessageBox.warning(self, "Kullanıcı Seçilmedi", "Lütfen geçmiş işlemleri filtrelemek için bir kullanıcı seçin (Ana pencereden).")
             logger.warning("Geçmiş İşlemler için kullanıcı adı belirlenemedi (_active_username_for_requests boş).")
             if hasattr(self, 'history_table'): self.history_table.setRowCount(0)
             if hasattr(self, 'history_date_label'): self.history_date_label.setText("<b>Kullanıcı seçilmedi.</b>")
             return
        try:
            start_dt_local = datetime.combine(start_qdate.toPyDate(), time.min)
            end_dt_local = datetime.combine(end_qdate.toPyDate(), time.max.replace(microsecond=999999))
            start_ms = int(start_dt_local.astimezone(timezone.utc).timestamp() * 1000)
            end_ms = int(end_dt_local.astimezone(timezone.utc).timestamp() * 1000)
            start_ms_str = str(start_ms)
            end_ms_str = str(end_ms)
            logger.info(f"Geçmiş işlemler için istek gönderiliyor: Kullanıcı='{username_to_emit}', Başlangıç(ms)='{start_ms_str}', Bitiş(ms)='{end_ms_str}'")
            self.request_historical_trades.emit(username_to_emit, start_ms_str, end_ms_str)
            start_date_str_display = start_qdate.toString("dd.MM.yyyy")
            end_date_str_display = end_qdate.toString("dd.MM.yyyy")
            if hasattr(self, 'history_date_label'):
                self.history_date_label.setText(f"<b>Gösterilen İşlemler: {start_date_str_display} - {end_date_str_display} ({username_to_emit})</b>")
            if hasattr(self, 'history_table'): 
                self.history_table.setRowCount(0)
        except Exception as e:
            logger.error(f"Geçmiş işlemler için tarih dönüşümü veya istek gönderimi sırasında hata: {e}", exc_info=True)
            QMessageBox.critical(self, "Filtreleme Hatası", f"Tarihler işlenirken veya istek gönderilirken bir hata oluştu:\n{type(e).__name__}: {e}")
            if hasattr(self, 'history_date_label'): self.history_date_label.setText("<b>Filtreleme sırasında bir hata oluştu.</b>")
            if hasattr(self, 'history_table'): self.history_table.setPlaceholderText("Filtreleme hatası.")
    
    def _format_timestamp(self, timestamp_ms: Optional[Union[int, float, str]]) -> str:
        # Bu metodun içeriği aynı kalabilir
        if timestamp_ms is None: return "N/A"
        try:
            numeric_timestamp_ms = float(timestamp_ms)
            if numeric_timestamp_ms < 0: return "Geçersiz Zaman"
            dt_object_local = datetime.fromtimestamp(numeric_timestamp_ms / 1000.0)
            return dt_object_local.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError, OverflowError) as e: logger.warning(f"Geçersiz timestamp ({timestamp_ms}): {e}"); return "Hatalı Zaman"
        except OSError as e: logger.error(f"Timestamp ({timestamp_ms}) formatlarken OSError: {e}"); return "OS Zaman Hatası"
        except Exception as e: logger.error(f"Timestamp ({timestamp_ms}) formatlarken beklenmedik hata: {e}", exc_info=True); return "Format Hatası"

    def _set_table_item(self, table: QTableWidget, row: int, col_key: str, value: Any,
                        indices: Dict[str, int], alignment: Qt.AlignmentFlag = Qt.AlignLeft | Qt.AlignVCenter,
                        foreground_color: Optional[QColor] = None, background_color: Optional[QColor] = None,
                        font: Optional[QFont] = None, user_data: Any = None):
        # Bu metodun içeriği aynı kalabilir
        col_index = indices.get(col_key)
        if col_index is None: logger.error(f"Tablo öğesi: Sütun '{col_key}' bulunamadı."); return
        item = QTableWidgetItem(str(value) if value is not None else "")
        item.setTextAlignment(alignment)
        if foreground_color: item.setForeground(foreground_color)
        if background_color: item.setBackground(background_color)
        if font: item.setFont(font)
        if user_data is not None: item.setData(Qt.UserRole, user_data)
        table.setItem(row, col_index, item)

    @pyqtSlot(str)
    def set_status_message(self, status_text: str):
        # Bu metodun içeriği aynı kalabilir
        logger.info(f"SLOT (set_status_message): Yeni durum = '{status_text}'")
        safe_text = str(status_text)
        self.status_label.setText(f"Durum: {safe_text}")
        is_running = "çalışıyor" in safe_text.lower() or "başlatılıyor" in safe_text.lower()
        if hasattr(self, 'start_button'): self.start_button.setEnabled(not is_running)
        if hasattr(self, 'stop_button'): self.stop_button.setEnabled(is_running)
        self._update_close_button_state()

    @pyqtSlot(list)
    def update_historical_trades_table(self, history_data: List[Dict[str, Any]]):
        # Bu metodun içeriği aynı kalabilir
        logger.info(f"SLOT (update_historical_trades_table): {len(history_data)} geçmiş işlem verisi alınıyor...")
        if not hasattr(self, 'history_table'):
            logger.error("Geçmiş işlem tablosu (self.history_table) bulunamadı.")
            self._update_history_total_pnl_label(Decimal('0.0'), data_found=False, error=True)
            return
        self.history_table.setSortingEnabled(False)
        self.history_table.setRowCount(0)
        total_net_pnl_for_period = Decimal('0.0')
        data_successfully_processed = False
        try:
            if not isinstance(history_data, list):
                logger.error("Geçmiş işlem verisi liste değil!")
                self._update_history_total_pnl_label(Decimal('0.0'), data_found=False, error=True)
                return
            if not history_data:
                self._update_history_total_pnl_label(Decimal('0.0'), data_found=False)
                if hasattr(self, 'history_date_label') and "Gösterilen İşlemler:" in self.history_date_label.text():
                     self.history_date_label.setText(f"{self.history_date_label.text()} - <i>Veri Bulunamadı</i>")
                return
            self.history_table.setRowCount(len(history_data))
            bold_font = QFont(); bold_font.setBold(True)
            for row_index, trade in enumerate(history_data):
                if not isinstance(trade, dict): continue
                open_time = self._format_timestamp(trade.get('open_timestamp'))
                close_time = self._format_timestamp(trade.get('close_timestamp'))
                symbol = trade.get('symbol', '?')
                side = str(trade.get('side', '?')).upper()
                side_color = QColor("#008000") if side == 'BUY' else (QColor("#DC143C") if side == 'SELL' else QColor("black"))
                amount_str = utils.format_decimal_auto(trade.get('amount'), decimals=8, default_on_error='Err') if utils else str(trade.get('amount', 'N/A'))
                entry_str = utils.format_decimal_auto(trade.get('entry_price'), decimals=4, default_on_error='-') if utils else str(trade.get('entry_price', '-'))
                exit_price_str = utils.format_decimal_auto(trade.get('exit_price'), decimals=4, default_on_error='-') if utils else str(trade.get('exit_price', '-'))
                gross_pnl_val = trade.get('gross_pnl')
                fee_val = trade.get('fee')
                net_pnl_val = trade.get('net_pnl')
                gross_pnl_str = f"{Decimal(str(gross_pnl_val)):+.2f}" if gross_pnl_val is not None else "-"
                fee_str = utils.format_decimal_auto(fee_val, decimals=8, default_on_error='-') if utils else str(fee_val or '-')
                net_pnl_str = "-"; net_pnl_color = QColor("black")
                if net_pnl_val is not None:
                    try:
                        net_pnl_dec = Decimal(str(net_pnl_val))
                        net_pnl_str = f"{net_pnl_dec:+.2f}"
                        total_net_pnl_for_period += net_pnl_dec
                        if net_pnl_dec > Decimal('0'): net_pnl_color = QColor("#008000")
                        elif net_pnl_dec < Decimal('0'): net_pnl_color = QColor("#DC143C")
                    except: net_pnl_str = "Hata"
                self._set_table_item(self.history_table, row_index, HIST_COL_OPEN_TIME, open_time, HIST_COL_INDICES, font=bold_font)
                self._set_table_item(self.history_table, row_index, HIST_COL_CLOSE_TIME, close_time, HIST_COL_INDICES, font=bold_font)
                self._set_table_item(self.history_table, row_index, HIST_COL_SYMBOL, symbol, HIST_COL_INDICES, font=bold_font)
                self._set_table_item(self.history_table, row_index, HIST_COL_SIDE, side, HIST_COL_INDICES, alignment=Qt.AlignCenter, foreground_color=side_color, font=bold_font)
                self._set_table_item(self.history_table, row_index, HIST_COL_AMOUNT, amount_str, HIST_COL_INDICES, alignment=Qt.AlignRight | Qt.AlignVCenter, font=bold_font)
                self._set_table_item(self.history_table, row_index, HIST_COL_ENTRY, entry_str, HIST_COL_INDICES, alignment=Qt.AlignRight | Qt.AlignVCenter, font=bold_font)
                self._set_table_item(self.history_table, row_index, HIST_COL_EXIT, exit_price_str, HIST_COL_INDICES, alignment=Qt.AlignRight | Qt.AlignVCenter, font=bold_font)
                self._set_table_item(self.history_table, row_index, HIST_COL_GROSS_PNL, gross_pnl_str, HIST_COL_INDICES, alignment=Qt.AlignRight | Qt.AlignVCenter, font=bold_font)
                self._set_table_item(self.history_table, row_index, HIST_COL_FEE, fee_str, HIST_COL_INDICES, alignment=Qt.AlignRight | Qt.AlignVCenter, font=bold_font)
                self._set_table_item(self.history_table, row_index, HIST_COL_NET_PNL, net_pnl_str, HIST_COL_INDICES, alignment=Qt.AlignRight | Qt.AlignVCenter, foreground_color=net_pnl_color, font=bold_font)
            data_successfully_processed = True
            logger.info(f"Geçmiş işlem tablosu güncellendi ({len(history_data)} satır). Toplam K/Z: {total_net_pnl_for_period:.2f}")
        except Exception as e:
            logger.error(f"Geçmiş işlemler tablosu güncellenirken hata: {e}", exc_info=True)
            self._update_history_total_pnl_label(Decimal('0.0'), data_found=data_successfully_processed, error=True)
        finally:
            if hasattr(self, 'history_table'): self.history_table.setSortingEnabled(True)
            if data_successfully_processed: self._update_history_total_pnl_label(total_net_pnl_for_period, data_found=True)
            elif not history_data and not data_successfully_processed: self._update_history_total_pnl_label(Decimal('0.0'), data_found=False)

    def _update_history_total_pnl_label(self, total_pnl: Decimal, data_found: bool, error: bool = False):
        # Bu metodun içeriği aynı kalabilir
        if not hasattr(self, 'history_total_pnl_label'): return
        pnl_text = "<b>Toplam Net K/Z: -</b>"; pnl_color_style = "color: black;"
        if error: pnl_text = "<b>Toplam Net K/Z: HESAPLAMA HATASI</b>"; pnl_color_style = "color: red;"
        elif not data_found: pnl_text = "<b>Toplam Net K/Z: (Veri Yok)</b>"; pnl_color_style = "color: gray;"
        else:
            quote_currency = "USDT" 
            try:
                pnl_str = utils.format_decimal_auto(total_pnl, decimals=2, sign=True, default_on_error="Hata") if utils else f"{total_pnl:+.2f}"
                pnl_text = f"<b>Toplam Net K/Z: {pnl_str} {quote_currency}</b>"
                if total_pnl > Decimal('0.0'): pnl_color_style = "color: #008000;"
                elif total_pnl < Decimal('0.0'): pnl_color_style = "color: #DC143C;"
            except Exception as fmt_err: logger.error(f"Toplam K/Z formatlarken hata: {fmt_err}"); pnl_text = "<b>Toplam Net K/Z: Format Hatası</b>"; pnl_color_style = "color: orange;"
        self.history_total_pnl_label.setText(pnl_text)
        self.history_total_pnl_label.setStyleSheet(f"font-weight: bold; {pnl_color_style}")

    @pyqtSlot(int)
    def _on_tab_changed(self, index: int):
        try:
            # <<< DEĞİŞİKLİK: Değişken isimleri güncellendi >>>
            is_open_positions_tab_active = (hasattr(self, 'positions_tab') and self.positions_tab is not None and index == self.tab_widget.indexOf(self.positions_tab))
            is_history_tab_active = (hasattr(self, 'history_tab') and self.history_tab is not None and index == self.tab_widget.indexOf(self.history_tab))
            is_reports_tab_active = (hasattr(self, '_reports_tab_ref') and self._reports_tab_ref is not None and index == self.tab_widget.indexOf(self._reports_tab_ref))

            logger.debug(f"Sekme değiştirildi: İndeks={index}, Pozisyonlar Aktif Mi={is_open_positions_tab_active}, Geçmiş Aktif Mi={is_history_tab_active}")

            if hasattr(self, 'close_selected_button'):
                self.close_selected_button.setVisible(is_open_positions_tab_active)
                if is_open_positions_tab_active:
                    self._update_close_button_state()
            
            if is_history_tab_active:
                if not self._active_username_for_requests or self._active_username_for_requests == "<Kullanıcı Yok>":
                    if hasattr(self, 'history_date_label'):
                        self.history_date_label.setText("<b>Lütfen ana menüden bir kullanıcı seçin ve ardından tarih aralığı belirleyip Filtrele'ye basın.</b>")
                    if hasattr(self, 'history_table'):
                        self.history_table.setRowCount(0)

            if is_reports_tab_active and self._reports_tab_ref:
                if self._active_username_for_requests:
                    self._reports_tab_ref.set_active_user(self._active_username_for_requests)
                else:
                    self._reports_tab_ref.set_active_user(None)
                    if hasattr(self._reports_tab_ref, 'report_output'):
                        self._reports_tab_ref.report_output.setText("Rapor oluşturmak için lütfen ana menüden bir kullanıcı seçin.")
        
        except Exception as e:
            logger.error(f"Sekme değiştirme (_on_tab_changed) hatası: {e}", exc_info=True)

    @pyqtSlot()
    def _update_close_button_state(self):
        # Bu metodun içeriği aynı kalabilir
        if not hasattr(self, 'close_selected_button'): return
        enable_button = False
        selected_rows = self.positions_table.selectionModel().selectedRows()
        if selected_rows:
            try:
                selected_row_index = selected_rows[0].row()
                status_col_idx = self._pos_col_indices.get(POS_COL_STATUS)
                id_col_idx = self._pos_col_indices.get(POS_COL_OPEN_TIME)
                if status_col_idx is not None and id_col_idx is not None:
                    status_item = self.positions_table.item(selected_row_index, status_col_idx)
                    id_item = self.positions_table.item(selected_row_index, id_col_idx)
                    if (status_item and status_item.text().lower() == 'open' and id_item and id_item.data(Qt.UserRole)):
                        enable_button = True
            except Exception as e: logger.error(f"_update_close_button_state hatası: {e}", exc_info=True)
        is_bot_running = False
        if hasattr(self, 'status_label'): is_bot_running = "çalışıyor" in self.status_label.text().lower()
        self.close_selected_button.setEnabled(enable_button and is_bot_running)

    @pyqtSlot()
    def _request_close_selected_position(self):
        # Bu metodun içeriği aynı kalabilir
        logger.info("'Seçili İşlemi Kapat' butonuna tıklandı.")
        selected_rows = self.positions_table.selectionModel().selectedRows()
        if not selected_rows: logger.warning("Kapatma isteği: Seçili satır yok."); return
        try:
            selected_row = selected_rows[0].row()
            id_col_idx = self._pos_col_indices.get(POS_COL_OPEN_TIME)
            symbol_col_idx = self._pos_col_indices.get(POS_COL_SYMBOL)
            if id_col_idx is None or symbol_col_idx is None:
                logger.error("Kapatma isteği: ID/Sembol sütun indeksi yok!"); QMessageBox.critical(self, "İç Hata", "Tablo yapılandırma sorunu."); return
            order_id_item = self.positions_table.item(selected_row, id_col_idx)
            symbol_item = self.positions_table.item(selected_row, symbol_col_idx)
            if not order_id_item or not symbol_item: logger.error(f"Kapatma isteği: Satır {selected_row} ID/Sembol hücresi yok."); return
            order_id_to_close = order_id_item.data(Qt.UserRole)
            symbol_text = symbol_item.text()
            if not order_id_to_close: logger.error(f"Kapatma isteği: Satır {selected_row} geçerli Order ID (UserRole) yok."); return
            reply = QMessageBox.question(self, 'Pozisyon Kapatma Onayı', f"<b>{symbol_text}</b> (ID: {order_id_to_close}) pozisyonunu manuel olarak kapatmak istediğinizden emin misiniz?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                logger.info(f"Manuel kapatma onaylandı: Order ID = '{order_id_to_close}', Sembol = '{symbol_text}'.")
                self.close_position_requested.emit(str(order_id_to_close))
                self.close_selected_button.setEnabled(False); self.close_selected_button.setText(" Kapatılıyor...")
            else: logger.info("Manuel pozisyon kapatma iptal edildi.")
        except Exception as e: logger.error(f"Pozisyon kapatma isteği gönderilirken hata: {e}", exc_info=True); QMessageBox.critical(self, "İstek Hatası", f"Pozisyon kapatma isteği gönderilirken hata: {e}")

    def clear_logs(self):
        # Bu metodun içeriği aynı kalabilir
        if hasattr(self, 'log_output') and isinstance(self.log_output, QTextEdit):
            self.log_output.clear()
            logger.info("Dashboard log alanı temizlendi.")

    def clear_all_data(self):
        # Bu metodun içeriği aynı kalabilir
        logger.info("Dashboard verileri temizleniyor (clear_all_data)...")
        if hasattr(self, 'positions_table'): self.positions_table.setRowCount(0)
        if hasattr(self, 'history_table'): self.history_table.setRowCount(0)
        if hasattr(self, '_reports_tab_ref') and self._reports_tab_ref and hasattr(self._reports_tab_ref, 'report_output'):
            self._reports_tab_ref.report_output.clear()
        self.clear_logs()
        self._update_total_pnl_label(Decimal('0.0'), False)
        logger.info("Dashboard verileri temizlendi.")

    def _finalize_position_update(self, total_pnl: Optional[Decimal] = None, has_pnl: bool = False, error: bool = False):
        # Bu metodun içeriği aynı kalabilir
        logger.debug("Pozisyon tablosu güncelleme işlemi sonlandırılıyor...")
        if hasattr(self, 'positions_table'): self.positions_table.setSortingEnabled(True)
        if error: self._update_total_pnl_label(None, False, is_error=True)
        elif total_pnl is not None: self._update_total_pnl_label(total_pnl, has_pnl)
        else: self._update_total_pnl_label(Decimal('0.0'), False)
        self._update_close_button_state()
        logger.debug("Pozisyon tablosu güncelleme işlemi sonlandırıldı.")

    def _update_total_pnl_label(self, total_pnl_dec: Optional[Decimal], has_valid_pnl: bool, is_error: bool = False):
        # Bu metodun içeriği aynı kalabilir
        if not hasattr(self, 'total_pnl_label'): return
        pnl_text = "Toplam Açık K/Z: -"
        pnl_color_style = "color: black;"
        if is_error:
            pnl_text = "Toplam Açık K/Z: HESAPLAMA HATASI"
            pnl_color_style = "color: red;"
        elif has_valid_pnl and total_pnl_dec is not None:
            quote_currency = "USDT"
            pnl_str = f"{total_pnl_dec:+.2f}"
            pnl_text = f"Toplam Açık K/Z: {pnl_str} {quote_currency}"
            if total_pnl_dec > 0: pnl_color_style = "color: #008000;"
            elif total_pnl_dec < 0: pnl_color_style = "color: #DC143C;"
        elif not has_valid_pnl and (total_pnl_dec is None or total_pnl_dec == Decimal('0.0')):
             pnl_text = "Toplam Açık K/Z: 0.00 USDT"
             pnl_color_style = "color: black;"
        self.total_pnl_label.setText(pnl_text)
        self.total_pnl_label.setStyleSheet(f"font-weight: bold; {pnl_color_style}")