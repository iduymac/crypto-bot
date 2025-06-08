# core/demo_exchange.py

import logging
import time
import random
import copy
import datetime
from decimal import Decimal, ROUND_DOWN, InvalidOperation, getcontext, Context # Context import edildi
# Python 3.8 uyumluluğu için Union ve Tuple importları
from typing import Union, Tuple, Optional, List, Dict, Any # Optional, List, Dict, Any eklendi
from utils import _to_decimal 
# --- Logger ---
# Kullanıcının tercih ettiği logger yapısı
try:
    # setup_logger kullanmak diğer modüllerle tutarlı olur
    from core.logger import setup_logger
    logger = setup_logger('demo_exchange')
except ImportError:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('demo_exchange_fallback')
    logger.warning("core.logger modülü bulunamadı, temel fallback logger kullanılıyor.")
# --- /Logger ---

# --- ccxt import ---
# Kullanıcının sağladığı ccxt import ve fallback mekanizması iyi görünüyor.
try:
    import ccxt
    from ccxt.base.errors import (
        ExchangeError, AuthenticationError, PermissionDenied, AccountNotEnabled,
        ArgumentsRequired, BadRequest, BadSymbol, BadResponse, NullResponse,
        InsufficientFunds, InvalidAddress, InvalidOrder, OrderNotFound, OrderNotCached,
        CancelPending, NetworkError, DDoSProtection, RateLimitExceeded,
        ExchangeNotAvailable, OnMaintenance, NotSupported
    )
    CCXT_AVAILABLE = True
    logger.info("CCXT kütüphanesi başarıyla import edildi.")
except ImportError:
    logger.error("CCXT kütüphanesi bulunamadı! Fiyat almak için public API fallback mekanizması çalışmayabilir.")
    ccxt = None
    # ccxt hatalarını tanımla ki kod çökmesin
    class ExchangeError(Exception): pass
    class AuthenticationError(ExchangeError): pass
    class PermissionDenied(AuthenticationError): pass
    class AccountNotEnabled(PermissionDenied): pass
    class ArgumentsRequired(ExchangeError): pass
    class BadRequest(ExchangeError): pass
    class BadSymbol(BadRequest): pass
    class BadResponse(ExchangeError): pass
    class NullResponse(BadResponse): pass
    class InsufficientFunds(ExchangeError): pass
    class InvalidAddress(BadRequest): pass
    class InvalidOrder(ExchangeError): pass
    class OrderNotFound(InvalidOrder): pass
    class OrderNotCached(InvalidOrder): pass
    class CancelPending(InvalidOrder): pass
    class NetworkError(ExchangeError): pass
    class DDoSProtection(NetworkError): pass
    class RateLimitExceeded(DDoSProtection): pass
    class ExchangeNotAvailable(NetworkError): pass
    class OnMaintenance(ExchangeNotAvailable): pass
    class NotSupported(ExchangeError): pass
    CCXT_AVAILABLE = False
# --- /ccxt import ---

# --- Gerekli Diğer Proje İçi Importlar ---
try:
    from core.exchange_api import ExchangeAPI
    EXCHANGE_API_AVAILABLE = True
    logger.debug("Gerekli proje içi modüller (ExchangeAPI) başarıyla import edildi.")
except ImportError as e:
    logger.error(f"Gerekli proje içi modüller (örn: ExchangeAPI) import edilemedi: {e}")
    EXCHANGE_API_AVAILABLE = False
    class ExchangeAPI: # Placeholder sınıfı oluştur
        def __init__(self, exchange_id, *args, **kwargs): pass
        def _format_symbol_for_exchange(self, symbol, exchange_id=None): return symbol.replace('-', '/').upper()
        def close(self): pass
        def get_default_precision(self): return {}
        def get_commission_rate(self): return None
        def has(self): return {}
        def is_demo_mode(self) -> bool:
            """Bu API'nin gerçek borsa modunda çalıştığını belirtir."""
            return False

# --- Yardımcı Fonksiyonlar ve Sabitler ---
try:
    from utils import DECIMAL_ZERO, DECIMAL_CONTEXT_DEFAULT_PREC, get_decimal_context as utils_get_decimal_context, _to_decimal as utils_to_decimal
    DECIMAL_CONTEXT = utils_get_decimal_context()
    if not DECIMAL_CONTEXT:
        DECIMAL_CONTEXT = getcontext()
        DECIMAL_CONTEXT.prec = DECIMAL_CONTEXT_DEFAULT_PREC
    logger.info(f"utils modülünden Decimal sabitleri ve context alındı. Precision: {DECIMAL_CONTEXT.prec}")
except ImportError:
    logger.error("utils modülü veya içindekiler import edilemedi. Dahili Decimal sabitleri ve context kullanılacak.")
    DECIMAL_CONTEXT_DEFAULT_PREC = 28
    DECIMAL_CONTEXT = getcontext()
    DECIMAL_CONTEXT.prec = DECIMAL_CONTEXT_DEFAULT_PREC
    DECIMAL_ZERO = Decimal('0')
    def utils_to_decimal(value: Any) -> Optional[Decimal]:
        if value is None: return None
        try: return Decimal(str(value).replace(',', '.'))
        except: return None

DEFAULT_COMMISSION_RATE = Decimal('0.001')
DEFAULT_PRECISION = {'amount': 8, 'price': 8}

class DemoExchangeAPI:
    def __init__(self, bot_core_ref, real_exchange_api: Union['ExchangeAPI', None] = None):
        if bot_core_ref is None:
            # logger global bir değişken olduğu için burada erişilebilir olmalı
            # Eğer logger None ise, print ile fallback yapabiliriz.
            log_func = logger.critical if logger else print
            log_func("DemoExchangeAPI başlatılamadı: Geçerli bir BotCore referansı sağlanmadı.")
            raise ValueError("DemoExchangeAPI için BotCore referansı (bot_core_ref) zorunludur.")

        self.bot_core_ref = bot_core_ref
        self.real_exchange_api = real_exchange_api
        self.exchange_name = "DemoMode" # Varsayılan isim
        self.exchange_id = 'binanceusdm' # Varsayılan ID
        self.market_details: Dict[str, Any] = {}
        self.precision: Dict[str, int] = copy.deepcopy(DEFAULT_PRECISION) # DEFAULT_PRECISION globalde tanımlı olmalı
        self.commission_rate: Decimal = DEFAULT_COMMISSION_RATE # DEFAULT_COMMISSION_RATE globalde tanımlı olmalı

        # --- self.has özelliğinin doğru bir şekilde SÖZLÜK olarak tanımlanması ---
        # Başlangıçta demo için varsayılan 'has' özelliklerini tanımla
        default_demo_has = {
            'createOrder': {'reduceOnly': False}, # Demo'da reduceOnly'yi False varsayalım
            'setMarginMode': True,                # Örnek (desteklendiğini varsayalım)
            'setLeverage': True,                  # Örnek (desteklendiğini varsayalım)
            'fetchTickers': False,                # DemoAPI'de fetch_tickers taklidi yoksa False
            'fetchBalance': True,                 # Demo bakiye yönetimi için
            # İhtiyaç duyulabilecek diğer ccxt 'has' anahtarlarını buraya
            # demo mod için mantıklı varsayılan değerlerle ekleyebilirsiniz.
            # Örnek: 'CORS': False, 'cancelOrder': True, 'fetchOrder': True vb.
        }
        self.has: Dict[str, Any] = default_demo_has.copy() # Kendi varsayılanlarımızı kopyalayarak başla

        # Eğer gerçek API referansı varsa ve 'has' özelliği bir sözlükse, onu bizimkilerle birleştir.
        if self.real_exchange_api and \
           hasattr(self.real_exchange_api, 'exchange') and self.real_exchange_api.exchange and \
           hasattr(self.real_exchange_api.exchange, 'has') and \
           isinstance(self.real_exchange_api.exchange.has, dict): # GERÇEK API'NİN 'has'I SÖZLÜK MÜ?
            try:
                real_api_has_copy = copy.deepcopy(self.real_exchange_api.exchange.has)
                
                # Gerçek API'nin özelliklerini bizim varsayılanlarımızın üzerine yazarak güncelle.
                # Bu, gerçek API'de olan ama bizim varsayılanlarımızda olmayan özellikleri de ekler.
                self.has.update(real_api_has_copy)
                
                # Demo için özellikle farklı olmasını istediğimiz 'has' ayarlarını
                # (örn: fetchTickers) burada tekrar zorlayabiliriz (gerçek API'de True olsa bile).
                self.has['fetchTickers'] = default_demo_has.get('fetchTickers', False)
                # Eğer reduceOnly'nin demo'da her zaman False olmasını istiyorsanız:
                if 'createOrder' not in self.has or not isinstance(self.has.get('createOrder'), dict):
                    self.has['createOrder'] = {} # Önce createOrder sözlüğünü oluştur
                self.has['createOrder']['reduceOnly'] = default_demo_has.get('createOrder',{}).get('reduceOnly', False)

                logger.info(f"DemoExchangeAPI 'has' özellikleri gerçek API'den ({getattr(self.real_exchange_api, 'exchange_name', '?')}) alındı ve birleştirildi.")
            except Exception as e_has_copy:
                logger.warning(f"Gerçek API 'has' özellikleri kopyalanamadı/işlenemedi: {e_has_copy}. Demo varsayılan 'has' özellikleri kullanılacak (zaten atanmıştı).")
        
        # exchange_id ve exchange_name ayarlanması (önceki kodunuzdaki gibi)
        try:
            id_assigned_in_init = False
            # 1. Gerçek API referansından ID ve detayları almayı dene
            if self.real_exchange_api and hasattr(self.real_exchange_api, 'exchange') and self.real_exchange_api.exchange:
                exchange_instance_id = getattr(self.real_exchange_api.exchange, 'id', None)
                if exchange_instance_id and isinstance(exchange_instance_id, str):
                    self.exchange_id = exchange_instance_id
                    self.exchange_name = f"DemoMode({self.exchange_id})"
                    id_assigned_in_init = True
                    logger.info(f"Demo borsa ID'si gerçek API referansından '{self.exchange_id}' olarak ayarlandı.")

                # Market detayları, hassasiyet ve komisyon oranını gerçek API'den kopyala (varsa)
                if hasattr(self.real_exchange_api, 'market_details') and self.real_exchange_api.market_details:
                    self.market_details = copy.deepcopy(self.real_exchange_api.market_details)
                    logger.info(f"Gerçek API'den {len(self.market_details)} market detayı başarıyla kopyalandı.")
                # ... (precision ve commission_rate kopyalama mantığı önceki kodunuzdaki gibi devam edebilir) ...
                if hasattr(self.real_exchange_api, 'precision') and isinstance(getattr(self.real_exchange_api, 'precision'), dict):
                     self.precision = copy.deepcopy(self.real_exchange_api.precision)
                     logger.info(f"Demo hassasiyet ayarları gerçek API'den kopyalandı: {self.precision}")
                if hasattr(self.real_exchange_api, 'commission_rate') and isinstance(getattr(self.real_exchange_api, 'commission_rate'), Decimal):
                     self.commission_rate = self.real_exchange_api.commission_rate
                     logger.info(f"Demo komisyon oranı gerçek API'den kopyalandı: {self.commission_rate}")


            # 2. Eğer ID hala atanmadıysa, kullanıcı ayarlarından almayı dene
            if not id_assigned_in_init and hasattr(self.bot_core_ref, 'user_manager') and self.bot_core_ref.user_manager:
                active_user_for_id = getattr(self.bot_core_ref, 'active_user', None)
                if active_user_for_id:
                    user_settings_for_id = self.bot_core_ref.user_manager.get_user(active_user_for_id)
                    if user_settings_for_id and isinstance(user_settings_for_id, dict):
                        exchange_settings_for_id = user_settings_for_id.get('exchange', {})
                        if isinstance(exchange_settings_for_id, dict):
                            exchange_id_from_settings_val = exchange_settings_for_id.get('name') # Varsayılanı self.exchange_id yapmaya gerek yok, zaten var.
                            if isinstance(exchange_id_from_settings_val, str) and exchange_id_from_settings_val.strip():
                                self.exchange_id = exchange_id_from_settings_val.strip()
                                self.exchange_name = f"DemoMode({self.exchange_id})"
                                id_assigned_in_init = True
                                logger.info(f"Demo borsa ID'si kullanıcı ayarlarından '{self.exchange_id}' olarak ayarlandı.")
            
            if not id_assigned_in_init:
                logger.warning(f"Demo borsa ID'si belirlenemedi. Varsayılan '{self.exchange_id}' (ve isim: {self.exchange_name}) kullanılacak.")

        except AttributeError as ae_init:
             logger.error(f"[{self.exchange_name}] Demo borsa ID/Adı/Detayları belirlenirken özellik (attribute) hatası: {ae_init}", exc_info=False)
        except Exception as setup_err_init:
            logger.error(f"[{self.exchange_name}] Demo borsa ID/Adı/Detayları belirlenirken genel hata: {setup_err_init}", exc_info=True)

        self._order_id_counter = int(time.time() * 100) # Daha fazla çeşitlilik için *100

        # Son kontroller ve loglama
        if not self.real_exchange_api:
            logger.warning(f"[{self.exchange_name}] Gerçek API referansı sağlanmadı. Fiyatlar yalnızca (varsa) CCXT public API ile alınmaya çalışılacak.")
        if not CCXT_AVAILABLE and not self.real_exchange_api: # CCXT_AVAILABLE globalde tanımlı olmalı
            logger.critical(f"[{self.exchange_name}] KRİTİK: Hem gerçek API referansı yok, hem de CCXT kütüphanesi kurulu değil! Fiyat alma mekanizması çalışmayacak.")

        # En sonda nihai 'has' özelliklerini logla
        logger.info(f"{self.exchange_name} (ID: {self.exchange_id}, Komisyon: {self.commission_rate:.4f}) başarıyla başlatıldı. 'has' özellikleri: {self.has}")

    def is_demo_mode(self) -> bool:
        """Bu API'nin demo modunda çalıştığını belirtir."""
        return True

    # ... (varsa diğer metotlarınız burada devam eder)

    def _get_precision_for_symbol(self, symbol: str, precision_type: str) -> Optional[int]:
        market = self.market_details.get(symbol)
        if not market and '/' in symbol:
            market = self.market_details.get(symbol.replace('/', ''))
        elif not market and '-' in symbol:
            market = self.market_details.get(symbol.replace('-', ''))

        if market and isinstance(market.get('precision'), dict):
            precision_value = market['precision'].get(precision_type)
            if precision_value is not None:
                try:
                    return int(precision_value)
                except (ValueError, TypeError):
                     logger.warning(f"[{self.exchange_name}] Market detaylarındaki hassasiyet ({symbol}, {precision_type}) sayıya çevrilemedi: {precision_value}. Varsayılan kullanılacak.")

        default_prec = self.precision.get(precision_type)
        if default_prec is not None:
             try:
                return int(default_prec)
             except (ValueError, TypeError):
                 logger.warning(f"[{self.exchange_name}] Varsayılan hassasiyet ({precision_type}) sayıya çevrilemedi: {default_prec}.")
                 return None

        logger.warning(f"[{self.exchange_name}] Hassasiyet tanımı ({symbol}, {precision_type}) bulunamadı. None döndürülüyor.")
        return None


    def _quantize(self, value: Any, symbol: str, precision_type: str = 'amount') -> Optional[Decimal]:
        if value is None:
            return None
        try:
            places = self._get_precision_for_symbol(symbol, precision_type)
            if places is None or places < 0:
                logger.error(f"[{self.exchange_name}] _quantize: Geçersiz veya bulunamayan hassasiyet değeri ({symbol}, {precision_type}): {places}. Orijinal değer Decimal'e çevrilip döndürülecek.")
                try:
                    return Decimal(str(value).replace(',', '.'))
                except InvalidOperation:
                    logger.error(f"[{self.exchange_name}] _quantize: Hassasiyet olmamasına rağmen değer Decimal'e çevrilemedi ('{value}').")
                    return None

            decimal_value = utils_to_decimal(value)
            if decimal_value is None: return None

            if places == 0:
                quantizer = Decimal('1')
            else:
                quantizer = Decimal('1') / (Decimal('10') ** places)

            quantized_value = decimal_value.quantize(quantizer, rounding=ROUND_DOWN)
            return quantized_value
        except InvalidOperation:
            logger.error(f"[{self.exchange_name}] _quantize: Geçersiz Decimal değeri '{value}' (Tip: {type(value)}), yuvarlama yapılamadı.", exc_info=False)
            return None
        except Exception as e:
            logger.error(f"[{self.exchange_name}] _quantize: Yuvarlama sırasında beklenmedik hata ('{value}', Symbol: {symbol}, Type: {precision_type}): {e}", exc_info=True)
            return None


    def get_symbol_price(self, symbol: str) -> Optional[float]:
        price = None
        source = "UNKNOWN"
        if self.real_exchange_api and hasattr(self.real_exchange_api, 'get_symbol_price'):
            try:
                price_from_real = self.real_exchange_api.get_symbol_price(symbol)
                if price_from_real is not None:
                     price_dec = utils_to_decimal(price_from_real)
                     if price_dec and price_dec > DECIMAL_ZERO:
                         price = float(price_dec)
                         source = f"REAL_API({getattr(self.real_exchange_api, 'exchange_name', '?')})"
                         logger.debug(f"[{self.exchange_name}] Fiyat gerçek API'den alındı ({symbol}): {price}")
                         return price
                     else:
                         logger.warning(f"[{self.exchange_name}] Gerçek API'den geçersiz fiyat alındı ({symbol}): {price_from_real}")
                else: logger.debug(f"[{self.exchange_name}] Gerçek API '{symbol}' için fiyat döndürmedi. Fallback denenecek.")
            except Exception as e:
                logger.warning(f"[{self.exchange_name}] Gerçek API'den fiyat alınırken hata ({symbol}): {e}. Fallback denenecek.", exc_info=False)
            finally:
                if price is not None: return price

        if price is None and CCXT_AVAILABLE:
            public_exchange_instance = None
            try:
                logger.debug(f"[{self.exchange_name}] CCXT public API ile fiyat deneniyor (Borsa: {self.exchange_id}, Sembol: {symbol})...")
                exchange_class = getattr(ccxt, self.exchange_id, None)
                if not exchange_class or not callable(exchange_class):
                    logger.error(f"[{self.exchange_name}] CCXT borsa sınıfı '{self.exchange_id}' bulunamadı veya başlatılabilir değil.")
                    return None

                public_exchange_instance = exchange_class({'enableRateLimit': True, 'timeout': 15000})
                if not hasattr(public_exchange_instance, 'fetch_ticker'):
                    logger.error(f"[{self.exchange_name}] CCXT borsa '{self.exchange_id}' fetch_ticker metodunu desteklemiyor.")
                    return None

                ccxt_formatted_symbol = symbol.replace('-', '/').upper()
                if EXCHANGE_API_AVAILABLE and self.real_exchange_api and hasattr(self.real_exchange_api, '_format_symbol_for_exchange'):
                     try:
                          ccxt_formatted_symbol = self.real_exchange_api._format_symbol_for_exchange(symbol) or ccxt_formatted_symbol
                          logger.debug(f"Sembol '{symbol}' -> '{ccxt_formatted_symbol}' (ExchangeAPI metodu ile).")
                     except Exception as fmt_err:
                          logger.warning(f"Gerçek API formatlama metodu hatası: {fmt_err}. Basit formatlama kullanılacak.")

                ticker = public_exchange_instance.fetch_ticker(ccxt_formatted_symbol)
                price_raw = ticker.get('last') or ticker.get('close') or ticker.get('ask') or ticker.get('bid')

                if price_raw is not None:
                    price_dec = utils_to_decimal(price_raw)
                    if price_dec and price_dec > DECIMAL_ZERO:
                        price = float(price_dec)
                        source = f"CCXT_PUBLIC({self.exchange_id})"
                        logger.debug(f"[{self.exchange_name}] Fiyat CCXT public API'den alındı ({symbol}): {price}")
                        if public_exchange_instance and hasattr(public_exchange_instance, 'close'): public_exchange_instance.close()
                        return price
                    else:
                        logger.warning(f"[{self.exchange_name}] CCXT'den geçersiz fiyat alındı ({symbol}): {price_raw}")
                else:
                    logger.warning(f"[{self.exchange_name}] CCXT public API ({self.exchange_id}) '{ccxt_formatted_symbol}' için geçerli fiyat alanı döndürmedi. Ticker: {ticker}")

            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.DDoSProtection, ccxt.RateLimitExceeded, ccxt.OnMaintenance) as e:
                logger.error(f"[{self.exchange_name}] CCXT Ağ/Borsa Erişimi Hatası ({type(e).__name__}): {e}", exc_info=False)
            except (ccxt.BadRequest, ccxt.BadSymbol) as e:
                logger.error(f"[{self.exchange_name}] CCXT İstek Hatası ({type(e).__name__}): Sembol '{ccxt_formatted_symbol}' geçersiz olabilir. Hata: {e}", exc_info=False)
            except (ccxt.BadResponse, ccxt.NullResponse) as e:
                 logger.error(f"[{self.exchange_name}] CCXT Yanıt Hatası ({type(e).__name__}): Borsa geçersiz yanıt döndürdü. Hata: {e}", exc_info=False)
            except ccxt.NotSupported as e:
                 logger.error(f"[{self.exchange_name}] CCXT Desteklenmeyen İşlem Hatası ({type(e).__name__}): Borsa bu işlemi desteklemiyor. Hata: {e}", exc_info=False)
            except ccxt.ExchangeError as e:
                logger.error(f"[{self.exchange_name}] CCXT Genel Borsa Hatası ({type(e).__name__}): {e}", exc_info=False)
            except Exception as e:
                logger.error(f"[{self.exchange_name}] CCXT public API ile fiyat alınırken beklenmedik hata ({symbol}): {e}", exc_info=True)
            finally:
                if public_exchange_instance and hasattr(public_exchange_instance, 'close'):
                    try: public_exchange_instance.close()
                    except: pass

        if price is None:
            logger.error(f"[{self.exchange_name}] '{symbol}' için güncel piyasa fiyatı alınamadı.")
            return None
        try: return float(price)
        except: return None


    def get_balance(self, currency: str) -> float:
        if not self.bot_core_ref:
            logger.error(f"[{self.exchange_name}] get_balance çağrılamadı: BotCore referansı eksik.")
            return 0.0
        try:
            if not currency or not isinstance(currency, str):
                 logger.error(f"[{self.exchange_name}] get_balance: Geçersiz para birimi girdisi: {currency}")
                 return 0.0
            balance_value = self.bot_core_ref.get_virtual_balance(currency.upper())
            logger.debug(f"[{self.exchange_name}] Sanal Bakiye Sorgulandı: {currency.upper()} = {balance_value}")
            return balance_value if isinstance(balance_value, float) else 0.0
        except AttributeError:
            logger.error(f"[{self.exchange_name}] BotCore referansında 'get_virtual_balance' metodu bulunamadı!")
            return 0.0
        except Exception as e:
            logger.error(f"[{self.exchange_name}] Sanal bakiye ({currency}) alınırken beklenmedik bir hata oluştu: {e}", exc_info=True)
            return 0.0


    def _get_next_order_id(self) -> str:
        self._order_id_counter += 1
        return f"demo_{self._order_id_counter}_{int(time.time()*1000)}"


    # demo_exchange.py dosyası içinde, DemoExchangeAPI sınıfının bir parçası olarak

    def create_order(self, symbol: str, type: str, side: str, amount: Any, price: Any = None, params: dict = {}) -> dict:
        order_type_internal = str(type).lower() # Gelen tipi hemen küçük harfe çevir
        limit_price_internal = price # Bu zaten Decimal veya None olabilir

        logger.info(f"[{self.exchange_name}] Yeni DEMO emir isteği: {symbol} {str(side).upper()} {order_type_internal.upper()} | "
                    f"Miktar: {amount} | Limit Fiyatı: {limit_price_internal} | Params: {params}")

        # --- Temel Girdi Kontrolleri ---
        if not symbol or not isinstance(symbol, str):
            return self._reject_order(symbol, order_type_internal, side, amount, limit_price_internal, "Geçersiz sembol.")
        if order_type_internal not in ['market', 'limit']:
            return self._reject_order(symbol, order_type_internal, side, amount, limit_price_internal, "Geçersiz emir tipi (market/limit).")
        side_lower = str(side).lower()
        if side_lower not in ['buy', 'sell']:
            return self._reject_order(symbol, order_type_internal, side_lower, amount, limit_price_internal, "Geçersiz emir yönü (buy/sell).")
        if amount is None: # Miktar None olamaz
            return self._reject_order(symbol, order_type_internal, side_lower, amount, limit_price_internal, "Miktar belirtilmemiş.")

        amount_dec = _to_decimal(amount) # utils._to_decimal fonksiyonunu kullanıyoruz
        if amount_dec is None or amount_dec <= DECIMAL_ZERO:
            return self._reject_order(symbol, order_type_internal, side_lower, amount, limit_price_internal, f"Geçersiz veya sıfır miktar: {amount}")

        limit_price_dec: Optional[Decimal] = None
        if order_type_internal == 'limit':
            if limit_price_internal is None: # Limit emir için fiyat zorunlu
                return self._reject_order(symbol, order_type_internal, side_lower, amount, limit_price_internal, "Limit emri için fiyat eksik.")
            limit_price_dec = _to_decimal(limit_price_internal)
            if limit_price_dec is None or limit_price_dec <= DECIMAL_ZERO:
                return self._reject_order(symbol, order_type_internal, side_lower, amount, limit_price_internal, f"Geçersiz limit fiyatı: {limit_price_internal}")
        # --- /Temel Girdi Kontrolleri ---

        # --- Fiyat ve Hassasiyet Ayarları ---
        # Güncel piyasa fiyatını al (dolum ve market emirleri için)
        current_price_float = self.get_symbol_price(symbol) # Bu zaten float veya None döner
        if current_price_float is None:
            return self._reject_order(symbol, order_type_internal, side_lower, amount_dec, limit_price_dec, f"Piyasa fiyatı alınamadı ({symbol}).")
        current_price_dec = _to_decimal(current_price_float) # Decimal'e çevir
        if current_price_dec is None or current_price_dec <= DECIMAL_ZERO: # Ek kontrol
            return self._reject_order(symbol, order_type_internal, side_lower, amount_dec, limit_price_dec, f"Geçersiz piyasa fiyatı ({current_price_float}).")

        # Emir dolum fiyatını belirle
        fill_price_dec: Optional[Decimal] = None
        is_filled_immediately = False # Demo modunda tüm emirler hemen dolacak

        if order_type_internal == 'market':
            # Market emirleri için küçük bir kayma (slippage) ekleyebiliriz
            try:
                slippage_percent = Decimal(str(random.uniform(-0.0005, 0.0005))) # +/- %0.05 kayma
                slippage_factor = DECIMAL_ONE + slippage_percent
                fill_price_dec = current_price_dec * slippage_factor
            except Exception: # Hata olursa kaymasız fiyat
                fill_price_dec = current_price_dec
            is_filled_immediately = True
            logger.debug(f"[{self.exchange_name}] Market emri için tahmini dolum fiyatı: {fill_price_dec:.8f} (Piyasa: {current_price_dec:.8f})")
        
        elif order_type_internal == 'limit':
            # Demo modunda, limit emirlerinin de hemen dolduğunu varsayalım.
            # Gerçekçi bir senaryoda, fiyat uygunsa hemen, değilse 'open' kalırdı.
            # Şimdilik, limit fiyatından dolduğunu kabul ediyoruz.
            fill_price_dec = limit_price_dec
            is_filled_immediately = True
            logger.info(f"[{self.exchange_name}] Demo: Limit emri ({symbol} @ {limit_price_dec:.8f}) "
                        f"hemen bu fiyattan dolduruluyor (demo davranışı). Piyasa: {current_price_dec:.8f}")

        # Miktarı ve dolum fiyatını borsanın hassasiyetine göre ayarla
        quantized_amount = self._quantize(amount_dec, symbol, 'amount') # self._quantize Decimal veya None döner
        quantized_fill_price: Optional[Decimal] = None
        if is_filled_immediately and fill_price_dec is not None:
            quantized_fill_price = self._quantize(fill_price_dec, symbol, 'price')

        # Ayarlanmış değerleri kontrol et
        if quantized_amount is None or quantized_amount <= DECIMAL_ZERO:
            return self._reject_order(symbol, order_type_internal, side_lower, amount_dec, limit_price_dec, f"Miktar ({amount_dec}) hassasiyete ayarlanamadı veya sıfır/negatif oldu: {quantized_amount}")
        if is_filled_immediately and (quantized_fill_price is None or quantized_fill_price <= DECIMAL_ZERO):
            return self._reject_order(symbol, order_type_internal, side_lower, amount_dec, limit_price_dec, f"Dolum fiyatı ({fill_price_dec}) hassasiyete ayarlanamadı veya sıfır/negatif oldu: {quantized_fill_price}")
        # --- /Fiyat ve Hassasiyet Ayarları ---

        # --- Maliyet ve Komisyon Hesaplama (Quote Currency Cinsinden) ---
        cost_dec = DECIMAL_ZERO      # İşlemin toplam değeri (quote currency)
        commission_dec = DECIMAL_ZERO # Ödenecek komisyon (quote currency)

        if is_filled_immediately and quantized_amount is not None and quantized_fill_price is not None:
            try:
                cost_dec = quantized_amount * quantized_fill_price # Pozisyonun değeri (Miktar_base * Fiyat_quote/base)
                commission_dec = cost_dec * self.commission_rate   # Komisyon = Değer * Oran
                logger.debug(f"[{self.exchange_name}] Emir Hesaplamaları: "
                             f"Ayarlı Miktar (Base)={quantized_amount:.8f}, Ayarlı Dolum Fiyatı={quantized_fill_price:.8f}, "
                             f"Pozisyon Değeri (Quote)={cost_dec:.8f}, Komisyon (Quote)={commission_dec:.8f}")
            except Exception as calc_err:
                return self._reject_order(symbol, order_type_internal, side_lower, amount_dec, limit_price_dec, f"Maliyet/komisyon hesaplama hatası: {calc_err}")
        # --- /Maliyet ve Komisyon Hesaplama ---

        # Para birimlerini al (örn: ETHFI/USDT -> base='ETHFI', quote='USDT')
        currency_info = self._get_currencies_from_symbol(symbol)
        if not currency_info:
            return self._reject_order(symbol, order_type_internal, side_lower, amount_dec, limit_price_dec, f"Para birimleri ({symbol}) belirlenemedi.")
        base_currency, quote_currency = currency_info

        # --- Bakiye Güncelleme Mantığı (Futures Benzeri) ---
        update_successful = False
        rejection_reason: Optional[str] = None

        if is_filled_immediately: # Demo'da tüm emirler hemen doluyor
            # Futures demo mantığı:
            # Ana teminat para birimi (genellikle 'USDT' olan quote_currency) üzerinden işlem yapılır.
            # Long veya short pozisyon açarken, base_currency'ye (örn: ETHFI, BTC) sahip olmak gerekmez.
            # Sadece komisyon quote_currency'den düşülür.
            # Pozisyonun PNL'i de quote_currency bakiyesini etkiler (bu TradeManager'da yapılır).

            if commission_dec > DECIMAL_ZERO:
                logger.info(f"[{self.exchange_name}] Demo: Komisyon ({commission_dec:.8f} {quote_currency}) düşülecek.")
                if self.bot_core_ref.update_virtual_balance(quote_currency, str(-commission_dec)):
                    update_successful = True # Komisyon başarıyla düşüldü
                else:
                    # Bu durum, komisyonu ödeyecek kadar bile quote_currency (örn: USDT) yoksa olur.
                    rejection_reason = f"Yetersiz teminat (komisyon için {quote_currency} bakiyesi yok)"
                    logger.error(f"[{self.exchange_name}] Demo: {rejection_reason}")
                    update_successful = False # Emir yine de reddedilebilir/başarısız sayılabilir
            else: # Komisyon yoksa veya sıfırsa, bakiye güncellemesine gerek yok, başarılı kabul et
                update_successful = True
                logger.info(f"[{self.exchange_name}] Demo: Komisyon sıfır veya yok, quote bakiye güncellemesi yapılmadı.")
            
            # Eski mantık (spot benzeri, base currency kontrolü yapan) aşağıdaki gibiydi ve kaldırıldı:
            # if side_lower == 'buy':
            #     required_quote = cost_dec + commission_dec
            #     if self.bot_core_ref.update_virtual_balance(quote_currency, str(-required_quote)):
            #         # SPOT ALIM MANTIĞI: Alınan base currency'yi bakiyeye ekle
            #         # if self.bot_core_ref.update_virtual_balance(base_currency, str(quantized_amount)):
            #         #    update_successful = True
            #         # else: # base eklenemedi (nadiren)
            #         #    rejection_reason = f"Bakiye artırma hatası ({base_currency})"
            #         #    self.bot_core_ref.update_virtual_balance(quote_currency, str(required_quote)) # quote iade
            #         update_successful = True # Futures demosunda base currency eklenmez.
            #     else:
            #         rejection_reason = f"Yetersiz bakiye ({quote_currency})"
            # elif side_lower == 'sell':
            #     # SPOT SATIŞ MANTIĞI: Satılan base currency'yi bakiyeden düş
            #     # required_base = quantized_amount
            #     # if self.bot_core_ref.update_virtual_balance(base_currency, str(-required_base)):
            #     #    quote_increase = cost_dec - commission_dec
            #     #    if self.bot_core_ref.update_virtual_balance(quote_currency, str(quote_increase)):
            #     #        update_successful = True
            #     #    else: # quote eklenemedi
            #     #        rejection_reason = f"Bakiye artırma hatası ({quote_currency})"
            #     #        self.bot_core_ref.update_virtual_balance(base_currency, str(required_base)) # base iade
            #     # else:
            #     #    rejection_reason = f"Yetersiz bakiye ({base_currency})" # <<-- ÖNCEKİ HATA BURADAN GELİYORDU
            #     # Futures demosunda base currency düşülmez. Sadece komisyon (yukarıda halledildi).
            #     update_successful = True # update_successful yukarıdaki komisyon kontrolüne göre ayarlandı.

        else: # Emir hemen dolmuyorsa (bu demo senaryosunda oluşmuyor)
            update_successful = True # Bakiye güncellemesi yapılmaz, emir 'open' kalır
        # --- /Bakiye Güncelleme Mantığı ---

        # --- Emir Sonucunu Oluştur ---
        order_status = 'rejected' # Varsayılan durum
        filled_amount_final = DECIMAL_ZERO
        final_cost_quote = DECIMAL_ZERO
        final_commission_quote = DECIMAL_ZERO
        final_average_price = None

        if update_successful and is_filled_immediately:
            order_status = 'closed' # Demo'da emirler hemen 'closed' (dolu) kabul ediliyor
            filled_amount_final = quantized_amount or DECIMAL_ZERO # Miktar zaten Decimal
            final_cost_quote = cost_dec # Pozisyonun değeri (quote cinsinden)
            final_commission_quote = commission_dec # Komisyon (quote cinsinden)
            final_average_price = quantized_fill_price # Dolum fiyatı (Decimal)
        elif update_successful and not is_filled_immediately: # Bu senaryo demo'da şu an yok
            order_status = 'open'
            logger.debug(f"[{self.exchange_name}] Emir 'open' olarak ayarlandı (bu mesaj şu anki demo mantığında görünmemeli).")
        # else: order_status 'rejected' kalır (eğer update_successful False ise)

        # _generate_fake_order float bekliyor, Decimal'leri float'a çevir
        return self._generate_fake_order(
            symbol=symbol,
            order_type=order_type_internal, # Orijinal (küçük harf) tip
            side=side_lower,                # Orijinal (küçük harf) yön
            amount=float(amount_dec) if amount_dec else None, # Sinyalden gelen orijinal miktar (hassasiyet ayarlanmamış)
            limit_price=float(limit_price_dec) if limit_price_dec else None, # Orijinal limit fiyatı
            fill_price=float(final_average_price) if final_average_price else None, # Gerçekleşen (hassas) dolum fiyatı
            status=order_status,
            filled=float(filled_amount_final),             # Gerçekleşen (hassas) miktar
            cost=float(final_cost_quote),                  # Gerçekleşen (hassas) pozisyon değeri (quote)
            commission=float(final_commission_quote),      # Gerçekleşen (hassas) komisyon (quote)
            reason=rejection_reason
        )

    def _reject_order(self, symbol: Optional[str], order_type: Optional[str], side: Optional[str], amount: Any, limit_price: Any, reason: str) -> dict:
        safe_symbol = str(symbol) if symbol else "N/A"
        safe_side = str(side) if side else "N/A"
        safe_type = str(order_type) if order_type else "N/A"
        safe_amount_str = str(amount) if amount is not None else "N/A"
        safe_limit_str = str(limit_price) if limit_price is not None else "N/A"
        # logger.error ile loglama _generate_fake_order içinde yapılacak
        # logger.error(f"[{self.exchange_name}] DEMO EMİR REDDEDİLDİ: {safe_symbol} {safe_side.upper()} {safe_type.upper()} Miktar:{safe_amount_str} Limit:{safe_limit_str} | Sebep: {reason}")

        try: original_amount_float = float(str(amount).replace(',', '.')) if amount is not None else None
        except: original_amount_float = None
        try: original_limit_float = float(str(limit_price).replace(',', '.')) if limit_price is not None else None
        except: original_limit_float = None

        return self._generate_fake_order(
            symbol=safe_symbol, order_type=safe_type, side=safe_side,
            amount=original_amount_float, limit_price=original_limit_float,
            fill_price=None, status='rejected',
            filled=0.0, cost=0.0, commission=0.0, reason=reason
        )


    def _generate_fake_order(self, symbol: str, order_type: str, side: str,
                             amount: Optional[float], limit_price: Optional[float], fill_price: Optional[float],
                             status: str, filled: float = 0.0, cost: float = 0.0,
                             commission: float = 0.0, reason: Optional[str] = None) -> dict:
        is_rejected = status.lower() == 'rejected'
        order_id = f"rejected_{int(time.time()*100 + random.randint(0,99))}" if is_rejected else self._get_next_order_id()
        timestamp_ms = int(time.time() * 1000)
        try:
            dt_object_utc = datetime.datetime.fromtimestamp(timestamp_ms / 1000.0, tz=datetime.timezone.utc)
            datetime_str_iso = dt_object_utc.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        except Exception:
            datetime_str_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

        base_currency, quote_currency = self._get_currencies_from_symbol(symbol) or ("BASE", "QUOTE")

        safe_amount = float(amount) if amount is not None else None
        safe_limit_price = float(limit_price) if limit_price is not None else None
        safe_fill_price = float(fill_price) if fill_price is not None else None
        safe_cost = float(cost) if cost is not None else 0.0
        safe_filled = float(filled) if filled is not None else 0.0
        safe_commission = float(commission) if commission is not None else 0.0
        remaining_amount = (safe_amount - safe_filled) if safe_amount is not None and safe_filled is not None else 0.0

        fake_order_dict = {
            'id': order_id, 'clientOrderId': f'demo_cli_{order_id}',
            'timestamp': timestamp_ms, 'datetime': datetime_str_iso,
            'lastTradeTimestamp': timestamp_ms if status.lower() in ['closed'] else None,
            'symbol': symbol, 'type': order_type.lower(), 'timeInForce': 'GTC',
            'postOnly': False, 'reduceOnly': False, 'side': side.lower(),
            'price': safe_limit_price, 'stopPrice': None, 'triggerPrice': None,
            'amount': safe_amount, 'cost': safe_cost if status.lower() == 'closed' else 0.0,
            'average': safe_fill_price, 'filled': safe_filled, 'remaining': remaining_amount,
            'status': status.lower(),
            'fee': {'currency': quote_currency.upper(), 'cost': safe_commission, 'rate': float(self.commission_rate)},
            'trades': [], 'fees': [{'currency': quote_currency.upper(), 'cost': safe_commission}] if safe_commission > 0 else [],
            'info': {'demo': True, 'reason': reason} if reason else {'demo': True},
        }

        # ---- LOGLAMA DÜZELTMESİ ----
        log_price_for_display = 'N/A'
        if safe_fill_price is not None:
            log_price_for_display = f"{safe_fill_price:.8f}"
        elif safe_limit_price is not None:
            log_price_for_display = f"{safe_limit_price:.8f} (Limit)"


        log_message = (f"[{self.exchange_name}] DEMO Emir Sonucu: ID={order_id}, Durum={status.upper()}, "
                       f"Sembol={symbol}, Yön={side.upper()}, "
                       f"Miktar={safe_filled:.8f}/{(safe_amount if safe_amount is not None else 'N/A')}, "
                       f"Fiyat={log_price_for_display}, Sebep={reason or 'Yok'}")
        if is_rejected:
            logger.error(log_message)
        else:
            logger.info(log_message)
        # ---- /LOGLAMA DÜZELTMESİ ----
        return fake_order_dict


    def _get_currencies_from_symbol(self, symbol: str) -> Optional[Tuple[str, str]]:
        if not isinstance(symbol, str) or not symbol:
            logger.error(f"[{self.exchange_name}] _get_currencies_from_symbol: Geçersiz sembol: {symbol}")
            return None
        try:
            symbol_upper = symbol.strip().upper()
            separator = None
            if '/' in symbol_upper: separator = '/'
            elif '-' in symbol_upper: separator = '-'

            if separator:
                parts = symbol_upper.split(separator)
                if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                    return parts[0].strip(), parts[1].strip()
                else: logger.warning(f"[{self.exchange_name}] Sembol '{separator}' ile ayrılamadı: '{symbol_upper}'.")
            else:
                common_quotes = sorted(['USDT', 'BUSD', 'USDC', 'TUSD', 'DAI', 'EUR', 'GBP', 'JPY', 'TRY', 'BTC', 'ETH', 'BNB', 'USD'], key=len, reverse=True)
                for quote in common_quotes:
                    if symbol_upper.endswith(quote):
                        base = symbol_upper[:-len(quote)].strip()
                        if base: return base, quote
            
            logger.error(f"[{self.exchange_name}] Sembol formatı anlaşılamadı: '{symbol}'")
            return None
        except Exception as e:
            logger.error(f"[{self.exchange_name}] Sembol ({symbol}) ayrıştırılırken hata: {e}", exc_info=True)
            return None


    def amount_to_precision(self, symbol: str, amount: Any) -> Optional[float]:
        quantized_decimal = self._quantize(amount, symbol, 'amount')
        return float(quantized_decimal) if quantized_decimal is not None else None

    def price_to_precision(self, symbol: str, price: Any) -> Optional[float]:
        quantized_decimal = self._quantize(price, symbol, 'price')
        return float(quantized_decimal) if quantized_decimal is not None else None

    def set_leverage(self, symbol: str, leverage: int, params: dict = {}) -> bool:
        try:
            leverage_int = int(leverage)
            if leverage_int < 1: leverage_int = 1
            logger.info(f"[{self.exchange_name}] Sanal Kaldıraç Ayarı: {symbol} için {leverage_int}x. Params: {params}")
            return True
        except (ValueError, TypeError) as e:
            logger.error(f"[{self.exchange_name}] Geçersiz kaldıraç değeri: {leverage}. Hata: {e}")
            return False

    def set_margin_mode(self, symbol: Optional[str], margin_mode: str, params: dict = {}) -> bool:
        normalized_mode = str(margin_mode).lower()
        target = f"'{symbol}' sembolü" if symbol else "Tüm semboller"
        valid_modes = ['isolated', 'cross']
        if normalized_mode not in valid_modes:
            logger.warning(f"[{self.exchange_name}] Geçersiz marjin modu '{margin_mode}'.")
        logger.info(f"[{self.exchange_name}] Sanal Marjin Modu Ayarı: {target} için '{normalized_mode}'. Params: {params}")
        return True

    def close(self):
        logger.debug(f"[{self.exchange_name}] Demo API kapatılıyor...")
        self.bot_core_ref = None
        if self.real_exchange_api:
             if hasattr(self.real_exchange_api, 'close') and callable(self.real_exchange_api.close):
                 try: self.real_exchange_api.close()
                 except Exception as e: logger.error(f"Real Exchange API kapatılırken hata: {e}", exc_info=False)
             self.real_exchange_api = None
        logger.info(f"[{self.exchange_name}] Demo API kapatıldı.")

    def __del__(self):
        try:
             name_to_log = getattr(self, 'exchange_name', 'DemoExchangeAPI(Unknown)')
             if logger is not None: logger.debug(f"[{name_to_log}] __del__ çağrıldı.")
        except Exception: pass
        self.close()

    def fetch_order(self, order_id: str, symbol: Optional[str] = None) -> Optional[dict]:
        logger.warning(f"[{self.exchange_name}] fetch_order({order_id}, {symbol}) demo'da desteklenmiyor.")
        return None

    def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> Optional[dict]:
        logger.warning(f"[{self.exchange_name}] cancel_order({order_id}, {symbol}) demo'da desteklenmiyor.")
        return None