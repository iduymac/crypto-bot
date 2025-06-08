# core/utils.py

import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, Context, getcontext
from typing import Union, Optional, Dict, Any, List # Dict, Any, List type hinting için eklendi

# --- Logger Düzeltmesi ---
try:
    from core.logger import setup_logger
    logger = setup_logger('utils')
except ImportError:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('utils_fallback')
    logger.warning("core.logger modülü bulunamadı, temel fallback logger kullanılıyor.")
# --- /Logger Düzeltmesi ---

# --- Decimal Sabitleri ---
DEFAULT_PRECISION = 8 # Sayı formatlama için varsayılan ondalık basamak sayısı
DECIMAL_CONTEXT_DEFAULT_PREC = 28 # Global Decimal context için varsayılan hassasiyet
try:
    # Global Decimal context'ini alıp hassasiyeti ayarlayalım
    DECIMAL_CONTEXT = getcontext()
    DECIMAL_CONTEXT.prec = DECIMAL_CONTEXT_DEFAULT_PREC
except Exception as e_getcontext:
    logger.error(f"Global Decimal context alınırken/ayarlanırken hata: {e_getcontext}. Varsayılan context kullanılacak.", exc_info=True)
    DECIMAL_CONTEXT = None # Hata durumunda None olarak bırak

# Sık kullanılan Decimal sabitleri
DECIMAL_ZERO = Decimal('0')
DECIMAL_ONE = Decimal('1')
DECIMAL_HUNDRED = Decimal('100')
# İhtiyaç duyulursa diğer sabitler eklenebilir:
# DECIMAL_TWO = Decimal('2')
# DECIMAL_THOUSAND = Decimal('1000')
# DECIMAL_EPSILON = Decimal('1e-9') # Çok küçük pozitif sayı

# --- Yardımcı Fonksiyonlar ---

def get_decimal_context(precision=DECIMAL_CONTEXT_DEFAULT_PREC, rounding_method=ROUND_HALF_UP) -> Context:
    """ Belirtilen hassasiyet ve yuvarlama metodu ile yeni bir Decimal Context oluşturur. """
    # Not: Bu fonksiyon global DECIMAL_CONTEXT'i değiştirmez, yeni bir context döndürür.
    # Eğer global context'i kullanmak yeterliyse, bu fonksiyona ihtiyaç olmayabilir.
    # Ancak spesifik hesaplamalar için farklı context'ler gerekirse faydalıdır.
    try:
        ctx = Context(prec=precision, rounding=rounding_method)
        return ctx
    except Exception as e:
        logger.error(f"Decimal Context oluşturulurken hata (precision={precision}, rounding={rounding_method}): {e}. Varsayılan context kullanılacak.", exc_info=True)
        # Hata durumunda global context'i (veya None ise varsayılanı) döndür
        return DECIMAL_CONTEXT or getcontext() # Fallback

def _to_decimal(value: Any) -> Optional[Decimal]:
    """
    Gelen değeri (int, float, str) Decimal'e çevirir.
    Virgülleri noktaya çevirir. Hata durumunda None döner.
    """
    if value is None:
        return None
    try:
        # Önce string'e çevirip virgülü noktaya çevirelim, sonra Decimal yapalım
        return Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError) as e:
        # Hata logunu debug yerine warning yapabiliriz, çünkü bu veri kaybına yol açabilir.
        logger.warning(f"Decimal'e çevirme hatası: Değer='{value}' (Tip: {type(value)}), Hata: {e}", exc_info=False) # exc_info=False logları şişirmemek için
        return None
    except Exception as e: # Beklenmedik diğer hatalar
        logger.error(f"Decimal'e çevirme sırasında beklenmedik hata: Değer='{value}' (Tip: {type(value)}), Hata: {e}", exc_info=True)
        return None

def calculate_stop_loss_price(entry_price: Any, stop_loss_percentage: Any, side: str) -> Optional[Decimal]:
    """
    Giriş fiyatı ve zarar kes yüzdesine göre SL fiyatını hesaplar.

    Args:
        entry_price: Giriş fiyatı (Decimal'e çevrilebilir olmalı).
        stop_loss_percentage: Zarar kes yüzdesi (örn. 2.0).
        side: İşlem yönü ('buy' veya 'sell').

    Returns:
        Decimal: Hesaplanan SL fiyatı.
        None: Eğer girdiler geçersizse veya hesaplama yapılamazsa.
    """
    entry_dec = _to_decimal(entry_price)
    sl_perc_dec = _to_decimal(stop_loss_percentage)
    # side None olmamalı ve geçerli bir string olmalı
    side_lower = str(side).strip().lower() if isinstance(side, str) else None

    # Girdi kontrolleri
    if entry_dec is None or sl_perc_dec is None or side_lower not in ['buy', 'sell']:
        logger.error(f"Geçersiz SL hesaplama girdileri: Giriş='{entry_price}', SL%='{stop_loss_percentage}', Yön='{side}'")
        return None
    # Yüzde pozitif olmalı (0 ise SL yok demektir)
    if sl_perc_dec <= DECIMAL_ZERO: # DECIMAL_ZERO artık tanımlı
        logger.debug(f"SL yüzdesi ({sl_perc_dec}) sıfır veya negatif, SL hesaplanmadı.")
        return None # 0% SL, SL yok anlamına gelir

    try:
        # Yüzdeyi ondalık çarpana çevir
        multiplier = sl_perc_dec / DECIMAL_HUNDRED # DECIMAL_HUNDRED kullanımı
        stop_loss_price: Optional[Decimal] = None # Tip belirleme

        if side_lower == 'buy':
            # Alış işlemi: SL, girişin altında olmalı
            stop_loss_price = entry_dec * (DECIMAL_ONE - multiplier) # DECIMAL_ONE kullanımı
        elif side_lower == 'sell':
            # Satış işlemi: SL, girişin üstünde olmalı
            stop_loss_price = entry_dec * (DECIMAL_ONE + multiplier) # DECIMAL_ONE kullanımı

        # Hesaplanan fiyatın geçerli olup olmadığını kontrol et (örn. negatif olmamalı)
        if stop_loss_price is not None and stop_loss_price < DECIMAL_ZERO:
             logger.warning(f"Hesaplanan SL fiyatı negatif: {stop_loss_price}. None döndürülüyor.")
             return None

        return stop_loss_price
    except Exception as e:
        logger.error(f"Zarar kes fiyatı hesaplanırken beklenmedik hata: {e}", exc_info=True)
        return None

def calculate_take_profit_price(entry_price: Any, take_profit_percentage: Any, side: str) -> Optional[Decimal]:
    """
    Giriş fiyatı ve kar al yüzdesine göre TP fiyatını hesaplar.

    Args:
        entry_price: Giriş fiyatı (Decimal'e çevrilebilir olmalı).
        take_profit_percentage: Kar al yüzdesi (örn. 4.0).
        side: İşlem yönü ('buy' veya 'sell').

    Returns:
        Decimal: Hesaplanan TP fiyatı.
        None: Eğer girdiler geçersizse veya hesaplama yapılamazsa.
    """
    entry_dec = _to_decimal(entry_price)
    tp_perc_dec = _to_decimal(take_profit_percentage)
    side_lower = str(side).strip().lower() if isinstance(side, str) else None

    if entry_dec is None or tp_perc_dec is None or side_lower not in ['buy', 'sell']:
        logger.error(f"Geçersiz TP hesaplama girdileri: Giriş='{entry_price}', TP%='{take_profit_percentage}', Yön='{side}'")
        return None
    if tp_perc_dec <= DECIMAL_ZERO: # DECIMAL_ZERO kullanımı
        logger.debug(f"TP yüzdesi ({tp_perc_dec}) sıfır veya negatif, TP hesaplanmadı.")
        return None # 0% TP, TP yok anlamına gelir

    try:
        multiplier = tp_perc_dec / DECIMAL_HUNDRED # DECIMAL_HUNDRED kullanımı
        take_profit_price: Optional[Decimal] = None

        if side_lower == 'buy':
            # Alış işlemi: TP, girişin üstünde olmalı
            take_profit_price = entry_dec * (DECIMAL_ONE + multiplier) # DECIMAL_ONE kullanımı
        elif side_lower == 'sell':
            # Satış işlemi: TP, girişin altında olmalı
            take_profit_price = entry_dec * (DECIMAL_ONE - multiplier) # DECIMAL_ONE kullanımı

        # Hesaplanan fiyatın geçerli olup olmadığını kontrol et
        if take_profit_price is not None and take_profit_price < DECIMAL_ZERO:
             logger.warning(f"Hesaplanan TP fiyatı negatif: {take_profit_price}. None döndürülüyor.")
             return None
        return take_profit_price
    except Exception as e:
        logger.error(f"Kar al fiyatı hesaplanırken beklenmedik hata: {e}", exc_info=True)
        return None

def calculate_pnl(entry_price: Any, current_price: Any, filled_amount: Any, side: str) -> Optional[Decimal]:
    """
    Verilen parametrelere göre Kar/Zararı (PNL) hesaplar.

    Args:
        entry_price: Pozisyona giriş fiyatı.
        current_price: Mevcut (veya çıkış) fiyatı.
        filled_amount: İşlem gören miktar (base currency).
        side: İşlem yönü ('buy' veya 'sell').

    Returns:
        Decimal: Hesaplanan PNL (quote currency).
        None: Eğer girdiler geçersizse veya hesaplama yapılamazsa.
    """
    entry_dec = _to_decimal(entry_price)
    current_dec = _to_decimal(current_price)
    amount_dec = _to_decimal(filled_amount)
    side_lower = str(side).strip().lower() if isinstance(side, str) else None

    # Temel girdi kontrolleri
    if entry_dec is None or current_dec is None or amount_dec is None or side_lower not in ['buy', 'sell']:
        logger.warning(f"PNL hesaplama için eksik/geçersiz veri: E='{entry_price}', C='{current_price}', A='{filled_amount}', S='{side}'")
        return None

    # Miktar ve fiyatlar pozitif olmalı (genellikle)
    if amount_dec <= DECIMAL_ZERO or entry_dec <= DECIMAL_ZERO or current_dec <= DECIMAL_ZERO:
         logger.debug(f"PNL hesaplama atlandı: Sıfır/negatif miktar veya fiyat (E={entry_dec}, C={current_dec}, A={amount_dec}).")
         return DECIMAL_ZERO # Sıfır PNL döndürmek mantıklı olabilir

    try:
        pnl = DECIMAL_ZERO
        if side_lower == 'buy': # Long pozisyon
            # PNL = (Çıkış Fiyatı - Giriş Fiyatı) * Miktar
            pnl = (current_dec - entry_dec) * amount_dec
        elif side_lower == 'sell': # Short pozisyon
            # PNL = (Giriş Fiyatı - Çıkış Fiyatı) * Miktar
            pnl = (entry_dec - current_dec) * amount_dec

        # logger.debug(f"PNL Hesaplandı: {pnl:.8f} (E={entry_dec}, C={current_dec}, A={amount_dec}, S={side})")
        return pnl
    except Exception as e:
        logger.error(f"PNL hesaplama hatası: {e} (Girdiler: E={entry_price}, C={current_price}, A={filled_amount}, S={side})", exc_info=True)
        return None

# <<<<<<<<<<<<<< DEĞİŞİKLİK: Fonksiyon adı format_decimal_auto olarak güncellendi >>>>>>>>>>>>>>>
def format_decimal_auto(number: Any, decimals: int = DEFAULT_PRECISION, default_on_error: str = 'N/A', rounding: str = ROUND_HALF_UP, sign: bool = False) -> str:
    """
    Sayısal bir değeri belirtilen ondalık basamağa göre formatlar.
    Hata durumunda veya None gelirse default_on_error döndürür.
    bot_core.py'nin beklentisine göre orijinal format_number fonksiyonunun adı değiştirilmiştir.

    Args:
        number: Formatlanacak sayı (int, float, str, Decimal, None).
        decimals: Gösterilecek ondalık basamak sayısı.
        default_on_error: Hata durumunda döndürülecek string.
        rounding: Yuvarlama metodu (örn: ROUND_HALF_UP, ROUND_DOWN).
        sign: Pozitif sayılar için '+' işareti eklenip eklenmeyeceği.

    Returns:
        str: Formatlanmış sayı veya hata string'i.
    """
    if number is None:
        return default_on_error

    number_dec = _to_decimal(number) # Önce Decimal'e çevir
    if number_dec is None: # Çevirme başarısızsa
        return default_on_error

    try:
        # Quantizer string'ini oluştur (örn: '1e-8' veya '1')
        quantizer_str = f'1e-{decimals}' if decimals > 0 else '1'
        quantizer = Decimal(quantizer_str)

        # Quantize ile yuvarla
        formatted_dec = number_dec.quantize(quantizer, rounding=rounding)

        # Python format string'ini oluştur
        # Örn: decimals=2, sign=True -> "{:+.2f}"
        # Örn: decimals=0, sign=False -> "{:d}" (veya "{:.0f}" ?)
        # Örn: decimals=4, sign=False -> "{:.4f}"
        format_spec = "." + str(decimals) + "f" if decimals > 0 else "d" # Tam sayı için 'd' kullanmak daha doğru olabilir
        if sign:
            format_spec = "+" + format_spec

        format_string = f"{{:{format_spec}}}"

        # Formatla ve döndür
        return format_string.format(formatted_dec)

    except Exception as e:
        logger.error(f"Sayı formatlama hatası ({number} -> {number_dec}, decimals={decimals}, sign={sign}): {e}", exc_info=False)
        return default_on_error

def censor_sensitive_data(data_to_censor: Dict[str, Any], keys_to_censor: Optional[List[str]] = None, censor_char: str = '*') -> Dict[str, Any]:
    """
    Bir sözlük içindeki hassas anahtarlara karşılık gelen değerleri sansürler (iç içe sözlükleri de işler).

    Args:
        data_to_censor: Sansürlenecek sözlük.
        keys_to_censor: Sansürlenecek anahtar isimlerinin listesi (küçük harfe duyarsız).
                        None ise varsayılan liste kullanılır.
        censor_char: Sansürleme için kullanılacak karakter.

    Returns:
        Dict: Sansürlenmiş yeni bir sözlük.
    """
    # Girdi dict değilse doğrudan döndür (örn. list içindeki elemanlar için)
    if not isinstance(data_to_censor, dict):
        return data_to_censor

    if keys_to_censor is None:
        # Varsayılan hassas anahtarlar (genişletilebilir)
        keys_to_censor = ['key', 'secret', 'token', 'pass', 'api_key', 'secret_key', 'webhook_secret', 'password']

    # Anahtarları küçük harfe çevir (karşılaştırma için)
    keys_to_censor_lower = [key.lower() for key in keys_to_censor]

    censored_data = {} # Yeni bir sözlük oluştur
    for key, value in data_to_censor.items():
        key_lower = str(key).lower() # Anahtarı string yap ve küçük harfe çevir

        # Anahtar sansürlenecekler listesinde mi kontrol et
        should_censor = any(c_key in key_lower for c_key in keys_to_censor_lower)

        if should_censor:
            # Değer string ise ve yeterince uzunsa, başını ve sonunu göster
            if isinstance(value, str) and len(value) > 6:
                censored_data[key] = f"{value[:3]}{censor_char * 5}{value[-3:]}"
            # Daha kısa string'ler için (3-6 karakter arası)
            elif isinstance(value, str) and len(value) > 2:
                censored_data[key] = f"{value[0]}{censor_char * (len(value)-2)}{value[-1]}"
            # 1 veya 2 karakterli string'ler veya boş string
            elif isinstance(value, str) and (len(value) > 0 and len(value) <=2):
                 censored_data[key] = censor_char * len(value)
            elif isinstance(value, str) and len(value) == 0: # Boş string ise
                 censored_data[key] = "" # Boş kalsın veya <EMPTY_CENSORED>
            # Diğer tipler için
            else:
                censored_data[key] = f"<{type(value).__name__}_CENSORED>"
        elif isinstance(value, dict):
            # İç içe sözlükler için özyinelemeli çağrı
            censored_data[key] = censor_sensitive_data(value, keys_to_censor, censor_char)
        elif isinstance(value, list):
            # Liste içindeki sözlükleri de sansürle
            censored_data[key] = [censor_sensitive_data(item, keys_to_censor, censor_char) if isinstance(item, dict) else item for item in value]
        else:
            # Diğer tipleri doğrudan kopyala
            censored_data[key] = value

    return censored_data


# --- Test Bloğu ---
if __name__ == '__main__':
    print("Utils Test Başlatılıyor...")
    # Test için basit logger
    if 'setup_logger' not in globals():
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        logger = logging.getLogger('utils_test')
        print("Test için basit logger ayarlandı.")

    # _to_decimal Testleri
    print("\n--- _to_decimal Testleri ---")
    assert _to_decimal("123.45") == Decimal("123.45")
    assert _to_decimal(123) == Decimal("123")
    assert _to_decimal(123.45) == Decimal("123.45")
    assert _to_decimal("123,45") == Decimal("123.45") # Virgül testi
    assert _to_decimal("-0.001") == Decimal("-0.001")
    assert _to_decimal(None) is None
    assert _to_decimal("abc") is None
    assert _to_decimal("") is None # Boş string InvalidOperation verir
    print("_to_decimal testleri başarılı.")

    # calculate_stop_loss_price Testleri
    print("\n--- calculate_stop_loss_price Testleri ---")
    assert calculate_stop_loss_price(50000, 2, 'buy') == Decimal('49000.0')
    assert calculate_stop_loss_price(50000, 2, 'sell') == Decimal('51000.0')
    assert calculate_stop_loss_price("50000.0", "2.5", 'buy') == Decimal('48750.0')
    assert calculate_stop_loss_price(50000, 0, 'buy') is None # %0 SL
    assert calculate_stop_loss_price(50000, -1, 'buy') is None # Negatif % SL
    assert calculate_stop_loss_price(None, 2, 'buy') is None # Geçersiz giriş
    assert calculate_stop_loss_price(50000, "abc", 'buy') is None # Geçersiz %
    assert calculate_stop_loss_price(50000, 2, 'hold') is None # Geçersiz yön
    assert calculate_stop_loss_price(1, 110, 'buy') is None # Fiyat negatif olacağı için None döner
    print("calculate_stop_loss_price testleri başarılı.")

    # calculate_take_profit_price Testleri
    print("\n--- calculate_take_profit_price Testleri ---")
    assert calculate_take_profit_price(50000, 4, 'buy') == Decimal('52000.0')
    assert calculate_take_profit_price(50000, 4, 'sell') == Decimal('48000.0')
    assert calculate_take_profit_price("48000", "5", 'sell') == Decimal('45600.0')
    assert calculate_take_profit_price(50000, 0, 'buy') is None # %0 TP
    assert calculate_take_profit_price(50000, -1, 'buy') is None # Negatif % TP
    assert calculate_take_profit_price(1, 110, 'sell') is None # Fiyat negatif olacağı için None döner
    print("calculate_take_profit_price testleri başarılı.")

    # calculate_pnl Testleri
    print("\n--- calculate_pnl Testleri ---")
    # Buy: (60000 - 50000) * 0.1 = 1000
    assert calculate_pnl(50000, 60000, 0.1, 'buy') == Decimal('1000.0')
    # Sell: (50000 - 45000) * 0.2 = 1000
    assert calculate_pnl(50000, 45000, 0.2, 'sell') == Decimal('1000.0')
    # Buy Zarar: (49000 - 50000) * 0.1 = -100
    assert calculate_pnl(50000, 49000, 0.1, 'buy') == Decimal('-100.0')
    # Sell Zarar: (50000 - 51000) * 0.2 = -200
    assert calculate_pnl(50000, 51000, 0.2, 'sell') == Decimal('-200.0')
    assert calculate_pnl(50000, 60000, 0, 'buy') == DECIMAL_ZERO # Sıfır miktar
    assert calculate_pnl(0, 60000, 0.1, 'buy') == DECIMAL_ZERO # Sıfır giriş
    print("calculate_pnl testleri başarılı.")

    # <<<<<<<<<<<<<< DEĞİŞİKLİK: Test bloğundaki çağrılar ve başlık güncellendi >>>>>>>>>>>>>>>
    print("\n--- format_decimal_auto Testleri ---")
    assert format_decimal_auto(123.456789, decimals=2) == "123.46"
    assert format_decimal_auto(123.456789, decimals=4) == "123.4568"
    assert format_decimal_auto(123.45, decimals=0) == "123"
    assert format_decimal_auto("123.45", decimals=1, rounding=ROUND_DOWN) == "123.4"
    assert format_decimal_auto(Decimal("123.9"), decimals=0) == "124"
    assert format_decimal_auto(None) == "N/A"
    assert format_decimal_auto("abc") == "N/A"
    assert format_decimal_auto(123.45, decimals=2, sign=True) == "+123.45"
    assert format_decimal_auto(-123.45, decimals=2, sign=True) == "-123.45"
    assert format_decimal_auto(0, decimals=0, sign=True) == "+0"
    print("format_decimal_auto testleri başarılı.")

    # censor_sensitive_data Testleri (Düzeltilmiş assert'ler ve ek testler)
    print("\n--- censor_sensitive_data Testleri ---")
    test_data = {
        "username": "testuser",
        "exchange": {
            "name": "binance",
            "api_key": "1234567890abcdef", # 16 karakter
            "secret_key": "VERYSECRETKEYHERE", # 17 karakter
            "password": "mypassword", # 10 karakter
            "some_other_value": 123
        },
        "signal": {
            "source": "webhook",
            "webhook_secret": "short", # 5 karakter
            "token": "a", # 1 karakter
            "empty_pass": "", # boş string
            "short_key": "xy" # 2 karakter
        },
        "my_keys": ["abc", {"nested_secret": "nestedValue123"}]
    }
    censored = censor_sensitive_data(test_data)
    print("Orijinal Veri:", test_data)
    print("Sansürlü Veri:", censored)
    assert censored['exchange']['api_key'] == "123*****cdef"
    assert censored['exchange']['secret_key'] == "VER*****ERE"
    assert censored['exchange']['password'] == "myp*****ord"
    assert censored['signal']['webhook_secret'] == "s***t" # 5 karakter: value[0] + *** + value[-1]
    assert censored['signal']['token'] == "*" # 1 karakter: *
    assert censored['signal']['empty_pass'] == "" # boş string: "" (veya <str_CENSORED> da kabul edilebilir, ama "" daha iyi)
    assert censored['signal']['short_key'] == "**" # 2 karakter: **
    assert isinstance(censored['my_keys'][1], dict)

    # nested_secret anahtarı "secret" içerdiği için değeri sansürlenir.
    test_data_nested_secret_check = {"nested_secret": "nestedValue123"} # 14 karakter
    censored_nested = censor_sensitive_data(test_data_nested_secret_check)
    assert censored_nested['nested_secret'] == 'nes*****123'
    assert censored['my_keys'][1]['nested_secret'] == 'nes*****123' # Orijinal testteki dict içinden kontrol

    assert censored['exchange']['name'] == "binance"
    assert censored['exchange']['some_other_value'] == 123
    assert censored['signal']['source'] == "webhook"
    assert censored['my_keys'][0] == "abc"
    print("censor_sensitive_data testleri başarılı.")

    print("\nUtils Test Tamamlandı.")