# strategies/simple_moving_average_strategy.py

import logging
import pandas as pd # Gerekli: pip install pandas
from decimal import Decimal, InvalidOperation # Hassas karşılaştırmalar için (opsiyonel)

# --- Düzeltme: Logger'ı doğrudan core modülünden al ---
try:
    # BaseStrategy'nin bulunduğu dizinden import et (veya tam yolu belirt)
    from .base_strategy import BaseStrategy # Göreceli import
    from core.logger import setup_logger
    logger = setup_logger('sma_strategy')
except ImportError:
    # Eğer core.logger veya base_strategy import edilemezse
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('sma_strategy_fallback')
    logger.warning("core.logger veya base_strategy bulunamadı, fallback logger kullanılıyor.")
    # BaseStrategy'nin var olmasını sağla ki sınıf tanımı hata vermesin
    if 'BaseStrategy' not in globals():
         from abc import ABC, abstractmethod
         class BaseStrategy(ABC):
            def __init__(self, *args, **kwargs): pass
            @abstractmethod
            def analyze(self, market_data): pass
            @abstractmethod
            def generate_signal(self, market_data): pass
# --- /Düzeltme ---

# Type hinting için
from typing import Dict, Any, Optional

class SimpleMovingAverageStrategy(BaseStrategy):
    """
    Basit Hareketli Ortalama (SMA) kesişim stratejisi.
    Fiyat, kısa periyotlu SMA'yı yukarı kestiğinde AL (long),
    aşağı kestiğinde SAT (short) sinyali üretir.
    """

    def __init__(self, symbol: str, parameters: Optional[Dict[str, Any]] = None):
        """
        SMA stratejisi başlatıcısı.

        Args:
            symbol (str): Stratejinin çalışacağı alım satım çifti.
            parameters (dict, optional): Stratejiye özel parametreler:
                'sma_period' (int): SMA hesaplama periyodu (Varsayılan: 20).
                'max_history' (int): Bellekte tutulacak maksimum fiyat kaydı sayısı
                                      (Varsayılan: sma_period * 5).
        """
        super().__init__(symbol, parameters)
        # Parametreleri al, yoksa varsayılanları kullan ve doğrula
        try:
            self.sma_period = int(self.parameters.get('sma_period', 20))
            if self.sma_period <= 1:
                 logger.warning(f"Geçersiz SMA periyodu ({self.sma_period}), varsayılan 20 kullanılacak.")
                 self.sma_period = 20
        except (ValueError, TypeError):
             logger.warning(f"Geçersiz SMA periyodu formatı ({self.parameters.get('sma_period')}), varsayılan 20 kullanılacak.")
             self.sma_period = 20

        # Bellek yönetimi için geçmiş veri sınırı
        self.max_history_default = self.sma_period * 5 # Hesaplama için yeterli ve biraz fazlası
        try:
             self.max_history = int(self.parameters.get('max_history', self.max_history_default))
             if self.max_history < self.sma_period + 2: # Sinyal üretimi için en az bu kadar lazım
                  logger.warning(f"max_history ({self.max_history}) SMA periyodu için çok küçük, {self.sma_period + 2}'ye ayarlandı.")
                  self.max_history = self.sma_period + 2
        except (ValueError, TypeError):
             logger.warning(f"Geçersiz max_history formatı ({self.parameters.get('max_history')}), varsayılan {self.max_history_default} kullanılacak.")
             self.max_history = self.max_history_default

        # Fiyat geçmişini saklamak için DataFrame
        # Timestamp'ı index yapmak sorguları hızlandırabilir ama ekleme/silmeyi yavaşlatabilir.
        # Şimdilik basit sütunlarla devam edelim.
        self.price_history = pd.DataFrame(columns=['timestamp', 'price'])
        # DataFrame'e eklerken kullanılacak sütun tiplerini belirlemek iyi olabilir:
        # self.price_history = pd.DataFrame(columns=['timestamp', 'price']).astype({'timestamp': 'int64', 'price': 'float64'})

        logger.info(f"SMA Stratejisi ({self.symbol}) başlatıldı: Periyot={self.sma_period}, Max Geçmiş={self.max_history}")


    def analyze(self, market_data: Dict[str, Any]):
        """
        Güncel piyasa verisini alır, geçmişe ekler ve DataFrame'i yönetir.

        Args:
            market_data (dict): Analiz edilecek güncel piyasa verisi.
                                Gerekli anahtarlar: 'timestamp', 'price'.
        """
        ts = market_data.get('timestamp')
        price = market_data.get('price')

        # Gerekli veriler var mı ve geçerli mi kontrol et
        if ts is None or price is None:
             logger.warning(f"analyze: Eksik market verisi ({self.symbol}): {market_data}")
             return
        try:
             # Fiyatı float yapmayı dene
             current_price = float(price)
             current_ts = int(ts) # Zaman damgasını int yap
        except (ValueError, TypeError):
             logger.warning(f"analyze: Geçersiz fiyat ({price}) veya timestamp ({ts}) formatı ({self.symbol}).")
             return

        # Yeni veriyi DataFrame'e ekle
        # ignore_index=True eski index'i korumaz, yeni sıralı index oluşturur.
        # Performans için append yerine pd.concat kullanmak daha iyi olabilir ama tek satır için fark az.
        # new_data = pd.DataFrame([{'timestamp': current_ts, 'price': current_price}])
        # self.price_history = pd.concat([self.price_history, new_data], ignore_index=True)
        # DataFrame.loc ile eklemek de bir yöntem:
        next_index = len(self.price_history)
        self.price_history.loc[next_index] = {'timestamp': current_ts, 'price': current_price}


        # --- İyileştirme: Geçmiş Veri Sınırlama ---
        # Eğer geçmiş veri sayısı belirlenen maksimumu aştıysa, en eski verileri sil.
        current_len = len(self.price_history)
        if current_len > self.max_history:
            # Silinecek satır sayısı
            rows_to_drop = current_len - self.max_history
            # En baştaki 'rows_to_drop' kadar satırı atla ve kalanını al (daha verimli olabilir)
            self.price_history = self.price_history.iloc[rows_to_drop:].reset_index(drop=True)
            # logger.debug(f"Geçmiş veri sınırlandı ({self.symbol}). Yeni boyut: {len(self.price_history)}")

        # logger.debug(f"Fiyat geçmişi güncellendi ({self.symbol}). Boyut: {len(self.price_history)}. Son Fiyat: {current_price}")


    def generate_signal(self, market_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        SMA kesişimine göre AL veya SAT sinyali üretir.

        Args:
            market_data (dict): Analiz için kullanılan en güncel piyasa verisi.

        Returns:
            dict or None: Sinyal sözlüğü veya sinyal yoksa None.
                          'amount' her zaman 0.0 olarak döndürülür.
        """
        current_len = len(self.price_history)

        # Sinyal üretimi için yeterli veri var mı? (SMA periyodu + 1 önceki değer)
        required_len = self.sma_period + 1
        if current_len < required_len:
            # logger.debug(f"Sinyal üretimi için yeterli geçmiş veri yok ({self.symbol}): {current_len}/{required_len}")
            return None

        # Hesaplamalar için sadece gerekli son veriyi al (performans için)
        # Rolling zaten pencereyi kaydıracağı için tüm history'yi vermek yeterli.
        try:
            # Son iki SMA değerini hesapla
            # .iloc[-2:] son iki satırı alır, rolling().mean() her pencere için ortalamayı hesaplar.
            sma_values = self.price_history['price'].rolling(window=self.sma_period).mean().iloc[-2:]
            if sma_values.isnull().any(): # Eğer ilk SMA değerleri NaN ise (başlangıçta olur)
                 logger.debug(f"SMA hesaplamasında NaN değerleri var ({self.symbol}), sinyal üretilemez.")
                 return None

            latest_sma = sma_values.iloc[-1]
            previous_sma = sma_values.iloc[-2]

            # Son iki fiyatı al
            latest_price = self.price_history['price'].iloc[-1]
            previous_price = self.price_history['price'].iloc[-2]

        except IndexError:
            logger.error(f"Fiyat/SMA geçmişi okunurken Index Hatası ({self.symbol}). Geçmiş boyutu: {current_len}")
            return None
        except Exception as e:
             logger.error(f"SMA veya fiyat alınırken hata ({self.symbol}): {e}", exc_info=True)
             return None


        signal = None
        # Karşılaştırmaları Decimal ile yapmak daha güvenli olabilir ama float da yeterli olabilir
        # latest_price_dec = Decimal(str(latest_price)); latest_sma_dec = Decimal(str(latest_sma))
        # previous_price_dec = Decimal(str(previous_price)); previous_sma_dec = Decimal(str(previous_sma))

        # Alım Sinyali: Fiyat SMA'yı yukarı keserse (Önceki <= SMA, Şimdiki > SMA)
        if previous_price <= previous_sma and latest_price > latest_sma:
             logger.info(f"AL Sinyali ({self.symbol}): Fiyat={latest_price:.4f} > SMA={latest_sma:.4f} (Önceki: F={previous_price:.4f} <= SMA={previous_sma:.4f})")
             signal = {
                 'symbol': self.symbol,
                 'side': 'buy',
                 'type': 'market', # Varsayılan piyasa emri
                 # --- Düzeltme: Miktar her zaman 0.0 olmalı ---
                 'amount': 0.0,
                 # --- /Düzeltme ---
                 'price': latest_price, # Bilgi amaçlı güncel fiyat
                 'stop_loss': None, # Strateji SL/TP üretmiyor
                 'take_profit': None
             }

        # Satım Sinyali: Fiyat SMA'yı aşağı keserse (Önceki >= SMA, Şimdiki < SMA)
        elif previous_price >= previous_sma and latest_price < latest_sma:
             logger.info(f"SAT Sinyali ({self.symbol}): Fiyat={latest_price:.4f} < SMA={latest_sma:.4f} (Önceki: F={previous_price:.4f} >= SMA={previous_sma:.4f})")
             signal = {
                 'symbol': self.symbol,
                 'side': 'sell',
                 'type': 'market',
                 # --- Düzeltme: Miktar her zaman 0.0 olmalı ---
                 'amount': 0.0,
                 # --- /Düzeltme ---
                 'price': latest_price,
                 'stop_loss': None,
                 'take_profit': None
             }
        # else: logger.debug(f"Sinyal koşulu yok ({self.symbol}): F={latest_price:.4f}, SMA={latest_sma:.4f}")


        return signal


# Bu dosyanın tek başına çalıştırılması için test bloğu (önceki haliyle iyi görünüyor)
if __name__ == '__main__':
    print("SimpleMovingAverageStrategy Test Başlatılıyor...")
     # Test için basit logger
    if 'setup_logger' not in globals():
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        logger = logging.getLogger('sma_strategy_test')
        # BaseStrategy'nin mock'unu tanımla
        from abc import ABC, abstractmethod
        class BaseStrategy(ABC):
           def __init__(self, *args, **kwargs): pass
           @abstractmethod
           def analyze(self, market_data): pass
           @abstractmethod
           def generate_signal(self, market_data): pass

    # Mock veri sağlayıcı (önceki gibi)
    class MockMarketDataSource:
        def __init__(self, initial_price=24000, price_steps=None):
             self._current_price = initial_price
             # Önceden tanımlı fiyat adımları veya rastgele
             self._price_steps = price_steps if price_steps else [50, 60, -40, 70, 80, -60, 90, 100, -80, 110, -50, 40, -70, 120, -100, 130, -30, 20, -90, 140]
             self._timestamp = int(time.time())
             self._step_index = 0

        def get_latest_data(self):
            self._timestamp += 60 # 1 dakika ekle
            step = self._price_steps[self._step_index % len(self._price_steps)]
            self._current_price += step
            self._step_index += 1
            # Rastgelelik ekleyebiliriz
            # self._current_price += random.uniform(-20, 20)
            return {'timestamp': self._timestamp, 'price': round(self._current_price, 2)}


    # Stratejiyi oluştur (daha kısa periyot ve tarih limiti ile test)
    test_period = 5
    test_history = 10
    sma_strategy = SimpleMovingAverageStrategy('TEST/USDT', parameters={'sma_period': test_period, 'max_history': test_history})
    print(f"Test Parametreleri: SMA Periyot={sma_strategy.sma_period}, Max Geçmiş={sma_strategy.max_history}")

    data_source = MockMarketDataSource(initial_price=1000)

    # Simülasyon döngüsü
    print("\nSimülasyon Başlıyor:")
    print("-" * 60)
    print("{:<5} | {:<10} | {:<10} | {:<6} | {}".format("Adım", "Fiyat", "SMA", "Boyut", "Sinyal"))
    print("-" * 60)

    for i in range(20): # 20 adım simüle et
        latest_data = data_source.get_latest_data()
        sma_strategy.analyze(latest_data)
        signal = sma_strategy.generate_signal(latest_data)

        current_price = latest_data['price']
        history_size = len(sma_strategy.price_history)
        sma_value = "N/A"
        if history_size >= sma_strategy.sma_period:
             try:
                  sma_value_calc = sma_strategy.price_history['price'].rolling(window=sma_strategy.sma_period).mean().iloc[-1]
                  sma_value = f"{sma_value_calc:.2f}"
             except: sma_value = "Hata" # Hesaplama hatası olursa

        signal_str = "Yok"
        if signal: signal_str = f"{signal['side'].upper()}"

        print("{:<5} | {:<10.2f} | {:<10} | {:<6} | {}".format(i+1, current_price, sma_value, history_size, signal_str))

        # Geçmiş boyutunun max_history'yi aşmadığını kontrol et (test)
        assert history_size <= sma_strategy.max_history

    print("-" * 60)
    print("Simülasyon Tamamlandı.")
    print("\nSon Fiyat Geçmişi:")
    print(sma_strategy.price_history.tail()) # Son 5 veriyi göster

    print("\nSimpleMovingAverageStrategy Test Tamamlandı.")