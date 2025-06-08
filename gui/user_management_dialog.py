# gui/user_management_dialog.py

import sys
from typing import Optional, Dict # Dict zaten varsa sadece Optional ekleyin
import copy # Yeni kullanıcı verisi için
from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout,
    QListWidget, QPushButton, QLabel, QMessageBox, QLineEdit,
    QAbstractItemView, QDialogButtonBox, QInputDialog # QInputDialog eklendi
)
from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtGui import QIcon # İkonlar için

import logging

# --- Düzeltme: Logger'ı doğrudan core modülünden al ---
try:
    from core.logger import setup_logger
    logger = setup_logger('user_mgmt_dialog')
except ImportError:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('user_mgmt_dialog_fallback')
    logger.warning("core.logger bulunamadı, fallback logger kullanılıyor.")
# --- /Düzeltme ---

# SettingsDialog'u düzenleme için import et (Hata yakalama ile)
try:
    # <<< İyileştirme: DEFAULT_SETTINGS'i de import et >>>
    from gui.settings_dialog import SettingsDialog, DEFAULT_SETTINGS
    SETTINGS_DIALOG_AVAILABLE = True
except ImportError:
    logger.error("SettingsDialog veya DEFAULT_SETTINGS import edilemedi! Kullanıcı düzenleme/ekleme düzgün çalışmayabilir.")
    # Hata durumunda None ata ki kontroller çalışsın
    SettingsDialog = None
    DEFAULT_SETTINGS = {} # Boş varsayılanlar
    SETTINGS_DIALOG_AVAILABLE = False

# UserConfigManager type hinting için
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from config.user_config_manager import UserConfigManager


class UserManagementDialog(QDialog):
    def __init__(self, user_manager: 'UserConfigManager', parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kullanıcı Yönetimi")
        if not user_manager:
             logger.critical("UserManagementDialog: UserConfigManager örneği sağlanmadı!")
             # Hata mesajı gösterip kapatabiliriz
             QMessageBox.critical(parent, "Kritik Hata", "Kullanıcı Yöneticisi yüklenemedi.")
             # QDialog'u hemen kapatmak için reject çağrılabilir
             QTimer.singleShot(0, self.reject) # Zamanlayıcı ile güvenli kapatma
             return

        self.user_manager = user_manager # UserConfigManager örneğini sakla
        self.setGeometry(200, 200, 480, 380) # Boyutu biraz ayarla

        self.layout = QVBoxLayout(self)

        # --- Kullanıcı Listesi ---
        self.layout.addWidget(QLabel("Kayıtlı Kullanıcılar:"))
        self.user_list = QListWidget()
        self.user_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.user_list.itemSelectionChanged.connect(self._update_button_states)
        # Çift tıklama ile düzenlemeyi açma (Kullanıcı dostu)
        self.user_list.itemDoubleClicked.connect(self._edit_user)
        self.layout.addWidget(self.user_list)

        # --- Butonlar ---
        button_layout = QHBoxLayout()
        self.add_button = QPushButton(QIcon.fromTheme("list-add"), " Yeni Ekle...") # İkon eklendi
        self.add_button.setToolTip("Yeni bir kullanıcı profili ekler.")
        self.edit_button = QPushButton(QIcon.fromTheme("document-edit"), " Ayarları Düzenle...") # İkon eklendi
        self.edit_button.setToolTip("Seçili kullanıcının ayarlarını düzenler (Çift tıklama ile de açılır).")
        self.delete_button = QPushButton(QIcon.fromTheme("list-remove"), " Sil") # İkon eklendi
        self.delete_button.setToolTip("Seçili kullanıcı profilini siler.")
        # Başlangıçta düzenle ve sil butonları pasif
        self.edit_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        # SettingsDialog yoksa Düzenle butonu her zaman pasif olmalı
        if not SETTINGS_DIALOG_AVAILABLE:
             self.edit_button.setEnabled(False)
             self.edit_button.setToolTip("Ayar düzenleme modülü yüklenemedi.")

        button_layout.addWidget(self.add_button)
        button_layout.addWidget(self.edit_button)
        button_layout.addWidget(self.delete_button)
        button_layout.addStretch() # Butonları sola yasla
        self.layout.addLayout(button_layout)

        # --- Kapat Butonu ---
        self.buttonBox = QDialogButtonBox(QDialogButtonBox.Close)
        self.buttonBox.rejected.connect(self.reject) # Close butonu reject sinyali yayar
        self.layout.addWidget(self.buttonBox)

        # --- Sinyal Bağlantıları ---
        self.add_button.clicked.connect(self._add_user)
        self.edit_button.clicked.connect(self._edit_user)
        self.delete_button.clicked.connect(self._delete_user)

        # Başlangıçta kullanıcı listesini yükle
        self._load_user_list()

        logger.info("UserManagementDialog başlatıldı.")

    def _load_user_list(self):
        """ user_manager'dan kullanıcıları alır ve listeyi günceller. """
        self.user_list.clear() # Listeyi temizle
        try:
            users = self.user_manager.get_all_users()
            if users:
                self.user_list.addItems(sorted(users)) # Alfabetik sıralı ekle
                logger.debug(f"Kullanıcı listesi yüklendi: {sorted(users)}")
            else:
                logger.info("Yönetilecek kullanıcı bulunamadı.")
        except Exception as e:
            logger.error(f"Kullanıcı listesi yüklenirken hata: {e}", exc_info=True)
            QMessageBox.warning(self, "Hata", f"Kullanıcı listesi yüklenemedi:\n{e}")
        finally:
            # Liste güncellenince buton durumunu ayarla (hata olsa bile)
            self._update_button_states()

    @pyqtSlot() # itemSelectionChanged sinyaline bağlı slot
    def _update_button_states(self):
        """ Listeden seçime göre düzenle ve sil butonlarını etkinleştirir/pasifleştirir. """
        selected_items = self.user_list.selectedItems()
        is_selected = bool(selected_items) # Seçili öğe var mı?

        # Sil butonu sadece seçim varsa aktif
        self.delete_button.setEnabled(is_selected)
        # Düzenle butonu seçim varsa VE SettingsDialog mevcutsa aktif
        self.edit_button.setEnabled(is_selected and SETTINGS_DIALOG_AVAILABLE)

    def _get_selected_username(self) -> Optional[str]:
        """ Listeden seçili kullanıcının adını döndürür, seçili değilse None. """
        selected_items = self.user_list.selectedItems()
        if selected_items:
            return selected_items[0].text()
        return None

    def _add_user(self):
        """ Yeni kullanıcı ekleme işlemini başlatır ve ekledikten sonra ayar dialoğunu açar. """
        username, ok = QInputDialog.getText(self, "Yeni Kullanıcı Ekle", "Yeni Kullanıcı Adı:", QLineEdit.Normal, "")

        if ok and username:
            username = username.strip() # Başındaki/sonundaki boşlukları sil
            if not username:
                QMessageBox.warning(self, "Geçersiz Ad", "Kullanıcı adı boş olamaz.")
                return

            try:
                # <<< İyileştirme: Yeni kullanıcıyı varsayılan ayarlarla oluştur >>>
                # UserConfigManager'a sadece username vermek yerine,
                # varsayılan ayarlarla birleştirilmiş tam bir veri gönderelim.
                new_user_data = copy.deepcopy(DEFAULT_SETTINGS) # Varsayılanları al
                new_user_data['username'] = username # Username'i ata

                # User Manager'a eklemeyi dene
                add_success = self.user_manager.add_user(new_user_data)

                if add_success:
                    logger.info(f"'{username}' kullanıcısı varsayılan ayarlarla eklendi.")
                    self._load_user_list() # Listeyi yenile
                    # Yeni eklenen kullanıcıyı listede seçili hale getir
                    items = self.user_list.findItems(username, Qt.MatchExactly)
                    if items:
                        self.user_list.setCurrentItem(items[0])

                    QMessageBox.information(self, "Kullanıcı Eklendi",
                                            f"'{username}' kullanıcısı başarıyla eklendi.\nŞimdi ayarlarını düzenleyebilirsiniz.")
                    # <<< İyileştirme: Ayarlar dialoğunu otomatik aç >>>
                    self._edit_user() # Yeni kullanıcı için hemen ayarları düzenle
                else:
                    # add_user False döndürdüyse (örn. kaydetme hatası)
                    QMessageBox.critical(self, "Hata", f"Kullanıcı '{username}' eklendi ancak kaydedilemedi.")

            except ValueError as e: # Kullanıcı zaten varsa UserConfigManager hata verir
                 logger.warning(f"Kullanıcı eklenemedi: {e}")
                 QMessageBox.warning(self, "Hata", str(e))
            except Exception as e:
                 logger.error(f"Kullanıcı eklenirken beklenmedik hata: {e}", exc_info=True)
                 QMessageBox.critical(self, "Kritik Hata", f"Kullanıcı eklenirken hata oluştu:\n{e}")
        elif ok: # Kullanıcı adı girilmedi ama Tamam'a basıldı
             QMessageBox.warning(self, "Geçersiz Ad", "Kullanıcı adı girmediniz.")
        # else: Kullanıcı İptal'e bastı, bir şey yapma


    def _edit_user(self):
        """ Seçili kullanıcının ayarlarını düzenlemek için SettingsDialog'u açar. """
        username = self._get_selected_username()
        if not username:
            # Eğer çift tıklama ile çağrıldıysa bu uyarıya gerek yok,
            # ama butonla çağrıldıysa gösterilebilir. Şimdilik gösterelim.
            QMessageBox.warning(self, "Kullanıcı Seçilmedi", "Lütfen ayarlarını düzenlemek istediğiniz kullanıcıyı listeden seçin.")
            return

        if not SETTINGS_DIALOG_AVAILABLE:
             QMessageBox.critical(self, "Modül Hatası", "Ayar düzenleme arayüzü (SettingsDialog) yüklenemedi.")
             return

        logger.info(f"'{username}' kullanıcısının ayarları düzenleniyor...")
        current_settings = self.user_manager.get_user(username)
        if not current_settings: # Kullanıcı bir şekilde silinmişse veya yüklenemediyse
            logger.error(f"'{username}' kullanıcısının ayarları user_manager'dan alınamadı!")
            QMessageBox.critical(self, "Veri Hatası", f"'{username}' kullanıcısının ayarları okunamadı.\nListe yenileniyor.")
            self._load_user_list() # Listeyi yenilemek sorunu gösterebilir
            return

        # SettingsDialog'u mevcut ayarlarla aç
        # Ayarlar zaten __init__ içinde varsayılanlarla birleştiriliyor olmalı
        settings_dialog = SettingsDialog(settings=current_settings, parent=self)
        # Dialog'u modal olarak çalıştır
        if settings_dialog.exec_(): # Kullanıcı "Kaydet"e bastıysa
            try:
                updated_settings = settings_dialog.get_settings() # Tip dönüşümleri yapılmış ayarları al
                # Username'in hala doğru olduğundan emin ol (genelde sorun olmaz)
                if updated_settings.get('username') != username:
                    logger.warning(f"Ayarlardan dönen username ('{updated_settings.get('username')}') beklenen ('{username}') ile farklı!")
                    updated_settings['username'] = username # Doğrusunu ata

                # User Manager ile güncelle
                update_success = self.user_manager.update_user(updated_settings)

                if update_success:
                    logger.info(f"'{username}' kullanıcısının ayarları başarıyla güncellendi ve kaydedildi.")
                    QMessageBox.information(self, "Başarılı", f"'{username}' kullanıcısının ayarları kaydedildi.")
                else:
                     logger.error(f"'{username}' ayarları güncellendi ancak user_manager kaydedemedi.")
                     QMessageBox.critical(self, "Kayıt Hatası", f"Ayarlar güncellendi ancak dosyaya kaydedilirken bir sorun oluştu.")

            except Exception as e:
                 logger.error(f"'{username}' ayarları güncellenirken/kaydedilirken hata: {e}", exc_info=True)
                 QMessageBox.critical(self, "Güncelleme Hatası", f"Ayarlar güncellenirken bir hata oluştu:\n{e}")
        else: # Kullanıcı "İptal"e bastı
            logger.info(f"'{username}' için ayar değişikliği iptal edildi.")


    def _delete_user(self):
        """ Seçili kullanıcıyı siler (onay alarak). """
        username = self._get_selected_username()
        if not username:
            QMessageBox.warning(self, "Kullanıcı Seçilmedi", "Lütfen silmek istediğiniz kullanıcıyı listeden seçin.")
            return

        reply = QMessageBox.question(self, "Kullanıcıyı Sil",
                                     f"'{username}' kullanıcısını ve tüm ayarlarını kalıcı olarak silmek istediğinizden emin misiniz?\n\nBu işlem geri alınamaz!",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

        if reply == QMessageBox.Yes:
            try:
                delete_success = self.user_manager.delete_user(username)
                if delete_success:
                    logger.info(f"'{username}' kullanıcısı başarıyla silindi.")
                    self._load_user_list() # Listeyi yenile
                    QMessageBox.information(self, "Başarılı", f"'{username}' kullanıcısı silindi.")
                else:
                     logger.error(f"'{username}' kullanıcısı silindi ancak user_manager kaydedemedi.")
                     QMessageBox.critical(self, "Kayıt Hatası", f"Kullanıcı silindi ancak değişiklik dosyaya kaydedilemedi.")
                     # Liste yine de yenilenmeli
                     self._load_user_list()

            except ValueError as e: # Kullanıcı bulunamazsa (nadiren olmalı)
                 logger.warning(f"Kullanıcı silinemedi: {e}")
                 QMessageBox.warning(self, "Hata", str(e))
                 self._load_user_list() # Listeyi yine de yenile
            except Exception as e:
                 logger.error(f"Kullanıcı silinirken beklenmedik hata: {e}", exc_info=True)
                 QMessageBox.critical(self, "Kritik Hata", f"Kullanıcı silinirken hata oluştu:\n{e}")


# Test bloğu (önceki haliyle kullanılabilir)
if __name__ == '__main__':
    # Mock User Manager (önceki gibi)
    class MockUserManager:
        # ... (MockUserManager içeriği önceki yanıttaki gibi kalabilir) ...
        _users = {"user1": copy.deepcopy(DEFAULT_SETTINGS), "user2": copy.deepcopy(DEFAULT_SETTINGS)}
        _users["user1"]["username"] = "user1"; _users["user1"]["exchange"]["api_key"] = "key1"
        _users["user2"]["username"] = "user2"; _users["user2"]["trading"]["default_leverage"] = 7

        def get_all_users(self): return list(self._users.keys())
        def get_user(self, name): data = self._users.get(name); return copy.deepcopy(data) if data else None # Deep copy önemli
        def delete_user(self, name):
            if name in self._users: del self._users[name]; print(f"Mock: Deleted {name}"); return True
            else: raise ValueError(f"'{name}' bulunamadı.")
        def update_user(self, data):
             uname = data.get('username');
             if uname in self._users: self._users[uname] = data; print(f"Mock: Updated {uname}"); return True # Tamamen üzerine yazalım
             else: raise ValueError(f"'{uname}' bulunamadı.")
        def add_user(self, data):
             uname = data.get('username');
             if uname in self._users: raise ValueError(f"'{uname}' zaten mevcut.")
             self._users[uname] = data; print(f"Mock: Added {uname}"); return True


    app = QApplication(sys.argv)
    # Test için basit logger
    if 'setup_logger' not in globals():
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        logger = logging.getLogger('user_mgmt_dialog_test')

    if not SETTINGS_DIALOG_AVAILABLE: print("UYARI: SettingsDialog import edilemediği için Düzenle butonu çalışmayacak.")

    dialog = UserManagementDialog(user_manager=MockUserManager())
    dialog.show()
    sys.exit(app.exec_())