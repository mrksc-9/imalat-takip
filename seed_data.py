from database import get_db, init_db
from datetime import datetime, timedelta

def seed_demo_data():
    """Örnek çelik imalat projeleri, montajlar, pozlar, siparişler, makineler ve aşama kayıtları oluşturur."""
    try:
        init_db()
        conn = get_db()
        cursor = conn.cursor()

        # Zaten veri var mı kontrol et
        cursor.execute("SELECT COUNT(*) as count FROM projects")
        if cursor.fetchone()['count'] > 0:
            conn.close()
            return

        today = datetime.now()
        d_start = (today - timedelta(days=25)).strftime("%Y-%m-%d")
        d_cut_start = (today - timedelta(days=20)).strftime("%Y-%m-%d")
        d_fitup_end = (today - timedelta(days=10)).strftime("%Y-%m-%d")
        d_weld_end = (today - timedelta(days=5)).strftime("%Y-%m-%d")
        d_paint_end = (today + timedelta(days=10)).strftime("%Y-%m-%d")
        d_end1 = (today + timedelta(days=30)).strftime("%Y-%m-%d")
        d_end2 = (today + timedelta(days=45)).strftime("%Y-%m-%d")
        d_end3 = (today + timedelta(days=15)).strftime("%Y-%m-%d")

        # 1. Projeler
        projects_data = [
            ("PRJ-2026-01", "MEGA LOJİSTİK DEPOSU ÇELİK KONSTRÜKSİYONU", "Atlas Lojistik A.Ş.", "Kocaeli Dilovası Şantiyesi", d_start, d_cut_start, d_fitup_end, d_weld_end, d_paint_end, d_end1, 145.5, "#3b82f6", "IKC12-", "Aktif", "Tamamlandı", "Aks 1-12 arası ana çelik karkas imalatı ve montajı."),
            ("PRJ-2026-02", "ORGANİZE SANAYİ FABRİKA BİNASI VE KÖPRÜ VİNÇ", "Tekno Kimya Sanayi", "Bursa OSB 4. Kısım", d_start, d_cut_start, d_fitup_end, d_weld_end, d_paint_end, d_end2, 88.0, "#10b981", "TK24-", "Aktif", "Bekliyor", "20 Tonluk Köprü Vinç Kirişleri ve Çatı Makasları."),
            ("PRJ-2026-03", "ENDÜSTRİYEL SİLO VE TAŞIYICI PLATFORM", "Bereket Un Fabrikaları", "Tekirdağ Çorlu Tesisleri", d_start, d_cut_start, d_fitup_end, d_weld_end, d_paint_end, d_end3, 32.4, "#f59e0b", "SL55-", "Aktif", "Tamamlandı", "Galvanizli ve boyalı platform imalatları.")
        ]

        for p in projects_data:
            cursor.execute('''
            INSERT INTO projects (code, name, customer, site_location, start_date, cutting_start_date, fitup_end_date, welding_end_date, paint_end_date, delivery_date, target_tonnage, color, pos_prefix, status, order_status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', p)
        conn.commit()

        # Proje 1 ID al
        cursor.execute("SELECT id FROM projects WHERE code = 'PRJ-2026-01'")
        p1_id = cursor.fetchone()['id']

        cursor.execute("SELECT id FROM projects WHERE code = 'PRJ-2026-02'")
        p2_id = cursor.fetchone()['id']

        # 2. Proje 1 Assemblies (Montaj Listesi)
        p1_assemblies = [
            (p1_id, "C-101", "Ana Taşıyıcı Kolon", "HEB 360", 8, 1420.0, 11360.0, "S355JR", 0, 0, 0, 8, 0, 8, 0, "TAMAMLANDI", 0, 0, 0, 8, "RAL 7016", "120 µm", 8),
            (p1_id, "C-102", "Ara Taşıyıcı Kolon", "HEB 300", 12, 1170.0, 14040.0, "S355JR", 0, 0, 0, 12, 0, 12, 0, "TAMAMLANDI", 0, 0, 0, 12, "RAL 7016", "120 µm", 12),
            (p1_id, "B-201", "Ana Çatı Kirişi", "IPE 450", 10, 1404.0, 14040.0, "S355JR", 0, 0, 2, 8, 2, 8, 0, "TAMAMLANDI", 0, 0, 0, 8, "RAL 7016", "120 µm", 4),
            (p1_id, "B-202", "Tali Çatı Kirişi", "IPE 300", 14, 422.0, 5908.0, "S275JR", 2, 4, 2, 6, 2, 6, 0, "BOYADA", 0, 6, 0, 0, "RAL 7016", "120 µm", 0),
            (p1_id, "TR-301", "Ana Çatı Makası", "2x L 100x100x10", 8, 890.0, 7120.0, "S355JR", 4, 2, 0, 2, 1, 2, 0, "BEKLIYOR", 2, 0, 0, 0, "RAL 7016", "120 µm", 0),
            (p1_id, "P-401", "Çatı Aşığı", "C 200x75x2.5", 80, 55.0, 4400.0, "S235JR", 0, 0, 0, 80, 0, 80, 0, "TAMAMLANDI", 0, 0, 0, 80, "RAL 9002", "80 µm", 80),
            (p1_id, "WB-501", "Rüzgar Çaprazı", "L 80x80x8", 24, 75.0, 1800.0, "S275JR", 8, 4, 2, 10, 2, 10, 0, "TAMAMLANDI", 0, 0, 0, 10, "RAL 7016", "120 µm", 0)
        ]

        for a in p1_assemblies:
            cursor.execute('''
            INSERT INTO assemblies (project_id, assembly_pos, description, profile_type, quantity, unit_weight, total_weight, material_grade,
                                    fab_fitup_qty, fab_welding_qty, fab_cleaning_qty, fab_done_qty, qa_pending_qty, qa_approved_qty, qa_rejected_qty,
                                    paint_status, paint_sandblast_qty, paint_paint_qty, paint_galv_qty, paint_done_qty, paint_ral, paint_dft, shipped_qty)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', a)
        conn.commit()

        # 3. Proje 1 Tek Parça Listesi (Single Parts)
        p1_parts = [
            (p1_id, "IKC12-P1", "Kolon Gövde Profili", "HEB 360", 8, 8, 0, 9500, 1250.0, 10000.0, "S355JR"),
            (p1_id, "IKC12-P2", "Kolon Taban Flanşı", "PL 30mm", 20, 20, 0, 450, 68.0, 1360.0, "S355JR"),
            (p1_id, "IKC12-P3", "Berkitme Plakası", "PL 15mm", 60, 60, 0, 280, 8.5, 510.0, "S275JR"),
            (p1_id, "IKC12-P4", "Kiriş Gövde Profili", "IPE 450", 10, 10, 0, 18000, 1210.0, 12100.0, "S355JR"),
            (p1_id, "IKC12-P5", "Kiriş Alın Birleşim Plakası", "PL 20mm", 20, 20, 0, 350, 18.2, 364.0, "S355JR"),
            (p1_id, "IKC12-P6", "Aşık Profili", "C 200x75x2.5", 80, 80, 0, 6000, 55.0, 4400.0, "S235JR"),
            (p1_id, "IKC12-P7", "Guse Plakası", "PL 12mm", 48, 36, 12, 220, 6.2, 297.6, "S235JR"),
            (p1_id, "IKC12-P8", "Çapraz Köşebent", "L 80x80x8", 24, 18, 6, 7500, 72.5, 1740.0, "S275JR")
        ]

        for p in p1_parts:
            cursor.execute('''
            INSERT INTO parts (project_id, pos_no, name, profile_type, quantity, cut_quantity, remaining_quantity, length, unit_weight, total_weight, material_grade)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', p)

        # 4. Proje 1 Malzeme Siparişleri
        p1_orders_verilen = [
            (p1_id, "PRJ-2026-01", "HEB 360", 0, 0, 12000, 8, 13680.0, "Kardemir A.Ş.", "Kolon imalatı için boy profil"),
            (p1_id, "PRJ-2026-01", "HEB 300", 0, 0, 12000, 12, 16848.0, "Kardemir A.Ş.", "Ara kolonlar"),
            (p1_id, "PRJ-2026-01", "IPE 450", 0, 0, 12000, 16, 15000.0, "Çolakoğlu Metalurji", "Ana çatı kirişleri"),
            (p1_id, "PRJ-2026-01", "SAC S355JR", 30, 2000, 6000, 4, 5652.0, "Erdemir", "Taban flanş plakaları"),
            (p1_id, "PRJ-2026-01", "SAC S275JR", 15, 1500, 6000, 6, 6358.5, "Erdemir", "Berkitme ve alın plakaları"),
            (p1_id, "PRJ-2026-01", "C 200x75x2.5", 2.5, 0, 6000, 80, 4400.0, "Tosyalı Profil", "Galvanizli aşık profili")
        ]

        for o in p1_orders_verilen:
            cursor.execute('''
            INSERT INTO material_orders_ordered (project_id, project_code, material, thickness, width, length, quantity, weight, supplier, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', o)

        p1_orders_gelen = [
            (p1_id, "PRJ-2026-01", "HEB 360", 0, 0, 12000, 8, 13680.0, (today - timedelta(days=18)).strftime("%Y-%m-%d"), "IRS-88412", "Eksiksiz teslim alındı"),
            (p1_id, "PRJ-2026-01", "HEB 300", 0, 0, 12000, 12, 16848.0, (today - timedelta(days=18)).strftime("%Y-%m-%d"), "IRS-88413", "Eksiksiz teslim alındı"),
            (p1_id, "PRJ-2026-01", "IPE 450", 0, 0, 12000, 16, 15000.0, (today - timedelta(days=15)).strftime("%Y-%m-%d"), "IRS-91022", "Test sertifikası ile teslim alındı"),
            (p1_id, "PRJ-2026-01", "SAC S355JR", 30, 2000, 6000, 4, 5652.0, (today - timedelta(days=14)).strftime("%Y-%m-%d"), "IRS-91055", "Sac plaka stok sahasına indirildi"),
            (p1_id, "PRJ-2026-01", "SAC S275JR", 15, 1500, 6000, 6, 6358.5, (today - timedelta(days=14)).strftime("%Y-%m-%d"), "IRS-91056", "Plazma kesim alanına sevk edildi"),
            (p1_id, "PRJ-2026-01", "C 200x75x2.5", 2.5, 0, 6000, 80, 4400.0, (today - timedelta(days=10)).strftime("%Y-%m-%d"), "IRS-93041", "Aşıklar stoklandı")
        ]

        for g in p1_orders_gelen:
            cursor.execute('''
            INSERT INTO material_orders_received (project_id, project_code, material, thickness, width, length, quantity, weight, received_date, waybill_no, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', g)

        # 5. Ön İmalat Planlama
        m_plans = [
            ("Hol 1 - Plazma 1", (today - timedelta(days=3)).strftime("%Y-%m-%d"), p1_id, "PRJ-2026-01", "Planlanan", "Tamamlandı", None, "Taban flanş plakaları kesimi"),
            ("Hol 1 - Plazma 1", (today - timedelta(days=3)).strftime("%Y-%m-%d"), p1_id, "PRJ-2026-01", "Uygulanan", "Tamamlandı", None, "20 adet kesildi"),
            ("Hol 1 - Plazma 1", (today - timedelta(days=2)).strftime("%Y-%m-%d"), p1_id, "PRJ-2026-01", "Planlanan", "Tamamlandı", None, "Berkitme plakaları"),
            ("Hol 1 - Plazma 1", (today - timedelta(days=2)).strftime("%Y-%m-%d"), p1_id, "PRJ-2026-01", "Uygulanan", "Tamamlandı", None, "60 adet kesildi"),
            ("Hol 2 - Testere 1", (today - timedelta(days=1)).strftime("%Y-%m-%d"), p1_id, "PRJ-2026-01", "Planlanan", "Tamamlandı", None, "HEB 360 boy kesimleri"),
            ("Hol 2 - Testere 1", (today - timedelta(days=1)).strftime("%Y-%m-%d"), p1_id, "PRJ-2026-01", "Uygulanan", "Tamamlandı", None, "8 boy kesildi"),
            ("Hol 2 - Lazer 1", today.strftime("%Y-%m-%d"), p2_id, "PRJ-2026-02", "Planlanan", "Planlandı", None, "Guse ve alın plakaları kesimi"),
            ("Hol 1 - Plazma 2", (today - timedelta(days=4)).strftime("%Y-%m-%d"), p1_id, "PRJ-2026-01", "Planlanan", "Durdu", "Torç arızası ve nozul değişimi", "2 saat duruş")
        ]

        for mp in m_plans:
            cursor.execute('''
            INSERT OR REPLACE INTO machine_plans (machine_name, date, project_id, project_code, plan_type, status, stoppage_reason, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', mp)

        # 6. Hızlı Kesim Girişi Kayıtları
        cuts = [
            (p1_id, "PRJ-2026-01", "IKC12-P1", "HEB 360", 8, (today - timedelta(days=16)).strftime("%Y-%m-%d"), "Hol 2 - Testere 1", "Ahmet Yılmaz", "Mustafa Çelik", "Gündüz", 1),
            (p1_id, "PRJ-2026-01", "IKC12-P2", "PL 30mm", 20, (today - timedelta(days=15)).strftime("%Y-%m-%d"), "Hol 1 - Plazma 1", "Mehmet Demir", "Ali Kara", "Gündüz", 1),
            (p1_id, "PRJ-2026-01", "IKC12-P3", "PL 15mm", 60, (today - timedelta(days=14)).strftime("%Y-%m-%d"), "Hol 1 - Plazma 1", "Mehmet Demir", "Mustafa Çelik", "Gündüz", 1),
            (p1_id, "PRJ-2026-01", "IKC12-P4", "IPE 450", 10, (today - timedelta(days=12)).strftime("%Y-%m-%d"), "Hol 2 - Testere 1", "Ahmet Yılmaz", "Mustafa Çelik", "Gündüz", 1),
            (p1_id, "PRJ-2026-01", "IKC12-P5", "PL 20mm", 20, (today - timedelta(days=11)).strftime("%Y-%m-%d"), "Hol 1 - Plazma 2", "Mehmet Demir", "Ali Kara", "Gündüz", 1),
            (p1_id, "PRJ-2026-01", "IKC12-P6", "C 200x75x2.5", 80, (today - timedelta(days=8)).strftime("%Y-%m-%d"), "Hol 2 - Testere 1", "Ahmet Yılmaz", "Mustafa Çelik", "Gündüz", 1),
            (p1_id, "PRJ-2026-01", "IKC12-P7", "PL 12mm", 36, (today - timedelta(days=2)).strftime("%Y-%m-%d"), "Hol 1 - Plazma 1", "Mehmet Demir", "Mustafa Çelik", "Gece", 1)
        ]

        for c in cuts:
            cursor.execute('''
            INSERT INTO cutting_entries (project_id, project_code, pos_no, profile, cut_quantity, cut_date, machine, operator, helper, shift, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', c)

        # 7. Kalite Kontrol Kayıtları
        cursor.execute("SELECT id FROM assemblies WHERE project_id = ? AND assembly_pos = 'C-101'", (p1_id,))
        c101_id = cursor.fetchone()['id']
        cursor.execute("SELECT id FROM assemblies WHERE project_id = ? AND assembly_pos = 'C-102'", (p1_id,))
        c102_id = cursor.fetchone()['id']
        cursor.execute("SELECT id FROM assemblies WHERE project_id = ? AND assembly_pos = 'B-201'", (p1_id,))
        b201_id = cursor.fetchone()['id']
        cursor.execute("SELECT id FROM assemblies WHERE project_id = ? AND assembly_pos = 'B-202'", (p1_id,))
        b202_id = cursor.fetchone()['id']

        qa_list = [
            (p1_id, c101_id, "C-101", 8, 4, "Kalite Kontrol Uzmanı", (today - timedelta(days=7)).strftime("%Y-%m-%d"), "Onaylandı", None, "Kaynak ve boyutsal kontroller uygun. Boyaya sevk edildi.", "QC-2026-001"),
            (p1_id, c102_id, "C-102", 12, 4, "Kalite Kontrol Uzmanı", (today - timedelta(days=6)).strftime("%Y-%m-%d"), "Onaylandı", None, "Gözle ve ultrasonik kaynak kontrolü tamamlandı.", "QC-2026-002"),
            (p1_id, b201_id, "B-201", 8, 4, "Kalite Kontrol Uzmanı", (today - timedelta(days=4)).strftime("%Y-%m-%d"), "Onaylandı", None, "Alın plakası ve gövde kaynakları onaylandı.", "QC-2026-003"),
            (p1_id, b202_id, "B-202", 2, 4, "Kalite Kontrol Uzmanı", today.strftime("%Y-%m-%d"), "Bekliyor", None, "Temizlik sonrası son muayene bekleniyor.", None)
        ]

        for qa in qa_list:
            cursor.execute('''
            INSERT INTO qa_inspections (project_id, assembly_id, assembly_pos, quantity, inspector_id, inspector_name, inspection_date, status, defect_type, comments, certificate_no)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', qa)

        # 8. Sevkiyatlar
        cursor.execute('''
        INSERT INTO shipments (project_id, dispatch_no, vehicle_plate, driver_name, driver_phone, carrier_company, dispatch_date, destination, total_quantity, total_tonnage, notes, created_by)
        VALUES (?, 'SVK-2026-001', '34 KRM 789', 'Ahmet Yılmaz', '0532 555 11 22', 'Öztrans Lojistik', ?, 'Kocaeli Dilovası Şantiyesi - 1. Etap', 20, 25.4, '1. Etap Ana Taşıyıcı Kolonlar ve Taban Flanşları.', 'Sevkiyatçı')
        ''', (p1_id, (today - timedelta(days=2)).strftime("%Y-%m-%d")))
        shipment_id_1 = cursor.lastrowid

        # Sevkiyat Kalemleri
        cursor.execute('''
        INSERT INTO shipment_items (shipment_id, assembly_id, assembly_pos, description, quantity, unit_weight, total_weight)
        VALUES (?, ?, 'C-101', 'Ana Taşıyıcı Kolon', 8, 1420.0, 11360.0),
               (?, ?, 'C-102', 'Ara Taşıyıcı Kolon', 12, 1170.0, 14040.0)
        ''', (shipment_id_1, c101_id, shipment_id_1, c102_id))

        # 9. Aktivite Günlüğü (Audit Log)
        logs = [
            (1, "yozi", "Proje Oluşturuldu", "projects", p1_id, "PRJ-2026-01 Mega Lojistik Deposu projesi açıldı.", "127.0.0.1"),
            (2, "admin", "Tekla Excel Yüklendi", "projects", p1_id, "Tekla Structures'tan 7 adet montaj ve 8 poz aktarıldı.", "127.0.0.1"),
            (3, "imalat", "Hızlı Kesim Girişi", "cutting_entries", p1_id, "Hol 2 Testere 1 makinesinde 8 adet IKC12-P1 kesildi.", "192.168.1.45"),
            (4, "kalite", "Kalite Onayı Verildi", "qa_inspections", p1_id, "C-101 kolonları kaliteden geçti (Sertifika: QC-2026-001).", "192.168.1.60"),
            (5, "boya", "Boya Tamamlandı", "paint_records", p1_id, "C-101 ve C-102 kolonlarının RAL 7016 boyası tamamlandı.", "192.168.1.72"),
            (6, "sevk", "Sevkiyat Oluşturuldu", "shipments", shipment_id_1, "SVK-2026-001 nolu irsaliye ile 25.4 Ton sevk edildi.", "192.168.1.50")
        ]

        for lg in logs:
            cursor.execute('''
            INSERT INTO activity_logs (user_id, username, action, entity_type, entity_id, details, ip_address)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', lg)

        conn.commit()
        conn.close()
        print("Zengin demo verileri basariyla yuklendi.")
    except Exception as e:
        print(f"Seed demo data notice: {e}")

if __name__ == '__main__':
    seed_demo_data()
