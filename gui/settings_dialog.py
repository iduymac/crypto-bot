# gui/settings_dialog.py

import sys
import json
import logging
import copy # Derin kopya için
from typing import Dict, Any

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QTabWidget, QWidget,
    QSpinBox, QDoubleSpinBox, QComboBox, QGroupBox,
    QToolTip, QDialogButtonBox, QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSlot # pyqtSlot eklendi (eğer butonlara bağlı özel slotlar varsa)

# --- Proje İçi Importlar ---
try:
    from core.logger import setup_logger
    logger = setup_logger('settings_dialog')
except ImportError:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('settings_dialog_fallback')
    logger.warning("core.logger bulunamadı, fallback logger kullanılıyor.")

# --- Decimal Kütüphanesi ---
from decimal import Decimal, InvalidOperation # <--- BU IMPORT ÖNEMLİ OLABİLİR


# Varsayılan ayarlar (Bu yapı, UserConfigManager'a yeni kullanıcı eklenirken de kullanılabilir)
DEFAULT_SETTINGS = {
    "exchange": {
        "name": "binanceusdm", # Futures için varsayılan
        "api_key": "",
        "secret_key": "",
        "password": "" # Bazı borsalar için şifre alanı
    },
    "risk": {
        "max_open_positions": 5,
        "max_risk_per_trade_percent": 2.0,
        "max_daily_loss_percent": 10.0
    },
    "signal": {
        "source": "webhook", # Varsayılan webhook olabilir
        "webhook_secret": "", # Webhook için güvenlik anahtarı
        "api_url": "",
        "telegram_token": "",
        "telegram_chat_id": ""
    },
    "trading": {
        "default_order_type": "market",
        "default_amount_type": "percentage", # 'percentage' veya 'fixed'
        "default_amount_value": 10.0, # Yüzde ise %, sabit ise USDT (kaldıraçsız ana para)
        "stop_loss_percentage": 2.0, # % cinsinden (0 = kapalı)
        "take_profit_percentage": 4.0, # % cinsinden (0 = kapalı)
        "tsl_activation_percent": 1.5, # % cinsinden (0 = kapalı)
        "tsl_distance_percent": 0.5, # % cinsinden (0 = kapalı)
        "default_leverage": 5, # Varsayılan kaldıraç
        "default_margin_mode": "ISOLATED" # 'ISOLATED' veya 'CROSSED'
    },
    "demo_settings": {
        "start_balances": {
             "USDT": "10000.0", # String olarak saklamak Decimal dönüşümü için daha iyi olabilir
             "BTC": "0.1"
        }
    },
    "enabled_signal_sources": ["webhook", "tradingview"], # BotCore'un hangi sinyal kaynaklarını dinleyeceği
    "active_strategies": [ # BotCore'un çalıştıracağı dahili stratejiler
        # Örnek:
        # {
        #     "name": "SimpleMovingAverageStrategy",
        #     "symbol": "BTC/USDT",
        #     "params": {"sma_period": 15, "max_history": 100}
        # }
    ]
}

class SettingsDialog(QDialog):
    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kullanıcı Ayarları")
        # Pencere boyutunu biraz daha büyütelim
        self.setGeometry(150, 150, 600, 700) # Daha geniş ve yüksek

        # Ayarları al ve varsayılanlarla birleştir (derin kopya ile)
        # Gelen settings None ise {} kullan, sonra varsayılanlarla birleştir
        current_settings = copy.deepcopy(settings) if settings is not None else {}
        logger.debug(f"Dialog başlatılırken gelen ayarlar: {current_settings}")

        # <<< İyileştirme: Daha sağlam birleştirme >>>
        self.settings = self._merge_settings(current_settings, DEFAULT_SETTINGS)
        logger.debug(f"Varsayılanlarla birleştirilmiş ayarlar: {self.settings}")

        # Ana Layout ve Tab Widget
        self.layout = QVBoxLayout(self)
        self.tab_widget = QTabWidget()

        # --- Sekmeleri Oluştur ---
        # Her sekme oluşturma metodu ilgili ayar bölümünü ('exchange', 'risk' vb.)
        # self.settings içinden okuyarak widget'ları doldurur.
        self._create_exchange_settings_tab()
        self._create_trading_settings_tab()
        self._create_risk_settings_tab()
        self._create_signal_settings_tab()
        self._create_demo_settings_tab()
        # TODO: active_strategies için ayrı bir sekme veya düzenleyici eklenebilir.
        # TODO: enabled_signal_sources için bir sekme/alan eklenebilir (Checkbox listesi?).

        self.layout.addWidget(self.tab_widget)

        # --- Kaydet / İptal Butonları ---
        self.buttonBox = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttonBox.accepted.connect(self.accept) # Save -> accept slot'u tetikler (QDialog'un)
        self.buttonBox.rejected.connect(self.reject) # Cancel -> reject slot'u tetikler (QDialog'un)
        self.layout.addWidget(self.buttonBox)

        self.setLayout(self.layout)

    def _merge_settings(self, current: Dict, default: Dict) -> Dict:
        """ Mevcut ayarları varsayılanlarla özyinelemeli olarak birleştirir. Eksik anahtarları ekler. """
        # Varsayılanların derin kopyasını alarak başla
        merged = copy.deepcopy(default)
        # Mevcut ayarları varsayılanların üzerine yaz/birleştir
        for key, value in current.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                # Eğer hem mevcut hem varsayılan değer sözlükse, iç içe birleştir
                merged[key] = self._merge_settings(value, merged[key])
            else:
                # Değilse veya tipler farklıysa, mevcut değeri doğrudan ata
                # (Tip kontrolü burada yapılmıyor, varsayılan yapı korunuyor)
                merged[key] = value # Mevcut değeri koru veya üzerine yaz
        return merged

    # --- Sekme Oluşturma Metotları ---

    def _create_exchange_settings_tab(self):
        """ Borsa ayarları sekmesini oluşturur. """
        tab = QWidget()
        layout = QFormLayout(tab)
        exchange_settings = self.settings.get('exchange', {}) # İlgili bölümü al

        # Borsa Seçimi
        self.exchange_name_combo = QComboBox()
        supported_exchanges = sorted(["binance", "binanceusdm", "bybit", "okx", "kucoinfutures", "gateio_futures", "mexc", "bitget"]) # Örnek liste
        self.exchange_name_combo.addItems(supported_exchanges)
        current_exchange = exchange_settings.get('name', '').lower()
        if current_exchange in supported_exchanges:
             self.exchange_name_combo.setCurrentText(current_exchange)
        else:
             logger.warning(f"Ayarlardaki borsa '{current_exchange}' desteklenenler listesinde yok, ilk seçenek gösterilecek.")
             if supported_exchanges: self.exchange_name_combo.setCurrentIndex(0)
        layout.addRow("Borsa Adı:", self.exchange_name_combo)

        # API Anahtarı
        self.api_key_input = QLineEdit(str(exchange_settings.get('api_key', ''))) # str() ile None gelirse diye önlem
        self.api_key_input.setPlaceholderText("Borsa API Anahtarınız")
        layout.addRow("API Anahtarı:", self.api_key_input)

        # Gizli Anahtar
        self.secret_key_input = QLineEdit(str(exchange_settings.get('secret_key', '')))
        self.secret_key_input.setEchoMode(QLineEdit.Password) # Şifreli gösterim
        self.secret_key_input.setPlaceholderText("Borsa Gizli Anahtarınız")
        layout.addRow("Gizli Anahtar:", self.secret_key_input)

        # API Şifresi (Passphrase) - Bazı borsalar (örn. KuCoin, OKX) için gerekli
        self.api_password_input = QLineEdit(str(exchange_settings.get('password', '')))
        self.api_password_input.setEchoMode(QLineEdit.Password)
        self.api_password_input.setPlaceholderText("API Şifresi (gerekiyorsa)")
        layout.addRow("API Şifresi (Passphrase):", self.api_password_input)

        layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow) # Alanların genişlemesini sağla
        self.tab_widget.addTab(tab, "Borsa API")


    def _create_trading_settings_tab(self):
        """ Temel alım/satım, SL/TP, TSL, Kaldıraç ve Marjin Modu ayarları. """
        tab = QWidget()
        main_layout = QVBoxLayout(tab) # Dikey ana layout
        trading_settings = self.settings.get('trading', {})

        # --- Genel İşlem Ayarları ---
        trading_groupbox = QGroupBox("Genel İşlem Ayarları")
        trading_layout = QFormLayout(trading_groupbox)

        self.default_order_type_combo = QComboBox()
        self.default_order_type_combo.addItems(["market", "limit"])
        self.default_order_type_combo.setCurrentText(str(trading_settings.get('default_order_type', 'market')))
        trading_layout.addRow("Varsayılan Emir Türü:", self.default_order_type_combo)

        self.default_amount_type_combo = QComboBox()
        self.default_amount_type_combo.addItems(["percentage", "fixed"])
        self.default_amount_type_combo.setCurrentText(str(trading_settings.get('default_amount_type', 'percentage')))
        trading_layout.addRow("Varsayılan Miktar Türü:", self.default_amount_type_combo)

        self.default_amount_value_spinbox = QDoubleSpinBox()
        self.default_amount_value_spinbox.setRange(0.0, 1000000.0); self.default_amount_value_spinbox.setSingleStep(0.1); self.default_amount_value_spinbox.setDecimals(4)
        try: self.default_amount_value_spinbox.setValue(float(trading_settings.get('default_amount_value', 10.0)))
        except (ValueError, TypeError): self.default_amount_value_spinbox.setValue(10.0); logger.warning("Geçersiz default_amount_value, varsayılan kullanıldı.")
        self.default_amount_value_spinbox.setToolTip("Miktar Türü 'percentage' ise bakiye yüzdesi (örn. 10.0), 'fixed' ise USDT (veya quote) cinsinden sabit ana para tutarıdır (örn. 100.0).")
        trading_layout.addRow("Varsayılan Miktar Değeri:", self.default_amount_value_spinbox)

        self.default_leverage_spinbox = QSpinBox()
        self.default_leverage_spinbox.setRange(1, 125); self.default_leverage_spinbox.setSuffix("x")
        try: self.default_leverage_spinbox.setValue(int(trading_settings.get('default_leverage', 5)))
        except (ValueError, TypeError): self.default_leverage_spinbox.setValue(5); logger.warning("Geçersiz default_leverage, varsayılan kullanıldı.")
        self.default_leverage_spinbox.setToolTip("Vadeli işlemlerde kullanılacak varsayılan kaldıraç (1x = kaldıraçsız).")
        trading_layout.addRow("Varsayılan Kaldıraç:", self.default_leverage_spinbox)

        self.default_margin_mode_combo = QComboBox()
        self.default_margin_mode_combo.addItems(["ISOLATED", "CROSSED"])
        current_margin_mode = str(trading_settings.get('default_margin_mode', 'ISOLATED')).upper()
        if current_margin_mode not in ["ISOLATED", "CROSSED"]: current_margin_mode = 'ISOLATED'
        self.default_margin_mode_combo.setCurrentText(current_margin_mode)
        self.default_margin_mode_combo.setToolTip("Vadeli işlemlerde varsayılan marjin modu (Isolated veya Cross).")
        trading_layout.addRow("Varsayılan Marjin Modu:", self.default_margin_mode_combo)

        main_layout.addWidget(trading_groupbox)

        # --- SL/TP Ayarları ---
        sltp_groupbox = QGroupBox("Stop Loss / Take Profit Ayarları (% Giriş Fiyatına Göre)")
        sltp_layout = QFormLayout(sltp_groupbox)

        self.default_sl_percent_spinbox = QDoubleSpinBox(); self.default_sl_percent_spinbox.setRange(0.0, 100.0); self.default_sl_percent_spinbox.setSingleStep(0.1); self.default_sl_percent_spinbox.setDecimals(2); self.default_sl_percent_spinbox.setSuffix(" %")
        try: self.default_sl_percent_spinbox.setValue(float(trading_settings.get('stop_loss_percentage', 2.0)))
        except (ValueError, TypeError): self.default_sl_percent_spinbox.setValue(2.0); logger.warning("Geçersiz stop_loss_percentage, varsayılan kullanıldı.")
        self.default_sl_percent_spinbox.setToolTip("Sinyalde SL belirtilmezse veya 0 ise, giriş fiyatından bu yüzde kadar uzağa SL konulur (0 = Kapalı).")
        sltp_layout.addRow("Varsayılan Stop Loss (%):", self.default_sl_percent_spinbox)

        self.default_tp_percent_spinbox = QDoubleSpinBox(); self.default_tp_percent_spinbox.setRange(0.0, 1000.0); self.default_tp_percent_spinbox.setSingleStep(0.1); self.default_tp_percent_spinbox.setDecimals(2); self.default_tp_percent_spinbox.setSuffix(" %")
        try: self.default_tp_percent_spinbox.setValue(float(trading_settings.get('take_profit_percentage', 4.0)))
        except (ValueError, TypeError): self.default_tp_percent_spinbox.setValue(4.0); logger.warning("Geçersiz take_profit_percentage, varsayılan kullanıldı.")
        self.default_tp_percent_spinbox.setToolTip("Sinyalde TP belirtilmezse veya 0 ise, giriş fiyatından bu yüzde kadar uzağa TP konulur (0 = Kapalı).")
        sltp_layout.addRow("Varsayılan Take Profit (%):", self.default_tp_percent_spinbox)

        main_layout.addWidget(sltp_groupbox)

        # --- İz Süren Stop Ayarları ---
        tsl_groupbox = QGroupBox("İz Süren Stop Loss (Trailing SL - % Giriş Fiyatına Göre)")
        tsl_layout = QFormLayout(tsl_groupbox)

        self.tsl_activation_percent_spinbox = QDoubleSpinBox(); self.tsl_activation_percent_spinbox.setRange(0.0, 1000.0); self.tsl_activation_percent_spinbox.setSingleStep(0.1); self.tsl_activation_percent_spinbox.setDecimals(2); self.tsl_activation_percent_spinbox.setSuffix(" %")
        try: self.tsl_activation_percent_spinbox.setValue(float(trading_settings.get('tsl_activation_percent', 1.5)))
        except (ValueError, TypeError): self.tsl_activation_percent_spinbox.setValue(1.5); logger.warning("Geçersiz tsl_activation_percent, varsayılan kullanıldı.")
        self.tsl_activation_percent_spinbox.setToolTip("Pozisyon bu yüzde kadar kâra geçtiğinde TSL aktifleşir (0 = Kapalı).")
        tsl_layout.addRow("TSL Aktivasyon Kârı (%):", self.tsl_activation_percent_spinbox)

        self.tsl_distance_percent_spinbox = QDoubleSpinBox(); self.tsl_distance_percent_spinbox.setRange(0.0, 100.0); self.tsl_distance_percent_spinbox.setSingleStep(0.1); self.tsl_distance_percent_spinbox.setDecimals(2); self.tsl_distance_percent_spinbox.setSuffix(" %")
        try: self.tsl_distance_percent_spinbox.setValue(float(trading_settings.get('tsl_distance_percent', 0.5)))
        except (ValueError, TypeError): self.tsl_distance_percent_spinbox.setValue(0.5); logger.warning("Geçersiz tsl_distance_percent, varsayılan kullanıldı.")
        self.tsl_distance_percent_spinbox.setToolTip("TSL aktifleştiğinde, stop fiyatı ulaşılan en iyi fiyattan bu yüzde kadar uzakta takip eder (0 = Kapalı).")
        tsl_layout.addRow("TSL Takip Mesafesi (%):", self.tsl_distance_percent_spinbox)

        main_layout.addWidget(tsl_groupbox)
        main_layout.addStretch() # Elemanları yukarı yasla
        self.tab_widget.addTab(tab, "İşlem Ayarları")


    def _create_risk_settings_tab(self):
        """ Risk ayarları sekmesini oluşturur. """
        tab = QWidget()
        layout = QFormLayout(tab)
        risk_settings = self.settings.get('risk', {})

        self.max_positions_spinbox = QSpinBox(); self.max_positions_spinbox.setRange(1, 100)
        try: self.max_positions_spinbox.setValue(int(risk_settings.get('max_open_positions', 5)))
        except (ValueError, TypeError): self.max_positions_spinbox.setValue(5); logger.warning("Geçersiz max_open_positions, varsayılan kullanıldı.")
        layout.addRow("Maks. Açık Pozisyon Sayısı:", self.max_positions_spinbox)

        self.max_risk_percent_spinbox = QDoubleSpinBox(); self.max_risk_percent_spinbox.setRange(0.0, 100.0); self.max_risk_percent_spinbox.setSingleStep(0.1); self.max_risk_percent_spinbox.setDecimals(2); self.max_risk_percent_spinbox.setSuffix(" %")
        try: self.max_risk_percent_spinbox.setValue(float(risk_settings.get('max_risk_per_trade_percent', 2.0)))
        except (ValueError, TypeError): self.max_risk_percent_spinbox.setValue(2.0); logger.warning("Geçersiz max_risk_per_trade_percent, varsayılan kullanıldı.")
        self.max_risk_percent_spinbox.setToolTip("Her işlemde riske edilecek maksimum bakiye yüzdesi. Pozisyon büyüklüğü buna göre hesaplanır.")
        layout.addRow("İşlem Başına Maks. Risk (%):", self.max_risk_percent_spinbox)

        self.max_daily_loss_percent_spinbox = QDoubleSpinBox(); self.max_daily_loss_percent_spinbox.setRange(0.0, 100.0); self.max_daily_loss_percent_spinbox.setSingleStep(0.1); self.max_daily_loss_percent_spinbox.setDecimals(2); self.max_daily_loss_percent_spinbox.setSuffix(" %")
        try: self.max_daily_loss_percent_spinbox.setValue(float(risk_settings.get('max_daily_loss_percent', 10.0)))
        except (ValueError, TypeError): self.max_daily_loss_percent_spinbox.setValue(10.0); logger.warning("Geçersiz max_daily_loss_percent, varsayılan kullanıldı.")
        self.max_daily_loss_percent_spinbox.setToolTip("Günlük toplam zarar bu yüzdeyi aşarsa yeni işlem açılmaz (0 = Kapalı).")
        layout.addRow("Günlük Maks. Zarar Limiti (%):", self.max_daily_loss_percent_spinbox)

        self.tab_widget.addTab(tab, "Risk Yönetimi")


    def _create_signal_settings_tab(self):
        """ Sinyal kaynakları ayarları sekmesini oluşturur. """
        tab = QWidget()
        layout = QFormLayout(tab)
        signal_settings = self.settings.get('signal', {})

        # NOT: Artık hangi sinyal kaynaklarının aktif olacağı 'enabled_signal_sources'
        # anahtarı altında (ana seviyede) bir liste olarak tutulabilir.
        # Buradaki ayarlar sadece ilgili kaynakların detayları içindir.

        # Webhook Güvenlik Anahtarı
        self.webhook_secret_input = QLineEdit(str(signal_settings.get('webhook_secret', '')))
        self.webhook_secret_input.setPlaceholderText("Webhook isteklerini doğrulamak için gizli anahtar (isteğe bağlı)")
        self.webhook_secret_input.setToolTip("Eğer ayarlanırsa, gelen webhook isteğinin JSON gövdesinde veya 'X-Secret-Key' başlığında bu değerin olması gerekir.")
        layout.addRow("Webhook Güvenlik Anahtarı:", self.webhook_secret_input)

        # TradingView Kaynağı (placeholder, özel ayar gerektirmez gibi)
        layout.addRow(QLabel("TradingView Kaynağı:"), QLabel("Webhook veya başka bir yöntemle alınır."))

        # Özel API Ayarları
        api_groupbox = QGroupBox("Özel API Ayarları")
        api_layout = QFormLayout(api_groupbox)
        self.signal_api_url_input = QLineEdit(str(signal_settings.get('api_url', '')))
        self.signal_api_url_input.setPlaceholderText("Örn: http://benim-sinyal-servisim.com/api")
        self.signal_api_url_input.setToolTip("Eğer 'custom_api' gibi bir kaynak etkinse, URL'yi buraya girin.")
        api_layout.addRow("Özel Sinyal API URL:", self.signal_api_url_input)
        layout.addWidget(api_groupbox)

        # Telegram Ayarları
        telegram_groupbox = QGroupBox("Telegram Ayarları")
        telegram_layout = QFormLayout(telegram_groupbox)
        self.telegram_token_input = QLineEdit(str(signal_settings.get('telegram_token', '')))
        self.telegram_token_input.setPlaceholderText("Telegram BotFather'dan alınan token")
        telegram_layout.addRow("Telegram Bot Token:", self.telegram_token_input)
        self.telegram_chat_id_input = QLineEdit(str(signal_settings.get('telegram_chat_id', '')))
        self.telegram_chat_id_input.setPlaceholderText("Sinyallerin gönderileceği Chat ID (veya kullanıcı adı)")
        telegram_layout.addRow("Telegram Chat ID:", self.telegram_chat_id_input)
        layout.addWidget(telegram_groupbox)

        self.tab_widget.addTab(tab, "Sinyal Kaynakları")


    def _create_demo_settings_tab(self):
        """
        Demo modu başlangıç bakiyeleri için sabit alanlar oluşturur.
        Pariteler doğrudan kod içinde tanımlanır.
        """
        tab = QWidget()
        layout = QFormLayout(tab) # Direkt QFormLayout kullanmak daha uygun
        
        # users.json'da tanımladığınız ve arayüzde göstermek istediğiniz pariteler:
        # BU LİSTEYİ users.json'daki PARİTELERİNİZLE EŞLEŞTİRİN!
        self.defined_demo_currencies = ["USDT", "BTC", "ETH", "BNB", "ADA", "SOL", "AVAX", "ETHFI", "XRP", "APT"]
        
        self.demo_balance_spinboxes: Dict[str, QDoubleSpinBox] = {} # Spinbox'ları saklamak için

        logger.debug(f"Demo ayarları sekmesi oluşturuluyor. Tanımlı pariteler: {self.defined_demo_currencies}")
        
        # self.settings içinden demo ayarlarını al
        demo_settings_from_file = self.settings.get('demo_settings', {})
        start_balances_from_file = demo_settings_from_file.get('start_balances', {})

        for currency_code in self.defined_demo_currencies:
            spinbox = QDoubleSpinBox()
            spinbox.setRange(0.0, 1000000000.0) # Çok geniş bir aralık
            spinbox.setDecimals(8) # Çoğu coin için 8 ondalık basamak
            spinbox.setSuffix(f" {currency_code}") # Para birimi etiketini ekle
            spinbox.setObjectName(f"demo_{currency_code.lower()}_balance_spinbox") # Nesneye bir ad verelim

            # Mevcut değeri settings'den yükle veya varsayılan olarak 0.0 kullan
            current_balance_str = start_balances_from_file.get(currency_code, "0.0")
            try:
                # Değeri float'a çevirip spinbox'a ata
                spinbox.setValue(float(str(current_balance_str).replace(',', '.')))
            except (ValueError, TypeError):
                spinbox.setValue(0.0) # Hata durumunda 0 ata
                logger.warning(f"'{currency_code}' için demo bakiye değeri ('{current_balance_str}') yüklenemedi, 0.0 olarak ayarlandı.")
            
            layout.addRow(f"Başlangıç {currency_code} Bakiyesi:", spinbox)
            self.demo_balance_spinboxes[currency_code] = spinbox # Spinbox'ı daha sonra erişmek için sakla

        self.tab_widget.addTab(tab, "Demo Modu Ayarları")
        logger.info(f"Demo Modu Ayarları sekmesi {len(self.defined_demo_currencies)} sabit parite alanı ile oluşturuldu.")


    # --- Ayarları Alma Metodu (Tip Dönüşümleri Eklendi) ---
    def get_settings(self) -> Dict[str, Any]:
        # Mevcut ayarların derin kopyasını alarak başla
        # Bu, widget'ları olmayan diğer ayarların (örn: username, active_strategies) korunmasını sağlar.
        updated_settings = copy.deepcopy(self.settings)
        logger.debug("Ayarlar okunuyor (get_settings)...")

        # --- Exchange Ayarları ---
        if hasattr(self, 'exchange_name_combo'): # Widget var mı kontrol et
            updated_settings["exchange"]["name"] = self.exchange_name_combo.currentText()
            updated_settings["exchange"]["api_key"] = self.api_key_input.text().strip()
            updated_settings["exchange"]["secret_key"] = self.secret_key_input.text().strip()
            updated_settings["exchange"]["password"] = self.api_password_input.text().strip()

        # --- Trading Ayarları ---
        if hasattr(self, 'default_order_type_combo'):
            updated_settings["trading"]["default_order_type"] = self.default_order_type_combo.currentText()
            updated_settings["trading"]["default_amount_type"] = self.default_amount_type_combo.currentText()
            updated_settings["trading"]["default_amount_value"] = self.default_amount_value_spinbox.value()
            updated_settings["trading"]["stop_loss_percentage"] = self.default_sl_percent_spinbox.value()
            updated_settings["trading"]["take_profit_percentage"] = self.default_tp_percent_spinbox.value()
            updated_settings["trading"]["tsl_activation_percent"] = self.tsl_activation_percent_spinbox.value()
            updated_settings["trading"]["tsl_distance_percent"] = self.tsl_distance_percent_spinbox.value()
            updated_settings["trading"]["default_leverage"] = self.default_leverage_spinbox.value()
            updated_settings["trading"]["default_margin_mode"] = self.default_margin_mode_combo.currentText()

        # --- Risk Ayarları ---
        if hasattr(self, 'max_positions_spinbox'):
            updated_settings["risk"]["max_open_positions"] = self.max_positions_spinbox.value()
            updated_settings["risk"]["max_risk_per_trade_percent"] = self.max_risk_percent_spinbox.value()
            updated_settings["risk"]["max_daily_loss_percent"] = self.max_daily_loss_percent_spinbox.value()

        # --- Signal Ayarları ---
        if hasattr(self, 'webhook_secret_input'):
            # 'source' anahtarı varsayılan ayarlarda vardı, widget'ı yoksa bile kalır.
            updated_settings["signal"]["webhook_secret"] = self.webhook_secret_input.text().strip()
            updated_settings["signal"]["api_url"] = self.signal_api_url_input.text().strip()
            updated_settings["signal"]["telegram_token"] = self.telegram_token_input.text().strip()
            updated_settings["signal"]["telegram_chat_id"] = self.telegram_chat_id_input.text().strip()

        # --- Demo Ayarlarını Spinbox'lardan Oku ---
        demo_balances_from_widgets: Dict[str, str] = {}
        if hasattr(self, 'demo_balance_spinboxes'): # Spinbox sözlüğü var mı kontrol et
            for currency_code, spinbox in self.demo_balance_spinboxes.items():
                # Spinbox değeri float döner, JSON için string'e çeviriyoruz.
                # Ondalık basamak sayısını spinbox'ın kendi ayarından alarak formatlıyoruz.
                balance_value_str = f"{spinbox.value():.{spinbox.decimals()}f}"
                demo_balances_from_widgets[currency_code] = balance_value_str
        
        # 'demo_settings' anahtarının varlığından ve tipinden emin ol
        if "demo_settings" not in updated_settings or not isinstance(updated_settings.get("demo_settings"), dict):
             updated_settings["demo_settings"] = {} 
        
        # Sadece widget'lardan okunan bakiyeleri 'start_balances' altına yaz.
        # Eğer `users.json` dosyasında `defined_demo_currencies` listesinde olmayan
        # başka pariteler varsa, onlar bu işlemle silinecektir.
        # Eğer korunmaları isteniyorsa, `start_balances_from_file` ile birleştirme yapılabilir,
        # ama bu, arayüzde görünmeyen paritelerin de dosyada kalmasına neden olur.
        # Şimdilik sadece arayüzde tanımlananları kaydediyoruz.
        updated_settings["demo_settings"]["start_balances"] = demo_balances_from_widgets
        
        logger.debug(f"Demo bakiyeleri spinbox'lardan okundu ve güncellendi: {demo_balances_from_widgets}")
        
        logger.info(f"Ayarlar toplandı, kaydedilecek nihai ayarlar: {updated_settings}")
        return updated_settings


    # QDialog'un accept metodu çağrıldığında (Save butonu) çalışır.
    # İsteğe bağlı olarak burada ek doğrulama yapabiliriz.
    def accept(self):
         logger.info("Ayarlar kaydediliyor (Save butonuna tıklandı)...")
         # TODO: Ek girdi doğrulamaları eklenebilir.
         # Örnek: TSL mesafesi, aktivasyondan küçük mü?
         tsl_act = self.tsl_activation_percent_spinbox.value()
         tsl_dist = self.tsl_distance_percent_spinbox.value()
         if tsl_act > 0 and tsl_dist >= tsl_act:
              QMessageBox.warning(self, "Geçersiz TSL Ayarı",
                                  f"TSL Takip Mesafesi ({tsl_dist}%) Aktivasyon Kârından ({tsl_act}%) küçük olmalıdır.")
              # Kaydetmeyi iptal etmek için accept() yerine reject() çağrılabilir veya sadece return
              return # Kaydetme, kullanıcı düzeltene kadar

         # Doğrulama başarılıysa, QDialog'un normal accept işlemini yapmasına izin ver.
         super().accept()


# Test bloğu
if __name__ == '__main__':
    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv)

    # Test için basit logger
    if 'setup_logger' not in globals():
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        logger = logging.getLogger('settings_dialog_test')

    # Örnek mevcut ayarlar (bazı alanlar eksik veya farklı tipte olabilir)
    existing_settings = {
        "username":"testuser", # Bu normalde burada olmaz, user_config'dedir
        "exchange": {"name": "bybit", "api_key": "123", "password":"abc"}, # secret eksik
        "trading": {"default_leverage": "20", "stop_loss_percentage": "1.5"} # Tipler string
    }

    settings_dialog = SettingsDialog(settings=existing_settings)
    if settings_dialog.exec_(): # Kullanıcı Save'e bastıysa
        saved_settings = settings_dialog.get_settings()
        print("\nKaydedilen Ayarlar:")
        # JSON olarak güzel formatta yazdır
        print(json.dumps(saved_settings, indent=4))

        # Tip kontrolleri (örnek)
        print("\nTip Kontrolleri:")
        print(f"- Leverage Tipi: {type(saved_settings['trading']['default_leverage'])}")
        print(f"- SL % Tipi: {type(saved_settings['trading']['stop_loss_percentage'])}")
        print(f"- Max Pozisyon Tipi: {type(saved_settings['risk']['max_open_positions'])}")
        print(f"- Borsa Adı Tipi: {type(saved_settings['exchange']['name'])}")
        print(f"- Demo USDT Tipi: {type(saved_settings['demo_settings']['start_balances']['USDT'])}")

        assert isinstance(saved_settings['trading']['default_leverage'], int)
        assert isinstance(saved_settings['trading']['stop_loss_percentage'], float)
        assert isinstance(saved_settings['risk']['max_open_positions'], int)
        assert isinstance(saved_settings['exchange']['name'], str)
        assert isinstance(saved_settings['demo_settings']['start_balances']['USDT'], str) # Demo string saklanıyor

    else:
        print("\nAyarlar iptal edildi.")

    # sys.exit(app.exec_()) # Dialog kapanınca uygulama biter