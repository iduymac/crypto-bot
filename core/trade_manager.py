# core/trade_manager.py

import logging
import threading
import time
from decimal import Decimal, InvalidOperation # ROUND_HALF_UP, Context gerekirse eklenebilir
import math # Gerekirse kullanılabilir
import copy
from typing import Optional, Dict, Any, Tuple, Union, List, TYPE_CHECKING

# PyQt Sinyalleri için import (Eğer QObject'ten miras alıyorsa)
from PyQt5.QtCore import QObject, pyqtSignal

# Tip kontrolü sırasında döngüsel importları önlemek için
if TYPE_CHECKING:
    from core.exchange_api import ExchangeAPI
    from core.risk_manager import RiskManager
    from core.database_manager import DatabaseManager
    # from core.demo_exchange import DemoExchangeAPI # Eğer özel tip kontrolü yapılıyorsa

# --- Logger Düzeltmesi ---
try:
    from core.logger import setup_logger
    logger = setup_logger('trade_manager')
except ImportError:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('trade_manager_fallback')
    logger.warning("core.logger bulunamadı, fallback logger kullanılıyor.")
# --- /Logger Düzeltmesi ---

# --- Utils Import ve Decimal Sabitleri/Context ---
# Bu kısım projenizdeki utils.py'ye göre ayarlanmalı
utils = None
DECIMAL_ZERO = Decimal('0')
DECIMAL_ONE = Decimal('1')
DECIMAL_HUNDRED = Decimal('100')
_to_decimal = lambda value: Decimal(str(value).replace(',', '.')) if value is not None else None
try:
    import utils
    from utils import (DECIMAL_ZERO, DECIMAL_ONE, DECIMAL_HUNDRED, _to_decimal,
                       calculate_pnl, calculate_stop_loss_price, calculate_take_profit_price)
    logger.info("utils modülünden fonksiyonlar ve sabitler başarıyla import edildi.")
except ImportError:
    logger.error("utils modülü veya gerekli fonksiyonlar/sabitler import edilemedi! Hesaplamalar düzgün çalışmayabilir.")
    # Basit fallback'ler yukarıda zaten tanımlı


# CCXT Exceptionları (opsiyonel ama iyi bir pratik)
try:
    from ccxt import InsufficientFunds, InvalidOrder, ExchangeError, NetworkError, NotSupported
except ImportError:
    logger.warning("CCXT exception sınıfları import edilemedi.")
    class InsufficientFunds(Exception): pass
    class InvalidOrder(Exception): pass
    class ExchangeError(Exception): pass
    class NetworkError(Exception): pass
    class NotSupported(Exception): pass


class TradeManager(QObject):
    log_signal = pyqtSignal(str, str)

    def __init__(self, exchange_api: 'ExchangeAPI',
                 risk_manager: Optional['RiskManager'] = None,
                 database_manager: Optional['DatabaseManager'] = None):
        super().__init__()

        if not exchange_api:
            logger.critical("[TradeManager] Başlatma Hatası: Geçerli bir exchange_api sağlanmadı!")
            raise ValueError("TradeManager başlatılamadı: exchange_api gereklidir.")

        self.exchange_api = exchange_api
        self.risk_manager = risk_manager
        self.database_manager = database_manager
        self._active_user: Optional[str] = None
        self.open_positions: Dict[str, Dict[str, Any]] = {} # order_id -> position_data
        self._positions_lock = threading.Lock() # Pozisyonlara erişim için kilit

        if not self.risk_manager: logger.warning("[TradeManager] RiskManager sağlanmadı.")
        if not self.database_manager: logger.warning("[TradeManager] DatabaseManager sağlanmadı.")
        if utils is None: logger.warning("[TradeManager] utils modülü yüklenemedi.")

        api_name = getattr(self.exchange_api, 'exchange_name', type(self.exchange_api).__name__)
        logger.info(f"[TradeManager] Başlatıldı. Kullanılan Borsa API: {api_name}")

    def load_and_track_reconciled_positions(self, positions_from_api: List[Dict[str, Any]], 
                                           user_trading_settings: Dict[str, Any]):
        """
        API'den çekilen ve borsada zaten açık olan pozisyonları botun takibine alır.
        """
        active_user = self._active_user or "Sistem"
        if not positions_from_api:
            logger.info(f"[{active_user}] Senkronize edilecek açık pozisyon API'den gelmedi.")
            return

        logger.info(f"[{active_user}] {len(positions_from_api)} adet mevcut açık pozisyon senkronize ediliyor...")
        
        # user_trading_settings'den genel SL/TP ve kaldıraç ayarlarını al
        default_sl_perc = _to_decimal(user_trading_settings.get('stop_loss_percentage', '0.0')) or DECIMAL_ZERO
        default_tp_perc = _to_decimal(user_trading_settings.get('take_profit_percentage', '0.0')) or DECIMAL_ZERO
        default_leverage = int(user_trading_settings.get('default_leverage', 1)) # Bu zaten TradeManager'da var
        # TSL ayarları da alınabilir.
        tsl_settings_from_config = {
            'enabled': bool(user_trading_settings.get('tsl_enabled', False)),
            'activation_percentage': _to_decimal(user_trading_settings.get('tsl_activation_percentage', '0.0')) or DECIMAL_ZERO,
            'callback_percentage': _to_decimal(user_trading_settings.get('tsl_distance_percent', '0.0')) or DECIMAL_ZERO
        }

        with self._positions_lock:
            for api_pos_data in positions_from_api:
                symbol = api_pos_data.get('symbol') # Bu CCXT formatında (örn: BTC/USDT)
                side = str(api_pos_data.get('side','')).lower() # 'buy' veya 'sell'
                
                # Pozisyon ID'si olarak ne kullanacağımıza karar vermeliyiz.
                # API'den gelen pozisyonun kendine ait bir 'id'si olmayabilir.
                # Sembol ve yön kombinasyonu genellikle benzersizdir (hedge mod hariç).
                # Şimdilik sembolü ve yönü birleştirerek basit bir ID oluşturalım.
                # Veya daha karmaşık bir ID (örn: exchangeName_symbol_side) veya timestamp ile rastgele bir ID.
                # ÖNEMLİ: Bu ID'nin bot içinde benzersiz olması ve aynı pozisyon için tutarlı olması gerekir.
                # Eğer API'den gelen 'raw_data' içinde benzersiz bir pozisyon ID'si varsa, o kullanılmalı.
                # Binance için 'raw_data' içinde 'symbol' ve 'positionSide' (eğer hedge mod) kullanılabilir.
                # Tek taraflı modda, sadece 'symbol' genellikle pozisyonu tanımlar.
                position_id_for_tracking = f"reconciled_{symbol.replace('/', '')}_{side}_{int(time.time()*1000)}"

                if position_id_for_tracking in self.open_positions:
                    logger.warning(f"[{active_user}] Senkronize edilen pozisyon ({symbol} {side}) zaten takip ediliyor (ID: {position_id_for_tracking}). Atlanıyor.")
                    continue

                try:
                    entry_price_dec = _to_decimal(api_pos_data.get('entry_price'))
                    amount_dec = _to_decimal(api_pos_data.get('amount')) # API'den gelen miktar base currency cinsinden olmalı
                    leverage_from_api = int(api_pos_data.get('leverage', default_leverage))
                    
                    if not all([symbol, side, entry_price_dec, amount_dec]) or entry_price_dec <= DECIMAL_ZERO or amount_dec <= DECIMAL_ZERO:
                        logger.warning(f"[{active_user}] API'den gelen pozisyon verisi eksik/geçersiz ({api_pos_data}). Atlanıyor.")
                        continue

                    # SL ve TP fiyatlarını hesapla (eğer ayarlarda varsa)
                    # Bu, pozisyon API'den geldiği için, o anki ayarlara göre SL/TP belirlenir.
                    # Borsa üzerinde zaten var olan SL/TP emirlerini çekip eşleştirmek daha gelişmiş bir yöntemdir.
                    sl_price_final: Optional[Decimal] = None
                    if default_sl_perc > DECIMAL_ZERO and utils and hasattr(utils, 'calculate_stop_loss_price'):
                        sl_price_calculated = utils.calculate_stop_loss_price(entry_price_dec, default_sl_perc, side)
                        sl_price_final = self._adjust_precision(symbol, sl_price_calculated, 'price')
                        if sl_price_final is None or \
                           (side == 'buy' and sl_price_final >= entry_price_dec) or \
                           (side == 'sell' and sl_price_final <= entry_price_dec):
                            logger.warning(f"[{active_user}] Senkronize edilen pozisyon ({symbol}) için hesaplanan SL geçersiz, SL ayarlanmayacak.")
                            sl_price_final = None


                    tp_price_final: Optional[Decimal] = None
                    if default_tp_perc > DECIMAL_ZERO and utils and hasattr(utils, 'calculate_take_profit_price'):
                        tp_price_calculated = utils.calculate_take_profit_price(entry_price_dec, default_tp_perc, side)
                        tp_price_final = self._adjust_precision(symbol, tp_price_calculated, 'price')
                        if tp_price_final is None or \
                           (side == 'buy' and tp_price_final <= entry_price_dec) or \
                           (side == 'sell' and tp_price_final >= entry_price_dec):
                            logger.warning(f"[{active_user}] Senkronize edilen pozisyon ({symbol}) için hesaplanan TP geçersiz, TP ayarlanmayacak.")
                            tp_price_final = None
                    
                    # _track_position benzeri bir yapı oluştur
                    position_data_to_track: Dict[str, Any] = {
                        'order_id': position_id_for_tracking, # Oluşturduğumuz ID
                        'user': active_user, 
                        'symbol': symbol,
                        'side': side.lower(), 
                        'leverage': leverage_from_api,
                        'amount': amount_dec, 
                        'entry_price': entry_price_dec,
                        'sl_price': sl_price_final, 
                        'tp_price': tp_price_final,
                        'timestamp': int(time.time() * 1000), # Pozisyonun bot tarafından fark edildiği zaman
                        'status': 'open', 
                        'tsl_enabled': tsl_settings_from_config.get('enabled', False),
                        'tsl_activation_percentage': tsl_settings_from_config.get('activation_percentage', DECIMAL_ZERO),
                        'tsl_callback_percentage': tsl_settings_from_config.get('callback_percentage', DECIMAL_ZERO),
                        'tsl_activated': False, 
                        'tsl_stop_price': None,
                        'tsl_highest_price': None, 
                        'tsl_lowest_price': None,
                        'api_order_details': api_pos_data.get('raw_data', api_pos_data) # Ham API verisi
                    }
                    self.open_positions[position_id_for_tracking] = position_data_to_track
                    logger.info(f"[{active_user}] API'den senkronize edilen pozisyon takibe alındı: ID={position_id_for_tracking}, {symbol} {side.upper()} @ {entry_price_dec:.8f}, Miktar={amount_dec:.8f}")
                    self.log_signal.emit(f"Mevcut Pozisyon Takip: {symbol} ID:{position_id_for_tracking}", "INFO")

                except Exception as e:
                    logger.error(f"[{active_user}] API'den gelen pozisyon ({api_pos_data.get('symbol')}) işlenirken hata: {e}", exc_info=True)
        
        logger.info(f"[{active_user}] Mevcut açık pozisyonların senkronizasyonu tamamlandı.")
    
    def _fetch_current_prices(self, symbols: List[str]) -> Dict[str, Optional[float]]:
        """
        Verilen sembol listesi için güncel piyasa fiyatlarını alır.
        Önce toplu olarak (fetch_tickers), başarısız olursa tek tek (get_symbol_price) dener.
        Demo ve gerçek modda çalışacak şekilde tasarlanmıştır.
        """
        prices: Dict[str, Optional[float]] = {}
        active_user_log_prefix = f"[{getattr(self, '_active_user', 'Sistem') or 'Sistem'}]"

        if not symbols: # Sembol listesi boşsa hemen çık
            logger.debug(f"{active_user_log_prefix} Fiyat alınacak sembol listesi boş.")
            return prices
        if not self.exchange_api:
            logger.error(f"{active_user_log_prefix} Fiyat alınamıyor: self.exchange_api (borsa arayüzü) mevcut değil.")
            return prices
        
        unique_symbols = list(set(symbols)) # Yinelenen sembolleri kaldır
        logger.debug(f"{active_user_log_prefix} {len(unique_symbols)} adet tekil sembol için fiyat alınacak: {unique_symbols}")

        # 'has' özelliğini ve 'fetch_tickers' metodunu kontrol etmek için kullanılacak API nesnesini belirle
        api_object_for_capability_check = None
        is_demo_mode_internal = False # Bu blok içinde kullanılacak demo modu bayrağı

        _DemoExchangeAPI_local_type = None
        try:
            from core.demo_exchange import DemoExchangeAPI as TempDemoAPI # Döngüsel importu önlemek için geçici import
            _DemoExchangeAPI_local_type = TempDemoAPI
        except ImportError:
            logger.debug(f"{active_user_log_prefix} _fetch_current_prices: DemoExchangeAPI import edilemedi (tip kontrolü için).")

        if _DemoExchangeAPI_local_type and isinstance(self.exchange_api, _DemoExchangeAPI_local_type):
            is_demo_mode_internal = True
            # Demo modunda, 'has' kontrolü için öncelikle DemoExchangeAPI'nin kendisine eklediğimiz 'has' özelliğine bakarız.
            if hasattr(self.exchange_api, 'has') and isinstance(self.exchange_api.has, dict):
                api_object_for_capability_check = self.exchange_api # DemoAPI'nin kendi 'has' özelliği
                logger.debug(f"{active_user_log_prefix} Demo modunda 'has' kontrolü için DemoExchangeAPI.has kullanılacak.")
            # Eğer DemoExchangeAPI'de 'has' yoksa ama 'real_exchange_api' varsa, onun ccxt nesnesini kullanmayı deneyebiliriz (nadiren gerekebilir).
            elif hasattr(self.exchange_api, 'real_exchange_api') and self.exchange_api.real_exchange_api and \
               hasattr(self.exchange_api.real_exchange_api, 'exchange') and self.exchange_api.real_exchange_api.exchange:
                api_object_for_capability_check = self.exchange_api.real_exchange_api.exchange
                logger.debug(f"{active_user_log_prefix} Demo modunda 'has' kontrolü için gerçek API referansı ({type(api_object_for_capability_check)}) kullanılacak.")
            else:
                logger.warning(f"{active_user_log_prefix} Demo modunda 'has' özelliği için uygun API nesnesi bulunamadı.")
        elif hasattr(self.exchange_api, 'exchange') and self.exchange_api.exchange: # Gerçek ExchangeAPI ise (ccxt nesnesi var)
            api_object_for_capability_check = self.exchange_api.exchange
            logger.debug(f"{active_user_log_prefix} Gerçek modda 'has' kontrolü için ExchangeAPI.exchange ({type(api_object_for_capability_check)}) kullanılacak.")
        else:
            logger.warning(f"{active_user_log_prefix} Fiyat alma: 'has' özelliği için uygun API nesnesi bulunamadı (API Tipi: {type(self.exchange_api)}). Toplu fiyat alma atlanacak.")

        fetch_tickers_is_supported = False
        if api_object_for_capability_check and hasattr(api_object_for_capability_check, 'has') and isinstance(api_object_for_capability_check.has, dict):
            fetch_tickers_is_supported = api_object_for_capability_check.has.get('fetchTickers', False)
        logger.debug(f"{active_user_log_prefix} 'fetchTickers' API desteği: {fetch_tickers_is_supported} (Kontrol edilen nesne: {type(api_object_for_capability_check)})")

        # Gerçek API çağrısı için kullanılacak nesne (fetch_tickers gibi metotları içeren)
        api_to_call_fetch_tickers = None
        if fetch_tickers_is_supported: # Sadece destekleniyorsa çağrı nesnesini belirle
            if is_demo_mode_internal:
                # DemoExchangeAPI'nin kendisi fetch_tickers'ı destekliyorsa (veya taklit ediyorsa) onu kullanırız.
                if hasattr(self.exchange_api, 'fetch_tickers') and callable(getattr(self.exchange_api, 'fetch_tickers')):
                    api_to_call_fetch_tickers = self.exchange_api
                else:
                    # DemoAPI'de fetch_tickers yoksa, toplu alım bu yolla yapılamaz.
                    logger.debug(f"{active_user_log_prefix} DemoExchangeAPI 'fetch_tickers' metoduna sahip değil. Toplu alım atlanacak.")
                    # fetch_tickers_is_supported = False # Bu bayrağı burada değiştirmek yerine, aşağıdaki if'te kontrol etmek daha iyi.
            elif hasattr(self.exchange_api, 'exchange') and self.exchange_api.exchange and \
                 hasattr(self.exchange_api.exchange, 'fetch_tickers') and callable(getattr(self.exchange_api.exchange, 'fetch_tickers')): # Gerçek mod
                api_to_call_fetch_tickers = self.exchange_api.exchange # ccxt nesnesi
            else:
                logger.warning(f"{active_user_log_prefix} Fiyat alma: API çağrısı için uygun `Workspace_tickers` metodu bulunamadı!")

        # Toplu fiyat alma denemesi
        if fetch_tickers_is_supported and api_to_call_fetch_tickers:
            logger.debug(f"{active_user_log_prefix} Toplu fiyat alma deneniyor ({len(unique_symbols)} sembol)... (Çağrılacak API: {type(api_to_call_fetch_tickers)})")
            try:
                tickers = api_to_call_fetch_tickers.fetch_tickers(unique_symbols)
                if tickers and isinstance(tickers, dict):
                    for symbol_key_original in unique_symbols:
                        ticker_data = tickers.get(symbol_key_original)
                        # Alternatif sembol formatlarını da kontrol et (örn: BTC/USDT vs BTCUSDT)
                        if not ticker_data and '/' in symbol_key_original:
                            ticker_data = tickers.get(symbol_key_original.replace('/',''))
                        elif not ticker_data and '-' in symbol_key_original: # Başka bir yaygın format
                            ticker_data = tickers.get(symbol_key_original.replace('-',''))

                        if ticker_data and isinstance(ticker_data, dict):
                            price_raw = ticker_data.get('last') or ticker_data.get('close') or ticker_data.get('ask') or ticker_data.get('bid')
                            price_dec = _to_decimal(price_raw) # utils._to_decimal kullanıyoruz
                            if price_dec is not None and price_dec > DECIMAL_ZERO:
                                prices[symbol_key_original] = float(price_dec)
                            else:
                                prices[symbol_key_original] = None # Geçersiz fiyat
                                logger.debug(f"{active_user_log_prefix} Toplu alımda '{symbol_key_original}' için geçersiz fiyat: {price_raw}")
                        else:
                            prices[symbol_key_original] = None # Ticker bulunamadıysa None ata
                            logger.debug(f"{active_user_log_prefix} Toplu alımda '{symbol_key_original}' için ticker verisi bulunamadı.")
                    logger.debug(f"{active_user_log_prefix} Toplu fiyat alma sonucu (bazıları None olabilir): {prices}")
                else: # tickers boş veya dict değilse
                     logger.warning(f"{active_user_log_prefix} Toplu fiyat alma (fetch_tickers) beklenen formatta veri döndürmedi (Dönen tip: {type(tickers)}). Tek tek denenecek.")
                     prices = {sym: None for sym in unique_symbols} # Hepsini None yap ki tek tek denensin
            except Exception as batch_err:
                logger.warning(f"{active_user_log_prefix} Toplu fiyat alma sırasında hata: {batch_err}. Tek tek denenecek.", exc_info=False)
                prices = {sym: None for sym in unique_symbols} # Hata durumunda sıfırla ki tek tek denensin
        else:
            logger.debug(f"{active_user_log_prefix} Toplu fiyat alma desteklenmiyor veya çağrılacak API nesnesi/metodu yok. Tek tek alınacak.")

        # Toplu alım başarısız olduysa veya bazıları None kaldıysa ya da toplu alım hiç denenmediyse, tek tek dene
        for symbol_to_fetch_individually in unique_symbols:
            if symbol_to_fetch_individually not in prices or prices.get(symbol_to_fetch_individually) is None:
                logger.debug(f"{active_user_log_prefix} '{symbol_to_fetch_individually}' için tek tek fiyat alınıyor...")
                # Tek tek fiyat alırken her zaman self.exchange_api.get_symbol_price() kullanılmalı,
                # çünkü bu DemoExchangeAPI içinde de doğru şekilde ele alınıyor.
                try:
                    price_raw_single = self.exchange_api.get_symbol_price(symbol_to_fetch_individually)
                    if price_raw_single is not None: # Dönen değer float olmalı
                         price_dec_single = _to_decimal(price_raw_single) # utils._to_decimal kullan
                         if price_dec_single is not None and price_dec_single > DECIMAL_ZERO:
                             prices[symbol_to_fetch_individually] = float(price_dec_single)
                         else:
                             prices[symbol_to_fetch_individually] = None # Geçersiz fiyat
                             logger.debug(f"{active_user_log_prefix} Tekli alımda '{symbol_to_fetch_individually}' için geçersiz fiyat: {price_raw_single}")
                    else:
                        prices[symbol_to_fetch_individually] = None # API'den None geldiyse
                    
                    # Çoklu istek varsa ve toplu alım yapılamadıysa/başarısızsa kısa bir bekleme (isteğe bağlı)
                    # Bu kontrol fetch_tickers_is_supported ve api_to_call_fetch_tickers'ın durumuna göre yapılmalı.
                    # Basitlik için, eğer birden fazla sembol varsa ve bu ilk başarılı toplu alım değilse bekle.
                    is_batch_fetch_attempted_and_failed_or_not_supported = not (fetch_tickers_is_supported and api_to_call_fetch_tickers)
                    if len(unique_symbols) > 1 and is_batch_fetch_attempted_and_failed_or_not_supported:
                        time.sleep(0.05) # API rate limitlerini zorlamamak için (küçük bir değer)
                except Exception as single_fetch_err:
                    logger.warning(f"{active_user_log_prefix} Tek fiyat alma hatası ({symbol_to_fetch_individually}): {single_fetch_err}", exc_info=False)
                    prices[symbol_to_fetch_individually] = None # Hata durumunda None ata
        
        valid_prices_count = sum(1 for p in prices.values() if p is not None)
        logger.info(f"{active_user_log_prefix} Fiyat alma tamamlandı: {valid_prices_count}/{len(unique_symbols)} geçerli fiyat alındı.")
        return prices
    
    def set_active_user(self, username: Optional[str]):
        # Bu metodun içeriği önceki gibi kalabilir
        with self._positions_lock:
            self._active_user = username
            # ... (loglama)
            if username: logger.info(f"[{self._active_user}] TradeManager için aktif kullanıcı '{username}' olarak ayarlandı.")
            else: logger.info(f"[{self._active_user if self._active_user else 'Sistem'}] TradeManager için aktif kullanıcı temizlendi.")


    def _adjust_precision(self, symbol: str, value: Any, precision_type: str = 'amount') -> Optional[Decimal]:
        # Bu metodun içeriği önceki gibi kalabilir (API hassasiyet ayarı)
        if value is None: return None
        value_dec = _to_decimal(value)
        if value_dec is None: return None # _to_decimal hata loglar

        if precision_type == 'amount' and value_dec <= DECIMAL_ZERO: return None
        if precision_type == 'price' and value_dec <= DECIMAL_ZERO: return None

        api_method_name = f"{precision_type}_to_precision"
        if hasattr(self.exchange_api, api_method_name) and callable(getattr(self.exchange_api, api_method_name)):
            try:
                precise_value_from_api = getattr(self.exchange_api, api_method_name)(symbol, float(value_dec))
                adjusted_dec = _to_decimal(precise_value_from_api)
                if adjusted_dec is None:
                    logger.error(f"API hassasiyet dönüşü ('{precise_value_from_api}') Decimal'e çevrilemedi ({symbol}).")
                    return None
                # Miktar için ek kontrol
                if precision_type == 'amount' and adjusted_dec <= DECIMAL_ZERO:
                    logger.error(f"Hassasiyete ayarlanmış miktar ({value} -> {adjusted_dec}) sıfır/geçersiz ({symbol}).")
                    return None
                return adjusted_dec
            except Exception as api_call_err:
                 logger.error(f"API.{api_method_name} hatası ({symbol}, {value}): {api_call_err}", exc_info=False)
                 return None
        logger.error(f"Hassasiyet ayarlama metodu ({api_method_name}) API'de bulunamadı ({symbol}).")
        return None

    def _get_currencies_from_symbol(self, symbol: str) -> Optional[Tuple[str, str]]:
        # Bu metodun içeriği önceki gibi kalabilir (Sembolü base/quote olarak ayırma)
        if hasattr(self.exchange_api, 'market_details') and isinstance(self.exchange_api.market_details, dict):
            # ... (market_details'dan okuma) ...
            # Önceki yanıtlardaki gibi
            market = self.exchange_api.market_details.get(symbol)
            if not market and '/' in symbol: market = self.exchange_api.market_details.get(symbol.replace('/',''))
            if not market and '-' in symbol: market = self.exchange_api.market_details.get(symbol.replace('-',''))

            if market and isinstance(market.get('base'), str) and isinstance(market.get('quote'), str):
                return market['base'], market['quote']
        # ... (manuel ayrıştırma fallback) ...
        # Önceki yanıtlardaki gibi
        parts = []
        if '/' in symbol: parts = symbol.split('/')
        elif '-' in symbol: parts = symbol.split('-')
        else:
            common_quotes = sorted(['USDT', 'BUSD', 'USDC', 'TUSD', 'DAI', 'EUR', 'TRY', 'BTC', 'ETH', 'BNB'], key=len, reverse=True)
            for cq in common_quotes:
                if symbol.upper().endswith(cq) and len(symbol) > len(cq):
                    base = symbol[:-len(cq)]; quote = cq
                    if base: return base, quote
        if len(parts) == 2 and parts[0] and parts[1]: return parts[0].upper(), parts[1].upper()
        logger.warning(f"Sembol formatı anlaşılamadı: '{symbol}'")
        return None


    def execute_trade(self, signal: Dict[str, Any], user_trading_settings: Dict[str, Any]):
        active_user = self._active_user
        if not active_user:
            logger.error("[TradeManager] İşlem gerçekleştirilemedi: Aktif kullanıcı ayarlanmamış.")
            self.log_signal.emit("Hata: Aktif kullanıcı ayarlanmamış.", "ERROR")
            return

        signal_symbol_raw = "N/A" 
        signal_action_raw = "N/A" 
        try:
            signal_action = str(signal.get('action', '')).lower()
            signal_action_raw = signal_action
            signal_symbol = str(signal.get('symbol', '')).strip().upper() # Sembol her zaman büyük harf ve boşluksuz olmalı
            signal_symbol_raw = signal_symbol # Ham sinyal sembolünü loglama için sakla
            signal_side = str(signal.get('side', '')).lower() if signal.get('side') else None
            signal_order_type = str(signal.get('type', 'market')).lower()
            signal_sl_price_raw = signal.get('stop_loss') 
            signal_tp_price_raw = signal.get('take_profit') 

            if not signal_action or signal_action not in ['open', 'close']:
                raise ValueError(f"Geçersiz veya eksik eylem: '{signal_action}'")
            if not signal_symbol: # Sembol boş olamaz
                raise ValueError("Eksik veya geçersiz sembol.")
            if signal_action == "open" and (not signal_side or signal_side not in ['buy', 'sell']): # Al veya sat olmalı
                raise ValueError(f"Açma işlemi için geçersiz taraf: '{signal_side}'")
            
            logger.info(f"[{active_user}] Ayrıştırılmış sinyal alındı (TradeManager): Eylem='{signal_action.upper()}', Sembol='{signal_symbol}', Taraf='{str(signal_side).upper() if signal_side else 'N/A'}'")

        except ValueError as ve: # Beklenen hatalar
            logger.error(f"[{active_user}] Sinyal verisi hatası (TradeManager): {ve}. Sinyal: {signal}")
            self.log_signal.emit(f"Sinyal Hatası ({signal_symbol_raw}): {ve}", "ERROR")
            return
        except Exception as sig_err: # Beklenmedik hatalar
            logger.error(f"[{active_user}] Sinyal verisi işlenirken beklenmedik hata (TradeManager): {sig_err}. Sinyal: {signal}", exc_info=True)
            self.log_signal.emit(f"Kritik Sinyal Hatası ({signal_symbol_raw}): {sig_err}", "CRITICAL")
            return

        if not user_trading_settings or not isinstance(user_trading_settings, dict):
            logger.error(f"[{active_user}] Kullanıcı alım satım ayarları eksik/geçersiz ({signal_symbol}).")
            self.log_signal.emit(f"Ayar Hatası ({signal_symbol}): Kullanıcı işlem ayarları yok.", "ERROR")
            return
        try:
            leverage = int(user_trading_settings.get('default_leverage', 1)); leverage = max(1, leverage)
            margin_mode = str(user_trading_settings.get('default_margin_mode', 'ISOLATED')).upper()
            sl_perc = _to_decimal(user_trading_settings.get('stop_loss_percentage', '0.0')) or DECIMAL_ZERO
            tp_perc = _to_decimal(user_trading_settings.get('take_profit_percentage', '0.0')) or DECIMAL_ZERO
            amount_type = str(user_trading_settings.get('default_amount_type', 'fixed')).lower()
            amount_value = _to_decimal(user_trading_settings.get('default_amount_value', '0.0')) or DECIMAL_ZERO
            tsl_enabled = bool(user_trading_settings.get('tsl_enabled', False))
            tsl_activation_perc = _to_decimal(user_trading_settings.get('tsl_activation_percentage', '0.0')) or DECIMAL_ZERO
            tsl_callback_perc_key = 'tsl_distance_percent' 
            tsl_callback_perc = _to_decimal(user_trading_settings.get(tsl_callback_perc_key, '0.0')) or DECIMAL_ZERO

        except Exception as settings_err:
            logger.error(f"[{active_user}] Kullanıcı ayarları işlenirken hata (TradeManager): {settings_err} ({signal_symbol})", exc_info=True)
            self.log_signal.emit(f"Ayar Hatası ({signal_symbol}): {settings_err}", "ERROR")
            return

        if signal_action == "open":
            logger.info(f"[{active_user}] Pozisyon AÇMA işlemi ({signal_symbol} {str(signal_side).upper()}) başlatılıyor...")
            if not signal_side: 
                logger.error(f"[{active_user}] Açma işlemi için 'side' (yön) belirtilmemiş. İşlem iptal ({signal_symbol}).")
                self.log_signal.emit(f"Açma Hatası ({signal_symbol}): Yön Eksik", "ERROR")
                return

            # --- YENİ EKLENEN BÖLÜM BAŞLANGICI ---
            # Yeni pozisyon açmadan önce aynı sembol için mevcut açık pozisyonu kapat
            current_open_position_for_symbol = self.get_position_by_symbol_thread_safe(signal_symbol)
            if current_open_position_for_symbol:
                opposing_side = current_open_position_for_symbol.get('side', 'unknown').lower()
                # Sadece ters yönde bir pozisyonu kapatmaya çalışmayız, aynı semboldeki herhangi bir açık pozisyonu kapatırız.
                # Ancak yine de bir bilgilendirme logu ekleyebiliriz.
                logger.info(f"[{active_user}] '{signal_symbol}' için mevcut açık pozisyon bulundu (ID: {current_open_position_for_symbol.get('order_id')}, Yön: {opposing_side.upper()}). Yeni '{signal_side.upper()}' sinyali öncesi kapatılıyor...")
                self.log_signal.emit(f"Önceki Pozisyon Kapatılıyor ({signal_symbol} {opposing_side.upper()})", "INFO")
                
                closed_successfully = self.close_position_by_symbol(signal_symbol, reason=f"Yeni '{signal_side.upper()}' sinyali ({signal_symbol}) öncesi otomatik kapatma")

                if closed_successfully:
                    logger.info(f"[{active_user}] '{signal_symbol}' için kapatma emri gönderildi/işlendi. Kapanmanın teyidi bekleniyor...")
                    max_wait_time_seconds = 30  # Saniye cinsinden maksimum bekleme süresi
                    wait_interval_seconds = 0.5 # Saniye cinsinden kontrol aralığı
                    waited_time = 0
                    
                    # Pozisyonun listeden kalktığını kontrol et
                    while self.get_position_by_symbol_thread_safe(signal_symbol) is not None and waited_time < max_wait_time_seconds:
                        logger.debug(f"[{active_user}] '{signal_symbol}' pozisyonunun kapanması ve listeden kaldırılması bekleniyor... ({waited_time:.1f}s/{max_wait_time_seconds}s)")
                        time.sleep(wait_interval_seconds)
                        waited_time += wait_interval_seconds
                    
                    if self.get_position_by_symbol_thread_safe(signal_symbol) is None:
                        logger.info(f"[{active_user}] '{signal_symbol}' pozisyonu başarıyla kapatıldı ve takip listesinden kaldırıldığı teyit edildi.")
                        self.log_signal.emit(f"Önceki Pozisyon ({signal_symbol}) Kapatıldı - Başarılı", "INFO")
                    else:
                        # Bu durumda bile devam edebiliriz, ancak logda belirtiriz.
                        # Binance tarafında pozisyonun anlık durumu farklı olabilir, ya da self.open_positions güncellemesinde bir gecikme/sorun olabilir.
                        logger.warning(f"[{active_user}] '{signal_symbol}' pozisyonu {max_wait_time_seconds}s içinde takip listesinden kaldırılmadı/kapanmadı. Yeni pozisyon açmaya devam ediliyor (Binance tarafı kontrol edilmeli).")
                        self.log_signal.emit(f"Kapatma Teyit Zaman Aşımı ({signal_symbol})", "WARNING")
                else:
                    # close_position_by_symbol false döndürdüyse (örn: API hatası, emir gönderilemedi vs.)
                    logger.error(f"[{active_user}] '{signal_symbol}' için mevcut pozisyonu kapatma denemesi BAŞARISIZ oldu. Yeni pozisyon AÇILMAYACAK.")
                    self.log_signal.emit(f"Önceki Pozisyon Kapatılamadı ({signal_symbol}) - İşlem İptal", "ERROR")
                    return # Yeni pozisyonu açma, çünkü önceki kapatılamadı.
            else:
                logger.info(f"[{active_user}] '{signal_symbol}' için mevcut açık pozisyon bulunamadı. Doğrudan yeni pozisyon açılacak.")
            # --- YENİ EKLENEN BÖLÜM SONU ---

            if self.risk_manager and hasattr(self.risk_manager, 'can_open_new_position'):
                try:
                    can_trade, reason = self.risk_manager.can_open_new_position()
                    if not can_trade:
                        logger.warning(f"[{active_user}] RİSK ENGELİ ({signal_symbol} {signal_side.upper()}): {reason}")
                        self.log_signal.emit(f"Risk Engeli ({signal_symbol}): {reason}", "WARNING")
                        return
                except Exception as risk_check_err:
                    logger.error(f"[{active_user}] RiskManager.can_open_new_position çağrılırken hata: {risk_check_err} ({signal_symbol})", exc_info=True)
                    self.log_signal.emit(f"Risk Kontrol Hatası ({signal_symbol}): {risk_check_err}", "ERROR")
                    return 

            entry_price_estimate_raw = self.exchange_api.get_symbol_price(signal_symbol)
            entry_price_dec = _to_decimal(entry_price_estimate_raw)
            if entry_price_dec is None or entry_price_dec <= DECIMAL_ZERO:
                logger.error(f"[{active_user}] Geçerli giriş fiyatı alınamadı ({signal_symbol}: {entry_price_estimate_raw}). İşlem iptal.")
                self.log_signal.emit(f"Fiyat Alınamadı ({signal_symbol})", "ERROR"); return
            logger.info(f"[{active_user}] '{signal_symbol}' tahmini giriş fiyatı: {entry_price_dec:.8f}")

            stop_loss_price_dec: Optional[Decimal] = None
            if signal_sl_price_raw is not None and isinstance(signal_sl_price_raw, (float, int, str)) and _to_decimal(signal_sl_price_raw) and _to_decimal(signal_sl_price_raw) > DECIMAL_ZERO: # str de kabul et
                stop_loss_price_dec = _to_decimal(signal_sl_price_raw)
            elif sl_perc > DECIMAL_ZERO and utils and hasattr(utils, 'calculate_stop_loss_price'): 
                stop_loss_price_dec = utils.calculate_stop_loss_price(entry_price_dec, sl_perc, signal_side)
            
            if stop_loss_price_dec is None: 
                logger.error(f"[{active_user}] Stop Loss fiyatı belirlenemedi ({signal_symbol}). İşlem iptal.")
                self.log_signal.emit(f"SL Belirlenemedi ({signal_symbol})", "ERROR"); return
            final_stop_loss_price = self._adjust_precision(signal_symbol, stop_loss_price_dec, 'price')
            if final_stop_loss_price is None or \
               (signal_side == 'buy' and final_stop_loss_price >= entry_price_dec) or \
               (signal_side == 'sell' and final_stop_loss_price <= entry_price_dec):
                logger.error(f"[{active_user}] SL ({final_stop_loss_price or stop_loss_price_dec}) giriş ({entry_price_dec}) ile hatalı/ayarlanamadı. İşlem iptal.")
                self.log_signal.emit(f"Hatalı SL ({signal_symbol})", "ERROR"); return
            logger.info(f"[{active_user}] Kullanılacak SL: {final_stop_loss_price:.8f}")
            
            take_profit_price_dec: Optional[Decimal] = None
            if signal_tp_price_raw is not None and isinstance(signal_tp_price_raw, (float, int, str)) and _to_decimal(signal_tp_price_raw) and _to_decimal(signal_tp_price_raw) > DECIMAL_ZERO: # str de kabul et
                take_profit_price_dec = _to_decimal(signal_tp_price_raw)
            elif tp_perc > DECIMAL_ZERO and utils and hasattr(utils, 'calculate_take_profit_price'):
                take_profit_price_dec = utils.calculate_take_profit_price(entry_price_dec, tp_perc, signal_side)
            
            final_take_profit_price: Optional[Decimal] = None
            if take_profit_price_dec:
                adjusted_tp = self._adjust_precision(signal_symbol, take_profit_price_dec, 'price')
                if adjusted_tp and adjusted_tp > DECIMAL_ZERO and \
                   ((signal_side == 'buy' and adjusted_tp > entry_price_dec) or \
                    (signal_side == 'sell' and adjusted_tp < entry_price_dec)):
                    final_take_profit_price = adjusted_tp
                    logger.info(f"[{active_user}] Kullanılacak TP: {final_take_profit_price:.8f}")
                else:
                    logger.warning(f"[{active_user}] Hesaplanan TP ({adjusted_tp or take_profit_price_dec}) geçersiz veya ayarlanamadı. TP kullanılmayacak.")
            
            final_amount_base: Optional[Decimal] = None
            amount_source = "Belirlenemedi"
            
            base_c, quote_c = self._get_currencies_from_symbol(signal_symbol) or (None, None)
            if not quote_c: 
                logger.error(f"[{active_user}] Miktar için quote para birimi belirlenemedi ({signal_symbol}). İşlem iptal.")
                self.log_signal.emit(f"Para Birimi Hatası ({signal_symbol})", "ERROR")
                return

            current_quote_balance_raw = self.exchange_api.get_balance(quote_c)
            current_quote_balance_dec = _to_decimal(current_quote_balance_raw)

            if current_quote_balance_dec is None or current_quote_balance_dec < DECIMAL_ZERO: # Bakiye sıfır olabilir ama None olmamalı
                logger.error(f"[{active_user}] Geçerli '{quote_c}' bakiyesi alınamadı ({current_quote_balance_raw}). İşlem iptal.")
                self.log_signal.emit(f"Bakiye Hatası ({signal_symbol}, {quote_c})", "ERROR")
                return
            
            is_currently_demo_mode = self.exchange_api.is_demo_mode()
            logger.info(f"[{active_user}] Miktar hesaplama için '{quote_c}' bakiyesi: {current_quote_balance_dec:.8f} (Demo Modu: {is_currently_demo_mode})")

            if self.risk_manager and hasattr(self.risk_manager, 'calculate_position_size') and \
               hasattr(self.risk_manager, 'max_risk_per_trade_percent') and self.risk_manager.max_risk_per_trade_percent > DECIMAL_ZERO:
                calc_size_base_from_risk = self.risk_manager.calculate_position_size(
                    symbol=signal_symbol,
                    entry_price=entry_price_dec,
                    stop_loss_price=final_stop_loss_price, # Ayarlanmış SL fiyatını kullan
                    quote_currency_balance=current_quote_balance_dec,
                    is_demo_mode=is_currently_demo_mode 
                )
                if calc_size_base_from_risk and calc_size_base_from_risk > DECIMAL_ZERO:
                    adj_amount = self._adjust_precision(signal_symbol, calc_size_base_from_risk, 'amount')
                    if adj_amount and adj_amount > DECIMAL_ZERO:
                        final_amount_base = adj_amount
                        amount_source = f"Risk Yön. (%{self.risk_manager.max_risk_per_trade_percent:.2f})"
                        logger.info(f"[{active_user}] RiskManager'dan poz. büyüklüğü (ayarlı): {final_amount_base:.8f} {base_c or ''}")
            
            if final_amount_base is None or final_amount_base <= DECIMAL_ZERO: # Risk managerdan gelmediyse veya sıfırsa
                logger.info(f"[{active_user}] RiskManager'dan miktar gelmedi/kullanılmıyor veya sıfır. Kullanıcı ayarlarına göre hesaplanacak.")
                temp_amount_calc = None
                if amount_type == 'fixed': # Sabit baz varlık miktarı
                    temp_amount_calc = amount_value 
                    amount_source = f"Sabit Miktar ({amount_value} {base_c or ''})"
                elif amount_type == 'quote_fixed': # Sabit quote varlık değeri
                     if entry_price_dec > DECIMAL_ZERO:
                        temp_amount_calc = amount_value / entry_price_dec 
                        amount_source = f"Sabit Quote Değeri ({amount_value} {quote_c or ''})"
                elif amount_type == 'percentage': # Bakiye yüzdesi (kaldıraçlı)
                    if current_quote_balance_dec > DECIMAL_ZERO and entry_price_dec > DECIMAL_ZERO:
                        quote_to_use_for_trade = (amount_value / DECIMAL_HUNDRED) * current_quote_balance_dec * Decimal(str(leverage))
                        temp_amount_calc = quote_to_use_for_trade / entry_price_dec
                        amount_source = f"Bakiye %{amount_value} x Kaldıraç {leverage} ({quote_to_use_for_trade:.2f} {quote_c or ''})"
                
                if temp_amount_calc and temp_amount_calc > DECIMAL_ZERO:
                    adjusted_from_settings = self._adjust_precision(signal_symbol, temp_amount_calc, 'amount')
                    if adjusted_from_settings and adjusted_from_settings > DECIMAL_ZERO:
                        final_amount_base = adjusted_from_settings
                        logger.info(f"[{active_user}] Kullanıcı ayarlarından poz. büyüklüğü (ayarlı): {final_amount_base:.8f} {base_c or ''} ({amount_source})")
                    else:
                        logger.warning(f"[{active_user}] Ayarlardan miktar ({temp_amount_calc}) ayarlanamadı/sıfır oldu.")
                else:
                     logger.warning(f"[{active_user}] Kullanıcı ayarlarından miktar hesaplanamadı (type: {amount_type}, value: {amount_value}).")

            if is_currently_demo_mode and final_amount_base and final_amount_base > DECIMAL_ZERO:
                logger.info(f"[{active_user}] DEMO MODU - TradeManager Son Kontrol (İstenen Miktar={final_amount_base:.8f} {base_c or ''}):")
                current_intended_unleveraged_value_quote = final_amount_base * entry_price_dec
                logger.info(f"[{active_user}]   Demo: İstenen miktarın ({final_amount_base:.8f} {base_c or ''}) kaldıraçsız değeri: {current_intended_unleveraged_value_quote:.2f} {quote_c or ''}")
                logger.info(f"[{active_user}]   Demo: Mevcut {quote_c or ''} demo bakiyesi: {current_quote_balance_dec:.2f}")
                logger.info(f"[{active_user}]   Demo: Kullanıcı ayarlarından gelen kaldıraç: {leverage}x")

                max_allowed_leveraged_position_value_quote = current_quote_balance_dec * Decimal(str(leverage))
                logger.info(f"[{active_user}]   Demo: İzin verilen maksimum kaldıraçlı pozisyon değeri (bakiye * kaldıraç): {max_allowed_leveraged_position_value_quote:.2f} {quote_c or ''}")

                if current_intended_unleveraged_value_quote > max_allowed_leveraged_position_value_quote:
                    logger.warning(
                        f"[{active_user}]   Demo Uyarısı: İstenen pozisyonun kaldıraçsız değeri ({current_intended_unleveraged_value_quote:.2f} {quote_c or ''}) "
                        f"> izin verilen maksimum kaldıraçlı demo pozisyon değerini ({max_allowed_leveraged_position_value_quote:.2f} {quote_c or ''}) aşıyor. "
                        f"Miktar, maksimum kaldıraçlı demo pozisyon değerine göre düzeltilecek."
                    )
                    
                    if entry_price_dec > DECIMAL_ZERO:
                        corrected_base_amount_for_leveraged_cap = max_allowed_leveraged_position_value_quote / entry_price_dec
                        adj_corrected_base = self._adjust_precision(signal_symbol, corrected_base_amount_for_leveraged_cap, 'amount')
                        
                        if adj_corrected_base and adj_corrected_base > DECIMAL_ZERO:
                            if adj_corrected_base < final_amount_base: 
                                logger.info(
                                    f"[{active_user}]   Demo Miktarı Düzeltildi (Kaldıraçlı Limit): "
                                    f"Önceki Miktar={final_amount_base:.8f}, Yeni Miktar={adj_corrected_base:.8f} {base_c or ''}"
                                )
                                self.log_signal.emit(
                                    f"Demo Miktar Düzeltildi ({signal_symbol}): {adj_corrected_base:.8f} (Kaldıraçlı Limit)", "WARNING"
                                )
                            else: 
                                logger.info(
                                    f"[{active_user}]   Demo Miktarı (Kaldıraçlı Limit sonrası): {adj_corrected_base:.8f} {base_c or ''} (Önceki: {final_amount_base:.8f})"
                                )
                            final_amount_base = adj_corrected_base
                        else:
                            logger.error(
                                f"[{active_user}]   Demo Düzeltme (Kaldıraçlı): Ayarlanan yeni miktar ({adj_corrected_base}) sıfır/geçersiz. "
                                f"İşlem miktarı sıfıra ayarlandı."
                            )
                            final_amount_base = DECIMAL_ZERO 
                    else:
                        logger.error(
                            f"[{active_user}]   Demo Düzeltme (Kaldıraçlı): Giriş fiyatı sıfır. Miktar sıfırlandı."
                        )
                        final_amount_base = DECIMAL_ZERO

            if final_amount_base is None or final_amount_base <= DECIMAL_ZERO:
                logger.error(f"[{active_user}] Nihai işlem büyüklüğü sıfır/negatif ({final_amount_base}). Sembol: {signal_symbol}. İşlem iptal.")
                self.log_signal.emit(f"Miktar Hesaplanamadı ({signal_symbol}) - İptal", "ERROR")
                return
            
            logger.info(f"[{active_user}] Nihai işlem büyüklüğü (baz varlık): {final_amount_base:.8f} {base_c or ''} (Kaynak: {amount_source})")

            if hasattr(self.exchange_api, 'set_leverage') and callable(getattr(self.exchange_api, 'set_leverage')):
                self.exchange_api.set_leverage(signal_symbol, leverage) 
            else: logger.warning(f"[{active_user}] Exchange API ({type(self.exchange_api)}) set_leverage metoduna sahip değil.")

            if hasattr(self.exchange_api, 'set_margin_mode') and callable(getattr(self.exchange_api, 'set_margin_mode')):
                self.exchange_api.set_margin_mode(signal_symbol, margin_mode) 
            else: logger.warning(f"[{active_user}] Exchange API ({type(self.exchange_api)}) set_margin_mode metoduna sahip değil.")

            order_price_for_api: Optional[float] = None 
            if signal_order_type == 'limit':
                limit_price_from_signal_raw = signal.get('price') 
                limit_price_to_use_dec = _to_decimal(limit_price_from_signal_raw) if limit_price_from_signal_raw is not None else entry_price_dec
                
                adjusted_limit_price_dec = self._adjust_precision(signal_symbol, limit_price_to_use_dec, 'price')
                if adjusted_limit_price_dec is None or adjusted_limit_price_dec <= DECIMAL_ZERO:
                    logger.error(f"[{active_user}] Limit emir fiyatı ({limit_price_to_use_dec}) ayarlanamadı/geçersiz ({signal_symbol}). İşlem iptal.")
                    self.log_signal.emit(f"Limit Fiyatı Ayarlanamadı ({signal_symbol})", "ERROR"); return
                order_price_for_api = float(adjusted_limit_price_dec)

            order_params: Dict[str, Any] = {} 
            # Binance özelinde, pozisyon tarafı (positionSide) hedge modda gerekebilir.
            # Eğer API wrapper'ınız bunu params içinde destekliyorsa veya özel bir metodu varsa,
            # burada veya exchange_api.create_order içinde ele alınmalıdır.
            # Örnek: if self.exchange_api.is_hedge_mode_active(symbol): order_params['positionSide'] = 'LONG' if signal_side == 'buy' else 'SHORT'
            
            order_to_place: Dict[str, Any] = {
                'symbol': signal_symbol,
                'type': signal_order_type,
                'side': signal_side, # 'buy' veya 'sell'
                'amount': float(final_amount_base), 
                'params': order_params
            }
            if signal_order_type == 'limit' and order_price_for_api is not None:
                order_to_place['price'] = order_price_for_api

            logger.info(f"[{active_user}] Borsa API'sine AÇMA emri gönderiliyor: {order_to_place}")
            try:
                order_response = self.exchange_api.create_order(**order_to_place) 
                
                if not order_response or not isinstance(order_response, dict) or not order_response.get('id'):
                    logger.error(f"[{active_user}] Emir oluşturma başarısız veya geçersiz API yanıtı ({signal_symbol}). Yanıt: {order_response}")
                    self.log_signal.emit(f"Emir Hatası ({signal_symbol}): Geçersiz API Yanıtı", "ERROR")
                    return

                order_id_str = str(order_response['id'])
                order_status = str(order_response.get('status', 'unknown')).lower()
                filled_amount_raw = order_response.get('filled')
                filled_amount_dec = _to_decimal(filled_amount_raw) or DECIMAL_ZERO
                
                avg_price_raw = order_response.get('average')
                avg_price_dec = _to_decimal(avg_price_raw)
                if signal_order_type == 'market' and (avg_price_dec is None or avg_price_dec <= DECIMAL_ZERO): # Market emri için ortalama fiyat gelmediyse
                    avg_price_dec = entry_price_dec # Tahmini giriş fiyatını kullan
                    logger.info(f"[{active_user}] Market emri için API'den ort. fiyat gelmedi/geçersiz, tahmini giriş fiyatı ({entry_price_dec}) kullanılacak.")

                logger.info(f"[{active_user}] AÇMA Emir ID: {order_id_str}, Durum: {order_status.upper()}, Dolan: {filled_amount_dec:.8f}, Ort.Fiyat: {(avg_price_dec or DECIMAL_ZERO):.8f}")
                self.log_signal.emit(f"Emir Gönderildi: {signal_symbol} {signal_side.upper()} ID:{order_id_str} Durum:{order_status.upper()}", "INFO")

                if order_status in ['closed', 'open', 'partially_filled'] and filled_amount_dec > DECIMAL_ZERO: # 'open' limit emirleri de takip edilebilir
                    actual_entry_price_for_tracking = avg_price_dec if avg_price_dec and avg_price_dec > DECIMAL_ZERO else entry_price_dec
                    if actual_entry_price_for_tracking is None or actual_entry_price_for_tracking <= DECIMAL_ZERO:
                        logger.error(f"[{active_user}] Pozisyon takibi için geçerli giriş fiyatı belirlenemedi ({actual_entry_price_for_tracking}). ID: {order_id_str}. Takip edilmeyecek.")
                        self.log_signal.emit(f"Takip Fiyat Hatası ({signal_symbol}, ID: {order_id_str})", "ERROR")
                        return

                    tsl_settings_dict = {
                        'enabled': tsl_enabled,
                        'activation_percentage': tsl_activation_perc, 
                        'callback_percentage': tsl_callback_perc   
                    }
                    self._track_position(
                        order_response=order_response, symbol=signal_symbol, side=signal_side,
                        entry_price=actual_entry_price_for_tracking, # Decimal
                        filled_amount=filled_amount_dec,             # Decimal
                        stop_loss_price=final_stop_loss_price,       # Decimal or None
                        take_profit_price=final_take_profit_price,   # Decimal or None
                        tsl_settings=tsl_settings_dict, leverage=leverage
                    )
                    if self.risk_manager and hasattr(self.risk_manager, 'notify_position_opened'):
                        self.risk_manager.notify_position_opened(order_id_str)
                
                elif order_status == 'rejected' or filled_amount_dec == DECIMAL_ZERO : 
                     logger.warning(f"[{active_user}] Emir (ID: {order_id_str}) durumu '{order_status}' veya dolan miktar sıfır. Takip edilmiyor.")
                     reason_from_api = "API'den detaylı red nedeni alınamadı."
                     if isinstance(order_response.get('info'), dict):
                         reason_from_api = order_response['info'].get('msg') or \
                                           order_response['info'].get('message') or \
                                           order_response['info'].get('reason') or \
                                           str(order_response['info']) 
                     elif isinstance(order_response.get('info'), str):
                          reason_from_api = order_response['info']
                     logger.warning(f"[{active_user}] API Red/DolanSıfır Nedeni (ID: {order_id_str}): {reason_from_api}")
                     self.log_signal.emit(f"Emir Red/DolanSıfır ({signal_symbol}): {reason_from_api}", "ERROR")
                elif order_status == 'open': # Limit emir açıldı ama dolmadı
                     logger.info(f"[{active_user}] Limit emir (ID: {order_id_str}) açıldı ancak henüz dolmadı. Dolduğunda _track_position çağrılmalı (ileride eklenebilir bir özellik).")
                     # İPUCU: Limit emirlerin dolumunu periyodik olarak kontrol edip _track_position'ı çağıran bir mekanizma eklenebilir.
                else: 
                     logger.warning(f"[{active_user}] Emir (ID: {order_id_str}) durumu '{order_status}', dolan miktar: {filled_amount_dec}. Beklenmedik durum, takip edilmiyor.")

            except (InsufficientFunds, InvalidOrder, ExchangeError, NetworkError, NotSupported) as api_err:
                error_message_from_api = str(api_err)
                try: # CCXT bazen hatayı JSON string içinde döndürür
                    import re; import json
                    json_match = re.search(r"({.*})", error_message_from_api)
                    if json_match:
                        error_detail = json.loads(json_match.group(1))
                        if isinstance(error_detail, dict):
                           error_message_from_api = error_detail.get('msg', error_detail.get('message', str(error_detail)))
                except: pass # JSON parse hatası olursa orijinal mesajı kullan

                logger.error(f"[{active_user}] Emir API Hatası ({signal_symbol}) - {type(api_err).__name__}: {error_message_from_api}", exc_info=False) # exc_info=False CCXT hataları için daha temiz log sağlar
                self.log_signal.emit(f"Emir Hatası ({signal_symbol}): {error_message_from_api}", "ERROR")
            except Exception as e:
                logger.critical(f"[{active_user}] Emir oluşturulurken kritik hata ({signal_symbol}): {e}", exc_info=True)
                self.log_signal.emit(f"Kritik Emir Hatası ({signal_symbol}): {type(e).__name__}", "CRITICAL")

        elif signal_action == "close":
            logger.info(f"[{active_user}] Pozisyon KAPATMA işlemi ({signal_symbol}) başlatılıyor...")
            close_reason = f"Sinyal: {signal.get('signal_id', 'Harici Kapatma Sinyali')}" # Sinyal ID'sini kullan
            if not self.close_position_by_symbol(signal_symbol, reason=close_reason):
                # close_position_by_symbol zaten loglama ve sinyal gönderme yapar.
                logger.error(f"[{active_user}] '{signal_symbol}' için sinyal ile pozisyon kapatma BAŞARISIZ oldu (detaylar için önceki loglara bakın).")
                # Burada ek bir log_signal.emit yapmaya gerek yok, close_position_by_symbol zaten yapar.
        else:
            logger.error(f"[{active_user}] Bilinmeyen sinyal eylemi: '{signal_action}'. Sinyal: {signal}")
            self.log_signal.emit(f"Bilinmeyen Eylem ({signal_symbol}): {signal_action}", "ERROR")

    def _track_position(self, order_response: Dict[str, Any], symbol: str, side: str,
                        entry_price: Decimal, filled_amount: Decimal, # Tipler Decimal
                        stop_loss_price: Optional[Decimal], take_profit_price: Optional[Decimal], # Tipler Decimal
                        tsl_settings: Dict[str, Any], leverage: int):
        # Bu metodun içeriği önceki gibi kalabilir, sadece gelen Decimal tiplerini doğru kullandığından emin olun.
        order_id_str = str(order_response['id'])
        timestamp_ms = order_response.get('timestamp', int(time.time() * 1000))

        with self._positions_lock:
            if order_id_str in self.open_positions:
                logger.warning(f"[{self._active_user or 'Sistem'}] Pozisyon ID '{order_id_str}' zaten takip ediliyor. Veriler güncelleniyor.")

            position_data: Dict[str, Any] = {
                'order_id': order_id_str, 'user': self._active_user, 'symbol': symbol,
                'side': side.lower(), 'leverage': leverage,
                'amount': filled_amount, 'entry_price': entry_price, # Decimal olarak sakla
                'sl_price': stop_loss_price, 'tp_price': take_profit_price, # Decimal olarak sakla
                'timestamp': timestamp_ms, 'status': 'open', # Başlangıç durumu 'open'
                'tsl_enabled': tsl_settings.get('enabled', False),
                'tsl_activation_percentage': tsl_settings.get('activation_percentage', DECIMAL_ZERO),
                'tsl_callback_percentage': tsl_settings.get('callback_percentage', DECIMAL_ZERO),
                'tsl_activated': False, 'tsl_stop_price': None,
                # TSL için tepe/dip fiyatlarını başlangıçta None yapmak daha doğru olabilir
                'tsl_highest_price': None, # entry_price if side.lower() == 'buy' else None,
                'tsl_lowest_price': None, # entry_price if side.lower() == 'sell' else None,
                'api_order_details': copy.deepcopy(order_response) # API'den gelen ham yanıtı sakla
            }
            self.open_positions[order_id_str] = position_data
            # Loglama için formatlı stringler
            sl_str = f"{stop_loss_price:.8f}" if stop_loss_price else "Yok"
            tp_str = f"{take_profit_price:.8f}" if take_profit_price else "Yok"
            tsl_info = f"Aktif={position_data['tsl_enabled']}, Act%={position_data['tsl_activation_percentage']:.2f}, Call%={position_data['tsl_callback_percentage']:.2f}"
            logger.info(f"[{self._active_user or 'Sistem'}] YENİ/GÜNCELLENMİŞ POZİSYON TAKİBE ALINDI: ID={order_id_str}, {symbol} {side.upper()} @ {entry_price:.8f}, Miktar={filled_amount:.8f}, SL={sl_str}, TP={tp_str}, TSL={tsl_info}")
            self.log_signal.emit(f"Pozisyon Takip: {symbol} ID:{order_id_str}", "INFO")


    def close_position_by_symbol(self, symbol: str, reason: str = "Signal: Close") -> bool:
        """
        Belirtilen sembol için açık olan ilk pozisyonu (varsa) kapatır.
        """
        active_user = self._active_user or "Sistem" # Eğer None ise "Sistem" kullan
        order_id_to_close: Optional[str] = None
        position_info_log: str = "Bulunamadı"

        with self._positions_lock: # Kilitli erişim
            for pid, pdata in self.open_positions.items():
                # Sembol eşleşmeli VE pozisyon durumu 'open' olmalı
                if pdata.get('symbol') == symbol and pdata.get('status') == 'open':
                    order_id_to_close = pid
                    position_info_log = f"ID={pid}, Yön={pdata.get('side','?')}, Miktar={pdata.get('amount',Decimal('0')):.8f}" # Miktar için fallback
                    logger.info(f"[{active_user}] '{symbol}' için kapatılacak açık pozisyon bulundu: {position_info_log}")
                    break # İlk bulunanı al (genellikle sembol başına bir pozisyon olur)
        
        if order_id_to_close:
            logger.info(f"[{active_user}] close_position_by_id çağrılıyor. ID: {order_id_to_close}, Sembol: {symbol}, Sebep: '{reason}'")
            # close_position_by_id zaten loglama ve GUI sinyali gönderme işlemlerini yapacaktır.
            return self.close_position_by_id(order_id_to_close, reason=reason)
        else:
            logger.warning(f"[{active_user}] Kapatma isteği: '{symbol}' sembolünde 'open' durumda pozisyon bulunamadı.")
            self.log_signal.emit(f"Kapatma Hatası ({symbol}): Açık Pozisyon Yok", "WARNING")
            return False

    def close_position_by_id(self, order_id: str, reason: str = "Bilinmiyor") -> bool:
        order_id_str = str(order_id)
        position_to_close: Optional[Dict[str, Any]] = None
        active_user = self._active_user or "Sistem"

        with self._positions_lock:
            if order_id_str in self.open_positions:
                if self.open_positions[order_id_str].get('status') == 'open':
                    position_to_close = copy.deepcopy(self.open_positions[order_id_str])
                    self.open_positions[order_id_str]['status'] = 'closing'
                    logger.debug(f"[{active_user}] Pozisyon durumu 'closing' olarak ayarlandı (ID: {order_id_str}).")
                else:
                    logger.warning(f"[{active_user}] Kapatma isteği (ID: {order_id_str}) ama pozisyon 'open' değil, durumu: '{self.open_positions[order_id_str].get('status')}'.")
                    return False
            else:
                logger.error(f"[{active_user}] Kapatılacak pozisyon (ID: {order_id_str}) takip listesinde bulunamadı.")
                return False
        
        if not position_to_close:
            return False

        symbol = position_to_close.get('symbol')
        side_of_open_position = position_to_close.get('side')
        amount_to_close_dec_raw = position_to_close.get('amount')
        amount_to_close_dec = _to_decimal(amount_to_close_dec_raw) if isinstance(amount_to_close_dec_raw, str) else amount_to_close_dec_raw

        entry_price_dec_saved_raw = position_to_close.get('entry_price')
        entry_price_dec_saved = _to_decimal(entry_price_dec_saved_raw) if isinstance(entry_price_dec_saved_raw, str) else entry_price_dec_saved_raw
        
        open_timestamp_saved = position_to_close.get('timestamp')
        leverage_saved = position_to_close.get('leverage', 1)

        if not symbol or not side_of_open_position or amount_to_close_dec is None or amount_to_close_dec <= DECIMAL_ZERO:
            logger.error(f"[{active_user}] Pozisyon (ID: {order_id_str}) kapatma verisi eksik/geçersiz. Kapatma iptal.")
            with self._positions_lock:
                 if order_id_str in self.open_positions and self.open_positions[order_id_str].get('status') == 'closing':
                      self.open_positions[order_id_str]['status'] = 'close_failed'
            self.log_signal.emit(f"Pozisyon Kapatma Hatası ({symbol or 'N/A'}, ID: {order_id_str}): Veri Eksik", "ERROR")
            return False

        logger.info(f"[{active_user}] Pozisyon (ID={order_id_str}) için kapatma emri gönderiliyor: {symbol} {side_of_open_position.upper()}, Miktar={amount_to_close_dec:.8f}, Sebep='{reason}'")
        close_side = 'sell' if side_of_open_position == 'buy' else 'buy'

        try:
            # --- DEĞİŞİKLİK BURADA ---
            # Binance Futures'ta pozisyonu sadece azaltmak (kapatmak) için reduceOnly bayrağı
            # genellikle zorunludur veya şiddetle tavsiye edilir.
            reduce_only_params = {'reduceOnly': True}
            # --- DEĞİŞİKLİK SONU ---

            amount_to_close_float = float(amount_to_close_dec)

            closing_order_response = self.exchange_api.create_order(
                symbol=symbol, type='market', side=close_side,
                amount=amount_to_close_float, params=reduce_only_params
            )

            if not closing_order_response or not isinstance(closing_order_response, dict) or not closing_order_response.get('id'):
                logger.error(f"[{active_user}] Kapatma emri başarısız veya geçersiz yanıt (ID: {order_id_str}). API Yanıtı: {closing_order_response}")
                with self._positions_lock:
                     if order_id_str in self.open_positions and self.open_positions[order_id_str].get('status') == 'closing':
                          self.open_positions[order_id_str]['status'] = 'close_failed'
                self.log_signal.emit(f"Pozisyon Kapatma Hatası ({symbol}, ID: {order_id_str}): API Yanıtı Yok", "ERROR")
                return False

            closing_order_id = str(closing_order_response['id'])
            closing_status = str(closing_order_response.get('status', 'unknown')).lower()
            filled_on_close_raw = closing_order_response.get('filled')
            filled_on_close = _to_decimal(filled_on_close_raw) if filled_on_close_raw is not None else DECIMAL_ZERO; filled_on_close = filled_on_close or DECIMAL_ZERO
            
            exit_price_raw = closing_order_response.get('average')
            exit_price = _to_decimal(exit_price_raw)
            if exit_price is None or exit_price <= DECIMAL_ZERO :
                logger.warning(f"[{active_user}] Kapatma emrinden çıkış fiyatı (average) alınamadı (ID: {closing_order_id}). Güncel fiyat kullanılacak.")
                current_market_price_raw = self.exchange_api.get_symbol_price(symbol)
                exit_price = _to_decimal(current_market_price_raw)
                if exit_price is None or exit_price <= DECIMAL_ZERO:
                     logger.error(f"[{active_user}] Pozisyon kapatma için güncel piyasa fiyatı da alınamadı ({symbol}). PNL hesaplanamayabilir.")

            fee_info = closing_order_response.get('fee', {})
            commission_raw = fee_info.get('cost') if isinstance(fee_info, dict) else None
            commission = _to_decimal(commission_raw) if commission_raw is not None else DECIMAL_ZERO; commission = commission or DECIMAL_ZERO
            
            close_timestamp = closing_order_response.get('timestamp', int(time.time()*1000))

            logger.info(f"[{active_user}] KAPATMA Emri Yanıtı (Pozisyon ID: {order_id_str}, Emir ID: {closing_order_id}): Durum={closing_status.upper()}, Dolan={filled_on_close:.8f}, Çıkış Fiyatı={(exit_price or DECIMAL_ZERO):.8f}, Komisyon={commission:.8f}")
            
            if closing_status == 'closed' and filled_on_close >= (amount_to_close_dec * Decimal('0.99')):
                removed_pos_data = None
                with self._positions_lock:
                    if order_id_str in self.open_positions and self.open_positions[order_id_str].get('status') == 'closing':
                        removed_pos_data = self.open_positions.pop(order_id_str)
                        logger.info(f"[{active_user}] Pozisyon (ID: {order_id_str}) takip listesinden başarıyla kaldırıldı.")
                    else:
                        logger.warning(f"[{active_user}] Kapatılan pozisyon (ID: {order_id_str}) listeden kaldırılırken bulunamadı veya durumu 'closing' değil.")
                
                if self.database_manager and removed_pos_data and entry_price_dec_saved and exit_price:
                    gross_pnl, net_pnl = None, None
                    if utils and hasattr(utils, 'calculate_pnl'):
                        gross_pnl = utils.calculate_pnl(entry_price_dec_saved, exit_price, filled_on_close, side_of_open_position)
                        if gross_pnl is not None:
                            net_pnl = gross_pnl - commission
                    
                    trade_data_for_db = {
                        "user": active_user, "order_id": order_id_str, "symbol": symbol,
                        "side": side_of_open_position, "amount": float(filled_on_close),
                        "entry_price": float(entry_price_dec_saved), "exit_price": float(exit_price),
                        "open_timestamp": open_timestamp_saved, "close_timestamp": close_timestamp,
                        "leverage": leverage_saved, "fee": float(commission),
                        "net_pnl": float(net_pnl) if net_pnl is not None else None,
                        "gross_pnl": float(gross_pnl) if gross_pnl is not None else None,
                        "close_order_id": closing_order_id, "close_reason": reason,
                        "exchange": getattr(self.exchange_api, 'exchange_name', type(self.exchange_api).__name__)
                    }
                    if hasattr(self.database_manager, 'save_closed_trade'):
                        if not self.database_manager.save_closed_trade(active_user, trade_data_for_db):
                            logger.error(f"[{active_user}] Kapanan işlem (ID: {order_id_str}) veritabanına kaydedilemedi.")
                    else: logger.error("DatabaseManager'da save_closed_trade metodu yok.")

                    if self.risk_manager and hasattr(self.risk_manager, 'update_daily_pnl') and net_pnl is not None:
                        self.risk_manager.update_daily_pnl(net_pnl)
                
                if self.risk_manager and hasattr(self.risk_manager, 'notify_position_closed'):
                     self.risk_manager.notify_position_closed(order_id_str)

                self.log_signal.emit(f"Pozisyon Kapatıldı: {symbol} ID:{order_id_str} Sebep:{reason}", "INFO")
                return True
            else:
                 logger.warning(f"[{active_user}] Kapatma emri (Pozisyon ID: {order_id_str}, Emir ID: {closing_order_id}) tam dolmadı/durumu '{closing_status}'. Pozisyon durumu 'close_failed' yapılıyor.")
                 with self._positions_lock:
                      if order_id_str in self.open_positions and self.open_positions[order_id_str].get('status') == 'closing':
                          self.open_positions[order_id_str]['status'] = 'close_failed'
                          self.open_positions[order_id_str]['closing_order_id_failed'] = closing_order_id
                 self.log_signal.emit(f"Pozisyon Kapatma Hatası ({symbol}, ID: {order_id_str}): Emir Tam Dolmadı/Durum '{closing_status}'", "ERROR")
                 return False

        except (InsufficientFunds, InvalidOrder, ExchangeError, NetworkError, NotSupported) as api_err:
             logger.error(f"[{active_user}] Pozisyon kapatılırken API Hatası (ID: {order_id_str}, Sembol: {symbol}) - {type(api_err).__name__}: {api_err}", exc_info=False)
             with self._positions_lock:
                  if order_id_str in self.open_positions and self.open_positions[order_id_str].get('status') == 'closing':
                       self.open_positions[order_id_str]['status'] = 'close_failed'
             self.log_signal.emit(f"Pozisyon Kapatma Hatası ({symbol}, ID: {order_id_str}): {type(api_err).__name__}", "ERROR")
             return False
        except Exception as e:
            logger.error(f"[{active_user}] Pozisyon (ID: {order_id_str}, Sembol: {symbol}) kapatılırken beklenmedik hata: {e}", exc_info=True)
            with self._positions_lock:
                  if order_id_str in self.open_positions and self.open_positions[order_id_str].get('status') == 'closing':
                       self.open_positions[order_id_str]['status'] = 'close_failed'
            self.log_signal.emit(f"Pozisyon Kapatma Kritik Hatası ({symbol}, ID: {order_id_str}): {type(e).__name__}", "CRITICAL")
            return False

    def check_and_close_positions(self):
        # Bu metodun içeriği önceki gibi kalabilir (SL/TP/TSL kontrolü)
        # Sadece _to_decimal gibi yardımcıları doğru kullandığından emin olun.
        # ...
        active_user = self._active_user or "Sistem"
        positions_to_check: Dict[str, Dict[str, Any]] = {}
        with self._positions_lock:
            positions_to_check = {oid: pdata.copy() for oid, pdata in self.open_positions.items() if pdata.get('status') == 'open'}

        if not positions_to_check: return
        symbols_to_check = list(set(pos['symbol'] for pos in positions_to_check.values() if pos.get('symbol')))
        if not symbols_to_check: return

        current_prices_float: Dict[str, Optional[float]] = self._fetch_current_prices(symbols_to_check)
        if not current_prices_float: logger.warning(f"[{active_user}] Pozisyon kontrolü: Fiyatlar alınamadı."); return

        positions_to_close_now: List[Tuple[str, str]] = []
        for order_id, position in positions_to_check.items():
            symbol, side = position.get('symbol'), position.get('side')
            if not symbol or not side: continue

            current_price_raw = current_prices_float.get(symbol)
            current_price_dec = _to_decimal(current_price_raw) if current_price_raw is not None else None
            if not current_price_dec or current_price_dec <= DECIMAL_ZERO: continue

            entry_price_dec = _to_decimal(position.get('entry_price')) # Bu zaten Decimal olmalı
            sl_price_dec = _to_decimal(position.get('sl_price'))       # Bu zaten Decimal olmalı
            tp_price_dec = _to_decimal(position.get('tp_price'))       # Bu zaten Decimal olmalı

            if not entry_price_dec or entry_price_dec <= DECIMAL_ZERO: continue

            close_reason: Optional[str] = None
            if sl_price_dec and sl_price_dec > DECIMAL_ZERO and \
               ((side == 'buy' and current_price_dec <= sl_price_dec) or (side == 'sell' and current_price_dec >= sl_price_dec)):
                close_reason = f"SL ({sl_price_dec:.8f}) tetiklendi (Mevcut: {current_price_dec:.8f})"
            elif tp_price_dec and tp_price_dec > DECIMAL_ZERO and \
                 ((side == 'buy' and current_price_dec >= tp_price_dec) or (side == 'sell' and current_price_dec <= tp_price_dec)):
                 close_reason = f"TP ({tp_price_dec:.8f}) tetiklendi (Mevcut: {current_price_dec:.8f})"
            elif position.get('tsl_enabled', False):
                 tsl_close_reason = self._check_trailing_stop(order_id, position, current_price_dec) # position kopyasını gönderir
                 if tsl_close_reason: close_reason = tsl_close_reason

            if close_reason:
                logger.info(f"[{active_user}] KAPATMA GEREKLİ: ID={order_id}, {symbol}, Sebep='{close_reason}'")
                positions_to_close_now.append((order_id, close_reason))

        if positions_to_close_now:
            logger.info(f"[{active_user}] {len(positions_to_close_now)} pozisyon SL/TP/TSL nedeniyle kapatılacak.")
            for oid, reason_text in positions_to_close_now:
                 with self._positions_lock: # Tekrar kontrol et
                     if oid not in self.open_positions or self.open_positions[oid].get('status') != 'open':
                         logger.info(f"[{active_user}] Pozisyon (ID: {oid}) zaten kapatılmış/kapatılıyor (SL/TP/TSL). Tekrar kapatılmayacak.")
                         continue
                 if not self.close_position_by_id(oid, reason=reason_text):
                      logger.error(f"[{active_user}] Otomatik SL/TP/TSL kapatma başarısız (ID: {oid}, Sebep: {reason_text}).")
                 time.sleep(0.1) # API rate limit için


    def _check_trailing_stop(self, order_id: str, position_data_copy: Dict[str, Any], current_price: Decimal) -> Optional[str]:
        # Bu metodun içeriği önceki gibi kalabilir.
        # Sadece _to_decimal gibi yardımcıları doğru kullandığından ve Decimal tipleriyle çalıştığından emin olun.
        # ...
        side = position_data_copy.get('side')
        entry_price = _to_decimal(position_data_copy.get('entry_price')) # Zaten Decimal olmalı
        activation_perc = _to_decimal(position_data_copy.get('tsl_activation_percentage')) # Zaten Decimal olmalı
        callback_perc = _to_decimal(position_data_copy.get('tsl_callback_percentage')) # Zaten Decimal olmalı
        is_tsl_activated_copy = bool(position_data_copy.get('tsl_activated', False))
        current_tsl_stop_price_copy = _to_decimal(position_data_copy.get('tsl_stop_price')) # Zaten Decimal olmalı
        highest_price_copy = _to_decimal(position_data_copy.get('tsl_highest_price')) # Zaten Decimal olmalı
        lowest_price_copy = _to_decimal(position_data_copy.get('tsl_lowest_price')) # Zaten Decimal olmalı
        symbol = position_data_copy.get('symbol', '')

        if not all([symbol, entry_price, activation_perc, callback_perc, side]) or activation_perc <= DECIMAL_ZERO or callback_perc <= DECIMAL_ZERO: return None

        updated_state_for_main_pos: Dict[str, Any] = {}
        reason_to_close: Optional[str] = None

        if not is_tsl_activated_copy: # Aktivasyon kontrolü (entry_price None olamaz)
            activation_price = entry_price * (DECIMAL_ONE + activation_perc / DECIMAL_HUNDRED) if side == 'buy' else entry_price * (DECIMAL_ONE - activation_perc / DECIMAL_HUNDRED)
            if (side == 'buy' and current_price >= activation_price) or (side == 'sell' and current_price <= activation_price):
                logger.info(f"TSL Aktive Edildi: ID={order_id}, Akt.Fiyat={activation_price:.8f}, Mevcut={current_price:.8f}")
                updated_state_for_main_pos['tsl_activated'] = True; is_tsl_activated_copy = True
                initial_tsl_stop = current_price * (DECIMAL_ONE - callback_perc / DECIMAL_HUNDRED) if side == 'buy' else current_price * (DECIMAL_ONE + callback_perc / DECIMAL_HUNDRED)
                adj_initial_tsl_stop = self._adjust_precision(symbol, initial_tsl_stop, 'price')
                if adj_initial_tsl_stop and adj_initial_tsl_stop > DECIMAL_ZERO:
                    updated_state_for_main_pos['tsl_stop_price'] = adj_initial_tsl_stop; current_tsl_stop_price_copy = adj_initial_tsl_stop
                else: updated_state_for_main_pos['tsl_stop_price'] = None; current_tsl_stop_price_copy = None

        if is_tsl_activated_copy:
            new_peak = None
            if side == 'buy':
                if highest_price_copy is None or current_price > highest_price_copy: new_peak = current_price
                updated_state_for_main_pos['tsl_highest_price'] = new_peak if new_peak else highest_price_copy
            elif side == 'sell':
                if lowest_price_copy is None or current_price < lowest_price_copy: new_peak = current_price
                updated_state_for_main_pos['tsl_lowest_price'] = new_peak if new_peak else lowest_price_copy
            
            peak_for_calc = new_peak or (highest_price_copy if side == 'buy' else lowest_price_copy)
            if peak_for_calc:
                potential_new_stop = peak_for_calc * (DECIMAL_ONE - callback_perc / DECIMAL_HUNDRED) if side == 'buy' else peak_for_calc * (DECIMAL_ONE + callback_perc / DECIMAL_HUNDRED)
                adj_potential_new_stop = self._adjust_precision(symbol, potential_new_stop, 'price')
                if adj_potential_new_stop and adj_potential_new_stop > DECIMAL_ZERO:
                    needs_update = current_tsl_stop_price_copy is None or \
                                   (side == 'buy' and adj_potential_new_stop > current_tsl_stop_price_copy) or \
                                   (side == 'sell' and adj_potential_new_stop < current_tsl_stop_price_copy)
                    if needs_update:
                        updated_state_for_main_pos['tsl_stop_price'] = adj_potential_new_stop; current_tsl_stop_price_copy = adj_potential_new_stop
            
            if current_tsl_stop_price_copy and current_tsl_stop_price_copy > DECIMAL_ZERO and \
               ((side == 'buy' and current_price <= current_tsl_stop_price_copy) or (side == 'sell' and current_price >= current_tsl_stop_price_copy)):
                reason_to_close = f"TSL ({current_tsl_stop_price_copy:.8f}) tetiklendi (Mevcut: {current_price:.8f})"

        if updated_state_for_main_pos:
            with self._positions_lock:
                if order_id in self.open_positions and self.open_positions[order_id].get('status') == 'open':
                    self.open_positions[order_id].update(updated_state_for_main_pos)
        return reason_to_close


    def get_open_positions_thread_safe(self) -> List[Dict[str, Any]]:
        # Bu metodun içeriği önceki gibi kalabilir.
        with self._positions_lock:
            return [copy.deepcopy(pdata) for pdata in self.open_positions.values()
                    if pdata.get('status') in ['open', 'closing']]

    def get_position_by_symbol_thread_safe(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Belirtilen sembol için 'open' durumunda bir pozisyon varsa döndürür."""
        with self._positions_lock:
            for pos_data in self.open_positions.values():
                if pos_data.get('symbol') == symbol and pos_data.get('status') == 'open':
                    return copy.deepcopy(pos_data) # Kopyasını döndür
            return None
    
    def close_all_positions(self, reason: str = "Tüm pozisyonları kapatma isteği") -> Tuple[int, int]:
        # Bu metodun içeriği önceki gibi kalabilir.
        active_user = self._active_user or "Sistem"
        logger.info(f"[{active_user}] Tüm açık pozisyonlar kapatılıyor... Sebep: '{reason}'")
        positions_to_close_ids: List[str] = []
        with self._positions_lock:
            positions_to_close_ids = [oid for oid, pdata in self.open_positions.items() if pdata.get('status') == 'open']
        if not positions_to_close_ids: logger.info(f"[{active_user}] Kapatılacak 'open' pozisyon yok."); return 0, 0
        
        closed_success_count, failed_count = 0, 0
        for order_id in positions_to_close_ids:
            if self.close_position_by_id(order_id, reason): closed_success_count += 1
            else: failed_count += 1
            time.sleep(0.1) 
        logger.info(f"[{active_user}] Tüm pozisyonları kapatma: Başarılı: {closed_success_count}, Başarısız/Beklemede: {failed_count}")
        return closed_success_count, failed_count