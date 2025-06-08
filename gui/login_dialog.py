# gui/login_dialog.py

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QLabel, QLineEdit, 
                             QPushButton, QMessageBox, QFormLayout)
from PyQt5.QtCore import Qt

# <<< YENİ >>> Gerekli yöneticileri import ediyoruz
from config.user_config_manager import UserConfigManager
from config.language_manager import LanguageManager

class LoginDialog(QDialog):
    # <<< DEĞİŞİKLİK >>> __init__ metodu artık lang_manager'ı parametre olarak alıyor
    def __init__(self, lang_manager: LanguageManager, parent=None):
        super().__init__(parent)
        
        if not lang_manager:
            # Bu kritik bir hata, lang_manager olmadan arayüz kurulamaz.
            # Basit bir fallback mesajı gösterip kapatabiliriz.
            QMessageBox.critical(self, "Critical Error", "Language Manager not loaded.")
            # QDialog'u hemen kapatmak için reject çağrılabilir
            # Ancak bu __init__ içinde riskli olabilir, QTimer ile yapalım.
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(0, self.reject)
            return

        self.user_manager = UserConfigManager()
        self.lang_manager = lang_manager # Dil yöneticisini sakla
        self.logged_in_user = None

        # <<< DEĞİŞİKLİK >>> Tüm sabit metinler artık lang_manager'dan geliyor
        self.setWindowTitle(self.lang_manager.get_string("login_title"))
        self.setMinimumWidth(300)
        self.setModal(True)

        # Arayüz Elemanları
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self.username_input = QLineEdit(self)
        self.password_input = QLineEdit(self)
        self.password_input.setEchoMode(QLineEdit.Password)

        # <<< DEĞİŞİKLİK >>> QLabel metinleri
        self.username_label = QLabel(self.lang_manager.get_string("username_label"))
        self.password_label = QLabel(self.lang_manager.get_string("password_label"))
        
        form_layout.addRow(self.username_label, self.username_input)
        form_layout.addRow(self.password_label, self.password_input)

        # <<< DEĞİŞİKLİK >>> QPushButton metni
        self.login_button = QPushButton(self.lang_manager.get_string("login_button"), self)
        
        layout.addLayout(form_layout)
        layout.addWidget(self.login_button)
        
        # Sinyal-Slot Bağlantıları
        self.login_button.clicked.connect(self.attempt_login)
        self.password_input.returnPressed.connect(self.attempt_login)

    def attempt_login(self):
        """Giriş denemesi yapar."""
        username = self.username_input.text()
        password = self.password_input.text()

        # <<< DEĞİŞİKLİK >>> Hata mesajları da artık lang_manager'dan geliyor
        if not username or not password:
            QMessageBox.warning(self, 
                                self.lang_manager.get_string("login_empty_error_title"), 
                                self.lang_manager.get_string("login_empty_error_message"))
            return

        if self.user_manager.verify_user(username, password):
            self.logged_in_user = username
            self.accept()
        else:
            QMessageBox.critical(self, 
                                 self.lang_manager.get_string("login_error_title"), 
                                 self.lang_manager.get_string("login_error_message"))

    def get_logged_in_user(self):
        """Giriş yapan kullanıcının adını döndürür."""
        return self.logged_in_user