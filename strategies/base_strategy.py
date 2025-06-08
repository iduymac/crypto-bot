# strategies/base_strategy.py

from abc import ABC, abstractmethod
import logging

# --- Düzeltme: Logger'ı doğrudan core modülünden al ---
try:
    from core.logger import setup_logger
    logger = setup_logger('base_strategy')
except ImportError:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('base_strategy_fallback')
    logger.warning("core.logger bulunamadı, fallback logger kullanılıyor.")
# --- /Düzeltme ---

# Type hinting için (opsiyonel ama faydalı)
from typing import Dict, Any, Optional

class BaseStrategy(ABC):
    """
    Tüm alım satım stratejileri için temel soyut sınıf (Abstract Base Class).
    Yeni stratejiler bu sınıftan miras almalı ve soyut metotları implemente etmelidir.
    """

    # Stratejinin BotCore veya diğer bileşenlere erişmesi gerekiyorsa,
    # __init__ metoduna ilgili referanslar eklenebilir.
    # Örnek: def __init__(self, symbol, parameters=None, bot_ref=None):
    #            self.bot_ref = bot_ref
    def __init__(self, symbol: str, parameters: Optional[Dict[str, Any]] = None):
        """
        Stratejinin başlatıcısı.

        Args:
            symbol (str): Stratejinin çalışacağı alım satım çifti (örn. 'BTC/USDT').
            parameters (dict, optional): Stratejiye özel yapılandırma parametreleri.
                                         Varsayılan: Boş sözlük.
        """
        if not symbol:
             raise ValueError("Strateji için geçerli bir 'symbol' gereklidir.")
        self.symbol = symbol
        # Gelen parametreleri kopyalamak, dışarıdaki orijinal sözlüğün değişmesini engeller.
        self.parameters = copy.deepcopy(parameters) if parameters is not None else {}
        logger.info(f"{self.__class__.__name__} stratejisi başlatıldı. Sembol: {self.symbol}, Parametreler: {self.parameters}")

    @abstractmethod
    # Daha fazla bağlam gerekirse: def analyze(self, market_data: Dict[str, Any], current_position: Optional[Dict] = None):
    def analyze(self, market_data: Dict[str, Any]):
        """
        Piyasa verisini analiz eder ve sinyal üretimi için gerekli hesaplamaları yapar veya durumu günceller.
        Bu metot genellikle stratejinin dahili durumunu (örn. indikatör değerleri) günceller.
        Miras alan sınıflar bu metodu implemente etmelidir.

        Args:
            market_data (dict): Analiz edilecek güncel veya geçmiş piyasa verisi.
                                Yapısı stratejiye ve veri kaynağına göre değişir.
                                Örnek: {'timestamp': 1678886400, 'price': 25000.5, 'volume': 100}
                                Veya OHLCV mum çubuğu verisi olabilir.
            # current_position (dict, optional): Varsa, bu sembol için mevcut açık pozisyon bilgisi.
        """
        # Bu metot soyut olduğu için burada bir implementasyon yoktur.
        # Miras alan sınıflar kendi analiz mantığını buraya eklemelidir.
        # Örnek: self.sma_value = calculate_sma(...)
        pass

    @abstractmethod
    # Daha fazla bağlam gerekirse: def generate_signal(self, market_data: Dict[str, Any], current_position: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    def generate_signal(self, market_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        En son analiz sonuçlarına ve piyasa verisine dayanarak bir alım satım sinyali üretir.
        Miras alan sınıflar bu metodu implemente etmelidir.

        Args:
            market_data (dict): Analiz için kullanılan en güncel piyasa verisi.
            # current_position (dict, optional): Varsa, bu sembol için mevcut açık pozisyon bilgisi.

        Returns:
            dict or None: Eğer bir alım/satım sinyali oluşmuşsa, TradeManager'ın anlayacağı
                          formatta bir sinyal sözlüğü döndürür.
                          Örnek Sinyal: {
                              'symbol': self.symbol,       # Zorunlu
                              'side': 'buy' veya 'sell',   # Zorunlu
                              'type': 'market' veya 'limit', # Zorunlu
                              'amount': 0.0,              # Zorunlu (RiskManager hesaplayacaksa 0.0 olabilir)
                              'stop_loss': 50000.0,       # Opsiyonel (Fiyat seviyesi)
                              'take_profit': 60000.0      # Opsiyonel (Fiyat seviyesi)
                              # 'limit_price': 55000.0    # Eğer type='limit' ise gerekli olabilir
                          }
                          Eğer sinyal yoksa None döndürür.
        """
        # Bu metot soyut olduğu için burada bir implementasyon yoktur.
        # Miras alan sınıflar kendi sinyal üretme mantığını buraya eklemelidir.
        pass

    # --- Opsiyonel Yardımcı Metotlar ---
    # Stratejilerin ortak kullanabileceği bazı metotlar buraya eklenebilir.

    # def update_parameters(self, new_parameters: Dict[str, Any]):
    #     """Çalışma sırasında strateji parametrelerini günceller."""
    #     if isinstance(new_parameters, dict):
    #         self.parameters.update(new_parameters)
    #         logger.info(f"{self.__class__.__name__} ({self.symbol}) parametreleri güncellendi: {self.parameters}")
    #     else:
    #         logger.warning(f"update_parameters: Geçersiz new_parameters tipi ({type(new_parameters)})")

    # def get_parameter(self, key: str, default: Any = None) -> Any:
    #     """ Belirli bir parametre değerini alır. """
    #     return self.parameters.get(key, default)


# Bu dosyanın tek başına çalıştırılması genellikle anlamlı değildir,
# çünkü soyut sınıfın bir örneği oluşturulamaz.
# Testler, bu sınıftan türetilmiş somut sınıflar üzerinden yapılmalıdır.
if __name__ == '__main__':
    print("BaseStrategy soyut bir sınıftır ve doğrudan çalıştırılamaz veya örneği oluşturulamaz.")
    print("Bu sınıftan miras alan ve soyut metotları implemente eden somut bir strateji sınıfı oluşturmalısınız.")

    # Örnek somut sınıf (test amaçlı)
    import copy # Örnek için import

    class MyConcreteStrategy(BaseStrategy):
        def __init__(self, symbol, parameters=None):
            super().__init__(symbol, parameters)
            self.last_price = None
            self.buy_threshold = self.parameters.get('buy_at', 50000)
            self.sell_threshold = self.parameters.get('sell_at', 48000)
            print(f"MyConcreteStrategy: Buy={self.buy_threshold}, Sell={self.sell_threshold}")

        def analyze(self, market_data: Dict[str, Any]):
            price = market_data.get('price')
            if price is not None:
                self.last_price = float(price)
                print(f"MyConcreteStrategy ({self.symbol}): Analiz edildi - Son Fiyat: {self.last_price}")
            else:
                 print(f"MyConcreteStrategy ({self.symbol}): Analiz - Fiyat verisi yok.")


        def generate_signal(self, market_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            if self.last_price is None:
                print(f"MyConcreteStrategy ({self.symbol}): Sinyal - Fiyat yok, sinyal üretilemez.")
                return None

            signal = None
            if self.last_price > self.buy_threshold:
                 print(f"MyConcreteStrategy ({self.symbol}): Sinyal - AL koşulu sağlandı ({self.last_price} > {self.buy_threshold})")
                 signal = {'symbol': self.symbol, 'side': 'buy', 'type': 'market', 'amount': 0.0} # Miktar 0.0
            elif self.last_price < self.sell_threshold:
                 print(f"MyConcreteStrategy ({self.symbol}): Sinyal - SAT koşulu sağlandı ({self.last_price} < {self.sell_threshold})")
                 signal = {'symbol': self.symbol, 'side': 'sell', 'type': 'market', 'amount': 0.0}
            else:
                 print(f"MyConcreteStrategy ({self.symbol}): Sinyal - Koşul sağlanmadı.")

            return signal

    # Test
    print("\nSomut Strateji Testi:")
    try:
        strategy_params = {'buy_at': 55000, 'sell_at': 54000}
        my_strategy = MyConcreteStrategy('BTC/USDT', strategy_params)

        data1 = {'price': 56000}
        my_strategy.analyze(data1)
        signal1 = my_strategy.generate_signal(data1)
        print(f"Veri: {data1}, Üretilen Sinyal: {signal1}")
        assert signal1 is not None and signal1['side'] == 'buy'

        data2 = {'price': 54500}
        my_strategy.analyze(data2)
        signal2 = my_strategy.generate_signal(data2)
        print(f"Veri: {data2}, Üretilen Sinyal: {signal2}")
        assert signal2 is None

        data3 = {'price': 53000}
        my_strategy.analyze(data3)
        signal3 = my_strategy.generate_signal(data3)
        print(f"Veri: {data3}, Üretilen Sinyal: {signal3}")
        assert signal3 is not None and signal3['side'] == 'sell'

    except Exception as e:
        print(f"Test sırasında hata: {e}")