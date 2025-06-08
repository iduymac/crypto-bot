# core/database_manager.py

import sqlite3
import os
import logging
from datetime import datetime
import sys # Hata loglama için
from typing import Optional, List, Dict, Any # <--- BU SATIRI EKLEYİN

# --- Düzeltme: Logger'ı doğrudan core modülünden al --
try:
    from core.logger import setup_logger
    logger = setup_logger('database_manager')
except ImportError:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('database_manager_fallback')
    logger.warning("core.logger bulunamadı, fallback logger kullanılıyor.")
# --- /Düzeltme ---

class DatabaseManager:
    def __init__(self, db_path="data/trades.db"):
        """
        SQLite veritabanı bağlantısını yöneten sınıf.
        Veritabanı dosyasını ve 'closed_trades' tablosunu oluşturur.
        """
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None # Tip ipucu eklendi
        self._connect() # Bağlantıyı kurmayı dene

        if self.conn:
            self._create_trades_table() # Sadece bağlantı başarılıysa tabloyu oluştur/kontrol et
            logger.info(f"DatabaseManager başlatıldı ve veritabanına bağlandı: {db_path}")
        else:
            # _connect içinde zaten kritik hata loglandı.
            logger.error(f"DatabaseManager başlatılamadı: Veritabanı bağlantısı kurulamadı ({db_path}).")
            # Uygulamanın veritabanı olmadan devam edip etmeyeceğine bağlı olarak
            # burada bir istisna fırlatmak (raise ConnectionError) düşünülebilir.

    def _connect(self):
        """ Veritabanına bağlanır. `check_same_thread=False` parametresi eklendi. """
        try:
            # 'data' klasörünün (veya db_path içindeki herhangi bir dizinin) mevcut olduğundan emin ol
            data_dir = os.path.dirname(self.db_path)
            if data_dir and not os.path.exists(data_dir): # Sadece dizin varsa ve mevcut değilse oluştur
                 os.makedirs(data_dir, exist_ok=True)
                 logger.info(f"Veritabanı için klasör oluşturuldu: {data_dir}")

            # Bağlantıyı kur ve ayarları yap
            # timeout: Bağlantı kurulurken veya bir işlem beklenirken zaman aşımı (saniye cinsinden)
            # check_same_thread=False: Farklı thread'lerden aynı bağlantının kullanılmasına izin verir.
            #                          Bu, "SQLite objects created in a thread..." hatasını çözer.
            #                          Ancak, eğer aynı anda birden fazla thread veritabanına yazmaya
            #                          çalışırsa dikkatli olunmalıdır. Mevcut yapınızda (BotCore ana döngüsü)
            #                          bu genellikle güvenlidir.
            self.conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row # Sorgu sonuçlarına sütun isimleriyle erişmek için
            self.conn.execute("PRAGMA journal_mode=WAL;") # Performans ve eşzamanlılık için WAL modu
            logger.info(f"Veritabanına bağlanıldı: {self.db_path} (check_same_thread=False ayarlandı)")
        except sqlite3.Error as e:
            logger.critical(f"Veritabanı bağlantı/yapılandırma hatası ({self.db_path}): {e}", exc_info=True)
            self.conn = None # Bağlantı başarısız olursa None olarak ayarla
        except OSError as e: # Klasör oluşturma hatası
            logger.critical(f"Veritabanı klasörü ({data_dir}) oluşturma hatası: {e}", exc_info=True)
            self.conn = None
        except Exception as e_general_connect: # Diğer beklenmedik hatalar
            logger.critical(f"Veritabanına bağlanırken beklenmedik genel hata ({self.db_path}): {e_general_connect}", exc_info=True)
            self.conn = None


    def _create_trades_table(self):
        """ Kapalı işlemler için tabloyu oluşturur (varsa atlar). """
        if not self.conn: # Bağlantı yoksa işlem yapma
            logger.error("Veritabanı bağlantısı yok, 'closed_trades' tablosu oluşturulamıyor.")
            return

        # Tablo şeması (TradeManager'dan gelen trade_data_for_db sözlüğüne uygun olmalı)
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS closed_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT NOT NULL,                 -- İşlemi yapan kullanıcı
            symbol TEXT NOT NULL,               -- İşlem çifti (örn: BTC/USDT)
            side TEXT NOT NULL,                 -- Pozisyon yönü (buy/sell)
            entry_price REAL NOT NULL,          -- Giriş fiyatı
            exit_price REAL NOT NULL,           -- Çıkış fiyatı
            amount REAL NOT NULL,               -- İşlem miktarı (base currency)
            gross_pnl REAL,                     -- Brüt Kar/Zarar (komisyon öncesi)
            fee REAL,                           -- Ödenen toplam komisyon (quote currency)
            net_pnl REAL,                       -- Net Kar/Zarar (komisyon sonrası)
            open_timestamp INTEGER NOT NULL,    -- Pozisyon açılış zaman damgası (milisaniye)
            close_timestamp INTEGER NOT NULL,   -- Pozisyon kapanış zaman damgası (milisaniye)
            order_id TEXT UNIQUE NOT NULL,      -- Pozisyonu açan emrin ID'si (benzersiz olmalı)
            close_order_id TEXT,                -- Pozisyonu kapatan emrin ID'si (varsa)
            close_reason TEXT,                  -- Kapanış sebebi (SL, TP, Manuel, Sinyal vb.)
            leverage INTEGER,                   -- Kullanılan kaldıraç (varsa)
            exchange TEXT                       -- İşlemin yapıldığı borsa adı (örn: DemoMode(binanceusdm))
        );
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(create_table_sql)
            # Sık sorgulanan sütunlara indeks eklemek performansı artırabilir
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_closed_trades_user_time ON closed_trades (user, close_timestamp DESC);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_closed_trades_symbol_time ON closed_trades (symbol, close_timestamp DESC);")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_closed_trades_order_id ON closed_trades (order_id);") # order_id zaten UNIQUE

            self.conn.commit()
            logger.info("'closed_trades' tablosu ve indeksleri başarıyla kontrol edildi/oluşturuldu.")
        except sqlite3.Error as e:
            logger.critical(f"'closed_trades' tablosu/indeksleri oluşturulurken SQLite hatası: {e}", exc_info=True)
            try:
                self.conn.rollback() # Hata durumunda geri almayı dene
            except sqlite3.Error as rb_err:
                 logger.error(f"Tablo oluşturma hatası sonrası rollback sırasında ek hata: {rb_err}")


    def save_closed_trade(self, user: str, trade_data: dict) -> bool:
        """ Kapanan bir işlemi veritabanına kaydeder. """
        if not self.conn:
            logger.error(f"Veritabanı bağlantısı yok, işlem (Kullanıcı: {user}, ID: {trade_data.get('order_id', 'N/A')}) kaydedilemiyor.")
            return False

        if not isinstance(trade_data, dict):
             logger.error(f"Geçersiz trade_data formatı (sözlük bekleniyordu, alınan: {type(trade_data)}). İşlem kaydedilemedi.")
             return False
        if not user or not isinstance(user, str):
             logger.error(f"Geçersiz 'user' argümanı ({user}). İşlem kaydedilemedi.")
             return False

        # SQL sorgusundaki sütun sırasıyla eşleşen parametreler
        # Bu sıra _create_trades_table içindeki sütun sırasıyla AYNI OLMALI (id hariç)
        # TradeManager'dan gelen verinin float/int olduğunu varsayıyoruz (orada dönüşüm yapılıyor)
        params = (
            user,
            trade_data.get('symbol'),
            trade_data.get('side'),
            trade_data.get('entry_price'),
            trade_data.get('exit_price'),
            trade_data.get('amount'),
            trade_data.get('gross_pnl'),
            trade_data.get('fee'), # TradeManager'da 'commission' olabilir, burada 'fee' olarak eşleşmeli
            trade_data.get('net_pnl'),
            trade_data.get('open_timestamp'),
            trade_data.get('close_timestamp'),
            trade_data.get('order_id'),       # Açılış emri ID'si
            trade_data.get('close_order_id'), # Kapanış emri ID'si
            trade_data.get('close_reason'),
            trade_data.get('leverage'),
            trade_data.get('exchange')
        )

        # Zorunlu alanların None olup olmadığını kontrol et (kullanıcı, sembol, taraf, fiyatlar, miktar, zamanlar, order_id)
        # Bu kontrolü daha kapsamlı hale getirebilirsiniz.
        # Örnek: if params[1] is None or params[2] is None ...
        # Şimdilik temel bir kontrol yapalım (order_id gibi kritik bir alan üzerinden)
        if params[11] is None: # order_id (params listesindeki 12. eleman, index 11)
            logger.error(f"Eksik zorunlu işlem verisi: 'order_id' None. İşlem kaydedilemedi. Kullanıcı: {user}, Veri: {trade_data}")
            return False

        insert_sql = """
        INSERT INTO closed_trades (
            user, symbol, side, entry_price, exit_price, amount,
            gross_pnl, fee, net_pnl, open_timestamp, close_timestamp,
            order_id, close_order_id, close_reason, leverage, exchange
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        try:
            cursor = self.conn.cursor()
            cursor.execute(insert_sql, params)
            self.conn.commit()
            # Loglama için PNL'i formatla (None kontrolüyle)
            net_pnl_val = trade_data.get('net_pnl')
            pnl_log_str = f"{float(net_pnl_val):.4f}" if net_pnl_val is not None else "N/A"
            logger.info(f"Kapalı işlem veritabanına kaydedildi: User='{user}', ID='{trade_data.get('order_id')}', Sembol='{trade_data.get('symbol')}', Net PNL='{pnl_log_str}'")
            return True
        except sqlite3.IntegrityError as ie: # Genellikle UNIQUE kısıtlaması (order_id) nedeniyle
             logger.warning(f"İşlem zaten veritabanında mevcut (SQLite IntegrityError): User='{user}', ID='{trade_data.get('order_id')}', Hata: {ie}")
             # Zaten varsa başarılı kabul edilebilir veya False döndürülebilir.
             # Bot mantığına göre bu durumun nasıl ele alınacağına karar verilmeli.
             # Şimdilik False döndürelim, çünkü yeni bir kayıt eklenmedi.
             return False
        except sqlite3.Error as e: # Diğer SQLite hataları
            logger.error(f"Kapalı işlem (User='{user}', ID='{trade_data.get('order_id')}') kaydedilirken SQLite hatası: {e}", exc_info=True)
            try:
                self.conn.rollback() # Hata durumunda geri almayı dene
            except sqlite3.Error as rb_err:
                 logger.error(f"Kayıt hatası sonrası rollback sırasında ek SQLite hatası: {rb_err}")
            return False
        except Exception as e_general_save: # Beklenmedik diğer genel hatalar
             logger.critical(f"Kapalı işlem kaydedilirken beklenmedik genel hata (User='{user}', ID='{trade_data.get('order_id')}'): {e_general_save}", exc_info=True)
             return False


    def get_historical_trades(self, user: Optional[str] = None, limit: Optional[int] = 1000,
                              offset: Optional[int] = 0, start_ms: Optional[int] = None,
                              end_ms: Optional[int] = None) -> list:
        """
        Geçmiş işlemleri veritabanından çeker.
        """
        if not self.conn:
            logger.error("Veritabanı bağlantısı yok, geçmiş işlemler çekilemiyor.")
            return []

        select_sql = "SELECT * FROM closed_trades"
        where_clauses = []
        params = []

        if user and isinstance(user, str) and user.strip():
            where_clauses.append("user = ?")
            params.append(user.strip())
        
        if start_ms is not None:
            try:
                where_clauses.append("close_timestamp >= ?")
                params.append(int(start_ms))
            except (ValueError, TypeError):
                logger.warning(f"Geçersiz başlangıç zaman damgası ('{start_ms}') geçmiş işlem filtresi için. Yok sayılıyor.")
        if end_ms is not None:
            try:
                where_clauses.append("close_timestamp <= ?")
                params.append(int(end_ms))
            except (ValueError, TypeError):
                logger.warning(f"Geçersiz bitiş zaman damgası ('{end_ms}') geçmiş işlem filtresi için. Yok sayılıyor.")

        if where_clauses:
            select_sql += " WHERE " + " AND ".join(where_clauses)

        select_sql += " ORDER BY close_timestamp DESC" # En son kapananlar en üstte

        # LIMIT ve OFFSET mantığını düzeltelim:
        # OFFSET sadece bir LIMIT varsa ve pozitifse anlamlıdır.
        # Raporlama gibi tüm verileri çekmek istediğimizde limit=None olur, bu durumda offset kullanılmamalıdır.
        
        limit_value_for_query = None
        if limit is not None and isinstance(limit, int):
            if limit > 0:
                limit_value_for_query = limit
            elif limit == -1 or limit == 0: # SQLite'ta -1 tüm satırları döndürür, 0 ise hiçbirini.
                                            # Genellikle tüm satırlar için limit=None kullanırız.
                logger.debug(f"Geçmiş işlem sorgusu için limit {limit} olarak belirtildi, bu tüm kayıtları getirebilir veya hiçbirini getirmeyebilir.")
                # Eğer tüm kayıtlar isteniyorsa (limit=None veya limit <= 0), offset anlamsızlaşır.
                # Bu durumu yönetmek için, limit None değilse ve pozitifse limit_value_for_query'yi ayarla.
                pass # Limit 0 veya -1 ise, sorguya LIMIT eklemeyelim ya da özel handle edelim.
                     # Şimdilik, sadece pozitif limitler için LIMIT ekleyeceğiz.

        if limit_value_for_query is not None: # Yani limit > 0
            select_sql += " LIMIT ?"
            params.append(limit_value_for_query)
            # Sadece geçerli bir LIMIT varsa OFFSET'i ekle
            if offset is not None and isinstance(offset, int) and offset >= 0:
                select_sql += " OFFSET ?"
                params.append(offset)
        elif offset is not None and offset > 0:
            # LIMIT yok ama OFFSET var. Bu genellikle SQLite'ta hataya yol açar.
            # Bu durumu önlemek için, eğer raporlama gibi bir senaryoda tüm veriler isteniyorsa
            # (ve bu yüzden limit None veya <=0 geliyorsa), offset'i de yoksaymak en güvenlisidir.
            logger.warning(f"OFFSET ({offset}) belirtildi ancak geçerli bir LIMIT yok. OFFSET yok sayılacak.")
            # Alternatif olarak, çok büyük bir LIMIT eklenebilir (örn: LIMIT 999999999 OFFSET ?), ama bu da ideal değil.

        logger.debug(f"Geçmiş işlem sorgusu: SQL='{select_sql}', PARAMS={tuple(params)}")
        try:
            cursor = self.conn.cursor()
            cursor.execute(select_sql, tuple(params))
            trades = [dict(row) for row in cursor.fetchall()]
            
            user_log = f"Kullanıcı='{user}'" if user else "Tüm Kullanıcılar"
            date_filter_log_parts = []
            if start_ms is not None: date_filter_log_parts.append(f"StartEpoch={start_ms}")
            if end_ms is not None: date_filter_log_parts.append(f"EndEpoch={end_ms}")
            filter_log_str = ", ".join(date_filter_log_parts) if date_filter_log_parts else "Filtresiz"
            limit_log = f"Limit={limit if limit is not None else 'Yok'}"
            offset_log = f"Offset={offset if (limit_value_for_query and offset is not None and offset >=0) else 'Yok'}"

            logger.info(f"Veritabanından {len(trades)} geçmiş işlem çekildi ({user_log}, {filter_log_str}, {limit_log}, {offset_log}).")
            return trades
        except sqlite3.Error as e:
            logger.error(f"Geçmiş işlemler çekilirken SQLite hatası (SQL: {select_sql}, Params: {params}): {e}", exc_info=True)
            return []
        except Exception as e_general_fetch:
            logger.critical(f"Geçmiş işlemler çekilirken beklenmedik genel hata: {e_general_fetch}", exc_info=True)
            return []


    def get_total_pnl(self, user: Optional[str] = None, start_ms: Optional[int] = None, end_ms: Optional[int] = None) -> float:
        """
        Belirli bir kullanıcının (veya tüm kullanıcıların) belirtilen tarih aralığındaki
        toplam net kar/zararını hesaplar.
        """
        if not self.conn:
            logger.error("Veritabanı bağlantısı yok, toplam PNL hesaplanamıyor.")
            return 0.0

        select_sql = "SELECT SUM(net_pnl) FROM closed_trades"
        where_clauses = []
        params = []

        if user and isinstance(user, str) and user.strip():
            where_clauses.append("user = ?")
            params.append(user.strip())
        if start_ms is not None:
             try: where_clauses.append("close_timestamp >= ?"); params.append(int(start_ms))
             except: logger.warning(f"Geçersiz başlangıç zaman damgası ('{start_ms}') toplam PNL filtresi için.")
        if end_ms is not None:
             try: where_clauses.append("close_timestamp <= ?"); params.append(int(end_ms))
             except: logger.warning(f"Geçersiz bitiş zaman damgası ('{end_ms}') toplam PNL filtresi için.")

        if where_clauses:
            select_sql += " WHERE " + " AND ".join(where_clauses)

        try:
            cursor = self.conn.cursor()
            cursor.execute(select_sql, tuple(params))
            result = cursor.fetchone() # Bir tuple döndürür, (SUM(net_pnl),) gibi
            total_pnl_value = result[0] if result and result[0] is not None else 0.0 # İlk elemanı al, None ise 0.0 yap
            
            user_log = f"Kullanıcı='{user}'" if user else "Tüm Kullanıcılar"
            logger.debug(f"Toplam PNL sorgulandı ({user_log}, Filtreler: {params}): {total_pnl_value}")
            return float(total_pnl_value)
        except sqlite3.Error as e:
            logger.error(f"Toplam PNL hesaplanırken SQLite hatası: {e}", exc_info=True)
            return 0.0
        except Exception as e_general_pnl:
             logger.critical(f"Toplam PNL hesaplanırken beklenmedik genel hata: {e_general_pnl}", exc_info=True)
             return 0.0


    def close(self):
        """ Veritabanı bağlantısını güvenli bir şekilde kapatır. """
        if self.conn:
            try:
                self.conn.close()
                logger.info(f"Veritabanı bağlantısı ({self.db_path}) kapatıldı.")
                self.conn = None # Bağlantı kapatıldıktan sonra referansı temizle
            except sqlite3.Error as e:
                 logger.error(f"Veritabanı bağlantısı ({self.db_path}) kapatılırken SQLite hatası: {e}", exc_info=True)
                 self.conn = None # Hata durumunda da referansı temizle
            except Exception as e_general_close:
                 logger.critical(f"Veritabanı bağlantısı kapatılırken beklenmedik genel hata ({self.db_path}): {e_general_close}", exc_info=True)
                 self.conn = None


    # Uygulama kapatılırken bağlantının kapanmasını sağlamak için __del__ kullanılabilir,
    # ancak BotCore._cleanup içindeki explicit close() çağrısı daha güvenilirdir.
    def __del__(self):
        logger.debug(f"DatabaseManager ({self.db_path}) __del__ çağrıldı.")
        self.close()

# Kullanım örneği (test için)
if __name__ == '__main__':
    print("DatabaseManager Test Başlatılıyor...")
    # Test için basit logger
    if 'setup_logger' not in globals():
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        logger = logging.getLogger('database_manager_test')

    # Test için geçici bir veritabanı dosyası kullan
    test_db_path = "data/test_trades_main.db" # Farklı isim kullanalım
    if os.path.exists(test_db_path):
        try: os.remove(test_db_path)
        except OSError as e: print(f"Uyarı: Eski test DB silinemedi: {e}")

    db_manager = DatabaseManager(db_path=test_db_path)

    # Eğer bağlantı kurulamadıysa test anlamsız olur
    if not db_manager.conn:
         print("KRİTİK HATA: Veritabanı bağlantısı kurulamadı, test durduruldu.")
         sys.exit(1)

    # Örnek işlem verileri
    now_ms = int(datetime.now().timestamp() * 1000)
    trade1 = {
        'symbol': 'BTC/USDT', 'side': 'buy', 'entry_price': 40000.0, 'exit_price': 41000.0, 'amount': 0.001,
        'gross_pnl': 1.0, 'fee': 0.1, 'net_pnl': 0.9,
        'open_timestamp': now_ms - 3600000, 'close_timestamp': now_ms - 60000,
        'order_id': 'test_ord_1', 'close_reason': 'TP', 'exchange': 'demo', 'leverage': 5
    }
    trade2 = {
        'symbol': 'ETH/USDT', 'side': 'sell', 'entry_price': 3000.0, 'exit_price': 3050.0, 'amount': '0.05', # String miktar
        'gross_pnl': -2.5, 'fee': 0.15, 'net_pnl': -2.65,
        'open_timestamp': now_ms - 1800000, 'close_timestamp': now_ms - 120000,
        'order_id': 'test_ord_2', 'close_reason': 'SL', 'exchange': 'real', 'leverage': 10, 'close_order_id': 'close_123'
    }
    trade3 = { # Farklı kullanıcı
        'symbol': 'BTC/USDT', 'side': 'sell', 'entry_price': 42000.0, 'exit_price': 41500.0, 'amount': 0.002,
        'gross_pnl': 1.0, 'fee': 0.12, 'net_pnl': 0.88,
        'open_timestamp': now_ms - 7200000, 'close_timestamp': now_ms, # Şu an kapandı
        'order_id': 'test_ord_3', 'close_reason': 'Manuel', 'exchange': 'demo', 'leverage': 5
    }
    trade4_missing = { # Eksik veri
        'symbol': 'ADA/USDT', 'side': 'buy', 'entry_price': 1.5, 'exit_price': 1.6, #'amount': 100, Eksik
        'gross_pnl': 10.0, 'fee': 0.1, 'net_pnl': 9.9,
        'open_timestamp': now_ms - 300000, 'close_timestamp': now_ms - 10000,
        'order_id': 'test_ord_4', 'close_reason': 'TP', 'exchange': 'demo', 'leverage': 1
    }
    trade5_invalid_type = { # Geçersiz tip
        'symbol': 'SOL/USDT', 'side': 'buy', 'entry_price': 150, 'exit_price': 160, 'amount': 10,
        'gross_pnl': 100, 'fee': 'yok', 'net_pnl': 99, # fee geçersiz
        'open_timestamp': now_ms - 200000, 'close_timestamp': now_ms - 5000,
        'order_id': 'test_ord_5', 'close_reason': 'TP', 'exchange': 'real', 'leverage': 20
    }


    # İşlemleri kaydet
    print("\n--- İşlem Kaydetme ---")
    print(f"Trade 1 Kaydetme Başarılı: {db_manager.save_closed_trade('user_A', trade1)}")
    print(f"Trade 2 Kaydetme Başarılı: {db_manager.save_closed_trade('user_A', trade2)}")
    print(f"Trade 3 Kaydetme Başarılı: {db_manager.save_closed_trade('user_B', trade3)}")
    print(f"Trade 1 Tekrar Kaydetme Başarılı: {db_manager.save_closed_trade('user_A', trade1)}") # Zaten var, False dönmeli
    print(f"Trade 4 (Eksik) Kaydetme Başarılı: {db_manager.save_closed_trade('user_A', trade4_missing)}") # False dönmeli
    print(f"Trade 5 (Tip Hatası) Kaydetme Başarılı: {db_manager.save_closed_trade('user_A', trade5_invalid_type)}") # False dönmeli


    # Geçmiş işlemleri çek
    print("\n--- Geçmiş İşlem Çekme ---")
    print("Tüm İşlemler (Son 10):")
    all_trades = db_manager.get_historical_trades(limit=10)
    for trade in all_trades: print(f"  {dict(trade)}")
    assert len(all_trades) == 3 # Sadece 3 işlem başarıyla kaydedilmiş olmalı

    print("\nUser_A İşlemleri:")
    user_a_trades = db_manager.get_historical_trades(user="user_A")
    for trade in user_a_trades: print(f"  {dict(trade)}")
    assert len(user_a_trades) == 2

    print("\nUser_B İşlemleri:")
    user_b_trades = db_manager.get_historical_trades(user="user_B")
    for trade in user_b_trades: print(f"  {dict(trade)}")
    assert len(user_b_trades) == 1

    print("\nOlmayan Kullanıcı İşlemleri:")
    non_existent_trades = db_manager.get_historical_trades(user="user_C")
    print(f"  Bulunan: {len(non_existent_trades)}")
    assert len(non_existent_trades) == 0

    # Tarih Aralığı Testi
    print("\nTarih Aralığı Testi (Son 5 dakika):")
    start_filter_ms = now_ms - 5 * 60 * 1000
    end_filter_ms = now_ms + 1000 # Şu ana kadar
    recent_trades = db_manager.get_historical_trades(start_ms=start_filter_ms, end_ms=end_filter_ms)
    print(f" Bulunan ({len(recent_trades)} adet):")
    for trade in recent_trades: print(f"  {dict(trade)}")
    # Sadece trade3 bu aralıkta olmalı (user B)
    assert len(recent_trades) == 1
    assert recent_trades[0]['order_id'] == 'test_ord_3'

    print("\nTarih Aralığı Testi (Çok eski):")
    old_start_ms = now_ms - 10 * 24 * 3600 * 1000 # 10 gün önce başla
    old_end_ms = now_ms - 5 * 24 * 3600 * 1000 # 5 gün önce bitir
    old_trades = db_manager.get_historical_trades(start_ms=old_start_ms, end_ms=old_end_ms)
    print(f" Bulunan ({len(old_trades)} adet):")
    assert len(old_trades) == 0

    # Toplam PNL hesapla
    print("\n--- Toplam PNL Hesaplama ---")
    pnl_all = db_manager.get_total_pnl()
    pnl_a = db_manager.get_total_pnl(user="user_A")
    pnl_b = db_manager.get_total_pnl(user="user_B")
    pnl_c = db_manager.get_total_pnl(user="user_C")
    print(f"Toplam PNL (Tümü): {pnl_all:.4f}") # Beklenen: 0.9 + (-2.65) + 0.88 = -0.87
    print(f"Toplam PNL (User_A): {pnl_a:.4f}") # Beklenen: 0.9 + (-2.65) = -1.75
    print(f"Toplam PNL (User_B): {pnl_b:.4f}") # Beklenen: 0.88
    print(f"Toplam PNL (User_C): {pnl_c:.4f}") # Beklenen: 0.0
    assert abs(pnl_all - (-0.87)) < 0.0001
    assert abs(pnl_a - (-1.75)) < 0.0001
    assert abs(pnl_b - 0.88) < 0.0001
    assert abs(pnl_c - 0.0) < 0.0001


    # Bağlantıyı kapat
    db_manager.close()
    print("\nVeritabanı bağlantısı kapatıldı.")

    # Kapalı bağlantıda işlem yapmayı dene (hata vermeli)
    print(f"Kapalı DB'ye Kaydetme Başarılı: {db_manager.save_closed_trade('user_A', trade1)}") # False dönmeli
    assert not db_manager.save_closed_trade('user_A', trade1)

    print("\nDatabaseManager Test Tamamlandı.")