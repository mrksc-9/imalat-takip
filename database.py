import sqlite3
import os
import hashlib
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'imalat_takip.db')

def hash_password(password):
    return hashlib.sha256(str(password).encode('utf-8')).hexdigest()

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # 1. KULLANICILAR TABLOSU
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT DEFAULT 'Yönetici', -- Yönetici, İmalat Şefi, Kalite Kontrolcü, Boyahane Sorumlusu, Satınalma, Sevkiyatçı, İzleyici
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # 2. AKTİVİTE / DENETİM GÜNLÜĞÜ (AUDIT LOG) TABLOSU
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        action TEXT NOT NULL,
        entity_type TEXT,
        entity_id INTEGER,
        details TEXT,
        ip_address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # 3. PROJELER TABLOSU
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        customer TEXT,
        site_location TEXT,
        start_date TEXT,
        cutting_start_date TEXT,
        fitup_end_date TEXT,
        welding_end_date TEXT,
        paint_end_date TEXT,
        delivery_date TEXT,
        target_tonnage REAL DEFAULT 0,
        color TEXT DEFAULT '#3b82f6',
        pos_prefix TEXT DEFAULT '',
        status TEXT DEFAULT 'Aktif', -- Aktif, Tamamlandı, Arşiv
        order_status TEXT DEFAULT 'Bekliyor', -- Bekliyor, Verildi, Tamamlandı
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Projeler tablosundaki eksik kolonları dinamik ekle (Migration)
    cursor.execute("PRAGMA table_info(projects)")
    p_cols = [c[1] for c in cursor.fetchall()]
    if 'cutting_start_date' not in p_cols: cursor.execute("ALTER TABLE projects ADD COLUMN cutting_start_date TEXT DEFAULT ''")
    if 'fitup_end_date' not in p_cols: cursor.execute("ALTER TABLE projects ADD COLUMN fitup_end_date TEXT DEFAULT ''")
    if 'welding_end_date' not in p_cols: cursor.execute("ALTER TABLE projects ADD COLUMN welding_end_date TEXT DEFAULT ''")
    if 'paint_end_date' not in p_cols: cursor.execute("ALTER TABLE projects ADD COLUMN paint_end_date TEXT DEFAULT ''")
    if 'color' not in p_cols: cursor.execute("ALTER TABLE projects ADD COLUMN color TEXT DEFAULT '#3b82f6'")
    if 'pos_prefix' not in p_cols: cursor.execute("ALTER TABLE projects ADD COLUMN pos_prefix TEXT DEFAULT ''")
    if 'order_status' not in p_cols: cursor.execute("ALTER TABLE projects ADD COLUMN order_status TEXT DEFAULT 'Bekliyor'")

    # 4. ASSEMBLIES (MONTAJ / MARKA LİSTESİ) TABLOSU
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS assemblies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        assembly_pos TEXT NOT NULL, -- Örn: C-1, B-101, TR-1
        description TEXT,           -- Örn: Kolon, Ana Kiriş, Makas
        profile_type TEXT,          -- Örn: HEB 300, IPE 360
        quantity INTEGER NOT NULL DEFAULT 1,
        unit_weight REAL NOT NULL DEFAULT 0,
        total_weight REAL NOT NULL DEFAULT 0,
        material_grade TEXT DEFAULT 'S275JR',
        
        -- İmalat Aşamaları Adetleri (Partial Quantities)
        fab_fitup_qty INTEGER DEFAULT 0,    -- Çatımdaki adet
        fab_welding_qty INTEGER DEFAULT 0,  -- Kaynaktaki adet
        fab_cleaning_qty INTEGER DEFAULT 0, -- Temizlik/Taşlamadaki adet
        fab_done_qty INTEGER DEFAULT 0,     -- İmalatı Biten toplam adet
        
        -- Kalite Kontrol Aşamaları
        qa_pending_qty INTEGER DEFAULT 0,   -- Kalite kontrolde bekleyen adet
        qa_approved_qty INTEGER DEFAULT 0,  -- Kaliteden onay alan adet
        qa_rejected_qty INTEGER DEFAULT 0,  -- Kaliteden red/revizyon yiyen adet
        
        -- Boya & Yüzey İşlem Aşamaları
        paint_status TEXT DEFAULT 'BEKLIYOR', -- BEKLIYOR, KUMLAMADA, BOYADA, GALVANIZDE, TAMAMLANDI, BOYANMAYACAK
        paint_sandblast_qty INTEGER DEFAULT 0,
        paint_paint_qty INTEGER DEFAULT 0,
        paint_galv_qty INTEGER DEFAULT 0,
        paint_done_qty INTEGER DEFAULT 0,
        paint_ral TEXT,
        paint_dft TEXT,
        
        -- Sevkiyat
        shipped_qty INTEGER DEFAULT 0,
        
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
        UNIQUE(project_id, assembly_pos)
    )
    ''')

    # 5. ASSEMBLY_PARTS (MONTAJ ELEMAN / PARÇA İLİŞKİSİ) TABLOSU
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS assembly_parts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        assembly_id INTEGER,
        assembly_pos TEXT NOT NULL,
        part_pos TEXT NOT NULL,       -- Örn: p1, p2, pl1
        description TEXT,             -- Örn: Flanş Plakası, Gövde Profili
        profile_type TEXT,            -- Örn: PL 20mm, HEB 300
        quantity_per_assembly INTEGER DEFAULT 1,
        total_quantity INTEGER DEFAULT 1,
        length REAL DEFAULT 0,
        unit_weight REAL DEFAULT 0,
        total_weight REAL DEFAULT 0,
        material_grade TEXT DEFAULT 'S275JR',
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE CASCADE
    )
    ''')

    # 6. PARTS (TEK PARÇA / POZ LİSTESİ - SINGLE PARTS) TABLOSU
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS parts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        pos_no TEXT NOT NULL,
        name TEXT NOT NULL,
        profile_type TEXT,
        quantity INTEGER NOT NULL DEFAULT 1,
        cut_quantity INTEGER DEFAULT 0,
        remaining_quantity INTEGER DEFAULT 0,
        length REAL DEFAULT 0,
        unit_weight REAL NOT NULL DEFAULT 0,
        total_weight REAL NOT NULL DEFAULT 0,
        material_grade TEXT DEFAULT 'S275JR',
        
        -- Eski alanlar (uyumluluk için)
        fab_status TEXT DEFAULT 'BEKLIYOR',
        fab_date TEXT,
        fab_notes TEXT,
        paint_status TEXT DEFAULT 'BEKLIYOR',
        paint_ral TEXT,
        paint_dft TEXT,
        paint_date TEXT,
        paint_notes TEXT,
        shipment_id INTEGER,
        ship_status TEXT DEFAULT 'BEKLIYOR',
        ship_date TEXT,
        notes TEXT,
        
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    ''')

    cursor.execute("PRAGMA table_info(parts)")
    pt_cols = [c[1] for c in cursor.fetchall()]
    if 'cut_quantity' not in pt_cols: cursor.execute("ALTER TABLE parts ADD COLUMN cut_quantity INTEGER DEFAULT 0")
    if 'remaining_quantity' not in pt_cols: cursor.execute("ALTER TABLE parts ADD COLUMN remaining_quantity INTEGER DEFAULT 0")
    if 'length' not in pt_cols: cursor.execute("ALTER TABLE parts ADD COLUMN length REAL DEFAULT 0")

    # 7. SİPARİŞ VERİLEN MALZEMELER (ORDERED)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS material_orders_ordered (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        project_code TEXT,
        material TEXT NOT NULL,       -- Örn: HEA 200, Sac S275JR, Kutu Profil
        thickness REAL DEFAULT 0,     -- mm
        width REAL DEFAULT 0,         -- mm (Sac için)
        length REAL DEFAULT 0,        -- mm
        quantity INTEGER DEFAULT 1,
        weight REAL DEFAULT 0,        -- kg
        supplier TEXT,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
    )
    ''')

    # 8. SİPARİŞ GELEN MALZEMELER (RECEIVED)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS material_orders_received (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        project_code TEXT,
        material TEXT NOT NULL,
        thickness REAL DEFAULT 0,
        width REAL DEFAULT 0,
        length REAL DEFAULT 0,
        quantity INTEGER DEFAULT 1,
        weight REAL DEFAULT 0,
        received_date TEXT,
        waybill_no TEXT,             -- İrsaliye No
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
    )
    ''')

    # 9. MAKİNELER TABLOSU
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS machines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        type TEXT DEFAULT 'Kesim', -- Kesim, Kaynak, Boya, vb.
        is_active INTEGER DEFAULT 1
    )
    ''')

    cursor.execute("SELECT COUNT(*) as count FROM machines")
    if cursor.fetchone()['count'] == 0:
        default_machines = [
            ("Hol 1 - Plazma 1", "Kesim"),
            ("Hol 1 - Plazma 2", "Kesim"),
            ("Hol 2 - Lazer 1", "Kesim"),
            ("Hol 2 - Testere 1", "Kesim"),
            ("Hol 2 - Oksijen Kesim", "Kesim"),
            ("Hol 3 - Çatım & Kaynak 1", "İmalat"),
            ("Boyahane - Kumlama", "Yüzey İşlem")
        ]
        cursor.executemany("INSERT OR IGNORE INTO machines (name, type) VALUES (?, ?)", default_machines)

    # 10. ÖN İMALAT PLAN / MAKİNE TAKVİMİ (MACHINE PLANS)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS machine_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        machine_name TEXT NOT NULL,
        date TEXT NOT NULL,          -- YYYY-MM-DD
        project_id INTEGER,
        project_code TEXT,
        plan_type TEXT DEFAULT 'Planlanan', -- Planlanan, Uygulanan
        status TEXT DEFAULT 'Planlandı',   -- Planlandı, Tamamlandı, Durdu
        stoppage_reason TEXT,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(machine_name, date, plan_type)
    )
    ''')

    # 11. HIZLI KESİLENLER GİRİŞİ LOGLARI (CUTTING ENTRIES)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS cutting_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        project_code TEXT NOT NULL,
        pos_no TEXT NOT NULL,
        profile TEXT,
        cut_quantity INTEGER NOT NULL DEFAULT 1,
        cut_date TEXT NOT NULL,
        machine TEXT,
        operator TEXT,
        helper TEXT,
        shift TEXT DEFAULT 'Gündüz',
        user_id INTEGER,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
    )
    ''')

    # 12. KALİTE KONTROL HAVUZU (QA INSPECTIONS)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS qa_inspections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        assembly_id INTEGER,
        assembly_pos TEXT NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1,
        inspector_id INTEGER,
        inspector_name TEXT,
        inspection_date TEXT,
        status TEXT DEFAULT 'Bekliyor', -- Bekliyor, Onaylandı, Reddedildi
        defect_type TEXT,               -- Örn: Kaynak Hatası, Boyut Sapması, Çapak
        comments TEXT,
        certificate_no TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE CASCADE
    )
    ''')

    # 13. BOYA / YÜZEY İŞLEM KAYITLARI (PAINT RECORDS)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS paint_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        assembly_id INTEGER,
        assembly_pos TEXT NOT NULL,
        process_type TEXT NOT NULL, -- Kumlama, Boya, Galvaniz
        quantity INTEGER NOT NULL DEFAULT 1,
        ral_code TEXT,
        dft_micron TEXT,
        lot_no TEXT,
        completion_date TEXT,
        operator_name TEXT,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    ''')

    # 14. SEVKİYATLAR TABLOSU
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS shipments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        dispatch_no TEXT NOT NULL,
        vehicle_plate TEXT NOT NULL,
        driver_name TEXT,
        driver_phone TEXT,
        carrier_company TEXT,
        dispatch_date TEXT NOT NULL,
        destination TEXT,
        total_quantity INTEGER DEFAULT 0,
        total_tonnage REAL DEFAULT 0,
        notes TEXT,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
    )
    ''')

    # 15. SEVKİYAT KALEMLERİ (SHIPMENT ITEMS)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS shipment_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shipment_id INTEGER NOT NULL,
        assembly_id INTEGER,
        assembly_pos TEXT NOT NULL,
        description TEXT,
        quantity INTEGER NOT NULL DEFAULT 1,
        unit_weight REAL NOT NULL DEFAULT 0,
        total_weight REAL NOT NULL DEFAULT 0,
        FOREIGN KEY (shipment_id) REFERENCES shipments(id) ON DELETE CASCADE,
        FOREIGN KEY (assembly_id) REFERENCES assemblies(id) ON DELETE SET NULL
    )
    ''')

    # 16. SİSTEM AYARLARI
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS system_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_name TEXT DEFAULT 'ÇELİK VE METAL İMALAT SAN. TİC. LTD. ŞTİ.',
        company_sub_title TEXT DEFAULT 'Çelik Konstrüksiyon & Endüstriyel İmalat Tesisleri',
        company_address TEXT DEFAULT 'Organize Sanayi Bölgesi 12. Cadde No:34',
        company_phone TEXT DEFAULT '+90 (212) 555 01 23',
        company_email TEXT DEFAULT 'info@celikimalat.com',
        company_tax_info TEXT DEFAULT 'O.S.B. V.D. - 1234567890'
    )
    ''')

    cursor.execute('SELECT COUNT(*) as count FROM system_settings')
    if cursor.fetchone()['count'] == 0:
        cursor.execute('''
        INSERT INTO system_settings (company_name, company_sub_title, company_address, company_phone, company_email, company_tax_info)
        VALUES ('ÇELİK VE METAL İMALAT SAN. TİC. LTD. ŞTİ.', 'Çelik Konstrüksiyon & Endüstriyel İmalat Tesisleri', 'Organize Sanayi Bölgesi 12. Cadde No:34', '+90 (212) 555 01 23', 'info@celikimalat.com', 'O.S.B. V.D. - 1234567890')
        ''')

    # 17. OPERATÖRLER TABLOSU
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS operators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operator_name TEXT UNIQUE NOT NULL,
        role TEXT DEFAULT 'Operatör', -- Operatör, Yardımcı, Kaynakçı, Boyacı
        monthly_salary REAL DEFAULT 35000,
        monthly_hours REAL DEFAULT 180,
        is_active INTEGER DEFAULT 1
    )
    ''')

    cursor.execute("SELECT COUNT(*) as count FROM operators")
    if cursor.fetchone()['count'] == 0:
        default_ops = [
            ("Ahmet Yılmaz", "Operatör"),
            ("Mehmet Demir", "Operatör"),
            ("Mustafa Çelik", "Yardımcı"),
            ("Ali Kara", "Kaynakçı"),
            ("Hasan Şen", "Boyacı")
        ]
        cursor.executemany("INSERT OR IGNORE INTO operators (operator_name, role) VALUES (?, ?)", default_ops)

    # 18. VARSAYILAN KULLANICILARI OLUŞTUR
    cursor.execute("SELECT COUNT(*) as count FROM users WHERE username='admin'")
    if cursor.fetchone()['count'] == 0:
        cursor.execute('''
        INSERT INTO users (username, password_hash, full_name, role)
        VALUES ('admin', ?, 'Sistem Yöneticisi', 'Yönetici')
        ''', (hash_password("admin123"),))

    cursor.execute("SELECT COUNT(*) as count FROM users WHERE username='yozi'")
    if cursor.fetchone()['count'] == 0:
        cursor.execute('''
        INSERT INTO users (username, password_hash, full_name, role)
        VALUES ('yozi', ?, 'Yönetici (Yozi)', 'Yönetici')
        ''', (hash_password("2507"),))

    cursor.execute("SELECT COUNT(*) as count FROM users WHERE username='imalat'")
    if cursor.fetchone()['count'] == 0:
        cursor.execute('''
        INSERT INTO users (username, password_hash, full_name, role)
        VALUES ('imalat', ?, 'İmalat Sorumlusu', 'İmalat Şefi')
        ''', (hash_password("imalat123"),))

    cursor.execute("SELECT COUNT(*) as count FROM users WHERE username='kalite'")
    if cursor.fetchone()['count'] == 0:
        cursor.execute('''
        INSERT INTO users (username, password_hash, full_name, role)
        VALUES ('kalite', ?, 'Kalite Kontrol Uzmanı', 'Kalite Kontrolcü')
        ''', (hash_password("kalite123"),))

    cursor.execute("SELECT COUNT(*) as count FROM users WHERE username='boya'")
    if cursor.fetchone()['count'] == 0:
        cursor.execute('''
        INSERT INTO users (username, password_hash, full_name, role)
        VALUES ('boya', ?, 'Boyahane Şefi', 'Boyahane Sorumlusu')
        ''', (hash_password("boya123"),))

    cursor.execute("SELECT COUNT(*) as count FROM users WHERE username='sevk'")
    if cursor.fetchone()['count'] == 0:
        cursor.execute('''
        INSERT INTO users (username, password_hash, full_name, role)
        VALUES ('sevk', ?, 'Sevkiyat ve Lojistik Şefi', 'Sevkiyatçı')
        ''', (hash_password("sevk123"),))

    # İndeksler
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_assemblies_project ON assemblies(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_assembly_parts_project ON assembly_parts(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_parts_project ON parts(project_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cutting_project ON cutting_entries(project_code)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cutting_date ON cutting_entries(cut_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_plans_machine_date ON machine_plans(machine_name, date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_created ON activity_logs(created_at)")

    conn.commit()
    conn.close()

# ----------------- LOGGING & AUDIT HELPER -----------------

def log_activity(action, entity_type=None, entity_id=None, details=None, username="Sistem", user_id=None, ip_address="127.0.0.1"):
    """Tüm kullanıcı işlemlerini denetim günlüğüne (Audit Log) yazar."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO activity_logs (user_id, username, action, entity_type, entity_id, details, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, username, action, entity_type, entity_id, details, ip_address))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Log yazma hatası: {e}")

# ----------------- HESAPLAMA VE METRİK YARDIMCILARI -----------------

def get_global_metrics():
    """Tüm projelerin toplam tonaj, kesim, montaj/imalat, boya ve sevk ilerlemelerini hesaplar."""
    conn = get_db()
    cursor = conn.cursor()

    # 1. Projeler Sayısı
    cursor.execute("SELECT COUNT(*) as count FROM projects WHERE status = 'Aktif'")
    active_projects = cursor.fetchone()['count']

    # 2. Parça / Poz Tonajları ve Kesim Durumu
    cursor.execute('''
    SELECT 
        COUNT(id) as total_parts_count,
        COALESCE(SUM(quantity), 0) as total_parts_qty,
        COALESCE(SUM(cut_quantity), 0) as total_parts_cut_qty,
        COALESCE(SUM(total_weight), 0) / 1000.0 as total_parts_tonnage,
        COALESCE(SUM((unit_weight * cut_quantity)), 0) / 1000.0 as total_cut_tonnage
    FROM parts
    ''')
    parts_row = cursor.fetchone()
    parts_stats = dict(parts_row) if parts_row else {
        'total_parts_count': 0, 'total_parts_qty': 0, 'total_parts_cut_qty': 0,
        'total_parts_tonnage': 0.0, 'total_cut_tonnage': 0.0
    }

    # 3. Assembly (Montaj Elemanları) İlerleme Tonajları
    cursor.execute('''
    SELECT
        COUNT(id) as total_assemblies_count,
        COALESCE(SUM(quantity), 0) as total_assemblies_qty,
        COALESCE(SUM(total_weight), 0) / 1000.0 as total_assembly_tonnage,
        
        -- İmalat Aşamaları
        COALESCE(SUM((unit_weight * fab_fitup_qty)), 0) / 1000.0 as fitup_tonnage,
        COALESCE(SUM((unit_weight * fab_welding_qty)), 0) / 1000.0 as welding_tonnage,
        COALESCE(SUM((unit_weight * fab_cleaning_qty)), 0) / 1000.0 as cleaning_tonnage,
        COALESCE(SUM((unit_weight * fab_done_qty)), 0) / 1000.0 as fab_completed_tonnage,
        
        -- Kalite Kontrol
        COALESCE(SUM((unit_weight * qa_pending_qty)), 0) / 1000.0 as qa_pending_tonnage,
        COALESCE(SUM((unit_weight * qa_approved_qty)), 0) / 1000.0 as qa_approved_tonnage,
        
        -- Boya & Yüzey İşlem
        COALESCE(SUM((unit_weight * (paint_sandblast_qty + paint_paint_qty + paint_galv_qty))), 0) / 1000.0 as paint_in_progress_tonnage,
        COALESCE(SUM((unit_weight * paint_done_qty)), 0) / 1000.0 as paint_completed_tonnage,
        
        -- Sevkiyat
        COALESCE(SUM((unit_weight * shipped_qty)), 0) / 1000.0 as shipped_tonnage
    FROM assemblies
    ''')
    ass_row = cursor.fetchone()
    ass_stats = dict(ass_row) if ass_row else {
        'total_assemblies_count': 0, 'total_assemblies_qty': 0, 'total_assembly_tonnage': 0.0,
        'fitup_tonnage': 0.0, 'welding_tonnage': 0.0, 'cleaning_tonnage': 0.0, 'fab_completed_tonnage': 0.0,
        'qa_pending_tonnage': 0.0, 'qa_approved_tonnage': 0.0, 'paint_in_progress_tonnage': 0.0,
        'paint_completed_tonnage': 0.0, 'shipped_tonnage': 0.0
    }

    # 4. Malzeme Sipariş Tonajları
    cursor.execute("SELECT COALESCE(SUM(weight), 0) / 1000.0 as total_ordered_tonnage FROM material_orders_ordered")
    ordered_tonnage = cursor.fetchone()['total_ordered_tonnage'] or 0.0

    cursor.execute("SELECT COALESCE(SUM(weight), 0) / 1000.0 as total_received_tonnage FROM material_orders_received")
    received_tonnage = cursor.fetchone()['total_received_tonnage'] or 0.0

    cursor.execute("SELECT COUNT(*) as count FROM shipments")
    total_shipments = cursor.fetchone()['count']

    conn.close()

    # Eğer assembly tanımlıysa assembly tonajını, değilse parts tonajını baz al
    total_ton = ass_stats['total_assembly_tonnage'] if ass_stats['total_assembly_tonnage'] > 0 else parts_stats['total_parts_tonnage']
    tot = total_ton or 0.0001

    cut_ton = parts_stats['total_cut_tonnage']
    fab_ton = ass_stats['fab_completed_tonnage']
    paint_ton = ass_stats['paint_completed_tonnage']
    ship_ton = ass_stats['shipped_tonnage']
    stock_ton = max(0.0, fab_ton - ship_ton)

    return {
        'active_projects': active_projects,
        'total_tonnage': round(total_ton, 2),
        'cut_tonnage': round(cut_ton, 2),
        'fab_completed_tonnage': round(fab_ton, 2),
        'paint_completed_tonnage': round(paint_ton, 2),
        'shipped_tonnage': round(ship_ton, 2),
        'factory_stock_tonnage': round(stock_ton, 2),
        'pending_fab_tonnage': round(max(0.0, total_ton - fab_ton), 2),
        
        # Yüzdeler
        'cut_pct': round(min((cut_ton / tot) * 100, 100.0), 1),
        'fab_pct': round(min((fab_ton / tot) * 100, 100.0), 1),
        'paint_pct': round(min((paint_ton / tot) * 100, 100.0), 1),
        'ship_pct': round(min((ship_ton / tot) * 100, 100.0), 1),
        'stock_pct': round(min((stock_ton / tot) * 100, 100.0), 1),
        
        'ordered_tonnage': round(ordered_tonnage, 2),
        'received_tonnage': round(received_tonnage, 2),
        'remaining_order_tonnage': round(max(0.0, ordered_tonnage - received_tonnage), 2),
        'total_shipments': total_shipments,
        
        'total_parts_count': parts_stats['total_parts_count'],
        'total_parts_qty': parts_stats['total_parts_qty'],
        'total_assemblies_count': ass_stats['total_assemblies_count'],
        'total_assemblies_qty': ass_stats['total_assemblies_qty']
    }

def get_project_summary(project_id):
    """Belirli bir projenin tüm tonaj ve aşama detaylarını döndürür."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM projects WHERE id = ?', (project_id,))
    project = cursor.fetchone()
    if not project:
        conn.close()
        return None

    proj_dict = dict(project)

    # Parça Listesi İstatistiği
    cursor.execute('''
    SELECT 
        COUNT(id) as total_parts_count,
        COALESCE(SUM(quantity), 0) as total_parts_qty,
        COALESCE(SUM(cut_quantity), 0) as total_parts_cut_qty,
        COALESCE(SUM(total_weight), 0) / 1000.0 as total_parts_tonnage,
        COALESCE(SUM((unit_weight * cut_quantity)), 0) / 1000.0 as total_cut_tonnage
    FROM parts
    WHERE project_id = ?
    ''', (project_id,))
    p_row = cursor.fetchone()
    p_stats = dict(p_row) if p_row else {
        'total_parts_count': 0, 'total_parts_qty': 0, 'total_parts_cut_qty': 0,
        'total_parts_tonnage': 0.0, 'total_cut_tonnage': 0.0
    }

    # Assembly İstatistiği
    cursor.execute('''
    SELECT
        COUNT(id) as total_assemblies_count,
        COALESCE(SUM(quantity), 0) as total_assemblies_qty,
        COALESCE(SUM(total_weight), 0) / 1000.0 as total_assembly_tonnage,
        
        COALESCE(SUM((unit_weight * fab_fitup_qty)), 0) / 1000.0 as fitup_tonnage,
        COALESCE(SUM((unit_weight * fab_welding_qty)), 0) / 1000.0 as welding_tonnage,
        COALESCE(SUM((unit_weight * fab_cleaning_qty)), 0) / 1000.0 as cleaning_tonnage,
        COALESCE(SUM((unit_weight * fab_done_qty)), 0) / 1000.0 as fab_completed_tonnage,
        
        COALESCE(SUM((unit_weight * qa_pending_qty)), 0) / 1000.0 as qa_pending_tonnage,
        COALESCE(SUM((unit_weight * qa_approved_qty)), 0) / 1000.0 as qa_approved_tonnage,
        COALESCE(SUM((unit_weight * qa_rejected_qty)), 0) / 1000.0 as qa_rejected_tonnage,
        
        COALESCE(SUM((unit_weight * (paint_sandblast_qty + paint_paint_qty + paint_galv_qty))), 0) / 1000.0 as paint_in_progress_tonnage,
        COALESCE(SUM((unit_weight * paint_done_qty)), 0) / 1000.0 as paint_completed_tonnage,
        
        COALESCE(SUM((unit_weight * shipped_qty)), 0) / 1000.0 as shipped_tonnage
    FROM assemblies
    WHERE project_id = ?
    ''', (project_id,))
    a_row = cursor.fetchone()
    a_stats = dict(a_row) if a_row else {
        'total_assemblies_count': 0, 'total_assemblies_qty': 0, 'total_assembly_tonnage': 0.0,
        'fitup_tonnage': 0.0, 'welding_tonnage': 0.0, 'cleaning_tonnage': 0.0, 'fab_completed_tonnage': 0.0,
        'qa_pending_tonnage': 0.0, 'qa_approved_tonnage': 0.0, 'qa_rejected_tonnage': 0.0,
        'paint_in_progress_tonnage': 0.0, 'paint_completed_tonnage': 0.0, 'shipped_tonnage': 0.0
    }

    # Malzeme Sipariş İstatistiği
    cursor.execute("SELECT COALESCE(SUM(weight), 0) / 1000.0 FROM material_orders_ordered WHERE project_id = ?", (project_id,))
    sip_ver = cursor.fetchone()[0] or 0.0

    cursor.execute("SELECT COALESCE(SUM(weight), 0) / 1000.0 FROM material_orders_received WHERE project_id = ?", (project_id,))
    sip_gel = cursor.fetchone()[0] or 0.0

    conn.close()

    total_ton = a_stats['total_assembly_tonnage'] if a_stats['total_assembly_tonnage'] > 0 else (p_stats['total_parts_tonnage'] if p_stats['total_parts_tonnage'] > 0 else proj_dict.get('target_tonnage', 0.0))
    tot = total_ton or 0.0001

    cut_ton = p_stats['total_cut_tonnage']
    fab_ton = a_stats['fab_completed_tonnage']
    paint_ton = a_stats['paint_completed_tonnage']
    ship_ton = a_stats['shipped_tonnage']
    stock_ton = max(0.0, fab_ton - ship_ton)

    proj_dict.update({
        'total_tonnage': round(total_ton, 2),
        'cut_tonnage': round(cut_ton, 2),
        'fab_completed_tonnage': round(fab_ton, 2),
        'paint_completed_tonnage': round(paint_ton, 2),
        'shipped_tonnage': round(ship_ton, 2),
        'factory_stock_tonnage': round(stock_ton, 2),
        'pending_fab_tonnage': round(max(0.0, total_ton - fab_ton), 2),
        
        'cut_pct': round(min((cut_ton / tot) * 100, 100.0), 1),
        'fab_pct': round(min((fab_ton / tot) * 100, 100.0), 1),
        'paint_pct': round(min((paint_ton / tot) * 100, 100.0), 1),
        'ship_pct': round(min((ship_ton / tot) * 100, 100.0), 1),
        
        'ordered_tonnage': round(sip_ver, 2),
        'received_tonnage': round(sip_gel, 2),
        'material_pct': round(min((sip_gel / (sip_ver or 0.0001)) * 100, 100.0), 1) if sip_ver > 0 else 0.0,
        
        'total_parts_count': p_stats['total_parts_count'],
        'total_parts_qty': p_stats['total_parts_qty'],
        'total_parts_cut_qty': p_stats['total_parts_cut_qty'],
        'total_assemblies_count': a_stats['total_assemblies_count'],
        'total_assemblies_qty': a_stats['total_assemblies_qty'],
        
        'fitup_tonnage': round(a_stats['fitup_tonnage'], 2),
        'welding_tonnage': round(a_stats['welding_tonnage'], 2),
        'cleaning_tonnage': round(a_stats['cleaning_tonnage'], 2),
        'qa_pending_tonnage': round(a_stats['qa_pending_tonnage'], 2),
        'qa_approved_tonnage': round(a_stats['qa_approved_tonnage'], 2)
    })

    return proj_dict
