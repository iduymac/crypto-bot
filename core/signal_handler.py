# core/signal_handler.py

import json
import logging

# --- Logger Düzeltmesi ---
try:
    from core.logger import setup_logger
    logger = setup_logger('signal_handler')
except ImportError:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('signal_handler_fallback')
    logger.warning("core.logger bulunamadı, fallback logger kullanılıyor.")
# --- /Logger Düzeltmesi ---

class SignalHandler:
    def __init__(self, signal_source='tradingview'):
        """
        Alım satım sinyallerini yöneten sınıf.
        """
        self.signal_source = signal_source.lower()
        # self.signal_format = 'json' # Bu satır gereksiz, gelen verinin dict olup olmadığını kontrol ediyoruz.
        logger.info(f"SignalHandler başlatıldı (Kaynak: {self.signal_source})")

    def parse_signal(self, signal_data):
        """
        Gelen sinyali kaynağa göre ayrıştırır ve standart bir formata dönüştürür.
        """
        if not isinstance(signal_data, dict):
             logger.error(f"parse_signal: Gelen sinyal verisi sözlük (dict) değil! Tip: {type(signal_data)}. Sinyal: {str(signal_data)[:200]}")
             return None

        parser_method = None
        # TradingView'dan veya genel webhook'tan gelen sinyalleri _parse_generic_webhook_signal ile işleyelim
        if self.signal_source == 'tradingview' or self.signal_source == 'webhook':
            parser_method = self._parse_generic_webhook_signal
        else:
             logger.warning(f"Desteklenmeyen veya bilinmeyen sinyal kaynağı: '{self.signal_source}'. Sinyal yok sayılıyor. Gelen veri: {signal_data}")
             return None

        if parser_method is None: # Ekstra güvenlik kontrolü
            logger.error(f"Sinyal kaynağı '{self.signal_source}' için ayrıştırıcı metot bulunamadı.")
            return None

        try:
            parsed_data = parser_method(signal_data) # signal_data zaten dict
            if parsed_data:
                 logger.info(f"Sinyal başarıyla ayrıştırıldı ({self.signal_source}): {parsed_data}")
            # Eğer parser None döndürdüyse (içeride loglanmış olmalı), parse_signal da None döndürür.
            return parsed_data
        except Exception as e:
            logger.error(f"{self.signal_source} sinyali ayrıştırılırken (parser_method çağrısında) beklenmedik hata: {e}. Gelen veri: {signal_data}", exc_info=True)
            return None

    def _parse_generic_webhook_signal(self, signal: dict):
        """
        TradingView'den veya genel bir webhook'tan geldiği varsayılan JSON formatındaki sinyali ayrıştırır.
        'action', 'ticker', 'side' gibi temel alanları okur.
        'order_type' ve 'quantity' için varsayılanlar kullanır. SL/TP bilgilerini de alır.
        """
        logger.debug(f"Genel webhook sinyali ayrıştırılıyor: {signal}")

        # --- Zorunlu ve Opsiyonel Alanları Oku ---
        action_raw = signal.get('action')
        ticker_raw = signal.get('ticker')
        side_raw = signal.get('side') # Opsiyonel olabilir (close için)
        order_type_raw = signal.get('order_type', 'market') # Varsayılan 'market'
        quantity_raw = signal.get('quantity', '0.0') # Varsayılan '0.0' (bot hesaplayacak)
        sl_raw = signal.get('stop_loss')
        tp_raw = signal.get('take_profit')
        signal_id_for_log = signal.get('signal_id', 'N/A')


        # --- Alan Doğrulamaları ve Dönüşümleri ---
        # Action
        if not action_raw or not isinstance(action_raw, str):
            logger.error(f"Eksik veya geçersiz 'action' alanı. Sinyal: {signal}")
            return None
        action = action_raw.strip().lower()
        if action not in ['open', 'close']:
            logger.error(f"Geçersiz 'action' değeri: '{action_raw}'. 'open' veya 'close' bekleniyordu. Sinyal: {signal}")
            return None

        # Ticker (Symbol)
        if not ticker_raw or not isinstance(ticker_raw, str):
            logger.error(f"Eksik veya geçersiz 'ticker' alanı. Sinyal: {signal}")
            return None
        symbol = ticker_raw.strip().upper() # Sembol her zaman büyük harf olsun

        # Side (Yön)
        side = None # Varsayılan None
        if action == 'open': # Sadece 'open' action'ı için 'side' zorunlu
            if not side_raw or not isinstance(side_raw, str):
                logger.error(f"Açma işlemi için eksik veya geçersiz 'side' alanı. Sinyal: {signal}")
                return None
            side = side_raw.strip().lower()
            if side not in ['buy', 'sell']:
                logger.error(f"Geçersiz 'side' değeri: '{side_raw}'. 'buy' veya 'sell' bekleniyordu. Sinyal: {signal}")
                return None
        elif action == 'close' and side_raw and isinstance(side_raw, str): # Kapatma için 'side' varsa al, yoksa None kalır
            temp_side = side_raw.strip().lower()
            if temp_side in ['buy', 'sell']:
                side = temp_side # Bot bunu kullanmayabilir ama bilgi olarak tutulabilir
            else:
                logger.warning(f"Kapatma sinyalinde geçersiz 'side' değeri: '{side_raw}'. Yok sayılıyor.")

        # Order Type
        order_type = str(order_type_raw).strip().lower()
        if order_type not in ['market', 'limit']:
             logger.warning(f"Desteklenmeyen 'order_type': '{order_type_raw}'. 'market' olarak ayarlandı.")
             order_type = 'market'

        # Quantity (Miktar) - Bot hesaplayacağı için her zaman 0.0
        parsed_amount = 0.0
        if action == 'open': # Sadece pozisyon açarken loglayalım
            logger.info(f"Sinyalden gelen 'quantity' (bilgi amaçlı, bot miktarı kendi hesaplayacak): {quantity_raw} (Sinyal ID: {signal_id_for_log})")

        # Stop Loss
        stop_loss_price = None
        if sl_raw is not None and str(sl_raw).strip() != '':
             try:
                  sl_price_val = float(str(sl_raw).replace(',', '.')) # Virgülü noktaya çevir
                  if sl_price_val > 0:
                       stop_loss_price = sl_price_val
                  else: logger.warning(f"Sinyaldeki 'stop_loss' ({sl_raw}) pozitif bir sayı değil.")
             except (ValueError, TypeError):
                  logger.warning(f"Sinyaldeki 'stop_loss' ({sl_raw}) geçerli bir fiyata (float) çevrilemedi.")

        # Take Profit
        take_profit_price = None
        if tp_raw is not None and str(tp_raw).strip() != '':
             try:
                  tp_price_val = float(str(tp_raw).replace(',', '.')) # Virgülü noktaya çevir
                  if tp_price_val > 0:
                       take_profit_price = tp_price_val
                  else: logger.warning(f"Sinyaldeki 'take_profit' ({tp_raw}) pozitif bir sayı değil.")
             except (ValueError, TypeError):
                  logger.warning(f"Sinyaldeki 'take_profit' ({tp_raw}) geçerli bir fiyata (float) çevrilemedi.")

        # --- Ayrıştırılmış Sinyal Sözlüğünü Oluştur ---
        parsed_signal = {
            'action': action,
            'symbol': symbol,
            'side': side,           # 'close' action'ı için None olabilir, TradeManager pozisyon yönünü kendi bulur
            'type': order_type,
            'amount': parsed_amount, # Her zaman 0.0, TradeManager pozisyon açarken miktarı kendi hesaplayacak
            'stop_loss': stop_loss_price, # Fiyat olarak None veya float
            'take_profit': take_profit_price, # Fiyat olarak None veya float
            'raw_signal_preview': {k: signal.get(k) for k in ['action', 'ticker','side','order_type','quantity','stop_loss','take_profit', 'signal_id'] if k in signal} # Önizleme için
        }
        return parsed_signal

# Test bloğu (isterseniz bu kısmı da güncelleyebiliriz veya silebilirsiniz)
if __name__ == '__main__':
    print("SignalHandler Test Başlatılıyor...")
    if 'setup_logger' not in globals():
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        logger = logging.getLogger('signal_handler_test')

    handler = SignalHandler(signal_source='webhook') # veya 'tradingview'

    print("\n--- Geçerli Açma Sinyalleri ---")
    valid_open_1 = {"action": "open", "ticker": "BTC/USDT", "side": "buy", "order_type": "market", "quantity": "0.01", "signal_id": "O1"}
    parsed_vo1 = handler.parse_signal(valid_open_1)
    print(f"Açma Sinyali 1: {valid_open_1} -> Parsed: {parsed_vo1}")
    assert parsed_vo1 and parsed_vo1['action'] == 'open' and parsed_vo1['side'] == 'buy' and parsed_vo1['amount'] == 0.0

    valid_open_2 = {"action": "open", "ticker": "ETHUSDT", "side": "SELL"} # order_type ve quantity eksik
    parsed_vo2 = handler.parse_signal(valid_open_2)
    print(f"Açma Sinyali 2: {valid_open_2} -> Parsed: {parsed_vo2}")
    assert parsed_vo2 and parsed_vo2['action'] == 'open' and parsed_vo2['side'] == 'sell' and parsed_vo2['type'] == 'market'

    print("\n--- Geçerli Kapatma Sinyalleri ---")
    # Orijinal TradingView sinyalinizde kapatma için order_type ve quantity vardı, onları da ekleyelim testlere
    valid_close_1 = {"action": "close", "ticker": "BTC/USDT", "order_type": "market", "quantity": "0.0", "signal_id": "UTB_CLOSE_LONG"}
    parsed_vc1 = handler.parse_signal(valid_close_1)
    print(f"Kapatma Sinyali 1: {valid_close_1} -> Parsed: {parsed_vc1}")
    assert parsed_vc1 and parsed_vc1['action'] == 'close' and parsed_vc1['symbol'] == 'BTC/USDT' and parsed_vc1['side'] is None

    valid_close_2 = {"action": "close", "ticker": "ETHUSDT", "side": "buy", "signal_id":"C2"} # Kapatma için side olsa da işlenir, ama kullanılmaz
    parsed_vc2 = handler.parse_signal(valid_close_2)
    print(f"Kapatma Sinyali 2: {valid_close_2} -> Parsed: {parsed_vc2}")
    assert parsed_vc2 and parsed_vc2['action'] == 'close' and parsed_vc2['symbol'] == 'ETHUSDT' and parsed_vc2['side'] == 'buy'

    print("\n--- Geçersiz Sinyaller ---")
    invalid_1 = {"ticker": "BTC/USDT", "side": "buy"} # action eksik
    parsed_i1 = handler.parse_signal(invalid_1)
    print(f"Geçersiz Sinyal 1: {invalid_1} -> Parsed: {parsed_i1}")
    assert parsed_i1 is None

    invalid_2 = {"action": "open", "side": "buy"} # ticker eksik
    parsed_i2 = handler.parse_signal(invalid_2)
    print(f"Geçersiz Sinyal 2: {invalid_2} -> Parsed: {parsed_i2}")
    assert parsed_i2 is None
    
    invalid_3 = {"action": "delete", "ticker": "LTC/USDT"} # action geçersiz
    parsed_i3 = handler.parse_signal(invalid_3)
    print(f"Geçersiz Sinyal 3: {invalid_3} -> Parsed: {parsed_i3}")
    assert parsed_i3 is None
    
    invalid_4 = {"action":"open", "ticker":"XRPUSDT"} # side eksik (open için zorunlu)
    parsed_i4 = handler.parse_signal(invalid_4)
    print(f"Geçersiz Sinyal 4: {invalid_4} -> Parsed: {parsed_i4}")
    assert parsed_i4 is None

    print("\nSignalHandler Test Tamamlandı.")