# main.py (Dil Desteği Entegre Edilmiş Hali)

import sys
import os

# Projenin kök dizinini sys.path'e ekle
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# --- Modül Importları ---
import logging
import threading
from PyQt5.QtWidgets import QApplication, QMessageBox, QDialog

try:
    from core.logger import setup_logger
    from gui.main_window import MainWindow
    from gui.login_dialog import LoginDialog
    from config.config_manager import ConfigManager
    from config.user_config_manager import UserConfigManager
    from core.database_manager import DatabaseManager
    from config.language_manager import LanguageManager # <<< YENİ: LanguageManager import edildi
except ImportError as e:
    QMessageBox.critical(None, "Kritik Import Hatası", f"Gerekli bir modül bulunamadı:\n{e}\n\nUygulama kapatılacak.")
    sys.exit(1)

# --- Loglama Sistemini Kur ---
try:
    setup_logger('__main__', level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.info("Ana loglayıcı başarıyla ayarlandı.")
except Exception as e_log:
    QMessageBox.critical(None, "Kritik Loglama Hatası", f"Loglama sistemi kurulamadı:\n{e_log}\n\nUygulama kapatılacak.")
    sys.exit(1)


# --- Ana Uygulama Fonksiyonu ---
def main():
    """Uygulamanın ana giriş noktası."""
    logger.info("Uygulama başlatılıyor...")
    app = QApplication(sys.argv)

    # --- Yöneticileri Başlat ---
    config_manager_instance = ConfigManager()
    user_config_manager_instance = UserConfigManager()
    database_manager_instance = DatabaseManager()
    
    # <<< YENİ: Dil Yöneticisini Başlatma ve Dili Yükleme >>>
    lang_manager_instance = LanguageManager(default_lang='tr')
    # Kayıtlı dil ayarını oku, yoksa varsayılanı ('tr') kullan
    saved_lang = config_manager_instance.get_setting('general', 'language', 'tr')
    lang_manager_instance.load_language(saved_lang)
    logger.info(f"Uygulama dili '{saved_lang}' olarak ayarlandı.")


    # --- Login Ekranını Göster ---
    # <<< DEĞİŞİKLİK >>> LoginDialog'a artık lang_manager'ı parametre olarak veriyoruz
    login_dialog = LoginDialog(lang_manager=lang_manager_instance)
    
    if login_dialog.exec_() == QDialog.Accepted:
        logged_in_user = login_dialog.get_logged_in_user()
        logger.info(f"'{logged_in_user}' kullanıcısı başarıyla giriş yaptı.")
        
        try:
            # <<< DEĞİŞİKLİK >>> MainWindow'a da lang_manager'ı parametre olarak iletiyoruz
            main_window = MainWindow(
                logged_in_user=logged_in_user,
                lang_manager=lang_manager_instance, # <<< YENİ PARAMETRE
                config_manager_instance=config_manager_instance,
                user_config_manager_instance=user_config_manager_instance,
                database_manager_instance=database_manager_instance
            )
            
            # Webhook listener'ı başlatma (mevcut kodunuz)
            webhook_thread = None
            try:
                from core.webhook_listener import run_webhook_server
                webhook_port = config_manager_instance.get_setting('general', 'webhook_port', 5000)
                webhook_host = config_manager_instance.get_setting('general', 'webhook_host', '0.0.0.0')
                
                if hasattr(main_window, 'bot_core') and main_window.bot_core:
                    webhook_thread = threading.Thread(target=run_webhook_server,
                                                     kwargs={'host': webhook_host, 'port': webhook_port, 'bot_core': main_window.bot_core},
                                                     daemon=True)
                    webhook_thread.start()
                    logger.info(f"Webhook listener thread'i başlatıldı ({webhook_host}:{webhook_port}).")
                else:
                    logger.error("BotCore nesnesi bulunamadı! Webhook başlatılamıyor.")
                    QMessageBox.warning(main_window, "Webhook Hatası", "Webhook listener başlatılamadı.")
            except Exception as e_wh:
                logger.error(f"Webhook başlatma sırasında hata: {e_wh}", exc_info=True)
                QMessageBox.critical(main_window, "Webhook Hatası", f"Webhook listener başlatılamadı:\n{e_wh}")

            main_window.show()
            sys.exit(app.exec_())

        except Exception as e_main:
            logger.critical(f"Ana uygulama penceresi başlatılırken kritik hata: {e_main}", exc_info=True)
            QMessageBox.critical(None, "Kritik Hata", f"Uygulama başlatılamadı:\n{e_main}")
            sys.exit(1)
            
    else:
        logger.info("Giriş iptal edildi veya başarısız oldu. Uygulama kapatılıyor.")
        sys.exit(0)


# --- Başlangıç Noktası ---
if __name__ == '__main__':
    main()