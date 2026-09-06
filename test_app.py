import unittest
import io
import openpyxl
from app import app
from database import init_db, get_db, get_global_metrics, get_project_summary
from excel_handler import generate_template_excel, parse_excel_parts, export_project_report_excel
from seed_data import seed_demo_data

class TestImalatTakipApp(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        init_db()
        seed_demo_data()
        with self.client.session_transaction() as sess:
            sess['user'] = {'id': 1, 'username': 'admin', 'role': 'admin', 'full_name': 'Sistem Yöneticisi'}

    def test_dashboard_route(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Kontrol Paneli'.encode('utf-8'), response.data)
        print("[OK] Dashboard rotasi basarili")

    def test_projects_route(self):
        response = self.client.get('/projeler')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Projeler'.encode('utf-8'), response.data)
        print("[OK] Projeler listesi rotasi basarili")

    def test_project_detail_route(self):
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id FROM projects LIMIT 1")
        row = c.fetchone()
        conn.close()
        p_id = row['id'] if row else 1
        response = self.client.get(f'/projeler/{p_id}')
        self.assertEqual(response.status_code, 200)
        print("[OK] Proje detay rotasi basarili")

    def test_stages_routes(self):
        # İmalat
        r1 = self.client.get('/imalat-takip')
        self.assertEqual(r1.status_code, 200)
        # Boya
        r2 = self.client.get('/boya-takip')
        self.assertEqual(r2.status_code, 200)
        # Sevk
        r3 = self.client.get('/sevk')
        self.assertEqual(r3.status_code, 200)
        # Muhasebe
        r4 = self.client.get('/muhasebe-irsaliye')
        self.assertEqual(r4.status_code, 200)
        print("[OK] Imalat, Boya, Sevk ve Muhasebe sayfalari basarili")

    def test_shipment_receipt_a4(self):
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id FROM shipments LIMIT 1")
        row = c.fetchone()
        conn.close()
        s_id = row['id'] if row else 1
        response = self.client.get(f'/sevk/{s_id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn('SEVKİYAT İRSALİYESİ'.encode('utf-8'), response.data)
        print("[OK] A4 Sevk Fisi / Ceki Listesi rotasi basarili")

    def test_excel_template_generation(self):
        excel_stream = generate_template_excel()
        self.assertIsNotNone(excel_stream)
        wb = openpyxl.load_workbook(excel_stream)
        self.assertIn("1-Montaj Listesi (Assembly)", wb.sheetnames)
        print("[OK] Excel sablon uretimi basarili")

    def test_tonnage_calculations(self):
        metrics = get_global_metrics()
        self.assertGreater(metrics['total_tonnage'], 0)
        self.assertGreaterEqual(metrics['fab_completed_tonnage'], 0)
        self.assertGreaterEqual(metrics['paint_completed_tonnage'], 0)
        self.assertGreaterEqual(metrics['shipped_tonnage'], 0)
        print(f"[OK] Tonaj hesaplama motoru dogrulandi (Toplam: {metrics['total_tonnage']} Ton, Imalat: {metrics['fab_completed_tonnage']} Ton, Boya: {metrics['paint_completed_tonnage']} Ton, Sevk: {metrics['shipped_tonnage']} Ton)")

if __name__ == '__main__':
    unittest.main()

