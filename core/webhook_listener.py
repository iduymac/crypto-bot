print("!!!! WEBHOOK_LISTENER.PY MODÜLÜ BAŞLANGICI (sys ve queue importlu) !!!!") # TEST
# core/webhook_listener.py

import logging
import threading
import json
import queue # BU SATIR ÇOK ÖNEMLİ
import sys   # <<<<<<<<<<<<<<<<<<< BU SATIR YENİ EKLENDİ (sys.modules için)

try:
    from flask import Flask, request, jsonify
    print("Flask modülleri (Flask, request, jsonify) başarıyla import edildi.")
except ImportError as e_flask:
    print(f"KRİTİK HATA: Flask import edilemedi: {e_flask}")
    Flask = None
    jsonify = None # jsonify de kullanılamaz
    request = None # request de kullanılamaz

try:
    from core.logger import setup_logger
    logger = setup_logger('webhook_listener')
    print("webhook_listener için logger başarıyla ayarlandı.")
except ImportError:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('webhook_listener_fallback')
    logger.warning("core.logger modülü bulunamadı, fallback logger kullanılıyor (webhook_listener).")
except Exception as e_logger_setup:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('webhook_listener_critical_fallback')
    logger.critical(f"webhook_listener için logger AYARLANAMADI: {e_logger_setup}", exc_info=True)

if Flask:
    flask_app = Flask(__name__)
    print("Flask uygulaması (flask_app) oluşturuldu.")
else:
    flask_app = None
    logger.critical("Flask import edilemediği için flask_app oluşturulamadı!")

bot_core_instance = None
webhook_secret_key = None
print(f"webhook_listener global değişkenleri tanımlandı. bot_core_instance: {bot_core_instance}, webhook_secret_key: {webhook_secret_key}")

if flask_app:
    @flask_app.route('/webhook', methods=['POST'])
    def handle_webhook():
        print("---- handle_webhook fonksiyonu BAŞLADI (çoklu JSON destekli) ----")
        global bot_core_instance, webhook_secret_key
        logger.debug(f"handle_webhook çağrıldı. bot_core_instance: {type(bot_core_instance)}, secret_key: {'Ayarlı' if webhook_secret_key else 'Ayarlı Değil'}")

        if not bot_core_instance:
            logger.error("Webhook: BotCore örneği mevcut değil.")
            return jsonify({"status": "error", "message": "Service temporarily unavailable (BotCore missing)"}), 503

        # BotCore örneğinde gerekli kuyruk yapısının olup olmadığını en başta kontrol edelim.
        if not hasattr(bot_core_instance, 'external_signal_queue'):
            logger.error("Webhook: BotCore örneğinde 'external_signal_queue' özelliği bulunamadı. Sinyaller işlenemiyor.")
            return jsonify({"status": "error", "message": "Internal server error: Bot not ready for signals (missing queue attribute)"}), 500
        
        if not isinstance(bot_core_instance.external_signal_queue, queue.Queue):
            logger.error(f"Webhook: BotCore.external_signal_queue beklenen tipte (queue.Queue) değil! Gerçek tip: {type(bot_core_instance.external_signal_queue)}. Sinyaller işlenemiyor.")
            return jsonify({"status": "error", "message": "Internal server error: Bot not ready for signals (invalid queue type)"}), 500

        request_valid = False
        signals = []  # Birden fazla sinyali tutacak liste
        raw_data_for_log = ""
        headers_for_log = {}

        try:
            raw_data_for_log = request.get_data(as_text=True)
            headers_for_log = dict(request.headers)

            logger.info("--- Gelen Webhook İsteği ---")
            logger.info(f"Kaynak IP: {request.remote_addr}")
            logger.info(f"Headers: {headers_for_log}")
            logger.info(f"Raw Body (ilk 500 karakter): {raw_data_for_log[:500]}")

            if not raw_data_for_log.strip():
                logger.error("İstek gövdesi boş veya sadece boşluk içeriyor.")
                return jsonify({"status": "error", "message": "Empty or whitespace-only request body"}), 400

            try:
                # Önce Flask'ın JSON olarak algılayıp algılamadığına bak (tek JSON veya JSON dizisi)
                if request.is_json:
                    data = request.get_json()
                    if isinstance(data, list):
                        signals = data  # Gelen veri zaten bir JSON dizisi [{...}, {...}]
                        logger.info(f"Flask tarafından JSON dizisi olarak algılandı ({len(signals)} sinyal).")
                    else:
                        signals = [data] # Gelen veri tek bir JSON nesnesi {...}
                        logger.info("Flask tarafından tek JSON nesnesi olarak algılandı.")
                elif raw_data_for_log: # Flask JSON olarak algılamadıysa, manuel ayrıştırmayı dene
                    # Her satırda ayrı bir JSON olabilir (\n ile ayrılmış)
                    potential_jsons = raw_data_for_log.strip().splitlines()
                    parsed_count = 0
                    for line in potential_jsons:
                        line = line.strip()
                        if not line:  # Boş satırları atla
                            continue
                        try:
                            signals.append(json.loads(line))
                            parsed_count +=1
                        except json.JSONDecodeError as e_line:
                            # Tek bir satırın hatalı olması diğerlerini engellememeli, sadece uyar
                            logger.warning(f"Bir JSON satırı parse edilemedi (atlandı): {e_line} -- Satır parçası: {line[:100]}")
                    
                    if parsed_count > 0:
                        logger.info(f"Manuel JSON ayrıştırma ile {parsed_count} sinyal bulundu ({len(potential_jsons)} satır denendi).")
                    
                    if not signals: # Manuel ayrıştırma denendi ama hiçbir geçerli JSON bulunamadıysa
                        logger.error(f"Manuel JSON ayrıştırma sonucunda geçerli sinyal bulunamadı. Raw: {raw_data_for_log[:200]}")
                        # Bu durumda `request.is_json` false olduğu ve `raw_data_for_log` dolu olduğu için
                        # `json.loads(raw_data_for_log)` direkt hata verirdi.
                        # Eğer splitlines sonrası hiçbir şey parse edilemediyse, bu genel bir hatadır.
                        return jsonify({"status": "error", "message": "Request body contains non-JSON data or malformed line-separated JSON"}), 400
                # Bu noktada 'signals' listesi dolu ya da (hiçbir şey parse edilemediyse) boş olabilir.

            except json.JSONDecodeError as jde: # request.get_json() için ana JSONDecodeError
                logger.error(f"Ana JSONDecodeError (muhtemelen request.get_json() ile): {jde}. Raw: {raw_data_for_log[:200]}")
                return jsonify({"status": "error", "message": "Request body is not valid JSON"}), 400
            except Exception as parse_err: # Diğer beklenmedik ayrıştırma hataları
                logger.error(f"Genel JSON ayrıştırma sırasında beklenmedik hata: {parse_err}", exc_info=True)
                return jsonify({"status": "error", "message": "Error parsing JSON data"}), 500

            if not signals: # Tüm ayrıştırma çabalarına rağmen sinyal listesi hala boşsa
                logger.error("JSON ayrıştırma sonrası sinyal listesi boş kaldı. İstek işlenemiyor.")
                return jsonify({"status": "error", "message": "No valid signal data found after parsing"}), 400

            # --- GÜVENLİK ANAHTARI KONTROLÜ ---
            if webhook_secret_key:
                provided_key = None
                # Öncelik header'da
                if 'X-Secret-Key' in headers_for_log:
                    provided_key = headers_for_log.get('X-Secret-Key')
                    logger.info("Güvenlik anahtarı header (X-Secret-Key) üzerinden kontrol ediliyor.")
                # Header'da yoksa ve sinyaller varsa, ilk sinyalin içindeki 'secret' alanına bak
                # Bu, birden fazla JSON olsa bile, gizli anahtarın ilk JSON'da veya header'da olmasını bekler.
                elif signals and isinstance(signals[0], dict) and 'secret' in signals[0]:
                    provided_key = signals[0].get('secret')
                    logger.info("Güvenlik anahtarı ilk JSON nesnesindeki 'secret' alanı üzerinden kontrol ediliyor.")
                
                if provided_key == webhook_secret_key:
                    request_valid = True
                    logger.info("Webhook güvenlik anahtarı DOĞRULANDI.")
                else:
                    logger.warning(f"Webhook güvenlik anahtarı EŞLEŞMEDİ! Beklenen (hash'lenmiş olabilir): '{webhook_secret_key[:5]}...', Sağlanan: '{str(provided_key)[:5]}...'")
                    return jsonify({"status": "error", "message": "Unauthorized"}), 401
            else:
                request_valid = True # Güvenlik anahtarı yapılandırılmamışsa, isteği geçerli say
                logger.info("Webhook güvenlik anahtarı yapılandırılmamış, istek doğrudan kabul ediliyor.")
            # --- /GÜVENLİK ANAHTARI KONTROLÜ ---

        except Exception as initial_err: # JSON ayrıştırma ve anahtar kontrolü sırasındaki genel hatalar için
            logger.error(f"Webhook ön işleme hatası (JSON ayrıştırma veya anahtar kontrolü): {initial_err}", exc_info=True)
            # Bu loglar zaten yukarıda daha spesifik olarak atılmış olabilir, ama genel bir güvence.
            logger.info(f"Headers (hata anında): {headers_for_log if headers_for_log else dict(request.headers)}")
            logger.info(f"Raw Body (hata anında, ilk 500 karakter): {raw_data_for_log[:500]}")
            return jsonify({"status": "error", "message": "Internal server error during request pre-processing"}), 500
        finally:
            logger.info("--- /Gelen Webhook İsteği (ön işleme bölümü tamamlandı) ---")


        # --- SİNYALLERİN KUYRUĞA EKLENMESİ ---
        if request_valid and signals:
            signals_added_to_queue = 0
            for i, signal_data_item in enumerate(signals):
                if not isinstance(signal_data_item, dict):
                    logger.warning(f"Sinyal #{i+1} bir sözlük (dictionary) değil, atlanıyor: Tip {type(signal_data_item)}, Veri: {str(signal_data_item)[:100]}")
                    continue

                try:
                    # Opsiyonel: Eğer 'secret' alanı sadece doğrulama içindi ve BotCore'a gitmemesi gerekiyorsa:
                    # current_signal_data_for_queue = {k: v for k, v in signal_data_item.items() if k != 'secret'}
                    # signal_wrapper = {'source': 'webhook', 'data': current_signal_data_for_queue}
                    # Şimdilik orijinal veriyi yolluyoruz, BotCore tarafı 'secret'ı görmezden gelebilir veya kullanabilir.
                    signal_wrapper = {'source': 'webhook', 'data': signal_data_item}
                    
                    bot_core_instance.external_signal_queue.put(signal_wrapper)
                    logger.info(f"Doğrulanmış sinyal #{i+1}/{len(signals)} BotCore kuyruğuna eklendi: {str(signal_wrapper)[:200]}...") # Loglamayı kısalt
                    signals_added_to_queue += 1
                except Exception as e_queue: # Kuyruğa ekleme sırasında oluşabilecek hatalar
                    logger.error(f"Webhook sinyali #{i+1} kuyruğa eklenirken hata: {e_queue}", exc_info=True)
                    # Bu durumda, bir sonraki sinyali işlemeye devam edebiliriz.
                    # Eğer bir sinyalin kuyruğa eklenememesi kritikse, burada 500 dönebilirsiniz.
                    # Şimdilik sadece loglayıp devam ediyoruz.
            
            if signals_added_to_queue > 0:
                logger.info(f"Toplam {signals_added_to_queue}/{len(signals)} sinyal başarıyla kuyruğa eklendi.")
                print(f"---- handle_webhook fonksiyonu BAŞARIYLA BİTTİ ({signals_added_to_queue} sinyal kuyruğa eklendi) ----")
                return jsonify({"status": "success", "message": f"{signals_added_to_queue}/{len(signals)} signal(s) received and queued"}), 200
            else: # Hiçbir sinyal kuyruğa eklenemediyse (örn. hepsi hatalı formatta dict değildi veya kuyruk hatası oldu)
                logger.warning("İstek geçerliydi ancak hiçbir sinyal kuyruğa eklenemedi.")
                print(f"---- handle_webhook fonksiyonu UYARI İLE BİTTİ (hiçbir sinyal kuyruğa eklenemedi) ----")
                return jsonify({"status": "warning", "message": "Request valid but no signals could be queued"}), 202 # Veya 400
        
        elif request_valid and not signals: # Bu durum yukarıda zaten handle edilmiş olmalı ama ek kontrol.
            logger.warning("Webhook isteği geçerli ancak ayrıştırılmış sinyal verisi bulunamadı (signals listesi boş).")
            print(f"---- handle_webhook fonksiyonu UYARI İLE BİTTİ (boş sinyal verisi) ----")
            return jsonify({"status": "error", "message": "Valid request but no processable signal data found"}), 400
        
        # request_valid false ise zaten yukarıda 401 dönülmüştü.
        # Buraya normalde gelinmemeli eğer request_valid false ise.
        logger.error("Webhook mantık hatası: İstek geçersiz veya sinyal yok ama uygun bir dönüş yapılmamış.")
        print(f"---- handle_webhook fonksiyonu BEKLENMEDİK HATA İLE BİTTİ ----")
        return jsonify({"status": "error", "message": "Invalid request or no data to process (unexpected state)"}), 400
else:
    # Bu kısım dosyanızda zaten var.
    logger.warning("Flask uygulaması (flask_app) None olduğu için webhook endpoint'i (@flask_app.route) tanımlanamadı.")

def run_webhook_server(host='0.0.0.0', port=5000, bot_core=None, secret_key=None):
    print(f"run_webhook_server çağrıldı. Host: {host}, Port: {port}, BotCore Tipi: {type(bot_core)}, Secret Key: {'Ayarlı' if secret_key else 'Ayarlı Değil'}")
    global bot_core_instance, webhook_secret_key, flask_app

    if flask_app is None:
        logger.critical("Flask uygulaması (flask_app) None olduğu için webhook sunucusu başlatılamıyor.")
        print("KRİTİK: Flask uygulaması yüklenemedi, webhook sunucusu başlatılamaz.")
        return

    if bot_core is None:
        logger.critical("Webhook sunucusu BotCore örneği olmadan başlatılamaz.")
        print("KRİTİK: BotCore örneği None, webhook sunucusu işlevsel olmayacak.")
        return

    bot_core_instance = bot_core
    webhook_secret_key = secret_key

    if webhook_secret_key:
        logger.info(f"Webhook sunucusu {host}:{port} adresinde GÜVENLİK ANAHTARI İLE başlatılıyor...")
    else:
        logger.warning(f"Webhook sunucusu {host}:{port} adresinde GÜVENLİK ANAHTARI OLMADAN başlatılıyor...")

    try:
        logger.info(f"Flask geliştirme sunucusu {host}:{port} adresinde başlatılıyor...")
        flask_app.run(host=host, port=port, debug=False, use_reloader=False)
        logger.info(f"Webhook sunucusu ({host}:{port}) durduruldu.")
    except OSError as e:
        logger.critical(f"Webhook sunucusu başlatılamadı ({host}:{port}): {e}. Port kullanımda olabilir.")
    except Exception as e:
        logger.critical(f"Webhook sunucusu çalışırken kritik hata oluştu ({host}:{port}): {e}", exc_info=True)

if __name__ == '__main__':
    print("--- webhook_listener.py doğrudan çalıştırılıyor (TEST AMAÇLI) ---")
    if not logger or logger.name.endswith('_fallback') or logger.name.endswith('_critical_fallback'):
         logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)-7s] %(name)s: %(message)s')
         logger = logging.getLogger('webhook_listener_standalone_test')
         logger.info("Bağımsız test için fallback logger ayarlandı.")

    class MockQueue:
        def __init__(self): self.items = []
        def put(self, item): self.items.append(item); logger.info(f"MockQueue'ya eklendi: {item}")
        def get_nowait(self): return self.items.pop(0) if self.items else None
        def empty(self): return not self.items
        def task_done(self): pass

    class MockBotCore:
        def __init__(self):
            self.external_signal_queue = MockQueue()
            logger.info("MockBotCore ve MockQueue oluşturuldu.")

    if flask_app:
        test_bot_core = MockBotCore()
        test_secret = "mytestsecret"
        logger.info(f"Test Webhook sunucusu 0.0.0.0:5001 adresinde başlatılıyor. Secret Key: '{test_secret}'")
        run_webhook_server(host='0.0.0.0', port=5001, bot_core=test_bot_core, secret_key=test_secret)
    else:
        logger.error("Flask (flask_app) düzgün yüklenemediği için test sunucusu başlatılamıyor.")

print("!!!! WEBHOOK_LISTENER.PY MODÜLÜ SONU !!!!")