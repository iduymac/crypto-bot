# core/logger.py

import logging
import os
from logging.handlers import RotatingFileHandler
import sys # Hata durumunda stderr'e yazmak için

# Varsayılan log dizini ve dosyası
DEFAULT_LOG_DIR = os.path.join('data', 'logs')
DEFAULT_LOG_FILE = 'bot.log' # Ana log dosyası
# Varsayılan log formatı
DEFAULT_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)-8s - %(message)s'
# Varsayılan log seviyesi
DEFAULT_LOG_LEVEL = logging.INFO

# Daha önce yapılandırılmış loglayıcıları takip etmek için (handler'ların tekrar tekrar eklenmesini önler)
_configured_loggers = set()

def setup_logger(name, log_file=None, log_dir=DEFAULT_LOG_DIR, level=DEFAULT_LOG_LEVEL, log_format=DEFAULT_LOG_FORMAT):
    """
    Belirtilen isim için bir logger yapılandırır ve döndürür.
    Logları dosyaya (RotatingFileHandler) yazar.
    Eğer logger zaten yapılandırılmışsa, mevcut logger'ı döndürür.

    Args:
        name (str): Logger'a verilecek isim (genellikle __name__).
        log_file (str, optional): Log dosyasının adı. None ise 'name' kullanılır.
        log_dir (str): Log dosyalarının saklanacağı dizin.
        level (int): Logger için minimum log seviyesi (logging.INFO, logging.DEBUG vb.).
        log_format (str): Log mesajlarının formatı.

    Returns:
        logging.Logger: Yapılandırılmış logger nesnesi.
    """
    global _configured_loggers

    logger = logging.getLogger(name)

    # Eğer bu logger için zaten handler eklenmişse (yani yapılandırılmışsa), tekrar yapma.
    # Bu, aynı isimle birden fazla çağrıldığında handler'ların çoğalmasını önler.
    # Not: Bu basit kontrol, aynı isimli logger'a farklı handler'lar eklenmesini engellemez,
    # sadece bu fonksiyonun aynı logger'ı tekrar tekrar aynı şekilde yapılandırmasını önler.
    if name in _configured_loggers and logger.hasHandlers():
        # Seviyenin istenen seviyeye ayarlandığından emin ol (eğer farklı istenirse)
        if logger.level != level and level is not None: # level None değilse ve farklıysa ayarla
             logger.setLevel(level)
        # print(f"DEBUG: Logger '{name}' zaten yapılandırılmış ve handler'ları var.")
        return logger

    # Seviyeyi ayarla (eğer daha önce ayarlanmadıysa veya farklıysa)
    if logger.level == logging.NOTSET or logger.level > level: # Sadece daha kısıtlayıcı bir seviyeye ayarla
        logger.setLevel(level)

    # Root logger'a konsol handler'ı eklenmiş olabilir, bu yüzden propagate=False yapmak
    # bu logger'ın mesajlarının root'a gitmesini engeller. İsteğe bağlı.
    # logger.propagate = False

    # --- Dosya Handler'ı Ayarla ---
    try:
        os.makedirs(log_dir, exist_ok=True)
        
        # Eğer log_file belirtilmemişse, logger'ın adını kullan (örn: 'module_name.log')
        # Veya her şeyin DEFAULT_LOG_FILE'a gitmesini istiyorsak, onu kullan.
        # Proje genelinde tek bir ana log dosyası daha yaygındır.
        # Farklı modüller için farklı log dosyaları isteniyorsa log_file parametresi kullanılabilir.
        # Şimdilik, eğer log_file None ise, DEFAULT_LOG_FILE kullanılsın.
        actual_log_file = log_file if log_file else DEFAULT_LOG_FILE
        log_path = os.path.join(log_dir, actual_log_file)

        # Handler oluştur (RotatingFileHandler)
        file_handler = RotatingFileHandler(log_path, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
        file_handler.setLevel(level) # Handler için de seviyeyi ayarla

        formatter = logging.Formatter(log_format)
        file_handler.setFormatter(formatter)

        # Handler'ı logger'a ekle (eğer benzer bir handler zaten yoksa)
        # Bu kontrol daha karmaşık olabilir, şimdilik basitçe ekliyoruz.
        # _configured_loggers seti bu fonksiyonun tekrar aynı şeyi yapmasını engellemeli.
        if not logger.handlers: # Sadece hiç handler'ı yoksa ekle (ya da daha spesifik kontrol)
            logger.addHandler(file_handler)
        elif not any(isinstance(h, RotatingFileHandler) and h.baseFilename == file_handler.baseFilename for h in logger.handlers):
            logger.addHandler(file_handler) # Aynı dosyaya yazan başka bir handler yoksa ekle


    except (OSError, IOError) as e:
        sys.stderr.write(f"KRİTİK LOGGER HATASI (Dosya): Logger '{name}' için dosya handler ({log_path}) ayarlanamadı: {e}\n")
    except Exception as e:
         sys.stderr.write(f"KRİTİK LOGGER HATASI (Genel): Logger '{name}' için dosya handler ayarlanamadı: {e}\n")

    _configured_loggers.add(name)
    # print(f"DEBUG: Logger '{name}' yapılandırıldı/güncellendi. Handler'lar: {logger.handlers}")

    return logger

if __name__ == '__main__':
    print("Logger Test Başlatılıyor...")
    logger1 = setup_logger('module1', level=logging.DEBUG) # bot.log'a gider
    logger2 = setup_logger('module2', log_file='module2_specific.log', level=logging.INFO) # module2_specific.log'a gider
    logger3 = setup_logger('module1') # Tekrar çağrılınca aynı logger dönmeli (bot.log)

    assert id(logger1) == id(logger3)
    assert logger1.level == logging.DEBUG

    logger1.debug("Bu bir debug mesajı (module1 -> bot.log).")
    logger1.info("Bu bir info mesajı (module1 -> bot.log).")
    logger2.info("Bu bir info mesajı (module2 -> module2_specific.log).")
    logger2.warning("Bu bir uyarı mesajı (module2 -> module2_specific.log).")
    logger1.error("Bu bir hata mesajı (module1 -> bot.log).")

    print("\nLog dosyalarını 'data/logs' klasöründe kontrol edin:")
    print(f"- {os.path.join(DEFAULT_LOG_DIR, DEFAULT_LOG_FILE)}")
    print(f"- {os.path.join(DEFAULT_LOG_DIR, 'module2_specific.log')}")
    print("\nLogger Test Tamamlandı.")