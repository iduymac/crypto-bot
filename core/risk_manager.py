# core/risk_manager.py

import logging
from decimal import Decimal, InvalidOperation # InvalidOperation ekledik
from datetime import date # timedelta'yı test bloğundan çıkardık, ana kodda gereksiz.
from typing import TYPE_CHECKING, Optional, Dict, Any, Tuple # Tuple ekledik

# --- Logger Kurulumu ---
# Bu bölümün dosyanızda zaten doğru olduğunu varsayıyoruz,
# eğer yoksa veya farklıysa, projenizdeki logger kurulumunu kullanın.
try:
    from core.logger import setup_logger
    logger = setup_logger('risk_manager')
except ImportError:
    # Fallback logger, eğer setup_logger bulunamazsa
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()] # Konsola log basması için
    )
    logger = logging.getLogger('risk_manager_fallback')
    logger.warning(
        "core.logger.setup_logger bulunamadı. Temel fallback logger kullanılıyor. "
        "Loglar sadece konsola basılabilir ve dosya kaydı yapılmayabilir."
    )
# --- /Logger Kurulumu ---

# --- Utils (Yardımcı Fonksiyonlar ve Sabitler) Import ---
# Bu bölüm, projenizdeki 'utils.py' dosyasının konumuna ve içeriğine bağlıdır.
try:
    # Eğer utils.py 'core' klasörünün bir üst dizinindeyse veya Python path'de ise:
    from utils import _to_decimal, DECIMAL_ZERO, DECIMAL_ONE, DECIMAL_HUNDRED
    
    # Eğer utils.py 'core' klasörünün içindeyse, şu şekilde deneyin:
    # from .utils import _to_decimal, DECIMAL_ZERO, DECIMAL_ONE, DECIMAL_HUNDRED
    
    logger.info("RiskManager: 'utils' modülünden yardımcı fonksiyonlar ve sabitler başarıyla import edildi.")
except ImportError as e_utils:
    logger.error(
        f"RiskManager: 'utils' modülü veya içindeki gerekli öğeler import edilemedi! Hata: {e_utils}. "
        "Bu durum, pozisyon büyüklüğü hesaplamaları gibi kritik işlevlerin düzgün çalışmamasına neden olabilir. "
        "Lütfen 'utils.py' dosyasının doğru konumda ve gerekli içeriğe sahip olduğundan emin olun. "
        "Fallback Decimal sabitleri ve _to_decimal fonksiyonu kullanılacak."
    )
    # Fallback (eğer utils import edilemezse, en azından kod çökmesin diye)
    DECIMAL_ZERO = Decimal('0')
    DECIMAL_ONE = Decimal('1')
    DECIMAL_HUNDRED = Decimal('100')

    def _to_decimal(value: Any) -> Optional[Decimal]:
        if value is None: return None
        try: return Decimal(str(value).replace(',', '.'))
        except (InvalidOperation, TypeError, ValueError): return None
        except Exception: return None
# --- /Utils Import ---

# --- Tip Kontrolü için İleriye Dönük Bildirimler ---
if TYPE_CHECKING:
    from core.trade_manager import TradeManager
    # from core.exchange_api import ExchangeAPI # Eğer exchange_api'ye doğrudan erişim gerekiyorsa
# --- /Tip Kontrolü ---

class RiskManager:
    def __init__(self,
                 trade_manager_ref: 'TradeManager',
                 user_config: Dict[str, Any],
                 max_open_positions_fallback: int = 5,
                 max_risk_per_trade_percent_fallback: float = 2.0,
                 max_daily_loss_percent_fallback: float = 10.0):
        """
        Risk yönetimi kurallarını uygulayan sınıf.

        Args:
            trade_manager_ref (TradeManager): TradeManager örneğine referans.
            user_config (Dict[str, Any]): Kullanıcının tam yapılandırma sözlüğü (users.json'dan).
                                            'risk' ve 'trading' anahtarlarını içermelidir.
            max_open_positions_fallback (int): user_config'de ayar yoksa kullanılacak varsayılan.
            max_risk_per_trade_percent_fallback (float): user_config'de ayar yoksa kullanılacak varsayılan.
            max_daily_loss_percent_fallback (float): user_config'de ayar yoksa kullanılacak varsayılan.
        """
        if trade_manager_ref is None:
            logger.critical("RiskManager başlatılamıyor: TradeManager referansı gerekli.")
            raise ValueError("RiskManager için TradeManager referansı gereklidir.")

        if not user_config or not isinstance(user_config, dict):
            logger.critical("RiskManager başlatılamıyor: user_config (kullanıcı ayarları) sözlüğü eksik veya geçersiz.")
            raise ValueError("RiskManager için user_config gereklidir.")

        self.trade_manager = trade_manager_ref
        self.user_config = user_config # Tüm kullanıcı ayarlarını sakla
        self.user_trading_settings = user_config.get('trading', {}) # TradeManager'a da geçiliyor, burada da tutabiliriz.
        risk_settings_from_config = user_config.get('risk', {})

        if not self.user_trading_settings: # trading ayarları yoksa veya boşsa
             logger.warning("RiskManager: Kullanıcı 'trading' ayarları user_config içinde boş veya bulunamadı. Bazı özellikler (örn: PNL referans para birimi) varsayılanları kullanabilir.")
        if not risk_settings_from_config: # risk ayarları yoksa veya boşsa
             logger.warning("RiskManager: Kullanıcı 'risk' ayarları user_config içinde boş veya bulunamadı. Fallback değerler kullanılacak.")

        try:
            # Maksimum Açık Pozisyon Sayısı
            # users.json: {"risk": {"max_open_positions": 3}}
            self.max_open_positions = int(risk_settings_from_config.get('max_open_positions', max_open_positions_fallback))

            # İşlem Başına Maksimum Risk Yüzdesi
            # users.json: {"risk": {"max_risk_per_trade_percent": 1.5}}
            raw_risk_per_trade = risk_settings_from_config.get('max_risk_per_trade_percent', str(max_risk_per_trade_percent_fallback))
            risk_per_trade_dec = _to_decimal(raw_risk_per_trade)
            if risk_per_trade_dec is None or risk_per_trade_dec < DECIMAL_ZERO: # Yüzde negatif olamaz
                logger.warning(f"RiskManager: Geçersiz 'max_risk_per_trade_percent' değeri: '{raw_risk_per_trade}'. "
                               f"Varsayılan {max_risk_per_trade_percent_fallback}% kullanılacak.")
                self.max_risk_per_trade_percent = _to_decimal(str(max_risk_per_trade_percent_fallback))
            else:
                self.max_risk_per_trade_percent = risk_per_trade_dec # Pozitif yüzde olarak sakla (örn: Decimal('1.5'))
            # Bu değer, calculate_position_size metodunda Decimal('100.0')'e bölünerek oran olarak kullanılır.

            # Günlük Maksimum Zarar Yüzdesi ve Limiti
            # users.json: {"risk": {"max_daily_loss_percent": 5.0}}
            raw_daily_loss_perc_config = risk_settings_from_config.get('max_daily_loss_percent', str(max_daily_loss_percent_fallback))
            daily_loss_perc_config_dec = _to_decimal(raw_daily_loss_perc_config)

            if daily_loss_perc_config_dec is None or daily_loss_perc_config_dec < DECIMAL_ZERO: # Yüzde negatif olamaz
                logger.warning(f"RiskManager: Geçersiz 'max_daily_loss_percent' değeri: '{raw_daily_loss_perc_config}'. "
                               f"Varsayılan {max_daily_loss_percent_fallback}% kullanılacak.")
                self.max_daily_loss_limit_percent = _to_decimal(str(max_daily_loss_percent_fallback)) # Pozitif yüzde
            else:
                self.max_daily_loss_limit_percent = daily_loss_perc_config_dec # Pozitif yüzde olarak sakla (örn: Decimal('5.0'))

            # self.max_daily_loss_limit: PNL'in ulaşmaması gereken negatif oransal eşik (örn: -0.05)
            if self.max_daily_loss_limit_percent > DECIMAL_ZERO:
                self.max_daily_loss_limit = -(self.max_daily_loss_limit_percent / DECIMAL_HUNDRED)
            else:
                # Yüzde 0 veya daha küçükse (kullanıcı tarafından böyle ayarlandıysa),
                # günlük zarar limiti kontrolü etkin bir şekilde devre dışı bırakılır.
                self.max_daily_loss_limit = None # Bu, can_open_new_position'da kontrol edilecek
                logger.info("RiskManager: Günlük zarar limiti yüzdesi (max_daily_loss_percent) 0 veya negatif olarak ayarlandığı için "
                            "günlük zarar kontrolü etkin bir şekilde devre dışı bırakıldı.")

            # Başlangıç kontrolleri ve uyarı logları
            if self.max_open_positions <= 0:
                logger.warning("RiskManager: max_open_positions <= 0 olarak ayarlandığı için pozisyon sayısı risk kontrolü etkin değil.")
            if self.max_risk_per_trade_percent <= DECIMAL_ZERO:
                 logger.warning("RiskManager: max_risk_per_trade_percent <= 0 olarak ayarlandığı için pozisyon büyüklüğü riske göre hesaplanmayabilir "
                                "(kullanıcı tanımlı miktar türüne bağlı).")
            # Günlük zarar limiti etkinliği yukarıda loglandı.

        except (ValueError, TypeError, InvalidOperation) as e:
             logger.error(f"RiskManager başlatılırken risk parametreleri işlenirken kritik hata: {e}. "
                          "Kod içinde tanımlı fallback değerler kullanılacak.", exc_info=True)
             # Kod içi fallback'ler (en kötü durum senaryosu)
             self.max_open_positions = int(max_open_positions_fallback) # int dönüşümü garanti
             self.max_risk_per_trade_percent = _to_decimal(str(max_risk_per_trade_percent_fallback)) or Decimal('2.0')
             self.max_daily_loss_limit_percent = _to_decimal(str(max_daily_loss_percent_fallback)) or Decimal('10.0')
             if self.max_daily_loss_limit_percent > DECIMAL_ZERO:
                 self.max_daily_loss_limit = -(self.max_daily_loss_limit_percent / DECIMAL_HUNDRED)
             else:
                 self.max_daily_loss_limit = None

        self._daily_pnl: Decimal = DECIMAL_ZERO  # Günlük birikmiş PNL (quote currency cinsinden)
        self._last_reset_date: date = date.today() # PNL'in en son sıfırlandığı tarih
        self._initial_daily_balance: Optional[Decimal] = None # Günlük PNL yüzdesini hesaplamak için referans bakiye

        logger.info(f"RiskManager başarıyla başlatıldı: "
                    f"Max Açık Pozisyon={self.max_open_positions}, "
                    f"İşlem Başına Risk %={self.max_risk_per_trade_percent:.2f}, "
                    f"Günlük Max Zarar %={self.max_daily_loss_limit_percent:.2f} "
                    f"(Oransal Limit: {self.max_daily_loss_limit if self.max_daily_loss_limit is not None else 'Devre Dışı'})")
        logger.debug(f"RiskManager - Kullanıcı İşlem Ayarları (trading): {self.user_trading_settings}")
        logger.debug(f"RiskManager - Kullanıcı Risk Ayarları (risk): {risk_settings_from_config}")

    def _reset_daily_pnl_if_needed(self) -> None:
        """
        Gün değiştiyse günlük PNL'i (self._daily_pnl) ve kaydedilmiş gün başı
        referans bakiyesini (self._initial_daily_balance) sıfırlar.
        """
        today = date.today()
        if today != self._last_reset_date:
            logger.info(f"RiskManager: Yeni gün ({today}) algılandı. "
                        f"Günlük PNL (önceki: {self._daily_pnl:.4f}) ve "
                        f"gün başı referans bakiye (önceki: {self._initial_daily_balance}) sıfırlanıyor.")
            self._daily_pnl = DECIMAL_ZERO
            self._initial_daily_balance = None # Yeni gün için referans bakiye yeniden belirlenmeli
            self._last_reset_date = today
            # Not: _initial_daily_balance'ın yeniden ayarlanması genellikle _get_current_daily_pnl_percent
            # metodu içinde, günün ilk sorgusunda veya işleminde yapılır.

    def _get_current_daily_pnl_percent(self) -> Optional[Decimal]:
        """
        Günlük PNL'in (self._daily_pnl) gün başındaki referans bakiyesine (self._initial_daily_balance)
        göre yüzdesel oranını hesaplar.

        Returns:
            Optional[Decimal]: Hesaplanan PNL yüzdesi/oranı (örn: -0.05 Decimal('-0.05') olarak),
                               veya hesaplanamazsa None.
        """
        self._reset_daily_pnl_if_needed() # Her sorguda gün kontrolü yap ve gerekirse sıfırla

        # Adım 1: Gün başı referans bakiye (self._initial_daily_balance) ayarlanmamışsa, ayarla.
        if self._initial_daily_balance is None:
            logger.debug("RiskManager: _get_current_daily_pnl_percent - Gün başı referans bakiye (self._initial_daily_balance) "
                         "henüz ayarlanmamış. Alınmaya çalışılacak...")
            
            # Gerekli bileşenlerin varlığını kontrol et
            if not (self.trade_manager and hasattr(self.trade_manager, 'exchange_api') and
                    hasattr(self.trade_manager.exchange_api, 'get_balance')):
                logger.warning("RiskManager: _get_current_daily_pnl_percent - TradeManager veya ExchangeAPI uygun değil. "
                               "Referans bakiye ayarlanamadı.")
                return None # Gerekli bileşenler yoksa PNL yüzdesi hesaplanamaz

            try:
                # Kullanıcının PNL hesaplaması için ana referans para birimi (genellikle USDT).
                # users.json -> trading -> quote_currency_for_pnl gibi bir ayardan okunmalı.
                # Eğer böyle bir ayar yoksa, varsayılan olarak 'USDT' kullanılabilir.
                pnl_ref_currency = self.user_trading_settings.get('quote_currency_for_pnl', 'USDT').upper()
                logger.debug(f"RiskManager: PNL referans para birimi '{pnl_ref_currency}' olarak belirlendi.")

                # O anki toplam referans para birimi bakiyesini al
                # exchange_api.get_balance float döndürüyor, _to_decimal ile Decimal'e çeviriyoruz.
                current_total_ref_balance_raw = self.trade_manager.exchange_api.get_balance(pnl_ref_currency)
                current_total_ref_balance_dec = _to_decimal(current_total_ref_balance_raw)

                if current_total_ref_balance_dec is not None and current_total_ref_balance_dec >= DECIMAL_ZERO:
                    # Gün başındaki bakiye = Şu anki bakiye - O ana kadar birikmiş PNL
                    # self._daily_pnl, zaten referans para birimi cinsinden olmalı.
                    calculated_initial_balance = current_total_ref_balance_dec - self._daily_pnl
                    
                    if calculated_initial_balance <= DECIMAL_ZERO:
                        # Eğer hesaplanan gün başı bakiye 0 veya negatifse, bu mantıksız bir durumdur.
                        # Bu genellikle, o anki PNL'in, o anki bakiyeden büyük olduğu anlamına gelir (ya da bakiye sıfır).
                        # Bu durumda, o anki toplam bakiyeyi başlangıç olarak almak, PNL yüzdesini
                        # o an için yaklaşık 0 yapar (eğer PNL de küçükse).
                        # Daha doğru bir yaklaşım, bot başladığında veya her gün başında toplam portföy değerini
                        # USD/USDT cinsinden hesaplayıp bu değere set etmektir.
                        logger.warning(f"RiskManager: Hesaplanan gün başı bakiye ({calculated_initial_balance:.4f}) sıfır veya negatif. "
                                       f"Mevcut toplam bakiye ({current_total_ref_balance_dec:.4f}) referans olarak kullanılacak. "
                                       f"Bu, mevcut PNL yüzdesini geçici olarak (yaklaşık) 0 yapabilir.")
                        self._initial_daily_balance = current_total_ref_balance_dec
                    else:
                        self._initial_daily_balance = calculated_initial_balance
                    
                    logger.info(f"RiskManager: Günlük PNL yüzdesi için referans bakiye ({pnl_ref_currency}) ayarlandı/güncellendi: "
                                f"{self._initial_daily_balance:.4f} "
                                f"(Temel: Mevcut Toplam Bakiye={current_total_ref_balance_dec:.4f}, Birikmiş Günlük PNL={self._daily_pnl:.4f})")
                else:
                    logger.error(f"RiskManager: _get_current_daily_pnl_percent - Günlük PNL yüzdesi için referans bakiye ({pnl_ref_currency}) "
                                 f"alınamadı veya sıfır/negatif. Alınan ham değer: '{current_total_ref_balance_raw}'.")
                    return None # Referans bakiye alınamazsa PNL yüzdesi hesaplanamaz
            except Exception as e:
                logger.error(f"RiskManager: _get_current_daily_pnl_percent - Referans bakiye alınırken/ayarlanırken beklenmedik hata: {e}", exc_info=True)
                return None # Hata durumunda PNL yüzdesi hesaplanamaz
        
        # Adım 2: Referans bakiye (artık ayarlanmış olmalı) pozitifse PNL yüzdesini hesapla.
        if self._initial_daily_balance is not None and self._initial_daily_balance > DECIMAL_ZERO:
            # PNL Oranı = (Toplam Günlük PNL / Gün Başı Referans Bakiyesi)
            # Örnek: PNL = -50, Başlangıç Bakiye = 1000 => Oran = -50 / 1000 = -0.05 (%-5)
            pnl_percentage_ratio = self._daily_pnl / self._initial_daily_balance
            logger.debug(f"RiskManager: Günlük PNL Oranı Hesaplandı: {pnl_percentage_ratio:.4f} (yani {pnl_percentage_ratio:.2%}) "
                         f"(Günlük PNL: {self._daily_pnl:.4f}, Gün Başı Ref. Bakiye: {self._initial_daily_balance:.4f})")
            return pnl_percentage_ratio
        elif self._initial_daily_balance == DECIMAL_ZERO and self._daily_pnl == DECIMAL_ZERO:
            # Eğer hem başlangıç bakiyesi hem de PNL sıfırsa, PNL oranı da sıfırdır.
            logger.debug("RiskManager: Başlangıç referans bakiyesi ve günlük PNL sıfır. PNL oranı %0.00 olarak kabul ediliyor.")
            return DECIMAL_ZERO
        else:
            # Bu durum, initial_balance'ın None, sıfır veya negatif olduğu ama PNL'in sıfırdan farklı olabileceği
            # (veya initial_balance pozitif ama PNL hesaplanamadığı) durumları kapsar.
            logger.warning(f"RiskManager: _get_current_daily_pnl_percent - Gün başı referans bakiye sıfır, negatif veya "
                           f"ayarlanamamış durumda ({self._initial_daily_balance}). PNL yüzdesi/oranı hesaplanamıyor.")
            return None # Yüzde/oran hesaplanamadı

    def can_open_new_position(self) -> Tuple[bool, str]:
        """
        Yeni bir pozisyon açılıp açılamayacağını kontrol eder.

        Returns:
            Tuple[bool, str]: (Açılabilir mi?, Neden)
        """
        self._reset_daily_pnl_if_needed() # Her zaman gün kontrolü ile başla

        # 1. Maksimum açık pozisyon kontrolü
        # Sadece max_open_positions pozitif bir değere ayarlandıysa bu kontrolü yap.
        if self.max_open_positions > 0:
            try:
                # TradeManager'dan o anki açık pozisyon sayısını al
                current_open_count = len(self.trade_manager.get_open_positions_thread_safe())
                if current_open_count >= self.max_open_positions:
                    reason = (f"Maksimum açık pozisyon limitine ({self.max_open_positions}) ulaşıldı "
                              f"(Mevcut açık pozisyon sayısı: {current_open_count}).")
                    logger.warning(f"RiskManager Kontrol: Yeni pozisyon açılamaz. {reason}")
                    return False, reason
                else:
                    logger.debug(f"RiskManager Kontrol: Açık pozisyon sayısı ({current_open_count}) "
                                 f"limitin ({self.max_open_positions}) altında.")
            except Exception as e:
                 logger.error(f"RiskManager Kontrol: Açık pozisyon sayısı alınırken hata oluştu: {e}. "
                              "Güvenlik amacıyla yeni pozisyon açılması engellendi.", exc_info=True)
                 return False, "Açık pozisyon sayısı kontrolünde bir hata oluştu."
        else:
            logger.debug("RiskManager Kontrol: Maksimum açık pozisyon limiti kontrolü devre dışı "
                         "(max_open_positions <= 0 olarak ayarlanmış).")

        # 2. Günlük zarar limiti kontrolü
        # Bu kontrol sadece self.max_daily_loss_limit_percent > 0 ise (yani limit etkinse)
        # ve self.max_daily_loss_limit (hesaplanmış oransal limit) None değilse (yani geçerli bir şekilde hesaplanmışsa) yapılır.
        if self.max_daily_loss_limit_percent > DECIMAL_ZERO and self.max_daily_loss_limit is not None:
            current_pnl_ratio = self._get_current_daily_pnl_percent() # PNL'in yüzdesel oranını al (örn: -0.05)

            if current_pnl_ratio is not None:
                # self.max_daily_loss_limit negatif bir orandır (örn: -0.10)
                # Eğer mevcut PNL oranı (örn: -0.12), bu negatif limitten DAHA KÜÇÜK veya EŞİTSE, pozisyon açma.
                # Yani, zararımız izin verilen maksimum zarara eşit veya daha fazlaysa.
                if current_pnl_ratio <= self.max_daily_loss_limit:
                    reason = (f"Günlük zarar limitine ({self.max_daily_loss_limit_percent:.2f}%) ulaşıldı veya aşıldı. "
                              f"Mevcut günlük PNL oranı: {current_pnl_ratio:.2%}, "
                              f"İzin verilen maksimum zarar oranı: {self.max_daily_loss_limit:.2%}.")
                    logger.warning(f"RiskManager Kontrol: Yeni pozisyon açılamaz. {reason}")
                    return False, reason
                else:
                    logger.debug(f"RiskManager Kontrol: Günlük PNL oranı ({current_pnl_ratio:.2%}) henüz günlük "
                                 f"izin verilen maksimum zarar oranını ({self.max_daily_loss_limit:.2%}) aşmadı.")
            else:
                # Yüzdesel PNL hesaplanamadıysa (örn: başlangıç bakiyesi alınamadıysa),
                # güvenlik amacıyla pozisyon açmayı engellemek daha doğru bir yaklaşım olabilir.
                reason = ("Günlük PNL yüzdesi/oranı hesaplanamadığı için günlük zarar limiti kontrolü tam olarak yapılamadı. "
                          "Güvenlik amacıyla yeni pozisyon açılması engellendi.")
                logger.warning(f"RiskManager Kontrol: Yeni pozisyon açılamaz. {reason}")
                return False, reason
        elif self.max_daily_loss_limit_percent <= DECIMAL_ZERO:
            # Bu durum __init__ içinde zaten loglanmıştı, burada sadece debug için.
            logger.debug("RiskManager Kontrol: Günlük zarar limiti yüzdesi 0 veya negatif ayarlandığı için "
                         "günlük zarar kontrolü aktif değil.")
        # else: self.max_daily_loss_limit is None (ama yüzde > 0 ise), bu __init__'te bir mantık hatası olurdu.
        # Ancak __init__ bunu engellemeli.

        # Tüm kontrollerden geçtiyse yeni pozisyon açmaya izin ver
        logger.info("RiskManager Kontrol: Yeni pozisyon açmak için risk limitleri uygun.")
        return True, "Risk limitleri dahilinde"

    def update_daily_pnl(self, closed_trade_pnl: Any) -> None:
        """
        Kapanan bir işlemin net PNL'ini günlük toplama ekler.
        TradeManager tarafından çağrılır.

        Args:
            closed_trade_pnl (Any): Kapanan işlemin net kar/zararı (Decimal'e çevrilebilir olmalı).
                                    Bu değer, ana quote para birimi (örn: USDT) cinsinden olmalıdır.
        """
        self._reset_daily_pnl_if_needed() # Her PNL güncellemesinden önce gün kontrolü

        pnl_dec = _to_decimal(closed_trade_pnl) # utils._to_decimal kullanılıyor
        if pnl_dec is None:
            logger.error(f"RiskManager: Günlük PNL güncellenemedi, geçersiz PNL değeri: '{closed_trade_pnl}'. "
                         "Değer Decimal'e çevrilemedi.")
            return

        try:
            self._daily_pnl += pnl_dec
            logger.info(f"RiskManager: Günlük PNL güncellendi. "
                        f"Bu işlemden eklenen PNL: {pnl_dec:+.4f}, "
                        f"Yeni Toplam Günlük PNL: {self._daily_pnl:.4f}")
        except Exception as e: # Genellikle Decimal operasyonlarında beklenmedik hata olmaz ama garanti için.
            logger.error(f"RiskManager: Günlük PNL güncellenirken (toplama sırasında) beklenmedik hata: {e}", exc_info=True)

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        quote_currency_balance: Decimal, # Mevcut ana quote currency bakiyesi (örn: USDT)
        is_demo_mode: bool = False,
        # leverage: Decimal = DECIMAL_ONE # Bu parametre risk hesaplamasında direkt kullanılmaz, kaldırılabilir.
    ) -> Optional[Decimal]:
        """
        Risk parametrelerine ve bakiye bilgilerine göre pozisyon büyüklüğünü (baz varlık cinsinden) hesaplar.
        Bu metodun ana amacı, işlem başına risk yüzdesine göre bir pozisyon büyüklüğü önermektir.
        Demo modu ve kullanıcı tanımlı sabit miktar/yüzde gibi ek kısıtlamalar da göz önüne alınır.
        """
        # Parametre kontrolleri
        if not all([symbol, isinstance(entry_price, Decimal), isinstance(stop_loss_price, Decimal), isinstance(quote_currency_balance, Decimal)]):
            logger.error(f"RiskManager [{symbol}] calculate_position_size: Eksik veya geçersiz tipte temel parametreler.")
            return None
        if entry_price <= DECIMAL_ZERO or stop_loss_price <= DECIMAL_ZERO:
            logger.error(f"RiskManager [{symbol}] calculate_position_size: Giriş ({entry_price}) veya SL ({stop_loss_price}) fiyatı sıfır/negatif olamaz.")
            return None
        if quote_currency_balance < DECIMAL_ZERO: # Bakiye en az 0 olmalı
             logger.error(f"RiskManager [{symbol}] calculate_position_size: Quote currency bakiyesi ({quote_currency_balance}) negatif olamaz.")
             return None

        # Birim başına risk (quote currency cinsinden). Örn: BTC/USDT için USDT cinsinden.
        risk_per_unit_base = abs(entry_price - stop_loss_price)
        if risk_per_unit_base <= DECIMAL_ZERO:
            logger.warning(f"RiskManager [{symbol}] calculate_position_size: Giriş ({entry_price}) ve SL ({stop_loss_price}) "
                           f"fiyatları aynı veya çok yakın, bu nedenle birim başına risk sıfır. "
                           "Pozisyon büyüklüğü sıfır olarak hesaplanacak veya işlem engellenecek.")
            return DECIMAL_ZERO # Sıfır risk, sıfır pozisyon büyüklüğü anlamına gelir.

        # 1. Adım: İşlem başına maksimum risk yüzdesine göre pozisyon büyüklüğü hesapla
        # self.max_risk_per_trade_percent, __init__'te pozitif bir yüzde olarak ayarlanmış olmalı (örn: Decimal('2.0') == %2)
        calculated_size_from_risk_percent: Optional[Decimal] = None
        if self.max_risk_per_trade_percent > DECIMAL_ZERO:
            # Risklenecek toplam tutar (quote currency cinsinden)
            # Örn: Bakiye=1000 USDT, Risk %=2 => Risklenecek Tutar = (2/100) * 1000 = 20 USDT
            total_capital_to_risk_quote = (self.max_risk_per_trade_percent / DECIMAL_HUNDRED) * quote_currency_balance
            
            # Pozisyon büyüklüğü (base currency cinsinden)
            # Örn: Risklenecek=20 USDT, Birim Başı Risk=500 USDT (SL mesafesi) => Büyüklük = 20 / 500 = 0.04 (base)
            calculated_size_from_risk_percent = total_capital_to_risk_quote / risk_per_unit_base
            
            logger.info(f"RiskManager [{symbol}] calculate_position_size (Risk Yüzdesine Göre): "
                        f"Bakiye={quote_currency_balance:.2f}, Risk Yüzdesi={self.max_risk_per_trade_percent}%, "
                        f"Risklenecek Tutar (Quote)={total_capital_to_risk_quote:.4f}, "
                        f"Birim Başı Risk (Quote)={risk_per_unit_base:.4f} "
                        f"-> Hesaplanan Poz. Büyüklüğü (Base)={calculated_size_from_risk_percent:.8f}")
        else:
            logger.info(f"RiskManager [{symbol}] calculate_position_size: İşlem başına risk yüzdesi "
                        f"({self.max_risk_per_trade_percent}%) sıfır veya negatif olduğu için, "
                        "risk yüzdesine göre pozisyon büyüklüğü hesaplanmadı.")
            # Bu durumda, miktar kullanıcı tanımlı ayarlardan (sabit miktar vs.) gelmeli.
            # Eğer o da yoksa, TradeManager None/sıfır miktar ile işlem yapmamalı.

        # 2. Adım: Kullanıcı tarafından tanımlanan işlem miktarı ayarlarını kontrol et (eğer varsa)
        # Bu ayarlar bir ÜST LİMİT veya alternatif bir miktar belirleyebilir.
        # self.user_trading_settings, __init__'te user_config['trading']'den alınır.
        size_limit_from_user_settings_base: Optional[Decimal] = None
        amount_type_user = self.user_trading_settings.get('default_amount_type', 'fixed').lower()
        amount_value_user_raw = self.user_trading_settings.get('default_amount_value', '0')
        amount_value_user = _to_decimal(amount_value_user_raw)

        if amount_value_user and amount_value_user > DECIMAL_ZERO:
            if amount_type_user == 'percentage': # Bakiye yüzdesi kadar quote ile işlem
                quote_value_for_trade = (amount_value_user / DECIMAL_HUNDRED) * quote_currency_balance
                if entry_price > DECIMAL_ZERO:
                    size_limit_from_user_settings_base = quote_value_for_trade / entry_price
                    logger.info(f"RiskManager [{symbol}] Kullanıcı Ayarı (Miktar Tipi: Yüzde %{amount_value_user}): "
                                f"Max Base Büyüklüğü = {size_limit_from_user_settings_base:.8f}")
            elif amount_type_user == 'quote_fixed': # Sabit quote miktarı ile işlem
                if entry_price > DECIMAL_ZERO:
                    size_limit_from_user_settings_base = amount_value_user / entry_price
                    logger.info(f"RiskManager [{symbol}] Kullanıcı Ayarı (Miktar Tipi: Sabit Quote {amount_value_user}): "
                                f"Max Base Büyüklüğü = {size_limit_from_user_settings_base:.8f}")
            elif amount_type_user == 'fixed': # Sabit base miktarı
                size_limit_from_user_settings_base = amount_value_user
                logger.info(f"RiskManager [{symbol}] Kullanıcı Ayarı (Miktar Tipi: Sabit Base {amount_value_user}): "
                            f"Base Büyüklüğü = {size_limit_from_user_settings_base:.8f}")
        
        # 3. Adım: Nihai pozisyon büyüklüğünü belirle
        final_calculated_size_base: Optional[Decimal] = None

        if calculated_size_from_risk_percent is not None and calculated_size_from_risk_percent > DECIMAL_ZERO:
            final_calculated_size_base = calculated_size_from_risk_percent
            if size_limit_from_user_settings_base is not None and size_limit_from_user_settings_base > DECIMAL_ZERO:
                # Eğer kullanıcı bir limit (yüzde, sabit quote, sabit base) belirlemişse,
                # risk bazlı hesaplanan miktar bu limiti aşmamalıdır.
                # (Sabit base durumu hariç, o zaten direkt miktardır)
                if amount_type_user in ['percentage', 'quote_fixed']:
                    if final_calculated_size_base > size_limit_from_user_settings_base:
                        logger.info(f"RiskManager [{symbol}]: Risk bazlı hesaplanan miktar ({final_calculated_size_base:.8f}) "
                                    f"kullanıcı tanımlı üst limitten ({size_limit_from_user_settings_base:.8f}) büyük. "
                                    "Kullanıcı limiti uygulanacak.")
                        final_calculated_size_base = size_limit_from_user_settings_base
                elif amount_type_user == 'fixed': # Eğer tip "fixed" (sabit base) ise, risk %'sini yok sayıp bunu kullan.
                    logger.info(f"RiskManager [{symbol}]: Miktar tipi 'sabit base' ({size_limit_from_user_settings_base:.8f}) "
                                "olarak ayarlandığı için bu miktar kullanılacak (risk %'si dikkate alınmayabilir).")
                    final_calculated_size_base = size_limit_from_user_settings_base

        elif size_limit_from_user_settings_base is not None and size_limit_from_user_settings_base > DECIMAL_ZERO:
            # Risk yüzdesi 0 veya hesaplanamadı, ama kullanıcı bir miktar tanımlamış.
            logger.info(f"RiskManager [{symbol}]: Risk yüzdesine göre miktar hesaplanamadı/sıfır. "
                        f"Kullanıcı tanımlı miktar ({size_limit_from_user_settings_base:.8f}, Tip: {amount_type_user}) kullanılacak.")
            final_calculated_size_base = size_limit_from_user_settings_base
        else:
            # Ne risk yüzdesine göre ne de kullanıcı ayarlarına göre geçerli bir miktar bulunamadı.
            logger.warning(f"RiskManager [{symbol}]: Ne risk yüzdesine göre ne de kullanıcı ayarlarına göre geçerli bir pozisyon büyüklüğü hesaplanamadı.")
            return DECIMAL_ZERO # Veya None, TradeManager'ın bunu nasıl ele aldığına bağlı.

        # 4. Adım: Demo Modu için Ek Güvenlik Kontrolü (Eğer gerekiyorsa)
        # Pozisyonun toplam (kaldıraçsız) değeri, mevcut demo quote bakiyesini aşmamalıdır.
        if is_demo_mode and final_calculated_size_base is not None and final_calculated_size_base > DECIMAL_ZERO:
            position_value_quote_unleveraged = final_calculated_size_base * entry_price
            if position_value_quote_unleveraged > quote_currency_balance:
                logger.warning(f"RiskManager [{symbol}] DEMO Uyarısı: Hesaplanan nihai pozisyonun kaldıraçsız değeri "
                               f"({position_value_quote_unleveraged:.2f} {self.user_trading_settings.get('quote_currency_for_pnl', 'QUOTE')}) "
                               f"mevcut demo bakiyesini ({quote_currency_balance:.2f}) aşıyor! "
                               "Miktar, tüm demo bakiyesini kullanacak şekilde sınırlandırılıyor.")
                if entry_price > DECIMAL_ZERO:
                    final_calculated_size_base = quote_currency_balance / entry_price
                else: # entry_price sıfırsa, bu zaten sorunlu bir durum.
                    final_calculated_size_base = DECIMAL_ZERO

        # Son Kontrol: Hesaplanan miktar sıfır veya negatifse DECIMAL_ZERO döndür
        if final_calculated_size_base is None or final_calculated_size_base <= DECIMAL_ZERO:
            logger.warning(f"RiskManager [{symbol}] calculate_position_size: Tüm hesaplamalar sonucunda nihai pozisyon büyüklüğü "
                           f"sıfır veya negatif ({final_calculated_size_base}). Geçerli bir büyüklük bulunamadı.")
            return DECIMAL_ZERO

        logger.info(f"RiskManager [{symbol}] calculate_position_size: Hesaplama Sonucu -> "
                    f"Nihai Pozisyon Büyüklüğü (Base Cinsinden) = {final_calculated_size_base:.8f}")
        return final_calculated_size_base

    def get_current_daily_pnl(self) -> Decimal:
        """ Mevcut birikmiş günlük PNL'i Decimal olarak döndürür. """
        self._reset_daily_pnl_if_needed() # Gün kontrolü yap
        return self._daily_pnl

    def notify_position_opened(self, order_id: str) -> None:
        """ TradeManager bir pozisyon başarıyla açtığında bu metod çağrılır. """
        # Bu metot, RiskManager'ın kendi iç durumunu güncellemesi için kullanılabilir
        # (örn: açık pozisyon sayısını kendi içinde de tutuyorsa).
        # Şimdilik sadece bilgilendirme amaçlı log basıyoruz.
        logger.info(f"RiskManager Bildirimi: Yeni pozisyon açıldığı bilgisi alındı (Pozisyon ID: {order_id}).")
        # Eğer RiskManager kendi içinde açık pozisyon sayısını veya detaylarını tutuyorsa,
        # burada ilgili güncellemeler yapılabilir.
        # Örn: self.internal_open_positions_count += 1

    def notify_position_closed(self, order_id: str, pnl_of_closed_trade: Optional[Decimal] = None) -> None:
        """
        TradeManager bir pozisyonu başarıyla kapattığında bu metod çağrılır.
        Kapanan işlemin PNL'i de bu metoda iletilir ve günlük PNL'e eklenir.
        """
        logger.info(f"RiskManager Bildirimi: Pozisyon kapatıldığı bilgisi alındı (Pozisyon ID: {order_id}).")
        # Örn: self.internal_open_positions_count -= 1

        if pnl_of_closed_trade is not None:
            self.update_daily_pnl(pnl_of_closed_trade) # Günlük PNL'i güncelle
        else:
            logger.warning(f"RiskManager Bildirimi (Pozisyon ID: {order_id}): Kapanan işlemin PNL bilgisi gelmedi, "
                           "günlük PNL bu işlem için güncellenemedi.")

# --- RiskManager Sınıfının Sonu ---

# Örnek Kullanım ve Testler (Dosyanın en sonunda, if __name__ == '__main__': bloğu içinde kalmalı)
if __name__ == '__main__':
    print("RiskManager Test Başlatılıyor...")
    # Test için basit logger (eğer dosyanın başında tanımlanmadıysa veya farklıysa)
    if 'logger' not in globals() or not hasattr(logger, 'info'): # Basit kontrol
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        logger = logging.getLogger('risk_manager_test_main')
        logger.info("RiskManager __main__ bloğu için test logger ayarlandı.")


    # --- Mock TradeManager ve ExchangeAPI ---
    class MockExchangeAPI:
        def get_balance(self, currency: str) -> float:
            if currency == 'USDT':
                return 10000.0 # Test için sabit bakiye
            return 0.0

    class MockTradeManager:
        def __init__(self):
            self._open_positions_list = [] # Artık bir liste tutalım
            self.exchange_api = MockExchangeAPI()
            self._active_user = "test_user" # Test için aktif kullanıcı

        def get_open_positions_thread_safe(self) -> list:
            return [pos for pos in self._open_positions_list] # Kopyasını döndür

        def add_mock_position(self, order_id: str, symbol: str = "TEST/USDT"):
             # Basit bir pozisyon objesi ekleyelim
             self._open_positions_list.append({'order_id': order_id, 'symbol': symbol, 'status': 'open'})

        def remove_mock_position(self, order_id: str):
             self._open_positions_list = [p for p in self._open_positions_list if p.get('order_id') != order_id]
    # --- /Mock TradeManager ve ExchangeAPI ---

    mock_tm_instance = MockTradeManager()
    
    # Test için user_config
    test_user_config_full = {
        "username": "test_user",
        "risk": {
            "max_open_positions": 2,
            "max_risk_per_trade_percent": 1.0, # %1
            "max_daily_loss_percent": 5.0      # %5
        },
        "trading": {
            "default_leverage": 10,
            "default_margin_mode": "ISOLATED",
            "default_amount_type": "percentage", # Pozisyon büyüklüğü için
            "default_amount_value": 20.0,      # Bakiye %20'si
            "quote_currency_for_pnl": "USDT"   # PNL hesaplaması için referans
        }
        # ... diğer ayarlar ...
    }
    
    # RiskManager örneği oluştur
    risk_manager_instance = RiskManager(
        trade_manager_ref=mock_tm_instance,
        user_config=test_user_config_full
        # Fallback parametreleri __init__ içinde user_config'den okunacağı için burada vermeye gerek yok.
    )

    print("\n--- RiskManager Başlatma Testi (Değerler user_config'den gelmeli) ---")
    print(f"Max Açık Pozisyon: {risk_manager_instance.max_open_positions} (Beklenen: 2)")
    assert risk_manager_instance.max_open_positions == 2
    print(f"İşlem Başına Risk %: {risk_manager_instance.max_risk_per_trade_percent} (Beklenen: 1.0)")
    assert risk_manager_instance.max_risk_per_trade_percent == Decimal('1.0')
    print(f"Günlük Max Zarar %: {risk_manager_instance.max_daily_loss_limit_percent} (Beklenen: 5.0)")
    assert risk_manager_instance.max_daily_loss_limit_percent == Decimal('5.0')
    print(f"Günlük Max Zarar Limiti (Oran): {risk_manager_instance.max_daily_loss_limit} (Beklenen: -0.05)")
    assert risk_manager_instance.max_daily_loss_limit == Decimal('-0.05')


    print("\n--- Açık Pozisyon Limiti Testi ---")
    can_trade, reason = risk_manager_instance.can_open_new_position()
    print(f"1. Pozisyon Açılabilir mi? {can_trade} (Neden: {reason})")
    assert can_trade
    mock_tm_instance.add_mock_position("pos1_id")
    print(f"  Açık Pozisyonlar: {len(mock_tm_instance.get_open_positions_thread_safe())}")

    can_trade, reason = risk_manager_instance.can_open_new_position()
    print(f"2. Pozisyon Açılabilir mi? {can_trade} (Neden: {reason})")
    assert can_trade
    mock_tm_instance.add_mock_position("pos2_id")
    print(f"  Açık Pozisyonlar: {len(mock_tm_instance.get_open_positions_thread_safe())}")

    can_trade, reason = risk_manager_instance.can_open_new_position()
    print(f"3. Pozisyon Açılabilir mi (Limit Aşıldı)? {can_trade} (Neden: {reason})")
    assert not can_trade # Limit 2 idi, aşıldı.
    print(f"  Açık Pozisyonlar: {len(mock_tm_instance.get_open_positions_thread_safe())}")

    mock_tm_instance.remove_mock_position("pos1_id") # Bir pozisyonu kapat
    print("  Bir pozisyon kapatıldı.")
    print(f"  Açık Pozisyonlar: {len(mock_tm_instance.get_open_positions_thread_safe())}")
    can_trade, reason = risk_manager_instance.can_open_new_position()
    print(f"Tekrar Deneme - Pozisyon Açılabilir mi? {can_trade} (Neden: {reason})")
    assert can_trade


    print("\n--- Günlük PNL Takibi ve Limiti Testi ---")
    # Başlangıçta _initial_daily_balance None olmalı, _get_current_daily_pnl_percent onu ayarlayacak.
    print(f"Başlangıç Günlük PNL: {risk_manager_instance.get_current_daily_pnl()}")
    assert risk_manager_instance.get_current_daily_pnl() == DECIMAL_ZERO
    
    # _get_current_daily_pnl_percent çağrısı _initial_daily_balance'ı mock_tm.exchange_api.get_balance() ile set etmeli.
    # Mock bakiye 10000 USDT. PNL 0 ise, oran da 0 olmalı.
    pnl_ratio_initial = risk_manager_instance._get_current_daily_pnl_percent()
    print(f"İlk PNL Oranı Sorgusu: {pnl_ratio_initial} (Beklenen: 0.0)")
    assert pnl_ratio_initial == DECIMAL_ZERO
    assert risk_manager_instance._initial_daily_balance == Decimal('10000.0') # Kontrol et
    
    # Zarar ekleyelim (USDT cinsinden)
    # %5 zarar limiti var, yani 10000 * -0.05 = -500 USDT limit.
    risk_manager_instance.update_daily_pnl(Decimal('-300')) # -300 USDT zarar
    print(f"Güncel Günlük PNL (USDT): {risk_manager_instance.get_current_daily_pnl()}")
    pnl_ratio_after_loss1 = risk_manager_instance._get_current_daily_pnl_percent()
    print(f"PNL Oranı (-300 USDT sonrası): {pnl_ratio_after_loss1:.4f} (Beklenen: -0.03)") # -300/10000 = -0.03
    assert pnl_ratio_after_loss1 is not None and abs(pnl_ratio_after_loss1 - Decimal('-0.03')) < Decimal('1e-9')
    can_trade, reason = risk_manager_instance.can_open_new_position()
    print(f"Pozisyon Açılabilir mi (-3% PNL)? {can_trade} (Neden: {reason})") # Limit -%5
    assert can_trade

    risk_manager_instance.update_daily_pnl(Decimal('-250')) # Toplam zarar -550 USDT oldu.
    print(f"Güncel Günlük PNL (USDT): {risk_manager_instance.get_current_daily_pnl()}") # -550 olmalı
    assert risk_manager_instance.get_current_daily_pnl() == Decimal('-550')
    pnl_ratio_after_loss2 = risk_manager_instance._get_current_daily_pnl_percent()
    print(f"PNL Oranı (-550 USDT sonrası): {pnl_ratio_after_loss2:.4f} (Beklenen: -0.055)") # -550/10000 = -0.055
    assert pnl_ratio_after_loss2 is not None and abs(pnl_ratio_after_loss2 - Decimal('-0.055')) < Decimal('1e-9')
    
    can_trade, reason = risk_manager_instance.can_open_new_position()
    print(f"Pozisyon Açılabilir mi (-5.5% PNL, Limit -5%)? {can_trade} (Neden: {reason})")
    assert not can_trade # Limit aşıldı: -0.055 <= -0.05

    # Gün değiştirme simülasyonu
    print("\nGün Değiştirme Simülasyonu...")
    risk_manager_instance._last_reset_date = date.today() - timedelta(days=1) # Düne ayarla
    print(f"Günlük PNL (Sıfırlama Öncesi): {risk_manager_instance.get_current_daily_pnl()}") # Sıfırlama burada tetiklenir
    print(f"Günlük PNL (Sıfırlama Sonrası): {risk_manager_instance.get_current_daily_pnl()}")
    assert risk_manager_instance.get_current_daily_pnl() == DECIMAL_ZERO
    assert risk_manager_instance._initial_daily_balance is None # Başlangıç bakiye de sıfırlanmalı
    
    # Yeni günde ilk PNL oranı sorgusu _initial_daily_balance'ı tekrar set etmeli
    pnl_ratio_new_day = risk_manager_instance._get_current_daily_pnl_percent()
    print(f"Yeni Gün İlk PNL Oranı: {pnl_ratio_new_day} (Beklenen: 0.0)")
    assert pnl_ratio_new_day == DECIMAL_ZERO
    assert risk_manager_instance._initial_daily_balance == Decimal('10000.0') # Mock bakiye
    
    can_trade, reason = risk_manager_instance.can_open_new_position()
    print(f"Yeni Günde Pozisyon Açılabilir mi? {can_trade} (Neden: {reason})")
    assert can_trade


    print("\n--- Pozisyon Büyüklüğü Hesaplama Testi (calculate_position_size) ---")
    # RiskManager örneğini test_user_config_full ile tekrar oluşturalım (ya da ayarları direkt set edelim)
    # Mevcut risk_manager_instance zaten bu config ile oluşturulmuştu.
    # max_risk_per_trade_percent = 1.0% idi.
    
    balance = Decimal("10000.0") # USDT
    entry = Decimal("50000")     # BTC/USDT giriş fiyatı
    sl = Decimal("49500")        # BTC/USDT SL fiyatı (birim başına 500 USDT risk)
    
    # Risklenecek Tutar = %1 * 10000 USDT = 100 USDT
    # Birim Başına Risk = 50000 - 49500 = 500 USDT
    # Pozisyon Büyüklüğü (BTC) = Risklenecek Tutar / Birim Başına Risk = 100 / 500 = 0.2 BTC
    
    pos_size = risk_manager_instance.calculate_position_size(
        symbol="BTC/USDT",
        entry_price=entry,
        stop_loss_price=sl,
        quote_currency_balance=balance,
        is_demo_mode=False # Önce gerçek mod
    )
    print(f"Bakiye: {balance}, Giriş: {entry}, SL: {sl}, Risk %: {risk_manager_instance.max_risk_per_trade_percent}")
    print(f"Hesaplanan Pozisyon Büyüklüğü (Base): {pos_size} (Beklenen: 0.2)")
    assert pos_size is not None and abs(pos_size - Decimal("0.2")) < Decimal("1e-9")

    # SL girişe eşitse veya geçersizse (risk_per_unit_base sıfır olur)
    pos_size_invalid_sl = risk_manager_instance.calculate_position_size("BTC/USDT", entry, entry, balance)
    print(f"Geçersiz SL (risk=0) ile Hesaplama: {pos_size_invalid_sl} (Beklenen: 0.0)")
    assert pos_size_invalid_sl == DECIMAL_ZERO

    # Risk %0 ise (calculate_position_size içinde None/sıfır döndürmeli)
    original_risk_perc = risk_manager_instance.max_risk_per_trade_percent
    risk_manager_instance.max_risk_per_trade_percent = DECIMAL_ZERO
    pos_size_zero_risk = risk_manager_instance.calculate_position_size("BTC/USDT", entry, sl, balance)
    print(f"Sıfır Risk Yüzdesi ile Hesaplama: {pos_size_zero_risk} (Beklenen: None veya 0.0)")
    # Önceki mantıkta eğer risk %0 ise None dönüyordu, bu da TradeManager'da kullanıcı tanımlı miktara düşmesini sağlıyordu.
    # Güncel calculate_position_size, eğer risk %0 ise None döndürür, bu da doğru.
    assert pos_size_zero_risk is None
    risk_manager_instance.max_risk_per_trade_percent = original_risk_perc # Değeri geri yükle

    print("\nRiskManager Test Tamamlandı.")