# core/exchange_api.py
import ccxt
import logging
import time
import re

from typing import Optional, Dict, Any, Union, List, Set

# --- Logger Kurulumu ---
try:
    from core.logger import setup_logger
    logger = setup_logger('exchange_api')
except ImportError:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('exchange_api_fallback')
    logger.warning("core.logger bulunamadı, fallback logger kullanılıyor.")
# --- /Logger Kurulumu ---

# ccxt Hatalarını import etmek, except bloklarında kullanmayı kolaylaştırır
from ccxt.base.errors import (
    ExchangeError, AuthenticationError, PermissionDenied, AccountNotEnabled,
    ArgumentsRequired, BadRequest, BadSymbol, BadResponse, NullResponse,
    InsufficientFunds, InvalidAddress, InvalidOrder, OrderNotFound, OrderNotCached,
    CancelPending, NetworkError, DDoSProtection, RateLimitExceeded,
    ExchangeNotAvailable, OnMaintenance, NotSupported
)


class ExchangeAPI:
    def __init__(self, exchange_name: str, api_key: Optional[str] = None,
                 secret_key: Optional[str] = None, password: Optional[str] = None):

        self.valid_quotes: Set[str] = {
            "USDT", "BUSD", "USDC", "TUSD", "PAX", "DAI", "IDRT", "UAH", "VND", "EUR", "GBP", "JPY",
            "AUD", "CAD", "CHF", "RUB", "TRY", "BTC", "ETH", "BNB", "XRP", "SOL", "ADA",
            "DOT", "DOGE", "AVAX", "LTC", "LINK", "MATIC"
        }

        self.exchange_name = exchange_name.lower()
        self.api_key = api_key
        self.secret_key = secret_key
        self.password = password
        self.exchange: Optional[ccxt.Exchange] = None

        self.markets_loaded: bool = False
        self.market_details: Dict[str, Any] = {}
        self.market_lookup: Dict[str, str] = {}

        # DEBUG: API anahtarlarının ExchangeAPI'ye ulaşıp ulaşmadığını kontrol et
        if self.api_key and self.secret_key:
            logger.debug(f"ExchangeAPI: {self.exchange_name} için API anahtarları __init__ metoduna ulaştı.")
        else:
            logger.debug(f"ExchangeAPI: {self.exchange_name} için API anahtarları __init__ metoduna ulaşmadı (public mod).")

        try:
            self.exchange = self._connect_to_exchange()
            if self.exchange:
                self._load_markets_and_build_lookup()
            else:
                logger.error(f"[{self.exchange_name}] Borsa bağlantısı (_connect_to_exchange) başarısız oldu.")
        except Exception as e:
            logger.critical(f"{self.exchange_name} API başlatılamadı: {e}", exc_info=True)
            raise



    def fetch_all_open_positions_from_exchange(self) -> List[Dict[str, Any]]:
        """
        Hesaptaki tüm açık vadeli işlem pozisyonlarını borsadan çeker.
        CCXT'nin standart `Workspace_positions()` metodunu kullanır.
        """
        if not self.exchange:
            logger.error(f"[{self.exchange_name}] Borsa bağlantısı kurulu değil (fetch_all_open_positions_from_exchange).")
            return []

        if not self.api_key or not self.secret_key:
            logger.warning(f"[{self.exchange_name}] API anahtarları sağlanmadığı için açık pozisyonlar sorgulanamıyor.")
            return []

        all_positions_details = []
        try:
            logger.debug(f"[{self.exchange_name}] CCXT'nin fetch_positions() metodu ile tüm açık pozisyonlar isteniyor...")

            params = {}
            if self.exchange_name in ['binance', 'binanceusdm']:
                params['type'] = 'future' # Binance'de futures pozisyonlarını çekmek için bu parametreyi zorla

            raw_positions = self.exchange.fetch_positions(params=params)

            if not raw_positions:
                logger.info(f"[{self.exchange_name}] API'den açık pozisyon verisi gelmedi (fetch_positions).")
                return []

            for pos_data in raw_positions:
                symbol_ccxt = pos_data.get('symbol')
                amount = pos_data.get('contracts') or pos_data.get('amount')
                side = pos_data.get('side')
                entry_price = pos_data.get('entryPrice')
                unrealized_pnl = pos_data.get('unrealizedPnl')
                leverage = pos_data.get('leverage')

                if not symbol_ccxt or not amount:
                    continue

                try:
                    position_amount = float(amount)
                except ValueError:
                    logger.warning(f"API'den gelen pozisyon miktarı ({symbol_ccxt}, '{amount}') float'a çevrilemedi. Atlanıyor.")
                    continue

                if position_amount != 0:
                    normalized_side = 'buy' if side == 'long' else ('sell' if side == 'short' else None)
                    if normalized_side is None:
                        logger.warning(f"Pozisyon ({symbol_ccxt}) için bilinmeyen taraf: {side}. Atlanıyor.")
                        continue

                    try:
                        entry_price_float = float(entry_price) if entry_price is not None else 0.0
                        unrealized_pnl_float = float(unrealized_pnl) if unrealized_pnl is not None else 0.0
                        leverage_int = int(leverage) if leverage is not None else 1
                    except ValueError as ve:
                        logger.warning(f"Pozisyon ({symbol_ccxt}) için sayısal değerler çevrilemedi: {ve}. Varsayılan değerler kullanılacak.")
                        entry_price_float = 0.0
                        unrealized_pnl_float = 0.0
                        leverage_int = 1

                    position_detail = {
                        'symbol': symbol_ccxt,
                        'api_symbol': pos_data.get('id', symbol_ccxt),
                        'side': normalized_side,
                        'amount': abs(position_amount),
                        'entry_price': entry_price_float,
                        'unrealized_pnl': unrealized_pnl_float,
                        'leverage': leverage_int,
                        'raw_data': pos_data.get('info', {})
                    }
                    all_positions_details.append(position_detail)
                    logger.info(f"[{self.exchange_name}] API'den açık pozisyon bulundu ve işlendi: {position_detail}")

            return all_positions_details

        except AuthenticationError as e:
            logger.error(f"[{self.exchange_name}] Kimlik doğrulama hatası - açık pozisyonlar alınamadı: {e}")
        except RateLimitExceeded as e:
            logger.warning(f"[{self.exchange_name}] Rate limit aşıldı - açık pozisyonlar alınamadı: {e}")
        except (NetworkError, ExchangeNotAvailable, OnMaintenance, BadResponse, NullResponse) as e:
            logger.error(f"[{self.exchange_name}] Ağ/Borsa/Yanıt Hatası - açık pozisyonlar alınamadı: {e}")
        except BadRequest as e:
            if "parameter 'symbol' is required" in str(e).lower() and self.exchange_name in ['binance', 'binanceusdm']:
                logger.error(f"[{self.exchange_name}] fetch_positions() sembol parametresi istiyor, tüm pozisyonlar alınamıyor. Borsa dokümantasyonunu kontrol edin veya bilinen tüm marketleri tek tek sorgulayın.")
            else:
                logger.error(f"[{self.exchange_name}] Geçersiz İstek Hatası - açık pozisyonlar alınamadı: {e}")
        except ExchangeError as e:
            logger.error(f"[{self.exchange_name}] Genel Borsa Hatası - açık pozisyonlar alınamadı: {e}")
        except Exception as e:
            logger.error(f"[{self.exchange_name}] Açık pozisyonlar alınırken beklenmedik genel hata: {e}", exc_info=True)

        return []
    
    def _connect_to_exchange(self) -> Optional[ccxt.Exchange]:
        """Borsaya bağlanır ve exchange nesnesini döndürür."""
        logger.info(f"{self.exchange_name} borsasına bağlanılıyor...")
        
        exchange_config = {'timeout': 30000}
        
        # API anahtarları varsa config'e ekle
        if self.api_key and self.secret_key:
            exchange_config['apiKey'] = self.api_key
            exchange_config['secret'] = self.secret_key
            if self.password:
                exchange_config['password'] = self.password
            # Anahtarlar başarılı bir şekilde eklendiyse bu logu yaz.
            logger.info(f"{self.exchange_name} için API anahtarları ile bağlanılıyor.")
        else:
            # Anahtarlar eksikse bu uyarıyı yaz.
            logger.warning(f"{self.exchange_name} için API anahtarları sağlanmadı (sadece public API erişimi).")


        try:
            exchange_instance = getattr(ccxt, self.exchange_name)(exchange_config)
            
            # Binance ve Binance USDM için futures varsayılan tipini ayarla
            if self.exchange_name in ['binance', 'binanceusdm']:
                # Exchange instance'ın `options` attribute'u her zaman var olmalı.
                # Eğer yoksa, yeni bir dict olarak başlatırız.
                if not hasattr(exchange_instance, 'options') or not isinstance(exchange_instance.options, dict):
                    exchange_instance.options = {}

                # `defaultType` ayarını yap
                if exchange_instance.options.get('defaultType') != 'future':
                    exchange_instance.options['defaultType'] = 'future'
                    logger.debug(f"{self.exchange_name} için varsayılan işlem tipi 'future' olarak ayarlandı.")
                
                # Binance Futures için API URL'ini kontrol edelim.
                # `urls` attribute'u bir dict'tir ve API türlerine göre URL'ler içerir.
                # `fapiPrivate` Binance Futures için özel API URL'idir.
                expected_fapi_url_prefix = 'https://fapi.binance.com'
                if 'fapiPrivate' in exchange_instance.urls and \
                   not exchange_instance.urls['fapiPrivate'].startswith(expected_fapi_url_prefix):
                    logger.warning(f"BinanceUSDM için fapiPrivate API URL'i beklenenden farklı: {exchange_instance.urls['fapiPrivate']}. Normalde {expected_fapi_url_prefix} ile başlamalı.")
                elif 'fapiPrivate' not in exchange_instance.urls:
                    logger.warning(f"BinanceUSDM exchange objesinde 'fapiPrivate' URL'i bulunamadı. Bu, Futures işlemleri için sorun yaratabilir.")


            logger.info(f"{self.exchange_name} borsasına başarıyla bağlanıldı (veya bağlantı nesnesi oluşturuldu).")
            return exchange_instance
        except AuthenticationError as e:
            logger.critical(f"Kimlik doğrulama hatası ({self.exchange_name}): {e}. API anahtarlarını ve izinleri kontrol edin!", exc_info=True)
            raise
        except Exception as e:
            logger.critical(f"{self.exchange_name} borsasına bağlanırken hata: {e}", exc_info=True)
            raise

    def _load_markets_and_build_lookup(self, force_reload: bool = False) -> bool:
        if not self.exchange:
            logger.error(f"Borsa bağlantısı ({self.exchange_name}) yok, marketler yüklenemiyor.")
            return False
        
        if self.markets_loaded and not force_reload:
            logger.debug(f"Marketler ({self.exchange_name}) zaten yüklü ve arama tablosu oluşturulmuş.")
            return True

        logger.info(f"{self.exchange_name} için marketler yükleniyor ve arama tablosu oluşturuluyor...")
        try:
            # Borsanın futures marketlerini destekleyip desteklemediğini kontrol et
            # ve options'ı buna göre ayarla (varsa).
            # Bu kısım borsa özelinde daha detaylı ayarlanabilir.
            # if self.exchange.has.get('fetchMarkets') and self.exchange.has.get('future'):
            #     if 'options' not in self.exchange.config: self.exchange.config['options'] = {}
            #     if self.exchange.options.get('defaultType') != 'future': # Sadece farklıysa ayarla
            #         logger.debug(f"Market yüklemeden önce {self.exchange_name} için 'defaultType' -> 'future' ayarlanıyor.")
            #         self.exchange.options['defaultType'] = 'future' # veya 'swap'

            markets_data = self.exchange.load_markets(reload=force_reload) # force_reload doğrudan reload parametresine gider
            
            if not markets_data or not isinstance(markets_data, dict):
                logger.warning(f"{self.exchange_name} için marketler yüklenemedi (boş veya geçersiz yanıt).")
                self.markets_loaded = False
                self.market_details = {}
                self.market_lookup = {}
                return False

            self.market_details = markets_data
            self.market_lookup = {} 

            for ccxt_symbol, market_info in self.market_details.items():
                if not isinstance(market_info, dict): continue # Sadece sözlükleri işle

                # CCXT standart sembolü (örn: 'BTC/USDT')
                std_symbol = market_info.get('symbol', ccxt_symbol).upper() # Genellikle ccxt_symbol ile aynı
                self.market_lookup[std_symbol] = std_symbol 

                # Borsanın kendi ID'si (örn: 'BTCUSDT')
                exchange_specific_id = market_info.get('id', '').upper()
                if exchange_specific_id and exchange_specific_id not in self.market_lookup:
                    self.market_lookup[exchange_specific_id] = std_symbol
                
                # Ayıraçsız versiyon (sembol bilgisinden üretilen)
                symbol_no_slash = std_symbol.replace('/', '')
                if symbol_no_slash and symbol_no_slash not in self.market_lookup:
                    self.market_lookup[symbol_no_slash] = std_symbol

                # Base ve Quote kullanarak ek varyasyonlar
                base = market_info.get('baseId', market_info.get('base', '')).upper()
                quote = market_info.get('quoteId', market_info.get('quote', '')).upper()

                if base and quote:
                    base_quote_no_slash = f"{base}{quote}"
                    if base_quote_no_slash not in self.market_lookup:
                        self.market_lookup[base_quote_no_slash] = std_symbol
                    
                    base_quote_dash = f"{base}-{quote}" # Örn: BTC-USDT
                    if base_quote_dash not in self.market_lookup:
                        self.market_lookup[base_quote_dash] = std_symbol
            
            self.markets_loaded = True
            logger.info(f"{len(self.market_details)} market başarıyla yüklendi ({self.exchange_name}). Arama tablosu {len(self.market_lookup)} girdi içeriyor.")
            return True

        except (NetworkError, ExchangeError) as e:
            logger.error(f"{self.exchange_name} marketleri yüklenirken/arama tablosu oluşturulurken CCXT hatası: {e}")
            self.markets_loaded = False
            return False
        except Exception as e:
            logger.error(f"{self.exchange_name} marketleri yüklenirken/arama tablosu oluşturulurken beklenmedik hata: {e}", exc_info=True)
            self.markets_loaded = False
            return False

    def load_markets(self, force_reload: bool = False) -> bool:
        """ Borsanın market bilgilerini yükler ve sembol arama tablosunu oluşturur. """
        return self._load_markets_and_build_lookup(force_reload=force_reload)

    def get_validated_symbol(self, symbol_from_signal: str) -> Optional[str]:
        if not self.markets_loaded and not self.market_lookup: # Eğer yüklenmemişse ve lookup boşsa
            logger.warning(f"Marketler ({self.exchange_name}) yüklenmemiş. Sembol doğrulaması için şimdi yükleniyor...")
            if not self._load_markets_and_build_lookup(force_reload=True):
                 logger.error(f"Marketler yüklenemediği için sembol '{symbol_from_signal}' doğrulanamıyor. Fallback kullanılacak.")
                 return symbol_from_signal.replace('/', '').replace('-', '').upper() if isinstance(symbol_from_signal, str) else None

        if not isinstance(symbol_from_signal, str) or not symbol_from_signal.strip():
            logger.warning(f"get_validated_symbol: Geçersiz sembol girdisi: '{symbol_from_signal}'")
            return None

        s_upper = symbol_from_signal.upper()
        
        # En yaygın sinyal formatları için öncelikli arama
        # 1. Ayıraçsız format (örn: BTCUSDT)
        no_separator_key = s_upper.replace('/', '').replace('-', '').replace('_', '')
        if no_separator_key in self.market_lookup:
            validated = self.market_lookup[no_separator_key]
            if validated.upper() != s_upper: logger.debug(f"Sembol '{symbol_from_signal}' -> '{validated}' olarak doğrulandı (ayıraçsız).")
            return validated

        # 2. Olduğu gibi (örn: BTC/USDT veya BTC-USDT)
        if s_upper in self.market_lookup:
            validated = self.market_lookup[s_upper]
            # if validated.upper() != s_upper: logger.debug(f"Sembol '{symbol_from_signal}' -> '{validated}' olarak doğrulandı (doğrudan).") # Genelde aynı olur
            return validated
        
        # Diğer olası formatları dene
        key_dash_to_slash = s_upper.replace('-', '/')
        if key_dash_to_slash in self.market_lookup:
            validated = self.market_lookup[key_dash_to_slash]
            logger.debug(f"Sembol '{symbol_from_signal}' -> '{validated}' olarak doğrulandı ('-' to '/').")
            return validated

        key_underscore_to_slash = s_upper.replace('_', '/')
        if key_underscore_to_slash in self.market_lookup:
            validated = self.market_lookup[key_underscore_to_slash]
            logger.debug(f"Sembol '{symbol_from_signal}' -> '{validated}' olarak doğrulandı ('_' to '/').")
            return validated

        logger.warning(f"Sembol '{symbol_from_signal}' için doğrulanmış market formatı bulunamadı ({self.exchange_name}). Fallback olarak '{no_separator_key}' kullanılıyor.")
        return no_separator_key


    def get_symbol_price(self, symbol: str) -> Optional[float]:
        if not self.exchange: logger.error("Borsa bağlantısı yok, fiyat alınamıyor."); return None
        exchange_symbol = self.get_validated_symbol(symbol)
        if not exchange_symbol:
            logger.error(f"Fiyat alınamadı: Sembol '{symbol}' için geçerli bir borsa formatı bulunamadı.")
            return None
        try:
            logger.debug(f"Fiyat alınıyor: {exchange_symbol} (Orijinal: {symbol})")
            ticker = self.exchange.fetch_ticker(exchange_symbol)
            price = ticker.get('last') or ticker.get('close') or ticker.get('ask') or ticker.get('bid')
            if price is None or price <= 0:
                logger.warning(f"{exchange_symbol} için geçerli fiyat bulunamadı (ticker: {ticker}).") # Warning olarak değiştirildi
                return None
            # logger.debug(f"{exchange_symbol} fiyatı başarıyla alındı: {price}") # Çok sık loglanabilir
            return float(price)
        except RateLimitExceeded as e: logger.warning(f"Rate Limit Aşıldı (CCXT) - Fiyat alınamadı ({exchange_symbol}): {e}."); return None
        except BadSymbol as e: logger.error(f"Geçersiz Sembol (CCXT) - {exchange_symbol} fiyatı alınamadı: {e}"); return None
        except AuthenticationError as e: logger.error(f"Kimlik Doğrulama Hatası (CCXT) - Fiyat alınamadı ({exchange_symbol}): {e}"); return None
        except (NetworkError, ExchangeNotAvailable, OnMaintenance) as e: logger.warning(f"Ağ/Borsa Ulaşım Hatası (CCXT) - Fiyat alınamadı ({exchange_symbol}): {e}"); return None
        except ExchangeError as e: logger.error(f"Genel Borsa Hatası (CCXT) - Fiyat alınamadı ({exchange_symbol}): {e}"); return None
        except Exception as e: logger.error(f"{exchange_symbol} fiyatı alınırken beklenmedik hata: {e}", exc_info=True); return None

    def create_order(self, symbol: str, type: str, side: str, amount: float, price: Optional[float] = None, params: Dict = {}):
        if not self.exchange: logger.error("Borsa bağlantısı yok, emir oluşturulamıyor."); return None
        exchange_symbol = self.get_validated_symbol(symbol)
        if not exchange_symbol:
            logger.error(f"Emir oluşturulamadı: Sembol '{symbol}' için geçerli bir borsa formatı bulunamadı.")
            return None
        try:
            order_type_lower = str(type).lower()
            side_lower = str(side).lower()
            
            # Miktar ve fiyatı borsanın hassasiyetine göre ayarla (string döner)
            precise_amount_str = self.exchange.amount_to_precision(exchange_symbol, amount)
            # Limit emir için fiyat None değilse hassasiyet ayarla
            precise_price_str = None
            if price is not None and order_type_lower == 'limit':
                precise_price_str = self.exchange.price_to_precision(exchange_symbol, price)

            # Stringleri float'a çevir (ccxt genellikle float bekler)
            # None ise None kalmalı
            amount_to_send = float(precise_amount_str) if precise_amount_str else 0.0 
            price_to_send = float(precise_price_str) if precise_price_str else None

            if amount_to_send <= 0:
                 logger.error(f"Emir için hassasiyete ayarlanmış miktar geçersiz veya sıfır: {amount_to_send} (Orijinal: {amount}, Hassas Str: {precise_amount_str}). Sembol: {exchange_symbol}")
                 return None
            
            logger.info(f"Emir gönderiliyor -> Borsa: {self.exchange_name}, Sembol: {exchange_symbol} (Orj: {symbol}), Tip: {order_type_lower}, Yön: {side_lower}, Miktar: {amount_to_send}, Fiyat: {price_to_send if price_to_send else 'Market'}, Params: {params}")
            order_response = None
            if order_type_lower == 'market':
                order_response = self.exchange.create_market_order(exchange_symbol, side_lower, amount_to_send, params)
            elif order_type_lower == 'limit':
                if price_to_send is None or price_to_send <= 0:
                     logger.error(f"Limit emri için geçersiz veya ayarlanmamış fiyat: {price_to_send} (Orijinal: {price}, Hassas Str: {precise_price_str}). Sembol: {exchange_symbol}")
                     return None
                order_response = self.exchange.create_limit_order(exchange_symbol, side_lower, amount_to_send, price_to_send, params)
            else:
                logger.error(f"Desteklenmeyen emir tipi: {type}")
                return None

            logger.info(f"Emir oluşturma API yanıtı ({exchange_symbol}, ID:{order_response.get('id', 'N/A')}): Durum={order_response.get('status', '?')}")
            logger.debug(f"Tam Emir Yanıtı ({exchange_symbol}): {order_response}")
            return order_response

        except RateLimitExceeded as e: logger.warning(f"Rate Limit Aşıldı (CCXT) - Emir gönderilemedi ({exchange_symbol}): {e}"); return None
        except AuthenticationError as e: logger.error(f"Kimlik Doğrulama Hatası (CCXT) - Emir gönderilemedi ({exchange_symbol}): {e}. API İzinlerini Kontrol Edin!"); return None
        except InsufficientFunds as e: logger.error(f"Yetersiz Bakiye (CCXT) - Emir gönderilemedi ({exchange_symbol}): {e}"); return None
        except InvalidOrder as e: logger.error(f"Geçersiz Emir (CCXT) - Emir gönderilemedi ({exchange_symbol}): {e}"); return None
        except BadSymbol as e: logger.error(f"Geçersiz Sembol (CCXT) - Emir gönderilemedi ({exchange_symbol}): {e}"); return None
        except (NetworkError, ExchangeNotAvailable, OnMaintenance) as e: logger.error(f"Ağ/Borsa Ulaşım Hatası (CCXT) - Emir gönderilemedi ({exchange_symbol}): {e}"); return None
        except ExchangeError as e: logger.error(f"Genel Borsa Hatası (CCXT) - Emir gönderilemedi ({exchange_symbol}): {e}"); return None
        except Exception as e: logger.error(f"Emir ({exchange_symbol}) gönderilirken beklenmedik hata: {e}", exc_info=True); return None

    def get_balance(self, currency: str) -> float:
        if not self.exchange: logger.error("Borsa bağlantısı yok, bakiye alınamıyor."); return 0.0
        try:
            currency_upper = currency.upper()
            # logger.debug(f"Bakiye alınıyor: {currency_upper}") # Çok sık loglanabilir
            balance = self.exchange.fetch_balance() # Tüm bakiyeleri çeker
            
            # Bakiye yapısını kontrol et (genel veya coin özelinde olabilir)
            free_balance = 0.0
            if currency_upper in balance: # Doğrudan anahtar olarak varsa
                balance_info = balance[currency_upper]
                if isinstance(balance_info, dict) and 'free' in balance_info:
                    free_balance = float(balance_info['free'] or 0.0)
                # Bazen doğrudan değer olabilir (nadiren)
                elif isinstance(balance_info, (int, float, str)): 
                    try: free_balance = float(balance_info) # Tümünü free kabul et (spot için olabilir)
                    except: pass
            elif 'free' in balance and isinstance(balance['free'], dict) and currency_upper in balance['free']: # 'free' altında dict varsa
                 free_balance = float(balance['free'][currency_upper] or 0.0)
            elif 'total' in balance and isinstance(balance['total'], dict) and currency_upper in balance['total']: # 'total' altında dict varsa (fallback)
                 logger.warning(f"{currency_upper} için 'free' bakiye bulunamadı, 'total' ({balance['total'][currency_upper]}) kullanılıyor.")
                 free_balance = float(balance['total'][currency_upper] or 0.0)

            # logger.debug(f"{currency_upper} Kullanılabilir Bakiye ('free'): {free_balance}") # Çok sık loglanabilir
            return free_balance

        except RateLimitExceeded as e: logger.warning(f"Rate Limit Aşıldı (CCXT) - Bakiye alınamadı ({currency}): {e}"); return 0.0
        except AuthenticationError as e: logger.error(f"Kimlik Doğrulama Hatası (CCXT) - Bakiye alınamadı ({currency}): {e}"); return 0.0
        except (NetworkError, ExchangeNotAvailable, OnMaintenance) as e: logger.warning(f"Ağ/Borsa Ulaşım Hatası (CCXT) - Bakiye alınamadı ({currency}): {e}"); return 0.0
        except ExchangeError as e: logger.error(f"Genel Borsa Hatası (CCXT) - Bakiye alınamadı ({currency}): {e}"); return 0.0
        except Exception as e: logger.error(f"{currency} bakiyesi alınırken beklenmedik hata: {e}", exc_info=True); return 0.0

    def amount_to_precision(self, symbol: str, amount: Union[float, str]) -> Optional[str]:
        if not self.exchange: logger.error("Borsa bağlantısı yok, miktar hassasiyeti ayarlanamıyor."); return str(amount)
        exchange_symbol = self.get_validated_symbol(symbol)
        if not exchange_symbol: return str(amount) # Fallback
        try:
            amount_float = float(str(amount).replace(',','.')) # Önce float'a çevir
            return self.exchange.amount_to_precision(exchange_symbol, amount_float)
        except Exception as e:
            logger.error(f"CCXT amount_to_precision hatası ({exchange_symbol}, Miktar: {amount}): {e}", exc_info=False)
            return str(amount) # Hata durumunda orijinali string olarak döndür

    def price_to_precision(self, symbol: str, price: Union[float, str, None]) -> Optional[str]:
        if not self.exchange: logger.error("Borsa bağlantısı yok, fiyat hassasiyeti ayarlanamıyor."); return str(price) if price is not None else None
        if price is None: return None
        exchange_symbol = self.get_validated_symbol(symbol)
        if not exchange_symbol: return str(price) # Fallback
        try:
            price_float = float(str(price).replace(',','.')) # Önce float'a çevir
            return self.exchange.price_to_precision(exchange_symbol, price_float)
        except Exception as e:
            logger.error(f"CCXT price_to_precision hatası ({exchange_symbol}, Fiyat: {price}): {e}", exc_info=False)
            return str(price) # Hata durumunda orijinali string olarak döndür

    def set_margin_mode(self, symbol: str, margin_mode: str, params: Dict = {}):
        if not self.exchange: logger.error("Borsa bağlantısı yok, marjin modu ayarlanamıyor."); return False
        
        has_set_margin_mode = self.exchange.has.get('setMarginMode', False)
        if not has_set_margin_mode and not hasattr(self.exchange, 'set_margin_mode'):
            logger.warning(f"{self.exchange_name} borsası 'setMarginMode' özelliğini desteklemiyor.")
            return False

        exchange_symbol = self.get_validated_symbol(symbol)
        if not exchange_symbol: return False

        normalized_mode = str(margin_mode).lower()
        if normalized_mode not in ['isolated', 'cross']:
            logger.error(f"Geçersiz marjin modu: '{margin_mode}'. 'isolated' veya 'cross' olmalı."); return False
        try:
            logger.info(f"Marjin Modu ayarlanıyor -> Sembol: {exchange_symbol} (Orj: {symbol}), Mod: {normalized_mode}, Params: {params}")
            response = self.exchange.set_margin_mode(normalized_mode, exchange_symbol, params)
            logger.info(f"Marjin Modu ayarlama API yanıtı ({exchange_symbol}): {response}")
            return True
        except AuthenticationError as e: logger.error(f"Kimlik Doğrulama Hatası (CCXT) - Marjin Modu ayarlanamadı ({exchange_symbol}): {e}"); return False
        except NotSupported as e: logger.warning(f"Marjin Modu ayarlama desteklenmiyor ({self.exchange_name}, Sembol: {exchange_symbol}): {e}"); return False
        except ExchangeError as e:
            error_msg_lower = str(e).lower()
            if "cannot be changed" in error_msg_lower or "no need to change" in error_msg_lower:
                logger.info(f"Marjin modu ({normalized_mode}) zaten ayarlı veya değiştirilemiyor ({exchange_symbol}): {e}") # Bilgi logu olarak değiştirildi
                return True
            else: logger.error(f"Borsa Hatası (CCXT) - Marjin Modu ayarlanamadı ({exchange_symbol}): {e}"); return False
        except (NetworkError, ExchangeNotAvailable, OnMaintenance) as e: logger.error(f"Ağ/Borsa Ulaşım Hatası (CCXT) - Marjin Modu ayarlanamadı ({exchange_symbol}): {e}"); return False
        except Exception as e: logger.error(f"Marjin Modu ({exchange_symbol}, {normalized_mode}) ayarlanırken beklenmedik hata: {e}", exc_info=True); return False

    def set_leverage(self, symbol: str, leverage: Union[int, float, str], params: Dict = {}):
        if not self.exchange: logger.error("Borsa bağlantısı yok, kaldıraç ayarlanamıyor."); return False

        has_set_leverage = self.exchange.has.get('setLeverage', False)
        if not has_set_leverage and not hasattr(self.exchange, 'set_leverage'):
            logger.warning(f"{self.exchange_name} borsası 'setLeverage' özelliğini desteklemiyor.")
            return False
        
        exchange_symbol = self.get_validated_symbol(symbol)
        if not exchange_symbol: return False
        try:
            leverage_float = float(str(leverage).replace('x','').replace('X','')) # 'x' karakterini temizle
            leverage_int = int(leverage_float) # Tam sayıya çevir
            if leverage_int < 1: 
                logger.error(f"Geçersiz kaldıraç değeri: {leverage}. >= 1 olmalı."); return False
        except (ValueError, TypeError): 
            logger.error(f"Geçersiz kaldıraç formatı: {leverage}. Sayı bekleniyor."); return False
        try:
            logger.info(f"Kaldıraç ayarlanıyor -> Sembol: {exchange_symbol} (Orj: {symbol}), Kaldıraç: {leverage_int}x, Params: {params}")
            response = self.exchange.set_leverage(leverage_int, exchange_symbol, params)
            logger.info(f"Kaldıraç ayarlama API yanıtı ({exchange_symbol}): {response}")
            return True
        except AuthenticationError as e: logger.error(f"Kimlik Doğrulama Hatası (CCXT) - Kaldıraç ayarlanamadı ({exchange_symbol}): {e}"); return False
        except NotSupported as e: logger.warning(f"Kaldıraç ayarlama desteklenmiyor ({self.exchange_name}, Sembol: {exchange_symbol}): {e}"); return False
        except BadRequest as e: # Binance bazen kaldıraç zaten ayarlıysa BadRequest fırlatabiliyor.
             if "no need to change leverage" in str(e).lower() or "leverage not modified" in str(e).lower():
                  logger.info(f"Kaldıraç ({leverage_int}x) zaten ayarlı veya değiştirilmedi ({exchange_symbol}): {e}")
                  return True
             else: logger.error(f"Hatalı İstek (CCXT) - Kaldıraç ayarlanamadı ({exchange_symbol}): {e}"); return False
        except ExchangeError as e: logger.error(f"Borsa Hatası (CCXT) - Kaldıraç ayarlanamadı ({exchange_symbol}): {e}"); return False
        except (NetworkError, ExchangeNotAvailable, OnMaintenance) as e: logger.error(f"Ağ/Borsa Ulaşım Hatası (CCXT) - Kaldıraç ayarlanamadı ({exchange_symbol}): {e}"); return False
        except Exception as e: logger.error(f"Kaldıraç ({exchange_symbol}, {leverage_int}x) ayarlanırken beklenmedik hata: {e}", exc_info=True); return False

    def close(self):
        if self.exchange and hasattr(self.exchange, 'close') and callable(self.exchange.close):
            try:
                logger.info(f"{self.exchange_name} API bağlantısı kapatılıyor...")
                self.exchange.close()
                logger.info(f"{self.exchange_name} API bağlantısı başarıyla kapatıldı.")
            except Exception as e:
                logger.error(f"{self.exchange_name} API bağlantısı kapatılırken hata: {e}", exc_info=True)
        else:
            logger.debug(f"{self.exchange_name} için kapatılacak aktif bağlantı veya close() metodu yok.")
        self.exchange = None
        self.markets_loaded = False # Bağlantı kapanınca marketlerin de geçersiz olduğunu belirt
        self.market_details = {}
        self.market_lookup = {}
    
    def _normalize_symbol_for_api(self, raw_symbol_input: str) -> str:
        """
        Ham sembol girdisini (örn: "ETH/USDT", "ETHUSDT:USDT", "ETH:USDT")
        API'nin genellikle beklediği düz formata (örn: "ETHUSDT") dönüştürür.
        Kullanıcının önerdiği gelişmiş mantığı temel alır.
        """
        s = raw_symbol_input.strip().upper()
        normalized_s: str

        if ':' in s:
            # Birden fazla iki nokta olması durumuna karşın sondan böleriz
            base_part, quote_candidate_part = s.rsplit(':', 1)
            
            cleaned_base = re.sub(r'[^A-Z0-9]', '', base_part)
            cleaned_quote_candidate = re.sub(r'[^A-Z0-9]', '', quote_candidate_part)

            # Durum 1: Temizlenmiş ana kısım (`cleaned_base`) zaten tam bir çift gibi görünüyor mu?
            # Örn: "ETHUSDT:USDT" -> cleaned_base="ETHUSDT". Bu "USDT" gibi geçerli bir kotasyon ile bitiyor mu?
            base_ends_with_valid_quote = False
            if len(cleaned_base) > 0:
                for vq in self.valid_quotes:
                    if cleaned_base.endswith(vq) and len(cleaned_base) > len(vq): # Sadece kotasyonun kendisi olmamalı
                        base_ends_with_valid_quote = True
                        break
            
            if base_ends_with_valid_quote:
                # Örn: "ETHUSDT:USDT" veya "ETHUSDT:GARBAGE" -> "ETHUSDT"
                normalized_s = cleaned_base
            # Durum 2: İki noktadan sonraki kısım (`cleaned_quote_candidate`) geçerli bir kotasyon varlığı mı?
            # Örn: "ETH:USDT" -> cleaned_base="ETH", cleaned_quote_candidate="USDT" -> "ETHUSDT"
            elif cleaned_quote_candidate in self.valid_quotes:
                normalized_s = cleaned_base + cleaned_quote_candidate
            # Durum 3: Yukarıdakiler değilse (örn: "ETH:XYZ" veya "GARBAGE:GARBAGE")
            # Sadece temizlenmiş ana kısmı kullan (en güvenli varsayım).
            else:
                normalized_s = cleaned_base
        else: 
            # İki nokta yoksa, diğer ayırıcıları ('/', '-', '_') temizle.
            # Örn: "ETH/USDT" -> "ETHUSDT", "BTC-USDT" -> "BTCUSDT"
            normalized_s = re.sub(r'[^A-Z0-9]', '', s)
        
        return normalized_s

    def get_futures_position_details(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Belirli bir vadeli işlem pozisyonunun detaylı bilgilerini borsadan çeker.
        Tüm açık pozisyonları çekip, verilen sembole göre filtreler.
        """
        if not self.exchange:
            logger.error(f"[{self.exchange_name}] Borsa bağlantısı kurulu değil (get_futures_position_details).")
            return None

        if not self.api_key or not self.secret_key:
            logger.warning(f"[{self.exchange_name}] API anahtarları sağlanmadığı için pozisyon detayı sorgulaması yapılamaz.")
            return None

        api_symbol_ccxt = self.get_validated_symbol(symbol)

        logger.debug(f"[{self.exchange_name}] '{symbol}' sembolü için get_validated_symbol tarafından normalize edilmiş API sembolü: '{api_symbol_ccxt}'")

        if not api_symbol_ccxt:
            logger.error(f"[{self.exchange_name}] Sembol '{symbol}' normalize edilemedi, boş sonuç (get_futures_position_details).")
            return None

        try:
            params = {}
            if self.exchange_name in ['binance', 'binanceusdm']:
                params['type'] = 'future'

            # Tüm pozisyonları çekiyoruz, ardından filtreleme yapıyoruz.
            # `Workspace_positions(symbols=[api_symbol_ccxt], params=params)` çağrısı bazı borsalarda hata verebilir.
            # En sağlam yol tümünü çekip Python'da filtrelemek.
            all_open_positions_from_exchange = self.exchange.fetch_positions(params=params)

            if not all_open_positions_from_exchange:
                logger.info(f"[{self.exchange_name}] '{api_symbol_ccxt}' için API'den pozisyon detayı verisi gelmedi (muhtemelen pozisyon yok).")
                return None

            for pos_data in all_open_positions_from_exchange:
                if pos_data.get('symbol', '').upper() == api_symbol_ccxt.upper():
                    position_amount_str = pos_data.get('contracts') or pos_data.get('amount')
                    entry_price_str = pos_data.get('entryPrice')
                    unrealized_pnl_str = pos_data.get('unrealizedPnl')

                    try:
                        position_amount = float(position_amount_str) if position_amount_str else 0.0
                        entry_price = float(entry_price_str) if entry_price_str else 0.0
                        unrealized_pnl = float(unrealized_pnl_str) if unrealized_pnl_str else 0.0
                    except ValueError:
                        logger.warning(f"[{self.exchange_name}] '{api_symbol_ccxt}' için pozisyon miktarı/giriş fiyatı/PnL float'a çevrilemedi.")
                        continue

                    if position_amount != 0:
                        logger.info(f"[{self.exchange_name}] Pozisyon detayı API'den alındı ({api_symbol_ccxt}): Giriş={entry_price:.8f}, PnL={unrealized_pnl:.2f}, Miktar={position_amount}")
                        return {
                            'symbol': symbol,
                            'entry_price': entry_price,
                            'unrealized_pnl': unrealized_pnl,
                            'position_amt': position_amount,
                            'raw_data': pos_data.get('info', {})
                        }
            
            logger.info(f"[{self.exchange_name}] '{api_symbol_ccxt}' için aktif pozisyon bulunamadı (yanıt listesi işlendi).")
            return None


        except AuthenticationError as e:
            logger.error(f"[{self.exchange_name}] Kimlik doğrulama hatası - pozisyon detayı alınamadı ({api_symbol_ccxt}): {e}")
        except RateLimitExceeded as e:
            logger.warning(f"[{self.exchange_name}] Rate limit aşıldı - pozisyon detayı alınamadı ({api_symbol_ccxt}): {e}")
        except (NetworkError, ExchangeNotAvailable, OnMaintenance, BadResponse, NullResponse, BadSymbol) as e:
            logger.error(f"[{self.exchange_name}] Ağ/Borsa/Yanıt/Sembol Hatası - pozisyon detayı alınamadı ({api_symbol_ccxt}): {e}")
        except ExchangeError as e:
            logger.error(f"[{self.exchange_name}] Genel Borsa Hatası - pozisyon detayı alınamadı ({api_symbol_ccxt}): {e}")
        except Exception as e:
            logger.error(f"[{self.exchange_name}] Pozisyon detayı ({api_symbol_ccxt}) alınırken beklenmedik genel hata: {e}", exc_info=True)

        return None
    
    def is_demo_mode(self):
        return False

    def __del__(self):
        logger.debug(f"ExchangeAPI ({self.exchange_name}) __del__ çağrıldı.")
        self.close()