# config/user_config_manager.py

import json
import os
import logging
import hashlib # <<< YENİ >>> Parola hash'leme için eklendi

# --- Düzeltme: Logger'ı doğrudan core modülünden al ---
try:
    from core.logger import setup_logger
    logger = setup_logger('user_config_manager')
except ImportError:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('user_config_manager_fallback')
    logger.warning("core.logger bulunamadı, fallback logger kullanılıyor.")
# --- /Düzeltme ---

# Kullanıcı verilerinin saklanacağı dosya yolu (data klasörü içinde)
USER_DATA_DIR = 'data'
USER_DATA_FILE = os.path.join(USER_DATA_DIR, 'users.json')

class UserConfigManager:
    def __init__(self):
        """
        Kullanıcıya özel yapılandırmaları yöneten sınıf.
        Kullanıcı verilerini bir dosyadan okur ve yazar.
        """
        self.users_data = {}
        # --- Düzeltme: Sadece 'data' dizinini oluştur ---
        # Gerekli olan ana 'data' klasörünü oluştur.
        # 'data/logs' klasörünü logger modülü kendi içinde halletmeli.
        try:
            os.makedirs(USER_DATA_DIR, exist_ok=True)
        except OSError as e:
            logger.error(f"Kullanıcı veri klasörü '{USER_DATA_DIR}' oluşturulurken hata: {e}")
        # --- /Düzeltme ---

        # Kullanıcı veri dosyasını yükle
        self._load_users()

    # <<< YENİ BÖLÜM: Parola Yönetimi >>>
    def _hash_password(self, password):
        """Verilen parolayı SHA-256 ile hash'ler."""
        if not password:
            return None
        return hashlib.sha256(password.encode('utf-8')).hexdigest()

    def verify_user(self, username, password):
        """Kullanıcı adı ve parolayı doğrular (Detaylı loglama ile)."""
        user_data = self.users_data.get(username)
        if not user_data:
            logger.warning(f"Doğrulama denemesi: '{username}' kullanıcısı bulunamadı.")
            return False

        stored_hash = user_data.get('password_hash')
        if not stored_hash:
            logger.error(f"'{username}' kullanıcısının 'password_hash' alanı eksik! Güvenlik riski.")
            return False

        input_hash = self._hash_password(password)

        # --- YENİ DEBUG BÖLÜMÜ ---
        print("\n--- DEĞER KONTROLÜ (verify_user içinden) ---")
        print(f"Dosyadan Okunan Hash: '{stored_hash}'")
        print(f"Girişten Gelen Hash:  '{input_hash}'")
        print(f"Tipleri Aynı Mı?      {type(stored_hash) == type(input_hash)}")
        print(f"Uzunlukları Aynı Mı?  {len(stored_hash) == len(input_hash)}")
        print(f"Değerler Eşleşti Mi?  {stored_hash == input_hash}")
        print("--- /DEĞER KONTROLÜ ---")
        # --- /YENİ DEBUG BÖLÜMÜ SONU ---

        if stored_hash == input_hash:
            logger.info(f"'{username}' kullanıcısı için parola doğrulandı.")
            return True
        else:
            logger.warning(f"'{username}' kullanıcısı için yanlış parola denemesi.")
            return False
            
    def set_password(self, username, password):
        """Bir kullanıcının parolasını ayarlar veya günceller."""
        if username not in self.users_data:
            raise ValueError(f"'{username}' adlı kullanıcı bulunamadı.")
        
        if not password or len(password) < 4: # Örnek bir kural
             raise ValueError("Parola çok kısa.")

        self.users_data[username]['password_hash'] = self._hash_password(password)
        return self._save_users() # Değişikliği kaydet
    # <<< / YENİ BÖLÜM SONU >>>


    def _load_users(self):
        """
        Kullanıcı verilerini dosyadan yükler.
        Dosya yoksa veya boşsa/hatalıysa boş bir sözlük başlatır.
        """
        if os.path.exists(USER_DATA_FILE):
            # Dosya boyutunu kontrol et (boş dosyayı okumaya çalışmamak için)
            try:
                if os.path.getsize(USER_DATA_FILE) == 0:
                    logger.info(f"Kullanıcı veri dosyası '{USER_DATA_FILE}' boş.")
                    self.users_data = {}
                    return # Boşsa okumaya çalışma
            except OSError as e:
                 logger.error(f"Kullanıcı veri dosyası boyutu okunurken hata '{USER_DATA_FILE}': {e}")
                 self.users_data = {} # Hata durumunda boş veri
                 return

            # Dosya varsa ve boş değilse okumayı dene
            try:
                with open(USER_DATA_FILE, 'r', encoding='utf-8') as f: # encoding eklendi
                    self.users_data = json.load(f)
                    if not isinstance(self.users_data, dict):
                         logger.error(f"Kullanıcı veri dosyası '{USER_DATA_FILE}' geçerli bir JSON sözlüğü içermiyor. İçerik tipi: {type(self.users_data)}")
                         self.users_data = {} # Hatalı formatta ise sıfırla
                    else:
                         logger.info(f"Kullanıcı verileri '{USER_DATA_FILE}' dosyasından başarıyla yüklendi.")
            except json.JSONDecodeError as e:
                logger.error(f"Kullanıcı veri dosyası '{USER_DATA_FILE}' çözümlenirken JSON hatası: {e}. Satır {e.lineno}, Sütun {e.colno}")
                self.users_data = {} # Hata durumunda boş veri
            except IOError as e:
                logger.error(f"Kullanıcı veri dosyası '{USER_DATA_FILE}' okunurken G/Ç hatası: {e}")
                self.users_data = {}
            except Exception as e:
                logger.error(f"Kullanıcı verileri '{USER_DATA_FILE}' yüklenirken beklenmedik hata: {e}", exc_info=True)
                self.users_data = {}
        else:
            logger.info(f"Kullanıcı veri dosyası '{USER_DATA_FILE}' bulunamadı, boş başlatılıyor.")
            self.users_data = {} # Dosya yoksa boş başlat


    def _save_users(self):
        """
        Güncel kullanıcı verilerini dosyaya kaydeder.
        """
        if not isinstance(self.users_data, dict):
             logger.error(f"_save_users: Kaydedilecek geçerli kullanıcı verisi (self.users_data) yok veya sözlük değil ({type(self.users_data)}).")
             return False

        try:
            # Kaydetmeden önce dizinin var olduğundan emin ol
            os.makedirs(USER_DATA_DIR, exist_ok=True)

            with open(USER_DATA_FILE, 'w', encoding='utf-8') as f: # encoding eklendi
                json.dump(self.users_data, f, indent=4, ensure_ascii=False) # ensure_ascii=False eklendi
            logger.info(f"Kullanıcı verileri '{USER_DATA_FILE}' dosyasına başarıyla kaydedildi.")
            return True
        except IOError as e:
            logger.error(f"Kullanıcı verileri '{USER_DATA_FILE}' dosyasına yazılırken G/Ç hatası: {e}")
            return False
        except TypeError as e:
             logger.error(f"Kullanıcı verileri JSON formatına dönüştürülürken hata (geçersiz veri tipi?): {e}")
             return False
        except Exception as e:
            logger.error(f"Kullanıcı verileri kaydedilirken beklenmedik hata: {e}", exc_info=True)
            return False

    def get_all_users(self):
        """
        Tüm kullanıcıların kullanıcı adlarının listesini döndürür.

        Returns:
            list: Kullanıcı adları listesi.
        """
        # users_data'nın sözlük olduğundan emin ol
        if not isinstance(self.users_data, dict):
             logger.warning("get_all_users: Kullanıcı verisi yüklenmemiş veya geçerli değil.")
             return []
        return list(self.users_data.keys())

    def get_user(self, username):
        """
        Belirtilen kullanıcının tüm yapılandırma verilerinin bir kopyasını döndürür.

        Args:
            username (str): Kullanıcı adı.

        Returns:
            dict or None: Kullanıcı verileri sözlüğünün kopyası veya kullanıcı bulunamazsa None.
        """
        if not isinstance(self.users_data, dict):
             logger.warning(f"get_user({username}): Kullanıcı verisi yüklenmemiş veya geçerli değil.")
             return None

        user_data = self.users_data.get(username)
        # Derin kopya döndürmek daha güvenli olabilir, ancak şimdilik sığ kopya yeterli.
        return user_data.copy() if user_data else None

    def add_user(self, user_data):
        """

        Yeni bir kullanıcı ekler. Kullanıcı adı mevcutsa hata verir.

        Args:
            user_data (dict): Eklenecek kullanıcı verileri (en az 'username' içermeli).

        Returns:
            bool: Ekleme başarılıysa True, değilse False.

        Raises:
            ValueError: Kullanıcı adı boşsa veya zaten mevcutsa.
        """
        if not isinstance(self.users_data, dict):
             logger.error("add_user: Kullanıcı verisi (self.users_data) geçerli değil.")
             return False

        if not isinstance(user_data, dict):
             logger.error(f"add_user: Geçersiz user_data tipi ({type(user_data)}). Sözlük bekleniyor.")
             return False

        username = user_data.get('username')
        if not username or not isinstance(username, str) or not username.strip():
            logger.warning("Kullanıcı adı boş, sadece boşluk içeriyor veya string değil.")
            raise ValueError("Geçerli bir kullanıcı adı gereklidir.")

        username = username.strip() # Baştaki/sondaki boşlukları temizle

        if username in self.users_data:
            logger.warning(f"'{username}' adlı kullanıcı zaten mevcut.")
            raise ValueError(f"'{username}' adlı kullanıcı zaten mevcut.")

        # Kullanıcı verilerini ekle (gelen verinin kopyasını saklamak iyi olabilir)
        self.users_data[username] = user_data.copy()
        # Değişikliği kaydet
        if self._save_users():
            logger.info(f"'{username}' adlı kullanıcı başarıyla eklendi.")
            return True
        else:
            # Kaydetme başarısız olduysa, eklenen kullanıcıyı bellekten geri al
            logger.error(f"'{username}' eklendi ancak dosyaya kaydedilemedi. Değişiklik geri alınıyor.")
            if username in self.users_data: # Tekrar kontrol et
                 del self.users_data[username]
            return False


    def update_user(self, user_data):
        """
        Mevcut bir kullanıcının yapılandırma verilerini günceller.
        Kullanıcı bulunamazsa hata verir.

        Args:
            user_data (dict): Güncellenecek kullanıcı verileri (en az 'username' içermeli).

        Returns:
            bool: Güncelleme başarılıysa True, değilse False.

        Raises:
            ValueError: Kullanıcı adı boşsa veya bulunamazsa.
        """
        if not isinstance(self.users_data, dict):
             logger.error("update_user: Kullanıcı verisi (self.users_data) geçerli değil.")
             return False

        if not isinstance(user_data, dict):
             logger.error(f"update_user: Geçersiz user_data tipi ({type(user_data)}). Sözlük bekleniyor.")
             return False

        username = user_data.get('username')
        if not username or not isinstance(username, str) or not username.strip():
            logger.warning("Güncelleme için kullanıcı adı boş, sadece boşluk içeriyor veya string değil.")
            raise ValueError("Geçerli bir kullanıcı adı gereklidir.")

        username = username.strip()

        if username not in self.users_data:
            logger.warning(f"'{username}' adlı kullanıcı bulunamadı, güncelleme yapılamıyor.")
            raise ValueError(f"'{username}' adlı kullanıcı bulunamadı.")

        # Mevcut verinin bir kopyasını al (geri alma ihtimaline karşı)
        original_data = self.users_data[username].copy()

        # Mevcut veriyi gelen veri ile güncelle
        try:
            # .update() yerine yeni veriyi doğrudan atamak, eski/istenmeyen anahtarları temizler.
            # Eğer sadece belirli alanların güncellenmesi isteniyorsa .update() kullanılabilir.
            # SettingsDialog'dan gelen verinin tam ve güncel olduğu varsayımıyla doğrudan atama yapalım.
            # Ancak username gibi kritik alanların üzerine yazılmadığından emin olunmalı.
            # En güvenlisi: username dışındaki veriyi güncellemek veya sadece beklenen anahtarları almak.
            # Şimdilik basitçe update kullanalım:
            self.users_data[username].update(user_data)
        except Exception as update_err:
             logger.error(f"'{username}' için bellek içi güncelleme sırasında hata: {update_err}")
             self.users_data[username] = original_data # Güncellemeyi geri al
             return False


        # Değişikliği kaydet
        if self._save_users():
            logger.info(f"'{username}' adlı kullanıcı başarıyla güncellendi.")
            return True
        else:
             # Kaydetme başarısız olduysa, bellek içi güncellemeyi geri al
             logger.error(f"'{username}' güncellendi ancak dosyaya kaydedilemedi. Bellek içi değişiklik geri alınıyor.")
             self.users_data[username] = original_data
             return False


    def delete_user(self, username):
        """
        Belirtilen kullanıcıyı siler. Kullanıcı bulunamazsa hata verir.

        Args:
            username (str): Silinecek kullanıcı adı.

        Returns:
            bool: Silme başarılıysa True, değilse False.

        Raises:
            ValueError: Kullanıcı bulunamazsa.
        """
        if not isinstance(self.users_data, dict):
             logger.error("delete_user: Kullanıcı verisi (self.users_data) geçerli değil.")
             return False

        if not username or not isinstance(username, str):
             logger.warning("Silme için geçersiz kullanıcı adı.")
             # Hata fırlatmak yerine False döndürebiliriz.
             raise ValueError("Geçersiz kullanıcı adı.")


        username = username.strip()

        if username not in self.users_data:
            logger.warning(f"'{username}' adlı kullanıcı bulunamadı, silinemiyor.")
            raise ValueError(f"'{username}' adlı kullanıcı bulunamadı.")

        # Kullanıcıyı silmeden önce yedekle (geri alma ihtimali için)
        deleted_data = self.users_data.pop(username) # Hem siler hem de veriyi döndürür

        # Değişikliği kaydet
        if self._save_users():
            logger.info(f"'{username}' adlı kullanıcı başarıyla silindi.")
            return True
        else:
            # Kaydetme başarısız olduysa, silinen kullanıcıyı geri yükle
            logger.error(f"'{username}' silindi ancak dosyaya kaydedilemedi. Bellek içi değişiklik geri alınıyor.")
            self.users_data[username] = deleted_data # Silinen veriyi geri koy
            return False


# Örnek kullanım (Dosyayı tek başına çalıştırarak test etmek için)
if __name__ == '__main__':
    print("UserConfigManager Test Başlatılıyor...")

    # Test için basit bir logger ayarla
    if 'setup_logger' not in globals():
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        logger = logging.getLogger('user_config_manager_test')
        print("Test için basit logger ayarlandı.")

    # Önceki testten kalan dosyayı sil (varsa)
    if os.path.exists(USER_DATA_FILE):
        try:
            os.remove(USER_DATA_FILE)
            print(f"Önceki kullanıcı veri dosyası '{USER_DATA_FILE}' silindi.")
        except OSError as e:
            print(f"Uyarı: Önceki kullanıcı dosyası silinemedi: {e}")

    # UserConfigManager örneği oluştur
    user_manager = UserConfigManager()

    print(f"Başlangıç Kullanıcıları: {user_manager.get_all_users()}")
    assert user_manager.get_all_users() == []

    # --- Kullanıcı Ekleme Testleri ---
    print("\n--- Kullanıcı Ekleme ---")
    user1 = {
        "username": "test_user_1",
        "exchange": {"name": "binance", "api_key": "key1", "secret_key": "secret1"},
        "risk": {"max_open_positions": 5},
    }
    add1_success = user_manager.add_user(user1)
    print(f"'test_user_1' eklendi (Başarılı: {add1_success})")
    assert add1_success
    assert "test_user_1" in user_manager.get_all_users()

    # Aynı kullanıcıyı tekrar eklemeyi dene (ValueError bekleniyor)
    try:
        user_manager.add_user({"username": "test_user_1"})
        print("Hata: Aynı kullanıcı tekrar eklenebildi!")
        assert False
    except ValueError as e:
        print(f"Beklenen hata alındı: {e}")

    # Boş kullanıcı adı eklemeyi dene (ValueError bekleniyor)
    try:
        user_manager.add_user({"username": "  "})
        print("Hata: Boş kullanıcı adı eklenebildi!")
        assert False
    except ValueError as e:
        print(f"Beklenen hata alındı: {e}")

    user2 = {
        "username": "test_user_2",
        "exchange": {"name": "kucoin"},
        "trading": {"default_leverage": 10}
    }
    add2_success = user_manager.add_user(user2)
    print(f"'test_user_2' eklendi (Başarılı: {add2_success})")
    assert add2_success
    assert len(user_manager.get_all_users()) == 2

    print(f"\nGüncel Kullanıcılar: {user_manager.get_all_users()}")

    # --- Kullanıcı Alma Testleri ---
    print("\n--- Kullanıcı Alma ---")
    retrieved_user1 = user_manager.get_user("test_user_1")
    print(f"'test_user_1' Verileri: {retrieved_user1}")
    assert retrieved_user1 == user1
    # Alınan veriyi değiştirmenin orijinali etkilemediğini kontrol et (copy sayesinde)
    if retrieved_user1:
         retrieved_user1["risk"]["max_open_positions"] = 99
    assert user_manager.get_user("test_user_1")["risk"]["max_open_positions"] == 5

    retrieved_user_nonexistent = user_manager.get_user("non_existent")
    print(f"'non_existent' Verileri: {retrieved_user_nonexistent}")
    assert retrieved_user_nonexistent is None

    # --- Kullanıcı Güncelleme Testleri ---
    print("\n--- Kullanıcı Güncelleme ---")
    update_data = {
        "username": "test_user_1", # Güncellenecek kullanıcı
        "risk": {"max_open_positions": 7, "max_daily_loss_percent": 15.0}, # Risk güncellendi
        "signal": {"source": "webhook"} # Yeni bölüm eklendi
    }
    update_success = user_manager.update_user(update_data)
    print(f"'test_user_1' güncellendi (Başarılı: {update_success})")
    assert update_success

    updated_user1 = user_manager.get_user("test_user_1")
    print(f"'test_user_1' Güncel Verileri: {updated_user1}")
    assert updated_user1["risk"]["max_open_positions"] == 7
    assert updated_user1["risk"]["max_daily_loss_percent"] == 15.0
    assert updated_user1["signal"]["source"] == "webhook"
    assert updated_user1["exchange"]["name"] == "binance" # Eski veri korunmalı

    # Olmayan kullanıcıyı güncellemeyi dene (ValueError bekleniyor)
    try:
        user_manager.update_user({"username": "non_existent", "key": "value"})
        print("Hata: Olmayan kullanıcı güncellenebildi!")
        assert False
    except ValueError as e:
        print(f"Beklenen hata alındı: {e}")

    # --- Kullanıcı Silme Testleri ---
    print("\n--- Kullanıcı Silme ---")
    delete_success = user_manager.delete_user("test_user_2")
    print(f"'test_user_2' silindi (Başarılı: {delete_success})")
    assert delete_success
    assert "test_user_2" not in user_manager.get_all_users()
    assert len(user_manager.get_all_users()) == 1

    # Olmayan kullanıcıyı silmeyi dene (ValueError bekleniyor)
    try:
        user_manager.delete_user("test_user_2") # Zaten silindi
        print("Hata: Silinmiş kullanıcı tekrar silinebildi!")
        assert False
    except ValueError as e:
        print(f"Beklenen hata alındı: {e}")

    # Kalan kullanıcıyı kontrol et
    assert user_manager.get_user("test_user_1") is not None

    # --- Dosya Kontrolü ---
    print("\n--- Dosya Kontrolü ---")
    # Yeni bir yönetici oluşturup dosyadan yükleyerek verinin kalıcı olduğunu kontrol et
    user_manager_reloaded = UserConfigManager()
    print(f"Dosyadan Yüklenen Kullanıcılar: {user_manager_reloaded.get_all_users()}")
    assert user_manager_reloaded.get_all_users() == ["test_user_1"]
    reloaded_user1_data = user_manager_reloaded.get_user("test_user_1")
    print(f"Dosyadan Yüklenen 'test_user_1' Verisi: {reloaded_user1_data}")
    assert reloaded_user1_data["risk"]["max_open_positions"] == 7 # Güncellenmiş veri olmalı

    print("\nUserConfigManager Test Tamamlandı.")