# config/language_manager.py

import json
import os
import logging

# Bu sınıfın kendi logger'ı olacak
try:
    from core.logger import setup_logger
    logger = setup_logger('language_manager')
except ImportError:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('language_manager_fallback')
    logger.warning("core.logger bulunamadı, fallback logger kullanılıyor.")

class LanguageManager:
    def __init__(self, lang_dir='config/lang', default_lang='tr'):
        """
        Uygulama metinlerini yöneten sınıf.
        
        Args:
            lang_dir (str): Dil JSON dosyalarının bulunduğu klasör.
            default_lang (str): Geçerli bir dil yüklenemezse kullanılacak varsayılan dil kodu (örn: 'tr').
        """
        self.lang_dir = lang_dir
        self.default_lang = default_lang
        self.available_languages = self._find_available_languages()
        self.current_lang_code = ""
        self.strings = {}

        if not self.available_languages:
            logger.error(f"Dil klasöründe ('{self.lang_dir}') hiçbir dil dosyası bulunamadı!")
        else:
            logger.info(f"Kullanılabilir diller bulundu: {list(self.available_languages.keys())}")
            
        # Başlangıçta varsayılan dili yükle
        self.load_language(self.default_lang)

    def _find_available_languages(self) -> dict:
        """Dil klasörünü tarar ve mevcut dil dosyalarını bulur."""
        languages = {}
        if not os.path.isdir(self.lang_dir):
            return languages
            
        try:
            for filename in os.listdir(self.lang_dir):
                if filename.startswith('lang_') and filename.endswith('.json'):
                    lang_code = filename.replace('lang_', '').replace('.json', '')
                    languages[lang_code] = os.path.join(self.lang_dir, filename)
            return languages
        except OSError as e:
            logger.error(f"Dil klasörü okunurken hata: {e}")
            return {}

    def load_language(self, lang_code: str) -> bool:
        """Belirtilen dil koduna ait dil dosyasını yükler."""
        if lang_code not in self.available_languages:
            logger.error(f"'{lang_code}' dili mevcut değil. Yükleme başarısız.")
            # Eğer mevcut dil geçerliyse onu koru, değilse varsayılanı dene
            if not self.strings:
                logger.warning(f"Hiçbir dil yüklü değil, varsayılan dil ('{self.default_lang}') deneniyor.")
                if self.default_lang in self.available_languages:
                    return self.load_language(self.default_lang)
                else:
                    logger.error("Varsayılan dil bile yüklenemiyor!")
                    self.strings = {} # Boşalt
                    self.current_lang_code = ""
            return False

        file_path = self.available_languages[lang_code]
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                self.strings = json.load(f)
            self.current_lang_code = lang_code
            logger.info(f"'{lang_code}' dili başarıyla yüklendi. ({file_path})")
            return True
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"'{lang_code}' dil dosyası ('{file_path}') okunurken hata: {e}")
            self.strings = {}
            self.current_lang_code = ""
            return False

    def get_string(self, key: str, **kwargs) -> str:
        """
        Belirtilen anahtara karşılık gelen metni döndürür.
        Metin içinde formatlama için değişkenler alabilir.
        
        Örnek: get_string('status_welcome', user='ibrahim', days=207)
        """
        string_template = self.strings.get(key)
        
        if string_template is None:
            logger.warning(f"'{self.current_lang_code}' dilinde '{key}' anahtarı bulunamadı.")
            return f"[{key}]" # Hata durumunda anahtarı göster

        if kwargs:
            try:
                return string_template.format(**kwargs)
            except KeyError as e:
                logger.error(f"'{key}' metnini formatlarken eksik değişken: {e}")
                return string_template # Formatlayamazsa şablonu döndür
        
        return string_template
        
# Bu dosyayı tek başına test etmek için
if __name__ == '__main__':
    # Test için 'config/lang' klasörünün bir üst dizinde olmamız gerekir.
    # Bu yüzden path'i manuel olarak ayarlayalım.
    # Proje ana dizinindeyken `python -m config.language_manager` komutuyla çalıştırılabilir.
    
    # Test logger
    if 'setup_logger' not in globals():
        logging.basicConfig(level=logging.DEBUG)
        logger = logging.getLogger('language_manager_test')

    print("--- LanguageManager Test Başlatılıyor ---")
    
    # Projenin kök dizinini varsayarak lang_dir'i ayarlayalım
    # Bu test betiği ana dizinden çalıştırıldığında doğru çalışır.
    test_lang_dir = os.path.join('config', 'lang')
    
    lang_manager = LanguageManager(lang_dir=test_lang_dir, default_lang='tr')
    
    print(f"Mevcut Diller: {lang_manager.available_languages.keys()}")
    
    # Türkçe metin testi
    print("\n--- Türkçe Testi ---")
    print(f"Giriş Butonu: {lang_manager.get_string('login_button')}")
    print(f"Hoş Geldiniz Mesajı: {lang_manager.get_string('status_welcome', user='TestUser', days=100)}")
    
    # İngilizce'ye geçiş
    print("\n--- İngilizce Testi ---")
    lang_manager.load_language('en')
    print(f"Login Button: {lang_manager.get_string('login_button')}")
    print(f"Welcome Message: {lang_manager.get_string('status_welcome', user='TestUser', days=100)}")
    
    # Rusça'ya geçiş
    print("\n--- Rusça Testi ---")
    lang_manager.load_language('ru')
    print(f"Кнопка входа: {lang_manager.get_string('login_button')}")
    print(f"Приветственное сообщение: {lang_manager.get_string('status_welcome', user='TestUser', days=100)}")
    
    # Olmayan anahtar testi
    print("\n--- Hata Testi ---")
    print(f"Olmayan Anahtar: {lang_manager.get_string('non_existent_key')}")