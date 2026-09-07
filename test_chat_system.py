import os
import sys
import unittest
import io

# Program dizinini sys.path'e ekle
BASE_DIR = r"c:\Users\ordum\OneDrive\Masaüstü\program\imalat_takip_sistemi"
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import database
from database import (
    init_db, get_db, hash_password,
    get_chat_messages, save_chat_message, clear_chat_messages, delete_chat_message,
    mark_chat_messages_as_read, get_chat_users_with_unread, get_unread_chat_summary
)
import app as flask_app_module
from app import app

class TestChatAndMessagingSystem(unittest.TestCase):
    def setUp(self):
        init_db()
        self.client = app.test_client()
        self.app_context = app.app_context()
        self.app_context.push()

        # Test için kullanıcıları hazırla
        conn = get_db()
        cursor = conn.cursor()
        
        # Test kullanıcısı 1 (Ali Usta - Kesim)
        cursor.execute("SELECT id FROM users WHERE username = 'ali_kesim'")
        row1 = cursor.fetchone()
        if not row1:
            cursor.execute("""
                INSERT INTO users (username, password_hash, full_name, role, is_active)
                VALUES ('ali_kesim', ?, 'Ali Kesici', 'usta', 1)
            """, (hash_password("123456"),))
            self.user1_id = cursor.lastrowid
        else:
            self.user1_id = row1['id']

        # Test kullanıcısı 2 (Mehmet Kalite - Kalite Mühendisi)
        cursor.execute("SELECT id FROM users WHERE username = 'mehmet_kalite'")
        row2 = cursor.fetchone()
        if not row2:
            cursor.execute("""
                INSERT INTO users (username, password_hash, full_name, role, is_active)
                VALUES ('mehmet_kalite', ?, 'Mehmet Kontrol', 'kalite mühendisi', 1)
            """, (hash_password("123456"),))
            self.user2_id = cursor.lastrowid
        else:
            self.user2_id = row2['id']

        # Test kullanıcısı 3 (Hasan Boya - Formen)
        cursor.execute("SELECT id FROM users WHERE username = 'hasan_boya'")
        row3 = cursor.fetchone()
        if not row3:
            cursor.execute("""
                INSERT INTO users (username, password_hash, full_name, role, is_active)
                VALUES ('hasan_boya', ?, 'Hasan Boyacı', 'formen', 1)
            """, (hash_password("123456"),))
            self.user3_id = cursor.lastrowid
        else:
            self.user3_id = row3['id']

        conn.commit()
        conn.close()

    def tearDown(self):
        self.app_context.pop()

    def test_1_department_channels(self):
        """Tüm birim kanallarına mesaj gönderme ve izole okuma testi"""
        channels = ['genel', 'kesim', 'imalat', 'kalite', 'boya', 'sevkiyat', 'muhasebe', 'yonetim']
        
        for ch in channels:
            clear_chat_messages(channel=ch)
            msg_text = f"{ch.upper()} birimi için test mesajı"
            msg_id = save_chat_message(
                channel=ch,
                user_id=self.user1_id,
                username='ali_kesim',
                full_name='Ali Kesici',
                message=msg_text
            )
            self.assertIsNotNone(msg_id)
            
            # Kanal mesajlarını oku
            msgs = get_chat_messages(channel=ch)
            self.assertTrue(len(msgs) >= 1)
            last_msg = msgs[-1]
            self.assertEqual(last_msg['message'], msg_text)
            self.assertEqual(last_msg['channel'], ch)
            self.assertIsNone(last_msg['receiver_id'])

        print("[OK] Test 1: 8 Adet Birim Kanalı İzolasyonu ve Mesajlaşması Başarılı.")

    def test_2_direct_messaging_dm(self):
        """İki kullanıcı arasında birebir (DM) özel mesajlaşma ve gizlilik testi"""
        # Önceki test DM geçmişini temizle
        clear_chat_messages(user_id=self.user1_id, partner_id=self.user2_id)
        clear_chat_messages(user_id=self.user3_id, partner_id=self.user1_id)
        
        # User 1 -> User 2'ye DM atar
        dm1 = save_chat_message(
            channel=None,
            user_id=self.user1_id,
            username='ali_kesim',
            full_name='Ali Kesici',
            message="Selam Mehmet Bey, parça muayenesi hazır mı?",
            receiver_id=self.user2_id
        )
        self.assertIsNotNone(dm1)

        # User 2 -> User 1'e yanıt verir
        dm2 = save_chat_message(
            channel=None,
            user_id=self.user2_id,
            username='mehmet_kalite',
            full_name='Mehmet Kontrol',
            message="Evet Ali Usta, birazdan kontrole geliyorum.",
            receiver_id=self.user1_id
        )
        self.assertIsNotNone(dm2)

        # User 1 ve User 2 arasındaki konuşmayı al
        convo = get_chat_messages(user_id=self.user1_id, partner_id=self.user2_id)
        self.assertEqual(len(convo), 2)
        self.assertEqual(convo[0]['message'], "Selam Mehmet Bey, parça muayenesi hazır mı?")
        self.assertEqual(convo[1]['message'], "Evet Ali Usta, birazdan kontrole geliyorum.")

        # User 3 bu özel konuşmayı göremez
        convo_u3 = get_chat_messages(user_id=self.user3_id, partner_id=self.user1_id)
        self.assertEqual(len(convo_u3), 0)

        # Genel kanalda bu mesajlar görünmez
        genel_msgs = get_chat_messages(channel='genel')
        for gm in genel_msgs:
            self.assertNotEqual(gm['message'], "Selam Mehmet Bey, parça muayenesi hazır mı?")

        print("[OK] Test 2: Birebir (DM) Özel Mesajlaşma ve İzolasyon Başarılı.")

    def test_3_unread_counts_and_read_status(self):
        """Okunmamış sayaçları ve Okundu (mark as read) testi"""
        clear_chat_messages(user_id=self.user1_id, partner_id=self.user2_id)
        
        # User 1 -> User 2'ye 3 adet mesaj atsın
        for i in range(1, 4):
            save_chat_message(
                channel=None,
                user_id=self.user1_id,
                username='ali_kesim',
                full_name='Ali Kesici',
                message=f"Acil bildirim #{i}",
                receiver_id=self.user2_id
            )

        # User 2 için okunmamış özetini kontrol et
        summary_u2 = get_unread_chat_summary(current_user_id=self.user2_id)
        self.assertEqual(summary_u2['total_unread'], 3)
        self.assertEqual(summary_u2['by_user'].get(str(self.user1_id)), 3)

        # User listesindeki okunmamış sayısını kontrol et
        user_list = get_chat_users_with_unread(current_user_id=self.user2_id)
        u1_entry = next((u for u in user_list if u['id'] == self.user1_id), None)
        self.assertIsNotNone(u1_entry)
        self.assertEqual(u1_entry['unread_count'], 3)

        # User 2 mesajları okundu olarak işaretlesin
        mark_chat_messages_as_read(current_user_id=self.user2_id, partner_id=self.user1_id)

        # Kontrol et: okunmamış 0 olmalı
        summary_u2_after = get_unread_chat_summary(current_user_id=self.user2_id)
        self.assertEqual(summary_u2_after['total_unread'], 0)

        print("[OK] Test 3: Okunmamış Sayaçları ve Okundu İşaretleme Başarılı.")

    def test_4_endpoints_and_web_ui(self):
        """Flask HTTP Rotaları ve API Uç Noktaları Testi"""
        # 1. /sohbet sayfası GET
        with self.client.session_transaction() as sess:
            sess['user'] = {
                'id': self.user1_id,
                'username': 'ali_kesim',
                'full_name': 'Ali Kesici',
                'role': 'usta'
            }
        
        resp = self.client.get('/sohbet')
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Sohbet".encode('utf-8'), resp.data)
        self.assertIn("#kesim".encode('utf-8'), resp.data)
        self.assertIn("#imalat".encode('utf-8'), resp.data)
        self.assertIn("#kalite".encode('utf-8'), resp.data)

        # 2. POST /api/chat/send (Kanal Mesajı)
        resp_send_chan = self.client.post('/api/chat/send', data={
            'channel': 'imalat',
            'message': 'İmalat holünde kaynak işlemi başladı.'
        })
        self.assertEqual(resp_send_chan.status_code, 200)
        res_json = resp_send_chan.get_json()
        self.assertEqual(res_json['status'], 'success')
        self.assertEqual(res_json['channel'], 'imalat')

        # 3. POST /api/chat/send (Birebir DM Mesajı)
        resp_send_dm = self.client.post('/api/chat/send', data={
            'receiver_id': str(self.user3_id),
            'message': 'Hasan Bey, boyahaneye 5 tonluk montaj sevk edildi.'
        })
        self.assertEqual(resp_send_dm.status_code, 200)
        res_dm_json = resp_send_dm.get_json()
        self.assertEqual(res_dm_json['status'], 'success')
        self.assertEqual(res_dm_json['receiver_id'], self.user3_id)

        # 4. GET /api/chat/messages?partner_id=...
        resp_msgs = self.client.get(f'/api/chat/messages?partner_id={self.user3_id}')
        self.assertEqual(resp_msgs.status_code, 200)
        msgs_data = resp_msgs.get_json()
        self.assertTrue(len(msgs_data) >= 1)
        self.assertIn("Hasan Bey, boyahaneye 5 tonluk montaj sevk edildi.", [m['message'] for m in msgs_data])

        # 5. GET /api/chat/users
        resp_users = self.client.get('/api/chat/users')
        self.assertEqual(resp_users.status_code, 200)
        users_json = resp_users.get_json()
        self.assertTrue(len(users_json) >= 2)

        # 6. GET /api/chat/unread-summary
        resp_unread = self.client.get('/api/chat/unread-summary')
        self.assertEqual(resp_unread.status_code, 200)

        # 7. POST /api/chat/mark-read
        resp_read = self.client.post('/api/chat/mark-read', json={'partner_id': self.user3_id})
        self.assertEqual(resp_read.status_code, 200)

        print("[OK] Test 4: /sohbet Arayüzü ve Tüm /api/chat/* Endpoint'leri Başarılı.")

    def test_5_message_deletion_and_photo(self):
        """Mesaj Silme ve Fotoğraf Yükleme Testi"""
        # Fotoğraf taklit eden dosya ile gönderim
        fake_photo = (io.BytesIO(b"dummy image data"), "test_parca.jpg")
        with self.client.session_transaction() as sess:
            sess['user'] = {
                'id': self.user1_id,
                'username': 'ali_kesim',
                'full_name': 'Ali Kesici',
                'role': 'usta'
            }

        resp_photo = self.client.post('/api/chat/send', data={
            'channel': 'kalite',
            'message': 'Fotoğraflı kaynak dikiş muayenesi',
            'photo': fake_photo
        }, content_type='multipart/form-data')
        self.assertEqual(resp_photo.status_code, 200)
        photo_json = resp_photo.get_json()
        self.assertEqual(photo_json['status'], 'success')
        self.assertTrue(photo_json['photo_url'].startswith('/static/uploads/chat/'))

        msg_id = photo_json['message_id']
        
        # Mesajı sil
        resp_del = self.client.post(f'/api/chat/message/{msg_id}/delete')
        self.assertEqual(resp_del.status_code, 200)

        # Mesajın silindiğini doğrula
        msgs = get_chat_messages(channel='kalite')
        msg_ids = [m['id'] for m in msgs]
        self.assertNotIn(msg_id, msg_ids)

        print("[OK] Test 5: Fotoğraf Yükleme ve Tekil Mesaj Silme Başarılı.")

if __name__ == '__main__':
    unittest.main()
