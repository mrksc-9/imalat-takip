import unittest
import io
import openpyxl
from app import app
from database import init_db, get_global_metrics, get_project_summary
from excel_handler import generate_template_excel, parse_excel_parts, export_project_report_excel

class TestImalatTakipApp(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        init_db()

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
        response = self.client.get('/projeler/1')
        self.assertEqual(response.status_code, 200)
        print("[OK] Proje detay rotasi basarili")

    def test_stages_routes(self):
        # İmalat
        r1 = self.client.get('/imalat')
        self.assertEqual(r1.status_code, 200)
        # Boya
        r2 = self.client.get('/boya')
        self.assertEqual(r2.status_code, 200)
        # Sevk
        r3 = self.client.get('/sevk')
        self.assertEqual(r3.status_code, 200)
        print("[OK] Imalat, Boya ve Sevk sayfalari basarili")

    def test_shipment_receipt_a4(self):
        response = self.client.get('/sevk/1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('SEVKİYAT & ÇEKİ LİSTESİ'.encode('utf-8'), response.data)
        print("[OK] A4 Sevk Fisi / Ceki Listesi rotasi basarili")

    def test_excel_template_generation(self):
        excel_stream = generate_template_excel()
        self.assertIsNotNone(excel_stream)
        wb = openpyxl.load_workbook(excel_stream)
        self.assertIn("Poz Listesi", wb.sheetnames)
        print("[OK] Excel sablon uretimi basarili")

    def test_excel_import_parser(self):
        excel_stream = generate_template_excel()
        parsed = parse_excel_parts(excel_stream)
        self.assertGreater(len(parsed), 0)
        self.assertEqual(parsed[0]['pos_no'], 'K-101')
        print(f"[OK] Excel parser basarili ({len(parsed)} parca okundu)")

    def test_tonnage_calculations(self):
        metrics = get_global_metrics()
        self.assertGreater(metrics['total_tonnage'], 0)
        self.assertGreaterEqual(metrics['fab_completed_tonnage'], 0)
        self.assertGreaterEqual(metrics['paint_completed_tonnage'], 0)
        self.assertGreaterEqual(metrics['shipped_tonnage'], 0)
        print(f"[OK] Tonaj hesaplama motoru dogrulandi (Toplam: {metrics['total_tonnage']} Ton, Imalat: {metrics['fab_completed_tonnage']} Ton, Boya: {metrics['paint_completed_tonnage']} Ton, Sevk: {metrics['shipped_tonnage']} Ton)")

if __name__ == '__main__':
    unittest.main()
