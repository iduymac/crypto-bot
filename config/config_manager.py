# config/config_manager.py

import json
import os
import logging

# --- Düzeltme: Logger'ı doğrudan core modülünden al ---
# Proje genelinde tutarlı loglama için core.logger kullanılıyor.
# Eğer bu dosya bağımsız çalıştırılacaksa (örn. test),
# if __name__ == '__main__': bloğunda basit bir logger ayarlanabilir.
try:
    # setup_logger fonksiyonunu core paketinden import et
    from core.logger import setup_logger
    # Bu modül için özel bir logger oluştur
    logger = setup_logger('config_manager')
except ImportError:
    # Eğer core.logger import edilemezse (nadiren olmalı),
    # temel bir logger ayarla ve uyarı ver.
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('config_manager_fallback')
    logger.warning("core.logger bulunamadı, fallback logger kullanılıyor.")
# --- /Düzeltme ---


class ConfigManager:
    def __init__(self, config_dir='config', config_file='settings.json'):
        """
        Botun genel ayarlarını yöneten sınıf.
        Ayarları belirtilen bir dosyadan okur ve yazar.

        Args:
            config_dir (str): Ayar dosyasının bulunduğu klasör adı.
            config_file (str): Ayar dosyasının adı.
        """
        self.config_dir = config_dir
        self.config_path = os.path.join(self.config_dir, config_file)
        self.settings = {}

        # Ayar klasörünü oluştur (varsa hata vermez)
        try:
            os.makedirs(self.config_dir, exist_ok=True)
        except OSError as e:
            # Klasör oluşturma hatasını logla ama programın devam etmesine izin ver (kritik olmayabilir)
            logger.error(f"Ayar klasörü '{self.config_dir}' oluşturulurken hata: {e}")

        # Başlangıçta ayarları yükle (opsiyonel, __init__ içinde çağrılabilir veya dışarıdan çağrılabilir)
        self.load_settings()


    def load_settings(self):
        """
        Ayar dosyasından ayarları yükler.
        Dosya yoksa veya hatalıysa boş bir sözlük döndürür ve loglar.

        Returns:
            dict: Yüklenen veya boş ayarlar sözlüğü.
        """
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f: # encoding='utf-8' eklendi
                    # Dosyanın boş olup olmadığını kontrol et
                    content = f.read()
                    if content.strip(): # Sadece boşluk karakteri varsa da boş say
                        self.settings = json.loads(content)
                        logger.info(f"Ayarlar '{self.config_path}' dosyasından başarıyla yüklendi.")
                    else:
                        logger.info(f"Ayar dosyası '{self.config_path}' boş veya sadece boşluk içeriyor.")
                        self.settings = {}
            except json.JSONDecodeError as e:
                logger.error(f"Ayar dosyası '{self.config_path}' çözümlenirken JSON hatası: {e}. Satır {e.lineno}, Sütun {e.colno}")
                self.settings = {} # Hata durumunda boş ayarlar
            except IOError as e:
                logger.error(f"Ayar dosyası '{self.config_path}' okunurken G/Ç hatası: {e}")
                self.settings = {}
            except Exception as e:
                logger.error(f"Ayarlar '{self.config_path}' yüklenirken beklenmedik hata: {e}", exc_info=True)
                self.settings = {}
        else:
            logger.info(f"Ayar dosyası '{self.config_path}' bulunamadı, varsayılan (boş) ayarlar kullanılacak.")
            self.settings = {}

        # Not: Varsayılan ayarların birleştirilmesi burada yapılmıyor.
        # Bu sorumluluk, ayarları kullanan yere (örn. SettingsDialog veya BotCore) bırakılmıştır.
        return self.settings.copy() # Ayarların bir kopyasını döndürmek daha güvenli olabilir

    def save_settings(self, settings_data=None):
        """
        Belirtilen ayarları (veya mevcut self.settings'i) dosyaya kaydeder.

        Args:
            settings_data (dict, optional): Kaydedilecek ayarlar sözlüğü. None ise,
                                           mevcut self.settings kullanılır.
        """
        # Eğer yeni veri gelmediyse, mevcut ayarları kullan
        if settings_data is not None:
            if isinstance(settings_data, dict):
                 self.settings = settings_data
            else:
                 logger.error(f"save_settings: Geçersiz settings_data tipi ({type(settings_data)}), kaydetme işlemi yapılamadı.")
                 return False # Başarısız

        if not isinstance(self.settings, dict):
            logger.error(f"save_settings: Kaydedilecek geçerli ayar verisi (self.settings) bulunamadı ({type(self.settings)}).")
            return False # Başarısız

        try:
            # Kaydetmeden önce klasörün varlığını tekrar kontrol etmek iyi olabilir
            os.makedirs(self.config_dir, exist_ok=True)

            with open(self.config_path, 'w', encoding='utf-8') as f: # encoding='utf-8' eklendi
                json.dump(self.settings, f, indent=4, ensure_ascii=False) # ensure_ascii=False Türkçe karakterler için
            logger.info(f"Ayarlar '{self.config_path}' dosyasına başarıyla kaydedildi.")
            return True # Başarılı
        except IOError as e:
             logger.error(f"Ayarlar '{self.config_path}' dosyasına yazılırken G/Ç hatası: {e}")
             return False # Başarısız
        except TypeError as e:
             logger.error(f"Ayarlar JSON formatına dönüştürülürken hata (geçersiz veri tipi?): {e}")
             return False # Başarısız
        except Exception as e:
            logger.error(f"Ayarlar '{self.config_path}' dosyasına kaydedilirken beklenmedik hata: {e}", exc_info=True)
            return False # Başarısız

    def get_setting(self, section, key, default=None):
        """
        Belirtilen bölümden bir ayar değeri alır.
        Eğer ayar bulunamazsa varsayılan değeri döndürür.
        """
        # settings'in sözlük olduğundan emin ol
        if not isinstance(self.settings, dict):
            logger.warning("get_setting: Ayarlar (self.settings) yüklenmemiş veya geçerli değil.")
            return default
        # Bölümün sözlük olduğundan emin ol
        section_data = self.settings.get(section)
        if not isinstance(section_data, dict):
            # logger.debug(f"get_setting: Bölüm '{section}' bulunamadı veya sözlük değil.")
            return default
        # Anahtarı al veya varsayılanı döndür
        return section_data.get(key, default)

    def set_setting(self, section, key, value):
        """
        Belirtilen bölüme bir ayar değeri ekler veya günceller.
        Bu değişiklik sadece bellekte yapılır, kaydetmek için save_settings çağrılmalıdır.
        """
        # settings'in sözlük olduğundan emin ol
        if not isinstance(self.settings, dict):
            logger.warning("set_setting: Ayarlar (self.settings) yüklenmemiş veya geçerli değil. Ayar yapılamıyor.")
            return

        # Bölüm yoksa oluştur
        if section not in self.settings or not isinstance(self.settings[section], dict):
            self.settings[section] = {}
        # Değeri ata
        self.settings[section][key] = value
        logger.debug(f"Ayar '{section}.{key}' bellekte '{value}' olarak ayarlandı.")


# Örnek kullanım (Dosyayı tek başına çalıştırarak test etmek için)
if __name__ == '__main__':
    print("ConfigManager Test Başlatılıyor...")

    # Test için basit bir logger ayarla (core.logger olmadan)
    if 'setup_logger' not in globals():
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        logger = logging.getLogger('config_manager_test')
        print("Test için basit logger ayarlandı.")

    # ConfigManager örneği oluştur (test için farklı bir dosya adı kullanalım)
    test_config_file = 'test_settings.json'
    test_config_path = os.path.join('config', test_config_file)
    # Önceki testten kalan dosyayı sil (varsa)
    if os.path.exists(test_config_path):
        try:
            os.remove(test_config_path)
            print(f"Önceki test dosyası '{test_config_path}' silindi.")
        except OSError as e:
            print(f"Uyarı: Önceki test dosyası silinemedi: {e}")

    config_manager = ConfigManager(config_file=test_config_file)

    # Başlangıçta ayarlar boş olmalı (dosya yok)
    initial_settings = config_manager.settings
    print(f"\nBaşlangıç Ayarları (bellekte): {initial_settings}")
    assert initial_settings == {}

    # Dosya olmadığı için yüklenen ayarlar da boş olmalı
    loaded_settings = config_manager.load_settings()
    print(f"Yüklenen Ayarlar (dosya yok): {loaded_settings}")
    assert loaded_settings == {}

    # Yeni ayarlar tanımla
    example_new_settings = {
        "exchange": {
            "name": "binance",
            "api_key": "TEST_API_KEY",
            "secret_key": "TEST_SECRET_KEY"
        },
        "risk": {
            "max_open_positions": 10,
            "max_risk_per_trade_percent": 1.5,
            "max_daily_loss_percent": 8.0
        },
        "signal": {
             "source": "telegram",
             "chat_id": "1234567890"
        },
        "extra": None,
        "feature_flags": ["beta_feature", "another_one"]
    }

    # Ayarları kaydet
    save_success = config_manager.save_settings(example_new_settings)
    print(f"\nAyarlar Kaydedildi (Başarılı: {save_success})")
    assert save_success

    # Ayarları tekrar yükleyip kontrol et
    config_manager_new = ConfigManager(config_file=test_config_file) # Yeni instance ile yükle
    reloaded_settings = config_manager_new.settings # __init__ içinde yüklenmiş olmalı
    print(f"\nTekrar Yüklenen Ayarlar: {reloaded_settings}")
    assert reloaded_settings == example_new_settings

    # Belirli bir ayarı al (yeni instance üzerinden)
    exchange_name = config_manager_new.get_setting('exchange', 'name', 'default_exchange')
    print(f"Okunan Borsa Adı: {exchange_name}")
    assert exchange_name == "binance"

    max_positions = config_manager_new.get_setting('risk', 'max_open_positions', 5)
    print(f"Okunan Maksimum Pozisyon: {max_positions}")
    assert max_positions == 10

    # Olmayan bir ayarı al (varsayılan dönmeli)
    non_existent = config_manager_new.get_setting('risk', 'non_existent_key', 'default_value')
    print(f"Okunan Olmayan Anahtar: {non_existent}")
    assert non_existent == 'default_value'

    # Olmayan bir bölümden ayar al (varsayılan dönmeli)
    non_existent_section = config_manager_new.get_setting('imaginary_section', 'some_key', 'section_default')
    print(f"Okunan Olmayan Bölüm: {non_existent_section}")
    assert non_existent_section == 'section_default'

    # Bellekte bir ayar değiştir
    config_manager_new.set_setting('trading', 'default_amount_type', 'fixed')
    print("\n'trading.default_amount_type' bellekte 'fixed' olarak ayarlandı.")
    print(f"Bellekteki Ayarlar: {config_manager_new.settings}")
    # Bu değişikliğin henüz dosyaya kaydedilmediğini kontrol et
    config_manager_recheck = ConfigManager(config_file=test_config_file)
    assert config_manager_recheck.get_setting('trading', 'default_amount_type') is None # Dosyada olmamalı

    # Değişiklikleri dosyaya kaydet
    save_success_2 = config_manager_new.save_settings() # Mevcut self.settings'i kaydet
    print(f"\nGüncellenmiş ayarlar kaydedildi (Başarılı: {save_success_2}).")
    assert save_success_2

    # Dosyayı tekrar yükleyip kontrol et
    config_manager_final_check = ConfigManager(config_file=test_config_file)
    print(f"Son Kontrol - Yüklenen Ayarlar: {config_manager_final_check.settings}")
    assert config_manager_final_check.get_setting('trading', 'default_amount_type') == 'fixed'

    print("\nConfigManager Test Tamamlandı.")