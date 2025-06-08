# core/bot_core.py

import logging
import time
import threading
import queue
import copy
from decimal import Decimal, ROUND_DOWN, InvalidOperation, ROUND_HALF_UP # ROUND_HALF_UP eklendi, kullanılmıyorsa kaldırılabilir
from datetime import datetime, timezone

from typing import Union, Optional, List, Dict, Any

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot, QThreadPool, QRunnable

try:
    from core.logger import setup_logger # DÜZELTİLDİ
    logger = setup_logger('bot_core')    # DÜZELTİLDİ
except ImportError:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('bot_core_fallback')
    logger.warning("core.logger modülü bulunamadı, temel fallback logger kullanılıyor.")

# Diğer core bileşenlerini import et
from core.exchange_api import ExchangeAPI
from core.risk_manager import RiskManager
from core.signal_handler import SignalHandler
from core.trade_manager import TradeManager # TradeManager importu burada
from core.database_manager import DatabaseManager
from config.config_manager import ConfigManager
from config.user_config_manager import UserConfigManager
import utils # utils.py import ediliyor

try:
    from strategies.base_strategy import BaseStrategy
    from strategies.simple_moving_average_strategy import SimpleMovingAverageStrategy # Örnek strateji
    STRATEGIES_AVAILABLE = True
except ImportError as e:
    logger.error(f"Strateji modülleri import edilemedi: {e}. Dahili stratejiler kullanılamayacak.")
    BaseStrategy = None
    SimpleMovingAverageStrategy = None
    STRATEGIES_AVAILABLE = False

# utils modülünden Decimal sabitlerini alalım (eğer import edilemezse fallback)
# Bu global sabitler BotCore içinde de kullanılabilir.
try:
    # utils'den gerekli sabitleri ve fonksiyonları import et (opsiyonel)
    # Eğer BotCore içinde spesifik olarak kullanılmıyorsa bu blok kaldırılabilir.
    from utils import DECIMAL_ZERO as UTILS_DECIMAL_ZERO # Çakışmayı önlemek için farklı isim
    DECIMAL_ZERO = UTILS_DECIMAL_ZERO # Sonra tekrar ata
    logger.debug(f"BotCore için utils.DECIMAL_ZERO ({DECIMAL_ZERO}) başarıyla import edildi.")
except ImportError:
    logger.warning("BotCore: utils modülünden DECIMAL_ZERO import edilemedi. Yerel fallback kullanılacak.")
    DECIMAL_ZERO = Decimal('0') # Fallback tanımı


class BotCore(QObject):
    status_changed_signal = pyqtSignal(str)
    positions_updated_signal = pyqtSignal(list)
    log_signal = pyqtSignal(str, str) # GUI'ye log göndermek için (mesaj, seviye)
    history_trades_updated_signal = pyqtSignal(list) # Geçmiş işlemler güncellendiğinde
    report_data_ready_signal = pyqtSignal(str) # Rapor verisi hazır olduğunda

    from PyQt5.QtCore import pyqtSlot
    from typing import Optional
    
    def __init__(self, config_manager: ConfigManager, user_manager: UserConfigManager, database_manager: DatabaseManager):
        super().__init__()
        logger.info("BotCore başlatılıyor...")

        if not isinstance(config_manager, ConfigManager):
            logger.critical("Geçerli bir ConfigManager örneği gereklidir.")
            raise TypeError("Geçerli bir ConfigManager örneği gereklidir.")
        if not isinstance(user_manager, UserConfigManager):
            logger.critical("Geçerli bir UserConfigManager örneği gereklidir.")
            raise TypeError("Geçerli bir UserConfigManager örneği gereklidir.")

        # DatabaseManager None olabilir (opsiyonel)
        if database_manager is not None and not isinstance(database_manager, DatabaseManager):
            logger.warning("DatabaseManager örneği sağlandı ancak geçerli bir DatabaseManager örneği değil. Veritabanı işlemleri yapılamayacak.")
            self.db_manager = None
        else:
            self.db_manager = database_manager

        self.config_manager = config_manager
        self.user_manager = user_manager

        self._is_running = False
        self._stop_event = threading.Event() # Bot döngüsünü durdurmak için
        self._bot_thread: Optional[threading.Thread] = None # Botun ana çalışma thread'i

        self.active_user: Union[str, None] = None # Aktif kullanıcı adı
        self.current_mode: str = 'real' # 'real' veya 'demo'

        # Bileşenlerin referansları
        self.exchange_api: Union[ExchangeAPI, None] = None # Aktif olan (gerçek veya demo)
        self.real_exchange_api: Union[ExchangeAPI, None] = None # Her zaman gerçek API'yi tutar (demo için fiyat almak üzere)
        self.risk_manager: Union[RiskManager, None] = None
        self.signal_handler: Union[SignalHandler, None] = None
        self.trade_manager: Union[TradeManager, None] = None # TradeManager burada tanımlı

        # Strateji yönetimi
        self.active_strategies: Dict[str, BaseStrategy] = {} # Sembol -> Strateji örneği
        self.external_signal_queue: queue.Queue = queue.Queue() # Webhook'tan gelen sinyaller için

        # Demo modu için sanal bakiye (BotCore tarafından yönetilir)
        self.virtual_balances: Dict[str, Decimal] = {} # currency -> Decimal(balance)
        self._balance_lock: threading.Lock = threading.Lock() # Sanal bakiye için kilit

        logger.info("BotCore nesnesi başarıyla oluşturuldu.")
    @pyqtSlot(str, str)  # Dashboard'dan order_id ve symbol geldiğini varsayıyoruz
    def handle_manual_close_position(self, order_id: str, symbol: Optional[str] = None, reason: str = "Arayüzden Manuel Kapatma"):
        """
        Kullanıcı arayüzünden (veya başka bir manuel kaynaktan) gelen
        pozisyon kapatma isteğini işler.
        İlgili pozisyonu TradeManager aracılığıyla kapatmaya çalışır.
        """
        # Aktif kullanıcıyı al veya varsayılan bir değer ata
        active_user_for_log = self.active_user if hasattr(self, 'active_user') and self.active_user else "BilinmeyenKullanıcı"
        
        logger.info(f"[{active_user_for_log}] Manuel pozisyon kapatma isteği alındı: Order ID='{order_id}', Sembol='{symbol or 'Belirtilmedi'}', Sebep='{reason}'")

        if not self._is_running: # Bot çalışıyor mu kontrolü
            msg = f"[{active_user_for_log}] Bot çalışmıyorken manuel pozisyon kapatılamaz (ID: {order_id})."
            logger.warning(msg)
            if hasattr(self, 'log_signal'): # log_signal var mı kontrol et
                self.log_signal.emit(msg, "WARNING")
            return

        if not self.trade_manager: # TradeManager var mı kontrolü
            msg = f"[{active_user_for_log}] TradeManager başlatılmamış. Pozisyon kapatılamıyor (ID: {order_id})."
            logger.error(msg)
            if hasattr(self, 'log_signal'):
                self.log_signal.emit(msg, "ERROR")
            return

        # TradeManager'da pozisyonu ID ile kapatacak metodu çağır
        # trade_manager.py dosyanızda 'close_position_by_id' metodu olduğunu gördük.
        if hasattr(self.trade_manager, 'close_position_by_id') and callable(getattr(self.trade_manager, 'close_position_by_id')):
            try:
                logger.info(f"[{active_user_for_log}] '{order_id}' ID'li pozisyon için TradeManager.close_position_by_id çağrılıyor...")
                
                # TradeManager'daki close_position_by_id metodunun bool döndürdüğünü varsayıyoruz.
                # Eğer tuple (success, message) döndürüyorsa, ona göre ayarlayın.
                # success, tm_message = self.trade_manager.close_position_by_id(order_id, reason=reason)
                success = self.trade_manager.close_position_by_id(order_id, reason=reason) # trade_manager.py'deki metod bool döndürüyor gibi

                # tm_message'ı kendimiz oluşturalım veya TradeManager'dan alalım
                tm_message = "İşlem TradeManager'a iletildi." # Varsayılan mesaj

                if success:
                    log_msg = f"[{active_user_for_log}] Pozisyon (ID: {order_id}) için kapatma emri başarıyla işlendi/iletildi. Mesaj: {tm_message}"
                    logger.info(log_msg)
                    if hasattr(self, 'log_signal'):
                        self.log_signal.emit(log_msg, "INFO")
                else:
                    # TradeManager.close_position_by_id false döndürdüğünde genellikle kendi içinde loglama yapar.
                    # Buradaki log, BotCore seviyesinde bir ek bilgi olur.
                    log_msg = f"[{active_user_for_log}] Pozisyon (ID: {order_id}) TradeManager tarafından kapatılamadı. Detaylar için TradeManager loglarına bakın. Mesaj: {tm_message}"
                    logger.error(log_msg)
                    if hasattr(self, 'log_signal'):
                        self.log_signal.emit(log_msg, "ERROR")

            except Exception as e_tm_call:
                err_msg = f"[{active_user_for_log}] TradeManager.close_position_by_id çağrılırken hata oluştu (ID: {order_id}): {e_tm_call}"
                logger.error(err_msg, exc_info=True)
                if hasattr(self, 'log_signal'):
                    self.log_signal.emit(err_msg, "ERROR")
        else:
            msg = f"[{active_user_for_log}] TradeManager'da 'close_position_by_id' adında pozisyon kapatma metodu bulunamadı."
            logger.error(msg)
            if hasattr(self, 'log_signal'):
                self.log_signal.emit(msg, "ERROR")

    @pyqtSlot(str, str, str) # String parametreler için dekoratör güncellenmişti
    def fetch_and_send_historical_trades(self, user: Optional[str] = None, start_ms_str: Optional[str] = None, end_ms_str: Optional[str] = None, limit: Optional[int] = None):
        # ADIM 1: String parametrelerini integer'a çevir
        start_ms: Optional[int] = None # Değişkenleri burada None olarak başlat
        end_ms: Optional[int] = None   # Değişkenleri burada None olarak başlat
        try:
            if start_ms_str is not None: # Gelen string None değilse çevirmeyi dene
                start_ms = int(start_ms_str)
            if end_ms_str is not None:   # Gelen string None değilse çevirmeyi dene
                end_ms = int(end_ms_str)
        except (ValueError, TypeError) as e:
            # logger.error(f"fetch_and_send_historical_trades: Zaman damgası string'den int'e çevrilirken hata: {e}. start_ms_str='{start_ms_str}', end_ms_str='{end_ms_str}'")
            if hasattr(self, 'history_trades_updated_signal'): # Sinyalin varlığını kontrol et
                self.history_trades_updated_signal.emit([]) # Hata durumunda boş liste gönder
            return # Fonksiyondan çık

        # ADIM 2: Çevrilmiş integer ve orijinal string değerlerle logla
        logger.critical(f"BotCore RECEIVED fetch_and_send_historical_trades: User='{user}' (tip {type(user)}), Start_ms={start_ms} (original_str='{start_ms_str}'), End_ms={end_ms} (original_str='{end_ms_str}'), Limit={limit} (tip {type(limit)})")

        # Eski debug logunuz (örn: "[fetch_and_send_historical_trades GİRDİ]...") varsa
        # ve yukarıdaki logger.critical satırı aynı bilgiyi veriyorsa, eski debug logunu silebilirsiniz.
        # Örnek:
        # logger.debug(f"[fetch_and_send_historical_trades GİRDİ] user: {user} (tip: {type(user)}), start_ms: {start_ms} (tip: {type(start_ms)}), end_ms: {end_ms} (tip: {type(end_ms)}), limit: {limit}")

        # ---- Fonksiyonun geri kalanı buradan itibaren devam eder ----
        # Bu kısım sizin orijinal kodunuzdaki gibi kalmalı, sadece start_ms ve end_ms'in
        # artık integer değerler içerdiğinden emin olun.

        # print(f"[DEBUG][BotCore] fetch_and_send_historical_trades ÇAĞRILDI.") # Bu tür print'ler yerine logger kullanmak daha iyidir.
        # print(f"    Gelen 'user' parametresi: '{user}' (tipi: {type(user)})")
        # print(f"    O anki self._is_running durumu: {self._is_running}")
        # print(f"    O anki self.active_user durumu: '{self.active_user}' (tipi: {type(self.active_user)})")
        
        logger.debug(f"fetch_and_send_historical_trades (iç işlem): Gelen user='{user}', BotÇalışıyor={self._is_running}, AktifKullanıcı='{self.active_user}'")

        if not self.db_manager:
            logger.error("Geçmiş işlemler çekilemiyor: DatabaseManager mevcut değil.")
            self.history_trades_updated_signal.emit([])
            return

        current_user_for_query = None
        # Kullanıcı belirleme mantığı (sizin kodunuzdaki gibi)
        if user is not None and isinstance(user, str) and user.strip() and user != "<Kullanıcı Yok>":
            current_user_for_query = user.strip()
            logger.debug(f"Geçmiş işlemler için parametreden gelen kullanıcı kullanılacak: '{current_user_for_query}'")
        elif self.active_user and isinstance(self.active_user, str) and self.active_user.strip():
            current_user_for_query = self.active_user
            logger.debug(f"Geçmiş işlemler için aktif bot kullanıcısı kullanılacak: '{current_user_for_query}' (Bot durumu: {'Çalışıyor' if self._is_running else 'Durmuş'})")
        else:
            logger.warning("Geçmiş işlemler için kullanıcı belirlenemedi: Ne parametreden geçerli bir kullanıcı geldi ne de botun aktif bir kullanıcısı var.")
            self.history_trades_updated_signal.emit([])
            return
            
        logger.debug(f"fetch_and_send_historical_trades: current_user_for_query='{current_user_for_query}' olarak belirlendi.")

        query_limit = limit # Fonksiyona gelen limit parametresini kullan
        if query_limit is None: # Eğer limit None olarak geldiyse (lambda'dan dolayı hep None gelecek) config'den al
            try:
                trade_limit_raw = self.config_manager.get_setting('gui_settings', 'historical_trades_limit', default=1000)
                query_limit = int(str(trade_limit_raw).strip())
                if query_limit < 0: query_limit = 1000
            except Exception: # Hata durumunda genel bir except bloğu
                logger.error("Geçmiş işlem limiti ayarı okunurken hata. Varsayılan 1000 kullanılacak.")
                query_limit = 1000
        
        logger.info(f"Geçmiş işlem sorgusu için DbQueryWorker başlatılıyor: User='{current_user_for_query}', Limit={query_limit}, Start={start_ms}, End={end_ms}")
        worker = self.DbQueryWorker(
            bot_core_ref=self,
            query_type='history',
            username=current_user_for_query,
            start_ms=start_ms, # integer olan start_ms
            end_ms=end_ms,    # integer olan end_ms
            # Eğer DbQueryWorker __init__ metodunuz limit alıyorsa, buraya query_limit'i ekleyin.
            # Örneğin: limit=query_limit
            # Eğer almıyorsa, DbQueryWorker içindeki limit mantığı kullanılır.
            # Şimdiki varsayım, DbQueryWorker'ın kendi içinde limiti config'den okuduğu yönünde.
        )
        QThreadPool.globalInstance().start(worker)

    @pyqtSlot(str, str, str)
    def fetch_and_send_report_data(self, user: Optional[str] = None, start_ms_str: Optional[str] = None, end_ms_str: Optional[str] = None):
        print(f"DEBUG PRINT - fetch_and_send_report_data - start_ms_str: {start_ms_str}, type: {type(start_ms_str)}") # GEÇİCİ DEBUG
        logger.info(f"DEBUG LOGGER - fetch_and_send_report_data - start_ms_str: {start_ms_str}, type: {type(start_ms_str)}") # GEÇİCİ DEBUG

        # ADIM 1: String parametrelerini integer'a çevir
        start_ms: Optional[int] = None
        end_ms: Optional[int] = None   # Değişkenleri burada None olarak başlat
        try:
            if start_ms_str is not None: # Gelen string None değilse çevirmeyi dene
                start_ms = int(start_ms_str)
            if end_ms_str is not None:   # Gelen string None değilse çevirmeyi dene
                end_ms = int(end_ms_str)
        except (ValueError, TypeError) as e:
            logger.error(f"fetch_and_send_report_data: Zaman damgası string'den int'e çevrilirken hata: {e}. start_ms_str='{start_ms_str}', end_ms_str='{end_ms_str}'")
            if hasattr(self, 'report_data_ready_signal'): # Sinyalin varlığını kontrol et
                self.report_data_ready_signal.emit("Rapor oluşturulamıyor: Geçersiz tarih formatı alındı.")
            return # Fonksiyondan çık

        # ADIM 2: Çevrilmiş integer ve orijinal string değerlerle logla
        logger.critical(f"BotCore RECEIVED fetch_and_send_report_data: User='{user}' (tip {type(user)}), Start_ms={start_ms} (original_str='{start_ms_str}'), End_ms={end_ms} (original_str='{end_ms_str}')")

        # Eski debug logunuz (örn: "[fetch_and_send_report_data GİRDİ]...") varsa
        # ve yukarıdaki logger.critical satırı aynı bilgiyi veriyorsa, eski debug logunu silebilirsiniz.
        # Örnek:
        # logger.debug(f"[fetch_and_send_report_data GİRDİ] user: {user} (tip: {type(user)}), start_ms: {start_ms} (tip: {type(start_ms)}), end_ms: {end_ms} (tip: {type(end_ms)})")

        # ---- Fonksiyonun geri kalanı buradan itibaren devam eder ----
        # Bu kısım sizin orijinal kodunuzdaki gibi kalmalı, sadece start_ms ve end_ms'in
        # artık integer değerler içerdiğinden emin olun.

        if not self.db_manager:
            logger.error("Rapor verisi çekilemiyor: DatabaseManager mevcut değil.")
            self.report_data_ready_signal.emit("Rapor oluşturulamıyor: Veritabanı bağlantısı yok.")
            return

        # Kullanıcı belirleme mantığı (sizin kodunuzdaki gibi)
        current_user_for_query = None
        if user is not None and isinstance(user, str) and user.strip() and user != "<Kullanıcı Yok>":
            current_user_for_query = user.strip()
        elif self.active_user and isinstance(self.active_user, str) and self.active_user.strip(): # Bot çalışıyorsa ve aktif kullanıcı varsa
             current_user_for_query = self.active_user
        # else: current_user_for_query None kalır

        if not current_user_for_query:
            logger.warning("Rapor için kullanıcı belirlenemedi.")
            self.report_data_ready_signal.emit("Rapor oluşturulamıyor: Kullanıcı seçilmemiş.")
            return

        logger.info(f"Rapor sorgusu için DbQueryWorker başlatılıyor: User='{current_user_for_query}', Start={start_ms}, End={end_ms}")
        worker = self.DbQueryWorker(
            bot_core_ref=self,
            query_type='report',
            username=current_user_for_query,
            start_ms=start_ms, # integer olan start_ms
            end_ms=end_ms      # integer olan end_ms
        )
        QThreadPool.globalInstance().start(worker)

    def _generate_report_text(self, username: str, trades_data: list, start_ms: Optional[int], end_ms: Optional[int]) -> str:
        # Metodun en başına gelen parametreleri loglayalım
        logger.debug(f"_generate_report_text çağrıldı. Kullanıcı: '{username}', Gelen start_ms: {start_ms} (tip: {type(start_ms)}), Gelen end_ms: {end_ms} (tip: {type(end_ms)})")
        logger.debug(f"Rapor için alınan işlem sayısı: {len(trades_data)}")

        # Tarihleri formatlamak için yardımcı bir iç fonksiyon (tekrarlanan kodu azaltmak için)
        # Bu _format_report_timestamp fonksiyonu sizin gönderdiğiniz kodda vardı ve doğru görünüyor.
        def _format_report_timestamp(ts_ms: Optional[int], ts_name: str) -> str:
            if ts_ms is None:
                return "Belirtilmemiş"
            try:
                numeric_ts_ms = int(ts_ms)
                if numeric_ts_ms < 0:
                    logger.warning(f"Rapor: Geçersiz (negatif) {ts_name} değeri: {numeric_ts_ms}. 'Geçersiz Tarih' olarak ayarlanacak.")
                    return "Geçersiz Tarih"
                timestamp_sec = numeric_ts_ms / 1000.0
                return datetime.fromtimestamp(timestamp_sec).strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                logger.error(f"Rapor: {ts_name} ({ts_ms}) sayıya çevrilemedi. 'Hatalı Değer' olarak ayarlanacak.")
                return "Hatalı Değer"
            except TypeError:
                 logger.error(f"Rapor: {ts_name} ({ts_ms}) için geçersiz tip. 'Tip Hatası' olarak ayarlanacak.")
                 return "Tip Hatası"
            except OSError as e:
                logger.error(f"Rapor: {ts_name} ({ts_ms}) ile tarih formatlarken OSError: {e}. 'OS Zaman Hatası' olarak ayarlanacak.")
                return "OS Zaman Hatası"
            except Exception as e:
                logger.error(f"Rapor: {ts_name} ({ts_ms}) ile tarih formatlarken genel hata: {e}. 'Format Hatası' olarak ayarlanacak.")
                return "Format Hatası"

        # İşlem yoksa özel mesaj döndür
        if not trades_data:
            formatted_start_date = _format_report_timestamp(start_ms, "start_ms")
            formatted_end_date = _format_report_timestamp(end_ms, "end_ms")
            no_trades_message = (f"{username} için belirtilen kriterlerde "
                                 f"(Başlangıç: {formatted_start_date}, Bitiş: {formatted_end_date}) "
                                 f"gösterilecek işlem bulunamadı.")
            logger.info(no_trades_message)
            return no_trades_message

        # Rapor başlıkları
        report_lines = [f"--- {username} için İşlem Raporu ---"]
        formatted_start_date_main = _format_report_timestamp(start_ms, "start_ms (rapor ana başlığı)")
        formatted_end_date_main = _format_report_timestamp(end_ms, "end_ms (rapor ana başlığı)")
        report_lines.append(f"Tarih Aralığı: {formatted_start_date_main} - {formatted_end_date_main}")
        report_lines.append(f"Toplam İşlem Sayısı: {len(trades_data)}")

        # total_net_pnl ve total_fees'in utils.py'deki DECIMAL_ZERO ile başlatıldığını varsayıyorum.
        # Eğer utils.py'yi import etmediyseniz veya DECIMAL_ZERO orada tanımlı değilse,
        # from decimal import Decimal yaparak Decimal('0.0') kullanın.
        # Kodunuzda Decimal('0.0') olarak tanımlanmış, bu doğru.
        total_net_pnl = Decimal('0.0')
        total_fees = Decimal('0.0')
        winning_trades = 0
        losing_trades = 0

        for trade in trades_data:
            pnl_val = trade.get('net_pnl')
            fee_val = trade.get('fee')

            if pnl_val is not None:
                try:
                    current_pnl_dec = Decimal(str(pnl_val))
                    total_net_pnl += current_pnl_dec
                    if current_pnl_dec > Decimal('0'): # Decimal ile karşılaştır
                        winning_trades += 1
                    elif current_pnl_dec < Decimal('0'): # Decimal ile karşılaştır
                        losing_trades += 1
                except InvalidOperation:
                    logger.warning(f"Raporda geçersiz net_pnl değeri (işlem ID: {trade.get('order_id', 'N/A')}): {pnl_val}")

            if fee_val is not None:
                try:
                    total_fees += Decimal(str(fee_val))
                except InvalidOperation:
                    logger.warning(f"Raporda geçersiz fee değeri (işlem ID: {trade.get('order_id', 'N/A')}): {fee_val}")

        # <<<<<<<<<<<<<< DEĞİŞİKLİK BURADA BAŞLIYOR >>>>>>>>>>>>>>>
        # Toplam PNL ve Komisyonları formatlayarak rapor satırlarına ekle
        pnl_str: str
        if utils and hasattr(utils, 'format_decimal_auto'):
            pnl_str = utils.format_decimal_auto(total_net_pnl, decimals=4, sign=True)
        else:
            if isinstance(total_net_pnl, Decimal):
                pnl_str = f"{total_net_pnl:+.4f}" # +/- işareti ve 4 ondalık
            else:
                logger.warning(f"total_net_pnl beklenmedik bir tipte: {type(total_net_pnl)}. Varsayılan gösterim kullanılacak: {total_net_pnl}")
                pnl_str = str(total_net_pnl)
        report_lines.append(f"Toplam Net PNL: {pnl_str}")

        fees_str: str
        if utils and hasattr(utils, 'format_decimal_auto'):
            fees_str = utils.format_decimal_auto(total_fees, decimals=8)
        else:
            if isinstance(total_fees, Decimal):
                fees_str = f"{total_fees:.8f}" # 8 ondalık
            else:
                logger.warning(f"total_fees beklenmedik bir tipte: {type(total_fees)}. Varsayılan gösterim kullanılacak: {total_fees}")
                fees_str = str(total_fees)
        report_lines.append(f"Toplam Ödenen Komisyon: {fees_str}")
        # <<<<<<<<<<<<<< DEĞİŞİKLİK BURADA BİTİYOR >>>>>>>>>>>>>>>

        report_lines.append(f"Kazanan İşlem Sayısı: {winning_trades}")
        report_lines.append(f"Kaybeden İşlem Sayısı: {losing_trades}")
        if len(trades_data) > 0:
            # Kazanma oranını Decimal kullanarak hesapla
            try:
                win_rate = (Decimal(winning_trades) / Decimal(len(trades_data))) * Decimal('100') if winning_trades > 0 else Decimal('0')
                report_lines.append(f"Kazanma Oranı: {win_rate:.2f}%")
            except ZeroDivisionError: # len(trades_data) 0 ise (bu if ile engellenmiş olmalı ama garanti)
                report_lines.append(f"Kazanma Oranı: N/A")

        report_lines.append("-" * 40)

        for i, trade in enumerate(trades_data):
            report_lines.append(f"İşlem {i+1}: ID={trade.get('order_id', 'N/A')}")
            report_lines.append(f"  Sembol: {trade.get('symbol', 'N/A')}, Yön: {str(trade.get('side', 'N/A')).upper()}")

            entry_price_val = trade.get('entry_price')
            exit_price_val = trade.get('exit_price')
            amount_val = trade.get('amount')
            net_pnl_trade_val = trade.get('net_pnl')
            fee_trade_val = trade.get('fee')

            # <<<<<<<<<<<<<< DEĞİŞİKLİK BURADA BAŞLIYOR >>>>>>>>>>>>>>>
            entry_price_str: str
            if utils and hasattr(utils, 'format_decimal_auto') and entry_price_val is not None:
                entry_price_str = utils.format_decimal_auto(entry_price_val, decimals=8)
            else: # utils yoksa veya değer None ise
                entry_price_str = str(entry_price_val or 'N/A')

            exit_price_str: str
            if utils and hasattr(utils, 'format_decimal_auto') and exit_price_val is not None:
                exit_price_str = utils.format_decimal_auto(exit_price_val, decimals=8)
            else:
                exit_price_str = str(exit_price_val or 'N/A')

            amount_str: str
            if utils and hasattr(utils, 'format_decimal_auto') and amount_val is not None:
                amount_str = utils.format_decimal_auto(amount_val, decimals=8)
            else:
                amount_str = str(amount_val or 'N/A')
            # <<<<<<<<<<<<<< DEĞİŞİKLİK BURADA BİTİYOR >>>>>>>>>>>>>>>
            report_lines.append(f"  Giriş Fyt: {entry_price_str}, Çıkış Fyt: {exit_price_str}, Miktar: {amount_str}")

            # <<<<<<<<<<<<<< DEĞİŞİKLİK BURADA BAŞLIYOR >>>>>>>>>>>>>>>
            net_pnl_trade_str: str
            if utils and hasattr(utils, 'format_decimal_auto') and net_pnl_trade_val is not None:
                net_pnl_trade_str = utils.format_decimal_auto(net_pnl_trade_val, decimals=4, sign=True)
            elif net_pnl_trade_val is not None: # utils yok ama değer var
                 try:
                    net_pnl_trade_str = f"{Decimal(str(net_pnl_trade_val)):+.4f}" # Decimal'e çevirip formatla
                 except InvalidOperation:
                    logger.warning(f"İşlem PNL'i ({net_pnl_trade_val}) Decimal'e çevrilemedi. Olduğu gibi yazdırılıyor.")
                    net_pnl_trade_str = str(net_pnl_trade_val)
            else: # Değer None ise
                net_pnl_trade_str = "N/A"

            fee_trade_str: str
            if utils and hasattr(utils, 'format_decimal_auto') and fee_trade_val is not None:
                fee_trade_str = utils.format_decimal_auto(fee_trade_val, decimals=8)
            else:
                fee_trade_str = str(fee_trade_val or 'N/A')
            # <<<<<<<<<<<<<< DEĞİŞİKLİK BURADA BİTİYOR >>>>>>>>>>>>>>>
            report_lines.append(f"  Net PNL: {net_pnl_trade_str}, Komisyon: {fee_trade_str}")

            open_ts_trade = trade.get('open_timestamp')
            close_ts_trade = trade.get('close_timestamp')

            open_dt_trade = _format_report_timestamp(open_ts_trade, f"işlem {i+1} open_timestamp")
            close_dt_trade = _format_report_timestamp(close_ts_trade, f"işlem {i+1} close_timestamp")

            report_lines.append(f"  Açılış: {open_dt_trade}, Kapanış: {close_dt_trade}")
            report_lines.append(f"  Kapanış Nedeni: {trade.get('close_reason', 'N/A')}")
            report_lines.append("-" * 20)

        logger.info(f"Rapor metni '{username}' için başarıyla oluşturuldu.")
        return "\n".join(report_lines)


    
    def start(self, username: str, mode='real') -> bool:
        """ Botu belirtilen kullanıcı ve mod için başlatır. """
        if self._is_running:
            logger.warning(f"Bot zaten '{self.active_user}' ({self.current_mode}) için çalışıyor. Yeniden başlatma işlemi yapılmayacak.")
            self.status_changed_signal.emit(f"Çalışıyor ({self.active_user} - {self.current_mode.capitalize()})")
            return False

        if not isinstance(username, str) or not username.strip():
            msg = f"Geçersiz kullanıcı adı belirtildi: '{username}'."
            logger.error(msg)
            self.status_changed_signal.emit(f"Hata: {msg}")
            return False
        if not isinstance(mode, str) or mode.lower() not in ['real', 'demo']:
            msg = f"Geçersiz mod belirtildi: '{mode}'. 'real' veya 'demo' olmalı."
            logger.error(msg)
            self.status_changed_signal.emit(f"Hata: {msg}")
            return False

        logger.info(f"Bot '{username}' için '{mode.lower()}' modunda başlatılıyor...")
        self.log_signal.emit(f"'{username}' için '{mode.lower()}' modunda başlatılıyor...", "INFO")
        self.status_changed_signal.emit(f"Başlatılıyor ({username} - {mode.capitalize()})...")

        self.active_user = username
        self.current_mode = mode.lower()
        self._stop_event.clear()

        user_settings = self.user_manager.get_user(username)
        if not user_settings or not isinstance(user_settings, dict):
            msg = f"Kullanıcı '{username}' için ayarlar bulunamadı veya geçerli formatta değil. Bot başlatılamıyor."
            logger.critical(msg)
            self.status_changed_signal.emit(f"Hata: {msg}")
            return False

        exchange_settings = user_settings.get('exchange', {})
        trading_settings = user_settings.get('trading', {})
        api_key = exchange_settings.get('api_key')
        secret_key = exchange_settings.get('secret_key')
        api_password = exchange_settings.get('password')
        exchange_id = exchange_settings.get('name', 'binanceusdm')

        logger.debug(f"BotCore.start: Kullanıcı '{username}' için API Key var mı? {'Evet' if api_key else 'Hayır'}, Secret Key var mı? {'Evet' if secret_key else 'Hayır'}")
        logger.debug(f"BotCore.start: Exchange ID: {exchange_id}")

        if self.current_mode == 'real':
            if not isinstance(api_key, str) or not api_key.strip() or \
               not isinstance(secret_key, str) or not secret_key.strip():
                msg = f"Gerçek mod için API anahtarı ve/veya gizli anahtar eksik veya geçersiz ({username}). Lütfen ayarları kontrol edin."
                logger.critical(msg)
                self.log_signal.emit(msg, "ERROR")
                self.status_changed_signal.emit(f"Hata: API eksik ({username})")
                return False
            else:
                api_key = api_key.strip()
                secret_key = secret_key.strip()
        elif self.current_mode == 'demo':
            pass
        else:
            msg = f"Geçersiz mod belirlendi: '{self.current_mode}'. 'real' veya 'demo' olmalı."
            logger.critical(msg)
            self.log_signal.emit(msg, "CRITICAL")
            self.status_changed_signal.emit(f"Hata: Geçersiz Mod ({self.current_mode})")
            return False

        try:
            self.real_exchange_api = None
            if api_key and secret_key:
                try:
                    self.real_exchange_api = ExchangeAPI(exchange_id, api_key, secret_key, api_password)
                    logger.info(f"Gerçek ExchangeAPI ({exchange_id}) bağlantısı başarıyla kuruldu.")
                except AuthenticationError as api_auth_err:
                    msg = f"Gerçek ExchangeAPI ({exchange_id}) kimlik doğrulama hatası: {api_auth_err}. API anahtarlarını kontrol edin!"
                    logger.critical(msg)
                    self.log_signal.emit(msg, "CRITICAL")
                    self.status_changed_signal.emit(f"Hata: API Kimlik Doğrulama ({type(api_auth_err).__name__})")
                    self._cleanup()
                    return False
                except Exception as api_err:
                    msg = f"Gerçek ExchangeAPI ({exchange_id}) başlatılamadı: {api_err}"
                    logger.critical(msg)
                    if self.current_mode == 'real':
                        self.log_signal.emit(msg, "CRITICAL")
                        self.status_changed_signal.emit(f"Hata: API Bağlantısı ({type(api_err).__name__})")
                        self._cleanup()
                        return False
            else:
                logger.warning("API anahtarları eksik. Real ExchangeAPI (public modda) oluşturulmaya çalışılıyor veya demo mod için gerekli değil.")
                try:
                    self.real_exchange_api = ExchangeAPI(exchange_id)
                    logger.info(f"Gerçek ExchangeAPI ({exchange_id}) public modda başlatıldı.")
                except Exception as public_api_err:
                    logger.warning(f"Gerçek ExchangeAPI (public modda) başlatılamadı: {public_api_err}")
                    self.real_exchange_api = None

            self.exchange_api = None
            if self.current_mode == 'demo':
                try:
                    from core.demo_exchange import DemoExchangeAPI
                    self.exchange_api = DemoExchangeAPI(bot_core_ref=self, real_exchange_api=self.real_exchange_api)
                    logger.info(f"DemoExchangeAPI kullanılıyor (Gerçek API Ref: {'Mevcut' if self.real_exchange_api else 'Yok'}).")
                    with self._balance_lock:
                        self.virtual_balances = {}
                        demo_settings = user_settings.get('demo_settings', {})
                        start_balances_config = demo_settings.get('start_balances', {'USDT': '10000.0'})
                        logger.info(f"Demo için başlangıç bakiye ayarları okunuyor: {start_balances_config}")
                        if isinstance(start_balances_config, dict):
                            for currency, balance_value in start_balances_config.items():
                                try:
                                    balance_dec = utils._to_decimal(balance_value)
                                    if balance_dec is None or balance_dec < DECIMAL_ZERO:
                                        logger.warning(f"Geçersiz/Negatif demo bakiye ({currency}={balance_value}). 0.0 olarak ayarlandı.")
                                        balance_dec = DECIMAL_ZERO
                                    self.virtual_balances[str(currency).strip().upper()] = balance_dec
                                except Exception as dec_err:
                                    logger.error(f"Başlangıç demo bakiye ayarlanırken hata ({currency}='{balance_value}'): {dec_err}", exc_info=True)
                                    self.virtual_balances[str(currency).strip().upper()] = DECIMAL_ZERO
                        log_balances = {k: f"{v:.8f}" for k, v in self.virtual_balances.items()}
                        logger.info(f"Sanal demo bakiye ayarlandı: {log_balances}")
                except ImportError:
                    msg = "DemoExchangeAPI modülü import edilemedi! Demo mod kullanılamıyor."
                    logger.critical(msg)
                    self.status_changed_signal.emit("Hata: Demo Modülü Yüklenemedi")
                    self._cleanup()
                    return False
                except Exception as demo_e:
                    msg = f"Demo modu başlatılırken kritik hata: {demo_e}"
                    logger.critical(msg, exc_info=True)
                    self.status_changed_signal.emit(f"Hata: Demo Modu ({type(demo_e).__name__})")
                    self._cleanup()
                    return False
            else:
                if not self.real_exchange_api:
                    msg = "Gerçek mod için ExchangeAPI örneği oluşturulamadı (API anahtarları eksik veya bağlantı başarısız)."
                    logger.critical(msg)
                    self.status_changed_signal.emit("Hata: API Bağlantısı Yok")
                    return False
                self.exchange_api = self.real_exchange_api
                logger.info("Gerçek ExchangeAPI kullanılıyor.")

            if not self.exchange_api:
                msg = "Aktif Exchange API ayarlanamadığı için bileşenler başlatılamıyor."
                logger.critical(msg)
                self._cleanup()
                return False

            self.trade_manager = TradeManager(
                exchange_api=self.exchange_api,
                risk_manager=None,
                database_manager=self.db_manager
            )
            self.trade_manager.set_active_user(username)
            logger.info(f"TradeManager başlatıldı (Mod: '{self.current_mode}').")
            if hasattr(self.trade_manager, 'log_signal') and hasattr(self, 'log_signal'):
                try:
                    self.trade_manager.log_signal.connect(self.log_signal)
                    logger.debug("TradeManager.log_signal, BotCore.log_signal'a bağlandı.")
                except Exception as connect_err:
                     logger.error(f"TradeManager.log_signal bağlanırken hata: {connect_err}", exc_info=True)

            try:
                if not isinstance(user_settings, dict) or 'risk' not in user_settings or 'trading' not in user_settings:
                    msg = (f"RiskManager başlatılamıyor: '{username}' kullanıcısının ayarlarında "
                           f"'risk' veya 'trading' bölümleri eksik. Lütfen users.json dosyasını kontrol edin.")
                    logger.critical(msg)
                    self.log_signal.emit(f"Hata: {msg}", "CRITICAL")
                    self.status_changed_signal.emit(f"Hata: Kullanıcı Ayarları Eksik ({username})")
                    self._cleanup()
                    return False

                self.risk_manager = RiskManager(
                    trade_manager_ref=self.trade_manager,
                    user_config=user_settings,
                )
                logger.info(f"RiskManager BotCore üzerinden başlatıldı: MaxPoz={getattr(self.risk_manager, 'max_open_positions', 'N/A')}, "
                            f"Risk/Trade %={getattr(self.risk_manager, 'max_risk_per_trade_percent', Decimal('0.0')):.2f}, "
                            f"Günlük Zarar Limiti %={getattr(self.risk_manager, 'max_daily_loss_limit_percent', Decimal('0.0')):.2f}")
                logger.debug(f"RiskManager içindeki user_trading_settings: {getattr(self.risk_manager, 'user_trading_settings', 'BULUNAMADI')}")

            except ValueError as val_err:
                msg = f"Risk yöneticisi başlatılırken yapılandırma hatası: {val_err}."
                logger.critical(msg, exc_info=True)
                self.log_signal.emit(f"Hata: Risk Yöneticisi ({val_err})", "CRITICAL")
                self._cleanup()
                return False
            except Exception as risk_general_err:
                msg = f"Risk yöneticisi başlatılırken beklenmedik bir hata oluştu: {risk_general_err}."
                logger.critical(msg, exc_info=True)
                self.log_signal.emit(f"Kritik Hata: Risk Yöneticisi ({type(risk_general_err).__name__})", "CRITICAL")
                self._cleanup()
                return False

            if self.trade_manager and self.risk_manager:
                if hasattr(self.trade_manager, 'risk_manager'):
                    self.trade_manager.risk_manager = self.risk_manager
                    logger.debug("TradeManager'a RiskManager referansı başarıyla atandı.")
                else:
                    logger.error("TradeManager sınıfında 'risk_manager' özelliği bulunamadı. Risk yönetimi düzgün çalışmayabilir.")
            else:
                 logger.critical("TradeManager veya RiskManager başlatılamadığı için birbirine bağlanamadı. Bot düzgün çalışmayacak.")
                 self._cleanup()
                 return False

            signal_settings_user = user_settings.get('signal', {})
            signal_source = signal_settings_user.get('source')
            if not signal_source or not isinstance(signal_source, str) or not signal_source.strip():
                signal_source = self.config_manager.get_setting('signal_settings', 'source', default='tradingview')
            self.signal_handler = SignalHandler(signal_source=signal_source)
            logger.info(f"SignalHandler başlatıldı (Sinyal Kaynağı: {signal_source}).")

            if STRATEGIES_AVAILABLE:
                self._configure_internal_strategies(user_settings)
            else:
                logger.warning("Strateji modülleri yüklenemediği için dahili stratejiler yapılandırılamıyor.")
                self.active_strategies = {}

        except Exception as e:
            msg = f"Bot bileşenleri başlatılırken beklenmedik bir genel hata oluştu ({username}, {mode}): {e}"
            logger.critical(msg, exc_info=True)
            self.log_signal.emit(f"Kritik Başlatma Hatası: {e}", "CRITICAL")
            self.status_changed_signal.emit(f"Hata: Başlatma ({type(e).__name__})")
            self._cleanup()
            return False

        if self.exchange_api and self.trade_manager:
            logger.info(f"[{username}] Başlangıçta borsadaki mevcut açık pozisyonlar kontrol ediliyor ve senkronize ediliyor...")
            try:
                positions_from_exchange = self.exchange_api.fetch_all_open_positions_from_exchange()

                if positions_from_exchange:
                    logger.info(f"[{username}] API'den {len(positions_from_exchange)} adet açık pozisyon bulundu. TradeManager ile senkronize ediliyor...")
                    if trading_settings:
                        self.trade_manager.load_and_track_reconciled_positions(positions_from_exchange, trading_settings)
                    else:
                        logger.error(f"[{username}] Pozisyon senkronizasyonu için 'trading_settings' bulunamadı.")
                else:
                    logger.info(f"[{username}] API'den senkronize edilecek açık pozisyon bulunamadı.")

            except Exception as e_sync:
                logger.error(f"[{username}] Başlangıçta pozisyon senkronizasyonu sırasında hata: {e_sync}", exc_info=True)
                self.log_signal.emit(f"Hata: Pozisyon Senkronizasyonu ({type(e_sync).__name__})", "ERROR")

        self._is_running = True
        try:
            self._bot_thread = threading.Thread(
                target=self._run_loop,
                args=(username, user_settings),
                daemon=True,
                name=f"BotLoop_{username}"
            )
            self._bot_thread.start()
        except Exception as thread_err:
            msg = f"Bot ana çalışma thread'i başlatılamadı: {thread_err}"
            logger.critical(msg, exc_info=True)
            self.log_signal.emit(f"Kritik Hata: Thread ({thread_err})", "CRITICAL")
            self.status_changed_signal.emit(f"Hata: Thread ({type(thread_err).__name__})")
            self._is_running = False
            self.active_user = None
            self._cleanup()
            return False

        logger.info(f"Bot '{username}' kullanıcısı için '{mode.capitalize()}' modunda başarıyla başlatıldı ve çalışıyor.")
        self.log_signal.emit(f"Bot '{username}' / '{mode.capitalize()}' başarıyla başlatıldı.", "INFO")
        self.status_changed_signal.emit(f"Çalışıyor ({username} - {mode.capitalize()})")
        self._send_position_update()
        return True

    def stop(self, close_positions_decision: bool = False, from_close_event: bool = False):
        """
        Botu durdurur ve isteğe bağlı olarak açık pozisyonları kapatır.
        Bu metot, `_cleanup` metodunu çağırarak kaynakları serbest bırakır.
        :param close_positions_decision: Bot durdurulurken açık pozisyonlar kapatılsın mı?
        :param from_close_event: Bu çağrının uygulamanın kapanış olayından gelip gelmediği.
        """
        if not self._is_running and not self._stop_event.is_set():
            logger.info("Bot zaten durdurulmuş durumda.")
            self.status_changed_signal.emit(f"Durduruldu ({self.active_user or 'Bilinmeyen'} - {self.current_mode.capitalize()})")
            return

        logger.info(f"Bot durduruluyor (Kullanıcı: {self.active_user}, Mod: {self.current_mode})...")
        self.log_signal.emit("Bot durduruluyor...", "INFO")
        self.status_changed_signal.emit(f"Durduruluyor ({self.active_user or 'Bilinmeyen'} - {self.current_mode.capitalize()})...")

        self._is_running = False  # Ana döngüyü durdur
        self._stop_event.set()    # _run_loop içindeki bekleme olaylarını kes

        if close_positions_decision:
            logger.info(f"[{self.active_user}] Kullanıcı isteği üzerine tüm açık pozisyonlar kapatılıyor...")
            self.log_signal.emit("Tüm pozisyonlar kapatılıyor...", "INFO")
            if self.trade_manager:
                try:
                    current_open_positions = self.trade_manager.get_open_positions_thread_safe()
                    if current_open_positions:
                        for pos in current_open_positions:
                            order_id = pos.get('order_id')
                            symbol = pos.get('symbol')
                            if order_id:
                                logger.info(f"Pozisyon kapatma isteği (ID: {order_id}, Sembol: {symbol})...")
                                self.handle_manual_close_position(order_id, symbol, reason="Bot Durduruldu - Tümünü Kapat")
                                time.sleep(0.1) # Her pozisyon kapatma arasında küçük bir bekleme
                            else:
                                logger.warning(f"Kapatılacak pozisyon için Order ID bulunamadı: {pos}")
                    else:
                        logger.info(f"[{self.active_user}] Kapatılacak açık pozisyon bulunamadı.")
                except Exception as e:
                    logger.error(f"Tüm pozisyonlar kapatılırken hata oluştu: {e}", exc_info=True)
                    self.log_signal.emit(f"Hata: Pozisyon kapatma ({type(e).__name__})", "ERROR")
            else:
                logger.warning("TradeManager mevcut değil, pozisyonlar kapatılamıyor.")
                self.log_signal.emit("Uyarı: TradeManager hazır değil, pozisyonlar kapatılamadı.", "WARNING")

        if self._bot_thread and self._bot_thread.is_alive():
            logger.info("Bot ana thread'inin bitmesi bekleniyor...")
            self._bot_thread.join(timeout=5)
            if self._bot_thread.is_alive():
                logger.warning("Bot ana thread'i zamanında sonlandırılamadı.")
            else:
                logger.info("Bot ana thread'i başarıyla sonlandırıldı.")

        self._cleanup() # Kaynakları temizle

        final_status_msg = f"Durduruldu ({self.active_user or 'Bilinmeyen'} - {self.current_mode.capitalize()})"
        self.log_signal.emit(f"Bot tamamen durduruldu. {final_status_msg}", "INFO")
        self.status_changed_signal.emit(final_status_msg)
        logger.info(f"Bot durdurma işlemi tamamlandı. {final_status_msg}")



    def _cleanup(self):
        """ Bot durdurulduğunda kaynakları temizler. """
        logger.info("Bot kaynakları ve bileşenleri temizleniyor...")

        # Exchange API bağlantılarını kapat
        apis_to_close = []
        if self.exchange_api and hasattr(self.exchange_api, 'close') and callable(self.exchange_api.close):
            apis_to_close.append(("Aktif ExchangeAPI", self.exchange_api))
        # Gerçek API, aktif API ile aynı olabilir, bu yüzden ayrı kontrol et
        if self.real_exchange_api and self.real_exchange_api is not self.exchange_api and \
           hasattr(self.real_exchange_api, 'close') and callable(self.real_exchange_api.close):
            apis_to_close.append(("Gerçek ExchangeAPI", self.real_exchange_api))

        for name, api_instance in apis_to_close:
            try:
                logger.debug(f"{name} kapatılıyor...")
                api_instance.close()
                logger.info(f"{name} başarıyla kapatıldı.")
            except Exception as e:
                logger.warning(f"{name} kapatılırken bir hata oluştu: {e}")

        self.exchange_api = None
        self.real_exchange_api = None
        self.risk_manager = None
        self.signal_handler = None
        self.trade_manager = None # TradeManager'ı da None yap
        self.active_strategies = {} # Aktif stratejileri temizle
        self.active_user = None # Aktif kullanıcıyı temizle

        # Sanal bakiyeleri temizle (sadece demo modunda anlamlı olabilir)
        with self._balance_lock:
            self.virtual_balances = {}

        # Harici sinyal kuyruğunu temizle
        if self.external_signal_queue:
            logger.debug("Harici sinyal kuyruğu temizleniyor...")
            while not self.external_signal_queue.empty():
                try:
                    item = self.external_signal_queue.get_nowait()
                    # Kuyruktan atılan öğeyi loglamak isteğe bağlı
                    if item is not None:
                        log_item = item
                        if utils is not None and hasattr(utils, 'censor_sensitive_data') and callable(getattr(utils, 'censor_sensitive_data')):
                            try:
                                log_item = utils.censor_sensitive_data(copy.deepcopy(item))
                            except: # Sansürleme hatası olursa orijinali logla (nadiren)
                                pass
                        logger.debug(f"Kuyruktan atılan öğe (temizlik): {log_item}")
                    self.external_signal_queue.task_done() # Her get için task_done
                except queue.Empty:
                    break # Kuyruk boşsa döngüden çık
                except Exception as q_err:
                    logger.warning(f"Sinyal kuyruğu temizlenirken hata: {q_err}")
                    break # Hata durumunda da döngüden çık
            logger.debug("Harici sinyal kuyruğu temizlendi.")

        # Veritabanı bağlantısını kapat (eğer BotCore sahibi ise)
        # Eğer DatabaseManager MainWindow tarafından yönetiliyorsa, MainWindow kapatır.
        # Bu yapıya göre BotCore db_manager'ı kullanıyor ama sahibi değil.
        # if self.db_manager and hasattr(self.db_manager, 'close') and callable(self.db_manager.close):
        #     logger.info("DatabaseManager bağlantısı kapatılıyor...")
        #     try:
        #         self.db_manager.close()
        #         logger.info("DatabaseManager bağlantısı başarıyla kapatıldı.")
        #     except Exception as e:
        #         logger.error(f"DatabaseManager kapatılırken bir hata oluştu: {e}", exc_info=True)

        self._stop_event.clear() # Bir sonraki start için temizle
        logger.info("Bot kaynakları ve bileşenleri başarıyla temizlendi.")


    def _configure_internal_strategies(self, user_settings: dict):
        """ Kullanıcı ayarlarına göre dahili stratejileri yapılandırır. """
        logger.info("Dahili stratejiler yapılandırılıyor...")
        self.active_strategies = {} # Önce temizle

        # Kullanıcı ayarlarından 'active_strategies' listesini al
        strategy_configs_raw = user_settings.get('active_strategies', [])
        # Gelenin liste olduğundan emin ol
        if not isinstance(strategy_configs_raw, list):
            logger.warning(f"'active_strategies' ayarı beklenen formatta (liste) değil, yok sayılıyor. Alınan tip: {type(strategy_configs_raw)}")
            strategy_configs = []
        else:
            strategy_configs = strategy_configs_raw

        if not strategy_configs:
            logger.info("Yapılandırılacak aktif dahili strateji bulunmuyor.")
            return

        for config in strategy_configs:
            if not isinstance(config, dict):
                logger.warning(f"Geçersiz strateji config formatı (sözlük bekleniyordu): {config}")
                continue

            name = config.get('name')
            params = config.get('params', {}) # Varsayılan boş sözlük
            symbol = config.get('symbol')
            enabled = bool(config.get('enabled', True)) # Varsayılan olarak etkin

            # Gerekli alanları kontrol et
            if not name or not isinstance(name, str) or not name.strip():
                logger.warning(f"Eksik veya geçersiz strateji ismi: {config.get('name')}. Strateji yapılandırılamadı.")
                continue
            if not symbol or not isinstance(symbol, str) or not symbol.strip():
                logger.warning(f"Eksik veya geçersiz strateji sembolü ({name}): {config.get('symbol')}. Strateji yapılandırılamadı.")
                continue
            if not isinstance(params, dict): # Parametreler sözlük olmalı
                logger.warning(f"Strateji parametreleri ({name} - {symbol}) sözlük formatında değil, boş sözlük kullanılacak. Parametreler: {params}")
                params = {}

            if not enabled:
                logger.info(f"Strateji '{name}' ({symbol}) ayarlarda devre dışı bırakılmış, yapılandırılmıyor.")
                continue

            # Strateji sınıfını bul ve örneğini oluştur
            try:
                strategy_class = None
                if STRATEGIES_AVAILABLE: # Strateji modülleri yüklendiyse
                    if name == 'SimpleMovingAverageStrategy' and SimpleMovingAverageStrategy:
                        strategy_class = SimpleMovingAverageStrategy
                    # Buraya başka stratejiler eklenebilir
                    # elif name == 'AnotherStrategy' and AnotherStrategy:
                    #     strategy_class = AnotherStrategy
                else: # Strateji modülleri yüklenemediyse
                    logger.warning(f"Strateji '{name}' ({symbol}) başlatılamadı çünkü strateji modülleri yüklenemedi.")
                    continue # Sonraki config'e geç

                if strategy_class and BaseStrategy and issubclass(strategy_class, BaseStrategy):
                    # Stratejiye gerekli referansları ver (exchange_api, trade_manager vb.)
                    # BaseStrategy __init__ buna göre güncellenmeli
                    try:
                        strategy_instance = strategy_class(
                            symbol=symbol,
                            parameters=params,
                            # BaseStrategy bu argümanları kabul etmeli (opsiyonel olarak)
                            # exchange_api=self.exchange_api,
                            # trade_manager=self.trade_manager
                        )
                        # Aynı sembol için birden fazla strateji varsa bu üzerine yazar.
                        # Ya da bir listeye eklenip sembol başına birden çok strateji desteklenebilir.
                        # Şimdilik sembol başına tek strateji varsayalım.
                        self.active_strategies[symbol] = strategy_instance
                        logger.info(f"Dahili strateji '{name}' ({symbol}) başarıyla başlatıldı. Parametreler: {params}")
                    except TypeError as init_err:
                        logger.error(f"Dahili strateji sınıfı '{name}' ({symbol}) başlatılırken TypeError: __init__ metodu beklenenden farklı argümanlar alıyor olabilir. Hata: {init_err}", exc_info=True)
                    except Exception as init_general_err:
                        logger.error(f"Dahili strateji sınıfı '{name}' ({symbol}) başlatılırken beklenmedik hata: {init_general_err}", exc_info=True)

                elif not strategy_class: # Eğer strateji sınıfı bulunamadıysa
                    logger.warning(f"Strateji '{name}' için sınıf bulunamadı veya desteklenmiyor.")
                else: # Strateji sınıfı BaseStrategy'den türememişse
                    logger.warning(f"Strateji sınıfı '{name}' BaseStrategy'den türememiş, başlatılamıyor.")

            except Exception as e:
                logger.error(f"Dahili strateji '{name}' ({symbol}) işlenirken bir hata oluştu: {e}", exc_info=True)

        logger.info(f"Dahili strateji yapılandırması tamamlandı. {len(self.active_strategies)} strateji aktif.")


    def _run_loop(self, username: str, user_settings: dict):
        """ Botun ana çalışma döngüsü. """
        logger.info(f"Bot ana çalışma döngüsü başlatıldı (Kullanıcı: {username}, Mod: {self.current_mode}).")

        # Ayarları önce kullanıcıdan, sonra genelden alacak bir yardımcı fonksiyon
        def get_loop_setting(setting_name, default_value):
            # Önce kullanıcı ayarlarındaki 'bot_settings' bölümüne bak
            user_bot_settings = user_settings.get('bot_settings', {})
            value = user_bot_settings.get(setting_name)
            
            if value is not None:
                logger.debug(f"'{setting_name}' ayarı kullanıcı ayarlarından alındı: {value}")
                return value
            
            # Kullanıcıda yoksa, genel config'e bak
            logger.debug(f"'{setting_name}' ayarı kullanıcıda bulunamadı, genel ayarlara bakılıyor.")
            return self.config_manager.get_setting('bot_settings', setting_name, default=default_value)

        # Döngü ayarlarını yeni yardımcı fonksiyonla al
        try:
            loop_interval_seconds_raw = get_loop_setting('loop_interval_seconds', 10)
            loop_interval_seconds = int(str(loop_interval_seconds_raw).strip())
            if loop_interval_seconds <= 0:
                logger.warning(f"Döngü aralığı ({loop_interval_seconds_raw}) <= 0. Minimum 1 saniye kullanılacak.")
                loop_interval_seconds = 1

            position_update_interval_raw = get_loop_setting('position_update_interval_seconds', 15)
            position_update_interval = int(str(position_update_interval_raw).strip())
            if position_update_interval <= 0:
                logger.warning(f"Pozisyon güncelleme aralığı ({position_update_interval_raw}) <= 0. Minimum 1 saniye kullanılacak.")
                position_update_interval = 1
                
        except (ValueError, TypeError) as e:
            logger.critical(f"Döngü ayarları okunurken format hatası: {e}", exc_info=True)
            self.status_changed_signal.emit(f"Hata: Döngü Ayarı Formatı")
            self._cleanup()
            return
            
        # Etkin sinyal kaynaklarını ve dahili strateji kullanımını belirle (Orijinal mantığınız korunuyor)
        try:
            enabled_sources_user = user_settings.get('enabled_signal_sources', [])
            if not isinstance(enabled_sources_user, list) or not enabled_sources_user:
                logger.debug("Kullanıcı ayarlarında etkin sinyal kaynağı yok veya geçersiz, global config'e bakılıyor...")
                enabled_sources_raw = self.config_manager.get_setting('signal_settings', 'enabled_sources', default=['webhook'])
            else:
                enabled_sources_raw = enabled_sources_user

            if not isinstance(enabled_sources_raw, list):
                logger.warning(f"'enabled_signal_sources' ayarı config'de liste formatında değil ({type(enabled_sources_raw)}). Varsayılan webhook kullanılacak.")
                enabled_sources = ['webhook']
            else:
                enabled_sources = [str(s).strip().lower() for s in enabled_sources_raw if isinstance(s, (str, int, float, bool)) and str(s).strip()]
                if not enabled_sources:
                    logger.warning("'enabled_signal_sources' ayarı listede geçerli kaynak içermiyor. Varsayılan webhook kullanılacak.")
                    enabled_sources = ['webhook']

            use_internal_strategies = 'internal_strategies' in enabled_sources and bool(self.active_strategies)
            logger.info(f"Çalışma döngüsü ayarları: Etkin sinyal kaynakları={enabled_sources}, Dahili Strateji Kullanımı={use_internal_strategies}, Döngü Aralığı={loop_interval_seconds}s, Pozisyon Güncelleme Aralığı={position_update_interval}s")

        except Exception as setup_err:
            logger.critical(f"Çalışma döngüsü başlangıç ayarları yüklenirken hata: {setup_err}", exc_info=True)
            self._is_running = False
            self._stop_event.set()
            self.status_changed_signal.emit(f"Hata: Döngü Kurulumu ({type(setup_err).__name__})")
            self._cleanup()
            return

        last_pos_update_time = 0
        while self._is_running:
            loop_start_time = time.monotonic()
            try:
                # trading_settings'i döngü içinde user_settings'den alıyoruz
                trading_settings = user_settings.get('trading', {})

                # 1. Harici sinyalleri işle
                if any(s for s in enabled_sources if s != 'internal_strategies'):
                    self._process_external_signals(username, enabled_sources, trading_settings)
                if not self._is_running: break

                # 2. Dahili stratejileri çalıştır
                if use_internal_strategies:
                    self._process_internal_strategies(username, trading_settings)
                if not self._is_running: break

                # 3. Pozisyonları kontrol et
                if self.trade_manager:
                    try:
                        self.trade_manager.check_and_close_positions()
                    except Exception as pos_check_err:
                        logger.error(f"Ana döngüde pozisyon kontrolü sırasında genel bir hata: {pos_check_err}", exc_info=True)

                # 4. GUI için pozisyonları periyodik olarak güncelle
                current_time_mono = time.monotonic()
                if current_time_mono - last_pos_update_time >= position_update_interval:
                    self._send_position_update()
                    last_pos_update_time = current_time_mono

            except Exception as loop_err:
                logger.critical(f"Bot ana çalışma döngüsünde kritik bir hata oluştu: {loop_err}", exc_info=True)
                self.log_signal.emit(f"Kritik Döngü Hatası: {loop_err}", "CRITICAL")
                self._is_running = False
                self._stop_event.set()
                logger.info("Kritik hata nedeniyle bot döngüsü sonlandırılıyor.")
                self.status_changed_signal.emit(f"Kritik Hata: Döngü ({type(loop_err).__name__})")
                self._cleanup()
                break

            # Döngü aralığı kadar bekle
            elapsed_this_loop = time.monotonic() - loop_start_time
            sleep_duration = max(0.0, loop_interval_seconds - elapsed_this_loop)

            if sleep_duration > 0:
                if self._stop_event.wait(timeout=sleep_duration):
                    logger.info("Bekleme sırasında durdurma sinyali alındı, döngü sonlandırılıyor.")
                    break

        logger.info(f"Bot ana çalışma döngüsü sonlandı (Kullanıcı: {username}, Mod: {self.current_mode}).")
        self.log_signal.emit("Bot çalışma döngüsü sonlandı.", "INFO")

    def _process_external_signals(self, username: str, enabled_sources: list, trading_settings: dict):
        """ Harici kaynaklardan (örn: webhook) gelen sinyalleri işler. """
        processed_count = 0
        try:
            # Döngü başına işlenecek maksimum sinyal sayısını config'den al
            max_process_per_cycle_raw = self.config_manager.get_setting('bot_settings', 'max_signals_per_cycle', default=10)
            try:
                max_process_per_cycle = int(str(max_process_per_cycle_raw).strip())
                if max_process_per_cycle <= 0: # 0 veya negatifse minimum 1 yap
                    logger.warning(f"Sinyal işleme limiti ({max_process_per_cycle_raw}) <= 0. Minimum 1 kullanılacak.")
                    max_process_per_cycle = 1
            except (ValueError, TypeError):
                logger.error(f"Sinyal işleme limiti ayarı formatı hatalı ({max_process_per_cycle_raw}). Varsayılan 10 kullanılacak.", exc_info=False)
                max_process_per_cycle = 10
        except Exception as setting_err: # Daha genel hata yakalama
            logger.critical(f"Sinyal işleme limiti ayarı okunurken beklenmedik kritik hata: {setting_err}", exc_info=True)
            self._is_running = False # Döngüyü hemen durdur
            self._stop_event.set()   # Beklemeleri kes
            self.status_changed_signal.emit(f"Hata: Sinyal İşlem Ayarı ({type(setting_err).__name__})")
            self._cleanup() # Temizlik yap
            return # Fonksiyondan çık


        while processed_count < max_process_per_cycle: # Döngü başına maksimum sinyali işle
            if not self._is_running: return # Bot durduysa çık

            try:
                # Sinyal kuyruğundan non-blocking olmayan bir şekilde almayı dene (timeout ile)
                raw_signal_wrapper = self.external_signal_queue.get(block=True, timeout=0.1) # Kısa bir timeout

                # Kuyruktan None gelirse, bu durdurma komutudur (BotCore.stop() tarafından eklenebilir)
                if raw_signal_wrapper is None:
                    logger.info("Harici sinyal kuyruğundan durdurma komutu (None) alındı.")
                    self._is_running = False # Ana döngüyü de durdurur
                    self.external_signal_queue.task_done() # Kuyruk için
                    return # Bu fonksiyondan çık

                processed_count += 1 # İşlenen sinyal sayısını artır

                # Sinyal yapısını kontrol et (wrapper dict olmalı)
                if not isinstance(raw_signal_wrapper, dict):
                    logger.warning(f"Kuyruktan alınan öğe sözlük formatında değil ({type(raw_signal_wrapper)}). Atlanıyor.")
                    self.external_signal_queue.task_done()
                    continue

                source = raw_signal_wrapper.get('source', 'bilinmeyen_harici_kaynak').lower()
                data = raw_signal_wrapper.get('data') # data'nın (asıl sinyal içeriği) dict olması beklenir

                if not isinstance(data, dict):
                    logger.warning(f"Geçersiz harici sinyal veri formatı (data sözlük bekleniyordu) [Kaynak: {source}]: {type(data)}. Sinyal atlanıyor.")
                    self.external_signal_queue.task_done()
                    continue

                # Hassas veriyi sansürleyerek logla (utils.py'deki fonksiyonu kullanır)
                log_data = data # Varsayılan olarak orijinal veri
                if utils is not None and hasattr(utils, 'censor_sensitive_data') and callable(getattr(utils, 'censor_sensitive_data')):
                    try:
                        # data'nın bir kopyasını sansürle ki orijinal data değişmesin
                        log_data = utils.censor_sensitive_data(copy.deepcopy(data))
                    except Exception as censor_err: # Hata olursa sansürsüz devam et
                        logger.warning(f"Sinyal verisi sansürlenirken hata: {censor_err}. Sansürsüz loglanacak.")
                elif utils is None: # utils modülü yüklenemediyse
                    logger.debug("Utils modülü yüklenemedi, sinyal verisi sansürlenemiyor.")


                logger.info(f"İşlenen harici sinyal ({processed_count}/{max_process_per_cycle}) [Kaynak: {source}]: {log_data}")

                # Sinyal kaynağı bot ayarlarında etkin mi kontrol et
                if isinstance(enabled_sources, list) and source in enabled_sources:
                    if self.signal_handler and self.trade_manager: # Gerekli bileşenler var mı?
                        try:
                            # SignalHandler'a ham sinyal verisini (data) gönder, o ayrıştırsın.
                            # SignalHandler: {'action': 'open'/'close', 'symbol': 'BTCUSDT', 'side': 'buy'/'sell'/None, ...}
                            parsed_signal = self.signal_handler.parse_signal(copy.deepcopy(data))

                            # ---- GÜNCELLENMİŞ KONTROL BLOĞU ----
                            if parsed_signal and isinstance(parsed_signal, dict) and parsed_signal.get('symbol'):
                                # Temel kontroller: parsed_signal bir sözlük mü ve 'symbol' anahtarı var mı?

                                current_action = str(parsed_signal.get('action', '')).lower()
                                current_symbol = str(parsed_signal.get('symbol', '')).strip().upper() # Zaten handler'da upper yapılıyor ama garanti
                                current_side = str(parsed_signal.get('side', '')).lower() if parsed_signal.get('side') else None # None olabilir

                                # Eyleme göre geçerlilik kontrolü
                                action_is_valid = False
                                if current_action == 'open':
                                    # Pozisyon açma sinyali için 'symbol' ve 'side' dolu ve geçerli olmalı
                                    if current_symbol and current_side and current_side in ['buy', 'sell']:
                                        action_is_valid = True
                                    else:
                                        logger.warning(f"AÇMA sinyali için 'symbol' veya 'side' eksik/geçersiz. Kaynak: {source}, Sinyal: {parsed_signal}")
                                elif current_action == 'close':
                                    # Pozisyon kapatma sinyali için sadece 'symbol' yeterli.
                                    # 'side' SignalHandler'dan None gelebilir.
                                    if current_symbol:
                                        action_is_valid = True
                                    else:
                                        logger.warning(f"KAPATMA sinyali için 'symbol' eksik. Kaynak: {source}, Sinyal: {parsed_signal}")
                                else: # Bilinmeyen bir 'action' değeri varsa
                                    logger.warning(f"Bilinmeyen 'action' ({current_action}) sinyalde. Kaynak: {source}, Sinyal: {parsed_signal}")

                                # Eğer sinyal geçerli bulunduysa TradeManager'a gönder
                                if action_is_valid:
                                    original_quantity_from_signal = data.get('quantity', 'N/A') # Ham sinyaldeki quantity (sadece log için)
                                    logger.info(f"Başarıyla ayrıştırılmış ve GEÇERLİ harici sinyal [Kaynak: {source}, Orj. Qty: {original_quantity_from_signal}]: {parsed_signal}")
                                    # GUI'ye log gönder
                                    self.log_signal.emit(f"Sinyal Alındı: {current_action.upper()} {current_symbol} {(current_side.upper() if current_side else '')}", "INFO")

                                    # TradeManager'a işlemi yapması için sinyali ve kullanıcı ayarlarını ver
                                    if self.trade_manager and hasattr(self.trade_manager, 'execute_trade'):
                                        try:
                                            self.trade_manager.execute_trade(parsed_signal, trading_settings)
                                        except Exception as exec_err:
                                            # Bu except bloğu genellikle TradeManager içindeki hataları yakalamaz,
                                            # sadece execute_trade çağrılırken oluşan direkt hataları yakalar (çok nadir).
                                            # TradeManager kendi log_signal'ını emit etmeli.
                                            logger.error(f"BotCore: Harici sinyal ile işlem çağrılırken (execute_trade) beklenmedik hata [Sinyal: {parsed_signal}]: {exec_err}", exc_info=True)
                                    else:
                                        logger.error("TradeManager örneği (self.trade_manager) veya execute_trade metodu bulunamadı! Sinyal işlenemiyor.")
                                
                                # else: action_is_valid False ise, zaten yukarıda loglandı.

                            elif parsed_signal is not None: # parsed_signal var ama yukarıdaki if 'e girmedi (örn: 'symbol' yok)
                                # Bu blok artık daha az olası, çünkü temel kontrolü yukarıya aldık.
                                logger.warning(f"Harici sinyal ayrıştırıldı ANCAK GEÇERSİZ bulundu [Kaynak: {source}, Veri: {log_data}] Sinyal: {parsed_signal}")
                                self.log_signal.emit(f"Geçersiz Sinyal Formatı (BotCore) [Kaynak: {source}]", "WARNING")
                            # else: parsed_signal is None ise (SignalHandler None döndürdüyse), SignalHandler zaten loglamış olmalı.

                        except Exception as parse_err: # self.signal_handler.parse_signal çağrılırken bir hata olursa
                            logger.error(f"SignalHandler.parse_signal çağrılırken hata oluştu [Kaynak: {source}, Veri: {log_data}]: {parse_err}", exc_info=True)
                            self.log_signal.emit(f"Sinyal Ayrıştırma Hatası ({type(parse_err).__name__}) [Kaynak: {source}]", "ERROR")
                    else: # self.signal_handler veya self.trade_manager None ise
                        logger.error("SignalHandler veya TradeManager başlatılmamış, harici sinyal işlenemiyor.")
                        self.log_signal.emit("Hata: Sinyal işleyici veya ticaret yöneticisi hazır değil.", "ERROR")
                else: # Sinyal kaynağı (source) bot ayarlarında etkin değilse
                    logger.debug(f"Harici sinyal kaynağı '{source}' etkin değil, sinyal atlanıyor.")

                self.external_signal_queue.task_done() # Kuyruktan alınan her öğe için çağrılmalı

            except queue.Empty: # Kuyruk boşsa (timeout sonrası) bu döngüden çık
                break # while döngüsünden çık
            except Exception as e: # Kuyruktan alma veya diğer genel hatalar
                logger.critical(f"Harici sinyal işleme döngüsünde beklenmedik bir kritik hata: {e}", exc_info=True)
                self.log_signal.emit(f"Kritik Sinyal İşleme Hatası: {e}", "CRITICAL")
                self._is_running = False # Hata durumunda botu durdur
                self._stop_event.set()   # Beklemeleri kes
                # self._cleanup() # Cleanup burada çağrılmamalı, ana döngü bitince çağrılır.
                break # while döngüsünden çık

        if processed_count > 0: # Sadece en az bir sinyal işlendiyse logla
            logger.debug(f"Bu döngüde toplam {processed_count} harici sinyal işlendi.")


    def _process_internal_strategies(self, username: str, trading_settings: dict):
        """ Dahili stratejilerden gelen sinyalleri işler. """
        # Exchange API ve TradeManager var mı kontrol et
        if not self.exchange_api or not self.trade_manager:
            logger.critical("Dahili stratejiler çalıştırılamıyor: Exchange API veya TradeManager mevcut değil.")
            return

        # Çalıştırılacak stratejilerin bir kopyasını al (döngü sırasında değişebilme ihtimaline karşı)
        strategies_to_run_copy = self.active_strategies.copy()

        if not strategies_to_run_copy:
            # logger.debug("Çalıştırılacak aktif dahili strateji bulunmuyor.") # Çok sık loglanabilir
            return

        # Stratejilerin çalıştığı tüm sembolleri topla
        symbols_to_fetch = list(strategies_to_run_copy.keys())

        if not symbols_to_fetch:
            logger.debug("Dahili stratejiler için takip edilecek sembol bulunmuyor.")
            return

        # Semboller için güncel fiyatları al (TradeManager üzerinden)
        current_prices = {} # Sembol -> Fiyat (Decimal)
        if self.trade_manager:
            try:
                # _fetch_current_prices Dict[str, Optional[float]] döndürür
                current_prices_float = self.trade_manager._fetch_current_prices(list(symbols_to_fetch))
                # Float'ları Decimal'e çevir
                for sym, price_float in current_prices_float.items():
                    if price_float is not None:
                        try:
                            current_prices[sym] = Decimal(str(price_float))
                        except InvalidOperation:
                            logger.warning(f"Strateji için fiyat Decimal'e çevrilemedi ({sym}, Fiyat: {price_float}). Bu sembol atlanacak.")
                    # else: Fiyat alınamadıysa zaten None kalır
            except Exception as fetch_err:
                logger.error(f"Dahili stratejiler için sembol fiyatları alınırken hata: {fetch_err}", exc_info=True)
                return # Fiyatlar alınamazsa devam etme
        else:
            logger.warning("TradeManager mevcut değil, dahili stratejiler için fiyatlar alınamıyor.")
            return

        if not current_prices:
            logger.debug("Dahili stratejiler için güncel fiyatlar alınamadı (boş döndü).")
            return

        # Her bir aktif stratejiyi işle
        for symbol, strategy in strategies_to_run_copy.items():
            if not self._is_running: break # Bot durduysa çık

            # Strateji örneğinin geçerli olduğundan emin ol
            if not BaseStrategy or not isinstance(strategy, BaseStrategy): # BaseStrategy None olabilir
                logger.warning(f"Aktif stratejiler listesinde geçersiz obje bulundu (BaseStrategy örneği bekleniyordu): {type(strategy)}. Atlanıyor.")
                continue

            try:
                current_price_dec = current_prices.get(symbol) # Decimal veya None

                if current_price_dec is not None and current_price_dec > DECIMAL_ZERO:
                    current_timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                    # Stratejiye verilecek market verisi
                    # Strateji BaseStrategy'den türediği için price ve timestamp float bekleyebilir.
                    market_data = {'symbol': symbol, 'price': float(current_price_dec), 'timestamp': current_timestamp_ms}

                    # Stratejinin analyze ve generate_signal metotları var mı kontrol et
                    if hasattr(strategy, 'analyze') and callable(getattr(strategy, 'analyze')) and \
                       hasattr(strategy, 'generate_signal') and callable(getattr(strategy, 'generate_signal')):

                        try:
                            # Verinin kopyasını gönder ki strateji orijinalini değiştirmesin
                            strategy.analyze(copy.deepcopy(market_data))
                        except Exception as analyze_err:
                            logger.error(f"Dahili strateji ({strategy.__class__.__name__} - {symbol}) analyze metodunda hata oluştu: {analyze_err}", exc_info=True)
                            # Analyze hatası sinyal üretimini etkileyebilir ama devam edelim

                        try:
                            # Sinyal üretmeyi dene
                            signal = strategy.generate_signal(copy.deepcopy(market_data))

                            # Sinyal geçerli mi kontrol et
                            if signal and isinstance(signal, dict) and \
                               all(k in signal for k in ['symbol', 'side', 'type', 'amount']): # Temel alanlar var mı?

                                # Sembol ve taraf geçerli mi?
                                if isinstance(signal.get('symbol'), str) and signal.get('symbol').strip() and \
                                   isinstance(signal.get('side'), str) and signal.get('side').lower() in ['buy', 'sell']:

                                    amount_from_strategy_raw = signal.get('amount')
                                    amount_for_trade_manager = None # TradeManager'a gidecek miktar

                                    if amount_from_strategy_raw is not None:
                                        try:
                                            temp_amount_dec = Decimal(str(amount_from_strategy_raw))
                                            if temp_amount_dec > DECIMAL_ZERO:
                                                amount_for_trade_manager = temp_amount_dec # Stratejinin miktarı geçerli
                                            elif temp_amount_dec == DECIMAL_ZERO:
                                                # Strateji miktarın hesaplanmasını istiyor
                                                amount_for_trade_manager = None # Veya Decimal('0.0')
                                                logger.info(f"Dahili strateji ({strategy.__class__.__name__} - {symbol}) miktar için 0.0 döndürdü. TradeManager hesaplayacak.")
                                            else: # Negatifse
                                                logger.warning(f"Dahili stratejiden ({strategy.__class__.__name__} - {symbol}) negatif miktar geldi: {amount_from_strategy_raw}. Yok sayılıyor.")
                                        except InvalidOperation:
                                            logger.warning(f"Dahili stratejiden ({strategy.__class__.__name__} - {symbol}) gelen miktar Decimal'e çevrilemedi: {amount_from_strategy_raw}. Yok sayılıyor.")

                                    # signal içindeki 'amount'u güncelle
                                    signal['amount'] = amount_for_trade_manager

                                    logger.info(f"Dahili Strateji Sinyali ({strategy.__class__.__name__} - {symbol}): {signal}")
                                    self.log_signal.emit(f"Dahili Sinyal: {symbol} {signal.get('side', '?').upper()}", "INFO")

                                    if self.trade_manager:
                                        try:
                                            # TradeManager, amount None veya 0.0 ise kendi hesaplamasını yapmalı
                                            self.trade_manager.execute_trade(signal, trading_settings)
                                        except Exception as exec_err: # Bu except bloğu yine gereksizleşiyor
                                            logger.error(f"BotCore: Dahili sinyal işleme çağrılırken hata ({symbol}): {exec_err}", exc_info=True)
                                            # TradeManager'dan sinyal gelmeli
                                    else:
                                        logger.warning("TradeManager başlatılmamış, dahili strateji sinyali işlenemedi.")
                                else: # Sembol veya taraf geçersizse
                                    logger.warning(f"Dahili stratejiden ({strategy.__class__.__name__} - {symbol}) gelen sinyalde sembol veya yön eksik/geçersiz: {signal}")
                                    self.log_signal.emit(f"Uyarı: Dahili sinyalde sembol/yön eksik ({symbol})", "WARNING")

                            elif signal is not None: # signal var ama dict değil veya temel alanlar eksikse
                                logger.warning(f"Dahili stratejiden ({strategy.__class__.__name__} - {symbol}) geçersiz formatlı sinyal alındı: {signal}")
                                self.log_signal.emit(f"Uyarı: Dahili sinyal formatı geçersiz ({symbol})", "WARNING")
                            # else: signal is None ise (strateji sinyal üretmedi), bir şey yapma

                        except Exception as generate_err: # strategy.generate_signal() hatası
                            logger.error(f"Dahili strateji ({strategy.__class__.__name__} - {symbol}) generate_signal metodunda hata oluştu: {generate_err}", exc_info=True)
                    else: # Stratejide analyze/generate_signal metotları yoksa
                        logger.error(f"Dahili strateji objesi ({strategy.__class__.__name__} - {symbol}) 'analyze' veya 'generate_signal' metodlarına sahip değil.")
                else: # current_price_dec None veya <= 0 ise
                    logger.debug(f"Dahili strateji ({strategy.__class__.__name__} - {symbol}) için güncel fiyat bulunamadı veya geçersiz ({current_price_dec}). Atlanıyor.")

            except Exception as e: # Strateji işleme döngüsünde genel hata
                logger.critical(f"Dahili strateji ({strategy.__class__.__name__} - {symbol}) işlenirken beklenmedik kritik bir hata oluştu: {e}", exc_info=True)
                self.log_signal.emit(f"Kritik Strateji Hatası: {symbol} - {type(e).__name__}", "CRITICAL")
                # Bu hatanın tüm botu durdurup durdurmayacağına karar verilmeli.
                # Şimdilik sadece bu stratejiyi atlayıp devam edelim.


    def _send_position_update(self):
        """ Açık pozisyonları alır ve GUI'ye güncelleme sinyali gönderir. """
        # TradeManager veya ExchangeAPI aktif değilse pozisyonları sorgulama
        if not self.trade_manager or not self.exchange_api or not self.exchange_api.exchange:
            logger.debug("Pozisyon güncelleme atlandı: TradeManager veya ExchangeAPI hazır değil.")
            try:
                self.positions_updated_signal.emit([])
            except Exception as emit_err:
                logger.error(f"Boş pozisyon listesi sinyali gönderilirken hata: {emit_err}", exc_info=True)
            return

        try:
            # TradeManager'dan kilitli erişimle pozisyonları al
            open_positions_list_raw = self.trade_manager.get_open_positions_thread_safe()

            if not isinstance(open_positions_list_raw, list):
                logger.error(f"TradeManager.get_open_positions_thread_safe() geçerli bir liste döndürmedi: {type(open_positions_list_raw)}. Pozisyonlar güncellenemiyor.")
                self.positions_updated_signal.emit([])
                return

            positions_list_for_gui = []
            symbols_for_price_fetch = set() # Fiyatları alınacak semboller

            if open_positions_list_raw:
                for pos_data in open_positions_list_raw:
                    if isinstance(pos_data, dict):
                        symbol = pos_data.get('symbol')
                        if isinstance(symbol, str) and symbol.strip():
                            symbols_for_price_fetch.add(symbol.strip())
                    else:
                        logger.warning(f"Açık pozisyon verisi beklenen formatta (sözlük) değil: {type(pos_data)}. Atlanıyor.")

            current_prices_float: Dict[str, Optional[float]] = {}
            if symbols_for_price_fetch:
                if self.trade_manager:
                    try:
                        current_prices_float = self.trade_manager._fetch_current_prices(list(symbols_for_price_fetch))
                    except Exception as price_fetch_err:
                        logger.error(f"Pozisyon güncelleme için güncel fiyatlar alınırken hata: {price_fetch_err}", exc_info=True)
                else:
                    logger.warning("TradeManager mevcut değil, pozisyon güncelleme için fiyatlar alınamıyor.")

            for pos_data in open_positions_list_raw:
                if not isinstance(pos_data, dict): continue

                symbol = pos_data.get('symbol')
                current_price_raw_float = current_prices_float.get(symbol) if symbol else None

                pos_copy = copy.deepcopy(pos_data)
                pos_copy['current_price'] = current_price_raw_float

                pnl = None
                entry_price_val_dec = utils._to_decimal(pos_data.get('entry_price')) if utils and utils._to_decimal else None
                amount_val_dec = utils._to_decimal(pos_data.get('amount')) if utils and utils._to_decimal else None
                side_val_str = pos_data.get('side')
                current_price_val_dec = utils._to_decimal(current_price_raw_float) if utils and utils._to_decimal and current_price_raw_float is not None else None

                if all([entry_price_val_dec, amount_val_dec, side_val_str, current_price_val_dec, utils, utils.calculate_pnl]) and \
                   entry_price_val_dec > DECIMAL_ZERO and amount_val_dec > DECIMAL_ZERO and current_price_val_dec > DECIMAL_ZERO:
                    try:
                        pnl = utils.calculate_pnl(
                            entry_price=entry_price_val_dec,
                            current_price=current_price_val_dec,
                            filled_amount=amount_val_dec,
                            side=side_val_str
                        )
                        if pnl is not None:
                            pos_copy['profit_loss'] = f"{pnl:.8f}"
                        else:
                            pos_copy['profit_loss'] = None
                            logger.debug(f"PNL hesaplama None döndürdü ({symbol}, ID: {pos_data.get('order_id')}).")
                    except InvalidOperation:
                        logger.error(f"PNL hesaplanırken geçersiz Decimal değeri ({symbol}, ID: {pos_data.get('order_id')})")
                        pos_copy['profit_loss'] = None
                    except Exception as pnl_calc_err:
                        logger.error(f"PNL hesaplanırken beklenmedik hata ({symbol}, ID: {pos_data.get('order_id')}): {pnl_calc_err}", exc_info=True)
                        pos_copy['profit_loss'] = None
                else:
                    pos_copy['profit_loss'] = None

                open_ts_ms = pos_copy.get('timestamp')
                if isinstance(open_ts_ms, (int, float)):
                    try:
                        dt_object_local = datetime.fromtimestamp(open_ts_ms/1000.0)
                        pos_copy['open_time_formatted'] = dt_object_local.strftime('%Y-%m-%d %H:%M:%S')
                    except Exception as dt_format_err:
                        logger.warning(f"Açılış zamanı formatlanırken hata (ID: {pos_data.get('order_id')}): {dt_format_err}")
                        pos_copy['open_time_formatted'] = str(open_ts_ms)
                else:
                    pos_copy['open_time_formatted'] = "N/A"

                positions_list_for_gui.append(pos_copy)

            try:
                self.positions_updated_signal.emit(copy.deepcopy(positions_list_for_gui))
                logger.debug(f"GUI'ye {len(positions_list_for_gui)} adet pozisyon bilgisi gönderildi.")
            except Exception as emit_err:
                logger.critical(f"Pozisyon güncelleme sinyali gönderilirken kritik hata oluştu: {emit_err}", exc_info=True)
                try: self.positions_updated_signal.emit([])
                except: pass

        except Exception as e:
            logger.critical(f"Pozisyon güncelleme sinyali hazırlanırken veya ana blokta kritik bir hata oluştu: {e}", exc_info=True)
            try:
                self.positions_updated_signal.emit([])
            except Exception as emit_err_final:
                logger.error(f"Kritik hata sonrası boş pozisyon listesi sinyali gönderilirken ek hata: {emit_err_final}", exc_info=True)


    def update_virtual_balance(self, currency: str, change_amount: str) -> bool:
        """ Sanal bakiyeyi günceller (sadece demo modunda kullanılır). """
        # Bu metot BotCore içinde doğru yerde.
        with self._balance_lock:
            try:
                if not isinstance(currency, str) or not currency.strip():
                    logger.error(f"Sanal bakiye güncelleme: Geçersiz para birimi girdisi: '{currency}'")
                    self.log_signal.emit(f"Sanal bakiye güncelleme hatası: Geçersiz para birimi '{currency}'", "ERROR")
                    return False

                curr_upper = currency.strip().upper()

                try:
                    # Değeri Decimal'e çevir
                    decimal_change = Decimal(str(change_amount).replace(',', '.'))
                except InvalidOperation:
                    msg = f"Sanal bakiye için geçersiz değişim değeri formatı (Decimal'e çevrilemedi): {currency} için '{change_amount}'"
                    logger.error(msg)
                    self.log_signal.emit(msg, "ERROR")
                    return False
                except Exception as conv_err:
                    msg = f"Sanal bakiye için değişim değeri çevrilirken beklenmedik hata ({currency} için '{change_amount}'): {conv_err}"
                    logger.error(msg, exc_info=True)
                    self.log_signal.emit(msg, "ERROR")
                    return False

                # Mevcut bakiyeyi al veya sıfırla
                current_balance_dec = self.virtual_balances.get(curr_upper, Decimal('0.0'))
                if not isinstance(current_balance_dec, Decimal): # Tip kontrolü ve düzeltme
                    logger.warning(f"Sanal bakiye ({curr_upper}) beklenen Decimal tipinde değil ({type(current_balance_dec)}). Decimal'e çevrilmeye çalışılıyor.")
                    try:
                        current_balance_dec = Decimal(str(current_balance_dec).replace(',', '.'))
                    except InvalidOperation:
                        logger.error(f"Düzeltme sırasında sanal bakiye ({curr_upper}) Decimal'e çevrilemedi. Sıfırlanıyor.")
                        current_balance_dec = Decimal('0.0')
                    except Exception as fix_err:
                        logger.error(f"Düzeltme sırasında sanal bakiye ({curr_upper}) işlenirken beklenmedik hata: {fix_err}. Sıfırlanıyor.", exc_info=True)
                        current_balance_dec = Decimal('0.0')

                # Yeni bakiyeyi hesapla
                new_balance_dec = current_balance_dec + decimal_change

                # Yetersiz bakiye kontrolü (azaltma durumunda)
                if decimal_change < Decimal('0.0') and new_balance_dec < Decimal('0.0'): # Eğer azaltma sonrası bakiye negatifse
                    msg = (f"Sanal bakiye yetersiz: {curr_upper} mevcut {current_balance_dec:.8f}, talep edilen değişim {decimal_change:.8f}. İşlem reddedildi.")
                    logger.warning(msg)
                    self.log_signal.emit(msg, "WARNING")
                    return False # Yetersiz bakiye

                # Hassasiyetle yuvarla (genellikle 8 ondalık yeterli)
                precision = 8 # Veya config'den alınabilir
                quantizer = Decimal('1e-' + str(precision))
                quantized_new_balance = new_balance_dec.quantize(quantizer, rounding=ROUND_DOWN) # Her zaman aşağı yuvarla

                # Bakiyeyi güncelle
                self.virtual_balances[curr_upper] = quantized_new_balance

                # Logla ve sinyal gönder
                msg = (f"Sanal Bakiye Güncellendi: {curr_upper} = {quantized_new_balance:.{precision}f} "
                       f"(Değişim: {decimal_change:.{precision}f}, Önceki: {current_balance_dec:.{precision}f})")
                logger.info(msg)
                self.log_signal.emit(msg, "INFO") # GUI'ye log gönder
                return True

            except Exception as e: # Beklenmedik genel hata
                msg = f"Sanal bakiye güncellenirken ({currency}, Değişim: {change_amount}) beklenmedik bir hata oluştu: {e}"
                logger.critical(msg, exc_info=True)
                self.log_signal.emit(f"Kritik Sanal Bakiye Hatası: {currency}", "CRITICAL")
                return False


    def get_virtual_balance(self, currency: str) -> float:
        """ Sanal bakiyeyi float olarak döndürür. """
        # Bu metot BotCore içinde doğru yerde.
        with self._balance_lock:
            if not isinstance(currency, str) or not currency.strip():
                logger.error(f"Sanal bakiye sorgulama: Geçersiz para birimi girdisi: '{currency}'")
                return 0.0

            curr_upper = currency.strip().upper()
            balance_dec = self.virtual_balances.get(curr_upper, Decimal('0.0'))

            if not isinstance(balance_dec, Decimal): # Tip kontrolü ve düzeltme
                logger.warning(f"Sanal bakiye ({curr_upper}) beklenen Decimal tipinde değil ({type(balance_dec)}). Düzeltilmeye çalışılıyor.")
                try:
                    balance_dec = Decimal(str(balance_dec).replace(',', '.'))
                except InvalidOperation:
                    logger.error(f"Düzeltme sırasında sanal bakiye ({curr_upper}) Decimal'e çevrilemedi. 0.0 döndürülüyor.")
                    return 0.0
                except Exception as fix_err:
                    logger.error(f"Düzeltme sırasında sanal bakiye ({curr_upper}) işlenirken beklenmedik hata: {fix_err}. 0.0 döndürülüyor.", exc_info=True)
                    return 0.0

            try:
                return float(balance_dec) # Float'a çevirerek döndür
            except Exception as float_conv_err:
                logger.error(f"Sanal bakiye Decimal değerinden float'a çevrilemedi ({curr_upper}, {balance_dec}): {float_conv_err}. 0.0 döndürülüyor.", exc_info=True)
                return 0.0

    # --- Helper Metotlar ---
    def get_active_user(self) -> Union[str, None]:
        """ Aktif kullanıcı adını döndürür. """
        return self.active_user

    def is_running(self) -> bool:
        """ Botun çalışıp çalışmadığını döndürür. """
        return self._is_running

    def get_current_mode(self) -> str:
        """ Botun mevcut çalışma modunu ('real' veya 'demo') döndürür. """
        return self.current_mode

    def get_open_positions(self) -> list: # GUI'den çağrılabilir
        """ Anlık açık pozisyonların bir kopyasını döndürür (GUI için). """
        logger.debug("GUI'den açık pozisyonlar isteniyor.")
        if self.trade_manager:
            try:
                # TradeManager'dan kilitli erişimle alınmış kopyayı döndür
                return self.trade_manager.get_open_positions_thread_safe()
            except Exception as e:
                logger.error(f"GUI'den açık pozisyonlar alınırken hata: {e}", exc_info=True)
                return [] # Hata durumunda boş liste
        return [] # TradeManager yoksa boş liste



    class DbQueryWorker(QRunnable): # Bu satırın girintisi, BotCore sınıfının bir metodu gibi değil,
                                    # BotCore içinde tanımlanmış bir iç sınıf gibi olmalı.
                                    # Yani BotCore'un def metotlarından BİR SEVİYE İÇERİDE.
                                    # Eğer BotCore class BotCore(QObject): satırı en soldaysa,
                                    # class DbQueryWorker(QRunnable): satırı 4 boşluk içeride olmalı.
        """ Veritabanı sorgularını ayrı bir thread'de çalıştırmak için QRunnable. """
        def __init__(self, bot_core_ref, query_type: str, username: str, start_ms: Union[int, None] = None, end_ms: Union[int, None] = None):
            super().__init__()
            self.bot_core_ref = bot_core_ref # BotCore örneğine referans
            self.query_type = query_type
            self.username = username
            self.start_ms = start_ms
            self.end_ms = end_ms
            self.setAutoDelete(True) # İş bittikten sonra kendini silsin

        @pyqtSlot()
        def run(self):
            """ İşçi thread'inde çalışacak kod. """
            logger.debug(f"Veritabanı sorgu işçisi başladı: Tip='{self.query_type}', Kullanıcı='{self.username}'")

            # BotCore referansı ve db_manager var mı kontrol et
            if not self.bot_core_ref or not self.bot_core_ref.db_manager:
                logger.error(f"Veritabanı işçisi çalıştırılamadı: BotCore veya DatabaseManager mevcut değil. Sorgu tipi: {self.query_type}")
                # Hata durumunda GUI'ye uygun sinyali gönder
                if self.query_type == 'history':
                    try:
                        self.bot_core_ref.history_trades_updated_signal.emit([]) # Boş liste
                    except:
                        pass # Sinyal gönderme hatası olursa yapacak bir şey yok
                elif self.query_type == 'report':
                    try:
                        self.bot_core_ref.report_data_ready_signal.emit("Rapor oluşturulamıyor: Veritabanı bağlantısı yok.")
                    except:
                        pass
                return # İşçiyi sonlandır

            try:
                if self.query_type == 'history':
                    # Geçmiş işlem limitini config'den al
                    try:
                        trade_limit_raw = self.bot_core_ref.config_manager.get_setting('gui_settings', 'historical_trades_limit', default=1000)
                        try:
                            trade_limit = int(str(trade_limit_raw).strip())
                            if trade_limit < 0:
                                trade_limit = 1000 # Negatifse varsayılana dön
                        except (ValueError, TypeError): # Sayıya çevrilemezse
                            logger.error(f"Geçmiş işlem limiti ayarı formatı hatalı (işçi): {trade_limit_raw}. Varsayılan 1000 kullanılacak.")
                            trade_limit = 1000
                    except Exception as setting_err: # Config'den okuma hatası
                        logger.critical(f"Geçmiş işlem limiti ayarı okunurken beklenmedik kritik hata (işçi): {setting_err}", exc_info=True)
                        trade_limit = 1000 # Hata durumunda varsayılan

                    # Veritabanından geçmiş işlemleri çek
                    historical_trades_from_db = self.bot_core_ref.db_manager.get_historical_trades(
                        user=self.username,
                        limit=trade_limit,
                        start_ms=self.start_ms,
                        end_ms=self.end_ms
                    )
                    logger.info(f"İşçi: '{self.username}' için veritabanından {len(historical_trades_from_db)} geçmiş işlem çekildi (limit: {trade_limit}, filtreli).")

                    # GUI'ye sinyal gönder (verinin kopyasını gönder)
                    try:
                        self.bot_core_ref.history_trades_updated_signal.emit(copy.deepcopy(historical_trades_from_db))
                        logger.info(f"İşçi: '{self.username}' için geçmiş işlem verisi GUI'ye başarıyla gönderildi.")
                    except Exception as emit_err: # Sinyal gönderme hatası
                        logger.critical(f"İşçi: Geçmiş işlem verisi sinyali gönderilirken kritik hata: {emit_err}", exc_info=True)
                        try:
                            self.bot_core_ref.history_trades_updated_signal.emit([]) # Boş liste
                        except:
                            pass

                elif self.query_type == 'report':
                    # Rapor için TÜM işlemleri çek (limit yok)
                    all_trades_for_user = self.bot_core_ref.db_manager.get_historical_trades(
                        user=self.username,
                        start_ms=self.start_ms,
                        end_ms=self.end_ms,
                        limit=None # Limitsiz
                    )
                    logger.info(f"İşçi: Rapor için '{self.username}' kullanıcısının belirtilen aralıktaki {len(all_trades_for_user)} işlemi çekildi.")

                    # Rapor metnini oluştur (BotCore'un metodunu kullanarak)
                    report_text = self.bot_core_ref._generate_report_text(self.username, all_trades_for_user, self.start_ms, self.end_ms)

                    # GUI'ye raporu gönder
                    try:
                        self.bot_core_ref.report_data_ready_signal.emit(report_text)
                        logger.info(f"İşçi: '{self.username}' için rapor oluşturuldu ve GUI'ye başarıyla gönderildi.")
                    except Exception as emit_err: # Sinyal gönderme hatası
                        logger.critical(f"İşçi: Rapor verisi sinyali gönderilirken kritik hata: {emit_err}", exc_info=True)
                        try:
                            self.bot_core_ref.report_data_ready_signal.emit(f"Rapor gönderilirken hata oluştu: {emit_err}")
                        except:
                            pass

            except Exception as e: # İşçi çalışırken genel hata
                logger.critical(f"Veritabanı sorgu işçisinde kritik hata oluştu (Tip: {self.query_type}, Kullanıcı: '{self.username}'): {e}", exc_info=True)
                error_msg = f"Veri çekilirken hata oluştu: {type(e).__name__}: {e}"
                if self.query_type == 'history':
                    try:
                        self.bot_core_ref.history_trades_updated_signal.emit([])
                    except:
                        pass
                elif self.query_type == 'report':
                    try:
                        self.bot_core_ref.report_data_ready_signal.emit(error_msg)
                    except:
                        pass

            logger.debug(f"Veritabanı sorgu işçisi sonlandı: Tip='{self.query_type}', Kullanıcı='{self.username}'")

    # Bu metodun BotCore sınıfının bir parçası olduğu varsayılıyor.
    # Dolayısıyla, BotCore sınıfının diğer metotları ile (örn: __init__, start, stop)
    # AYNI girinti seviyesinde olmalıdır.
    def update_settings_for_user(self, username: str, settings_data: dict) -> bool:
        """
        Belirtilen kullanıcı için ayarları günceller ve UserConfigManager'a kaydeder.
        Eğer bot çalışıyorsa ve güncellenen kullanıcı aktif kullanıcı ise,
        dinamik olarak uygulanabilecek ayarları (örn: RiskManager, Stratejiler) günceller.
        """
        logger.info(f"'{username}' kullanıcısı için ayar güncelleme isteği alındı.")

        if not isinstance(username, str) or not username.strip():
            logger.error(f"Ayar güncelleme başarısız: Geçersiz kullanıcı adı belirtildi: '{username}'.")
            self.log_signal.emit(f"Ayar güncelleme hatası: Geçersiz kullanıcı adı '{username}'", "ERROR")
            return False

        if not isinstance(settings_data, dict):
            logger.error(f"Ayar güncelleme başarısız: `settings_data` sözlük formatında olmalı. Alınan tip: {type(settings_data)}")
            self.log_signal.emit(f"Ayar güncelleme hatası: Geçersiz veri formatı ({type(settings_data)})", "ERROR")
            return False

        try:
            # Ayar verisindeki username ile gelen username eşleşiyor mu kontrol et
            settings_username = settings_data.get('username')
            if not settings_username or not isinstance(settings_username, str) or \
            settings_username.strip().lower() != username.strip().lower():
                logger.warning(f"Ayar güncelleme isteğindeki kullanıcı adı ('{username}') ile veri içindeki kullanıcı adı ('{settings_username}') eşleşmiyor veya geçersiz. Güncelleme iptal edildi.")
                self.log_signal.emit(f"Ayarlar eşleşmiyor veya geçersiz kullanıcı adı: {username}", "WARNING")
                return False # Eşleşmiyorsa veya geçersizse işlemi durdur

            # UserConfigManager ile kullanıcı ayarlarını güncelle/kaydet
            update_success = self.user_manager.update_user(settings_data)

            if update_success:
                logger.info(f"Ayarlar '{username}' için başarıyla UserConfigManager'a kaydedildi.")
                self.log_signal.emit(f"'{username}' kullanıcısının ayarları başarıyla kaydedildi.", "INFO")

                # Eğer bot çalışıyorsa VE güncellenen kullanıcı aktif kullanıcı ise,
                # dinamik olarak uygulanabilecek ayarları yeniden yükle/ayarla.
                if self._is_running and self.active_user and self.active_user.lower() == username.strip().lower():
                    logger.info(f"Aktif kullanıcı ('{self.active_user}') ayarları güncellendi. Dinamik olarak uygulanabilen ayarlar yeniden yükleniyor...")

                    # Kullanıcının güncel ayarlarını tekrar yükle
                    updated_user_data = self.user_manager.get_user(username) # user_manager zaten kopyasını döner

                    if not updated_user_data or not isinstance(updated_user_data, dict):
                        logger.error(f"Güncellenmiş kullanıcı ayarları '{username}' UserConfigManager'dan tekrar yüklenemedi.")
                        self.log_signal.emit(f"Hata: Güncellenmiş ayarlar yüklenemedi ({username})", "ERROR")
                        return False # Güncel ayarlar alınamazsa devam etme

                    # 1. RiskManager Ayarlarını Güncelle
                    if self.risk_manager:
                        risk_settings = updated_user_data.get('risk', {}) # Yeni risk ayarlarını al
                        if isinstance(risk_settings, dict):
                            try:
                                # max_open_positions
                                new_max_pos_raw = risk_settings.get('max_open_positions', self.risk_manager.max_open_positions) # Mevcutu koru (varsa)
                                try:
                                    new_max_pos = int(str(new_max_pos_raw).strip())
                                    if new_max_pos < 0: # 0 olabilir
                                        logger.warning(f"Dinamik güncelleme: Geçersiz 'max_open_positions' ayarı ({new_max_pos_raw}). Mevcut değer ({self.risk_manager.max_open_positions}) kullanılacak.")
                                        new_max_pos = self.risk_manager.max_open_positions # Eskisini koru
                                except (ValueError, TypeError): # Sayıya çevrilemezse
                                    logger.warning(f"Dinamik güncelleme: Geçersiz 'max_open_positions' ayar formatı ({new_max_pos_raw}). Mevcut değer ({self.risk_manager.max_open_positions}) kullanılacak.")
                                    new_max_pos = self.risk_manager.max_open_positions # Eskisini koru

                                # max_risk_per_trade_percent (float olarak al, RiskManager Decimal'e çevirir)
                                new_risk_perc_raw_float = risk_settings.get('max_risk_per_trade_percent', float(self.risk_manager.max_risk_per_trade * Decimal('100.0')))

                                # max_daily_loss_percent (float olarak al, RiskManager Decimal'e çevirir ve negatif yapar)
                                new_daily_loss_raw_float = risk_settings.get('max_daily_loss_percent', float(self.risk_manager.max_daily_loss_limit * Decimal('-100.0'))) # Negatifi pozitife çevir

                                # RiskManager'daki değerleri güncelle
                                self.risk_manager.max_open_positions = new_max_pos
                                try: # Decimal dönüşüm hatası olabilir
                                    risk_dec = Decimal(str(new_risk_perc_raw_float)) / Decimal('100.0')
                                    if not (Decimal('0.0') <= risk_dec <= Decimal('1.0')):
                                        logger.warning(f"Dinamik güncelleme: Geçersiz 'max_risk_per_trade_percent' sonucu ({risk_dec*100:.2f}%). %0-%100 arası olmalı. Kontrol edin.")
                                    else:
                                        self.risk_manager.max_risk_per_trade = risk_dec
                                except (InvalidOperation, TypeError, ValueError):
                                    logger.error(f"Dinamik güncelleme: 'max_risk_per_trade_percent' ({new_risk_perc_raw_float}) Decimal'e çevrilemedi. Risk/trade değişmedi.")

                                try:
                                    daily_loss_dec_input = Decimal(str(new_daily_loss_raw_float))
                                    new_limit = -abs(daily_loss_dec_input / Decimal('100.0'))
                                    if new_limit > Decimal('0.0'): # Hala pozitifse (abs hatası vs)
                                        logger.warning(f"Dinamik güncelleme: 'max_daily_loss_percent' sonucu ({new_limit*100:.2f}%) pozitif. Negatif olmalı. Kontrol edin.")
                                    else:
                                        self.risk_manager.max_daily_loss_limit = new_limit
                                except (InvalidOperation, TypeError, ValueError):
                                    logger.error(f"Dinamik güncelleme: 'max_daily_loss_percent' ({new_daily_loss_raw_float}) Decimal'e çevrilemedi. Günlük zarar limiti değişmedi.")

                                logger.info(f"RiskManager ayarları dinamik olarak güncellendi: MaxPoz={self.risk_manager.max_open_positions}, İşlem Başına Risk%={self.risk_manager.max_risk_per_trade*100:.2f}, Günlük Max Zarar%={self.risk_manager.max_daily_loss_limit*100:.2f}")
                                self.log_signal.emit("Risk ayarları dinamik olarak güncellendi.", "INFO")

                            except Exception as risk_update_general_err: # Risk ayarları güncellenirken genel hata
                                logger.critical(f"RiskManager ayarları dinamik olarak güncellenirken beklenmedik kritik hata: {risk_update_general_err}. Ayarlar eski değerlerde kaldı.", exc_info=True)
                                self.log_signal.emit(f"Kritik Hata: Risk ayarı güncelleme ({type(risk_update_general_err).__name__})", "CRITICAL")
                        else: # 'risk' bölümü sözlük değilse
                            logger.warning(f"Güncellenmiş kullanıcı ayarlarında 'risk' bölümü beklenmedik formatta (sözlük bekleniyor). Risk ayarları dinamik olarak güncellenemedi.")
                            self.log_signal.emit(f"Uyarı: Ayarlarda 'risk' bölümü hatalı format.", "WARNING")
                    else: # self.risk_manager None ise
                        logger.warning("RiskManager başlatılmamış, risk ayarları dinamik olarak güncellenemedi.")
                        self.log_signal.emit("Uyarı: RiskManager yüklü değil.", "WARNING")

                    # 2. Dahili Stratejileri Yeniden Yapılandır
                    if STRATEGIES_AVAILABLE: # Strateji modülleri yüklendiyse
                        try:
                            self._configure_internal_strategies(updated_user_data)
                            logger.info("Dahili stratejiler dinamik olarak yeniden yapılandırıldı.")
                            self.log_signal.emit("Dahili stratejiler güncellendi.", "INFO")
                        except Exception as strategy_update_err:
                            logger.critical(f"Dahili stratejiler dinamik olarak güncellenirken kritik hata: {strategy_update_err}", exc_info=True)
                            self.log_signal.emit(f"Kritik Hata: Strateji Güncelleme ({type(strategy_update_err).__name__})", "CRITICAL")

                    # Kullanıcıyı uyar
                    logger.warning("Bazı ayar değişiklikleri (örn: API anahtarları, borsa seçimi, döngü aralıkları) için botu yeniden başlatmanız gerekebilir.")
                    self.log_signal.emit("Uyarı: Tam ayar değişikliği için botu yeniden başlatın.", "WARNING")

                elif self._is_running: # Bot çalışıyor ama güncellenen kullanıcı aktif değilse
                    logger.info(f"Ayarları güncellenen kullanıcı ('{username}') şu anki aktif kullanıcı ('{self.active_user}') değil. Ayarlar sadece dosyaya kaydedildi.")
                else: # Bot çalışmıyorsa
                    logger.info("Bot şu an çalışmıyor. Ayarlar sadece dosyaya kaydedildi, bot başlatıldığında geçerli olacak.")

                return True # UserConfigManager'a kaydetme başarılıysa True dön

            else: # UserConfigManager.update_user() False döndürdüyse
                logger.error(f"Ayarlar '{username}' için UserConfigManager'a kaydedilemedi. Detaylar için UserConfigManager loglarına bakın.")
                # GUI'ye de bilgi verilebilir
                self.log_signal.emit(f"Hata: '{username}' için ayarlar kaydedilemedi!", "ERROR")
                return False

        except ValueError as ve: # UserConfigManager'dan gelebilir (örn. kullanıcı bulunamadı)
            logger.error(f"Ayar güncelleme işlemi sırasında ValueError (Kullanıcı: '{username}'): {ve}", exc_info=True)
            self.log_signal.emit(f"Ayar Güncelleme Hatası (ValueError): {ve}", "ERROR")
            return False
        except Exception as e: # Beklenmedik genel hata
            logger.critical(f"Ayar güncelleme işlemi sırasında beklenmedik kritik bir hata (Kullanıcı: '{username}'): {e}", exc_info=True)
            self.log_signal.emit(f"Kritik Ayar Güncelleme Hatası: {e}", "CRITICAL")
            return False