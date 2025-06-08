# debug_login.py

import os
import sys

# Proje ana dizinini yola ekle, böylece modülleri bulabilir
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

print("--- Login Testi Başladı ---")

try:
    from config.user_config_manager import UserConfigManager
    print("UserConfigManager başarıyla import edildi.")
except ImportError as e:
    print(f"HATA: UserConfigManager import edilemedi: {e}")
    print("Lütfen bu betiği projenin ana dizininden çalıştırdığınızdan emin olun.")
    sys.exit(1)

# Test edilecek kullanıcı bilgileri
test_username = "ibrahim"
test_password = "1234"

print(f"Kullanıcı adı '{test_username}' ve parola '{test_password}' test ediliyor...")

# Yöneticiyi başlat
manager = UserConfigManager()

# Doğrulama fonksiyonunu çağır
is_valid = manager.verify_user(test_username, test_password)

# Sonucu yazdır
if is_valid:
    print("\nSONUÇ: >>> Doğrulama BAŞARILI! <<<")
    print("UserConfigManager ve users.json dosyası doğru çalışıyor.")
    print("Sorun büyük ihtimalle giriş ekranına yazarken bir yazım hatasından kaynaklanıyor.")
else:
    print("\nSONUÇ: >>> Doğrulama BAŞARISIZ! <<<")
    print("UserConfigManager veya users.json dosyasında bir sorun var gibi görünüyor.")

print("--- Login Testi Bitti ---")