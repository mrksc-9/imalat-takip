import sqlite3
import os
import re
import hashlib
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'imalat_takip.db')
DATABASE_URL = os.environ.get('DATABASE_URL')

ROLES_LIST = [
    'admin',
    'patron',
    'genel müdür',
    'fabrika müdürü',
    'imalat müdürü',
    'imalat mühendisi',
    'iş hazırlama',
    'satınalma',
    'kalite mühendisi',
    'formen',
    'usta',
    'izleyici'
]

MODULES_LIST = [
    ('projeler', 'Proje & Tekla Listeleri'),
    ('siparis_takip', 'Sipariş Takip & Malzeme'),
    ('on_imalat_plan', 'Ön İmalat Plan (Makineler)'),
    ('kesim_takip', 'Kesim Girişi'),
    ('imalat_plan', 'İmalat Planı & Terminler'),
    ('imalat_takip', 'İmalat Takip (Çatım/Kaynak)'),
    ('kalite_kontrol', 'Kalite Kontrol (QA/QC)'),
    ('boya_takip', 'Boya & Yüzey İşlem'),
    ('sevk', 'Sevkiyat & İrsaliye'),
    ('muhasebe_irsaliye', 'Muhasebe & İrsaliye Takip'),
    ('kullanicilar', 'Kullanıcılar & Roller'),
    ('ayarlar', 'Firma Başlığı & Sistem Ayarları')
]

def hash_password(password):
    return hashlib.sha256(str(password).encode('utf-8')).hexdigest()

class PostgresRow:
    """PostgreSQL satırlarını hem sözlük key (row['col']) hem indis (row[0]) ile erişilebilir kılan sarmalayıcı."""
    def __init__(self, data_dict, tuple_vals):
        self._dict = data_dict
        self._tuple = tuple_vals
    def __getitem__(self, item):
        if isinstance(item, int):
            return self._tuple[item]
        return self._dict[item]
    def __contains__(self, key):
        return key in self._dict
    def get(self, key, default=None):
        return self._dict.get(key, default)
    def keys(self):
        return self._dict.keys()
    def values(self):
        return self._dict.values()
    def items(self):
        return self._dict.items()
    def __iter__(self):
        return iter(self._dict)
    def __len__(self):
        return len(self._dict)
    def __repr__(self):
        return repr(self._dict)

class PostgresCursorWrapper:
    def __init__(self, cursor):
        self._cur = cursor
        self.lastrowid = None

    def _convert_sql(self, sql):
        # 1. PRAGMA table_info(tbl) -> information_schema
        if sql.strip().upper().startswith("PRAGMA TABLE_INFO"):
            match = re.search(r"PRAGMA\s+TABLE_INFO\s*\(\s*['\"]?(\w+)['\"]?\s*\)", sql, re.IGNORECASE)
            if match:
                table_name = match.group(1).lower()
                return f"""
                SELECT 0 as cid, column_name as name, data_type as type, 0 as notnull, NULL as dflt_value, 0 as pk
                FROM information_schema.columns
                WHERE table_name = '{table_name}'
                """
        # 2. '?' yer tutucularını '%s' yap
        converted = sql.replace('?', '%s')
        # 3. INSERT OR IGNORE INTO -> INSERT INTO ... ON CONFLICT DO NOTHING
        if re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", converted, re.IGNORECASE):
            converted = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", converted, flags=re.IGNORECASE)
            if "ON CONFLICT" not in converted.upper():
                converted = converted.rstrip().rstrip(';') + " ON CONFLICT DO NOTHING"
        return converted

    def execute(self, sql, params=None):
        sql_conv = self._convert_sql(sql)
        is_insert = sql_conv.strip().upper().startswith("INSERT INTO")
        should_add_returning = is_insert and "RETURNING" not in sql_conv.upper() and "ON CONFLICT DO NOTHING" not in sql_conv.upper()
        if should_add_returning:
            sql_exec = sql_conv.rstrip().rstrip(';') + " RETURNING id"
        else:
            sql_exec = sql_conv

        try:
            if params is not None:
                if isinstance(params, (list, tuple)):
                    self._cur.execute(sql_exec, tuple(params))
                else:
                    self._cur.execute(sql_exec, params)
            else:
                self._cur.execute(sql_exec)

            if should_add_returning:
                try:
                    row = self._cur.fetchone()
                    if row:
                        self.lastrowid = row[0]
                except Exception:
                    pass
        except Exception as e:
            if should_add_returning:
                if params is not None:
                    self._cur.execute(sql_conv, tuple(params) if isinstance(params, (list, tuple)) else params)
                else:
                    self._cur.execute(sql_conv)
            else:
                raise e

        return self

    def executemany(self, sql, seq_of_params):
        sql_conv = self._convert_sql(sql)
        self._cur.executemany(sql_conv, seq_of_params)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        if hasattr(self._cur, 'description') and self._cur.description:
            colnames = [col[0] for col in self._cur.description]
            d = dict(zip(colnames, row))
            return PostgresRow(d, tuple(row))
        return row

    def fetchall(self):
        rows = self._cur.fetchall()
        if not rows:
            return []
        if hasattr(self._cur, 'description') and self._cur.description:
            colnames = [col[0] for col in self._cur.description]
            return [PostgresRow(dict(zip(colnames, r)), tuple(r)) for r in rows]
        return rows

    def __iter__(self):
        return iter(self.fetchall())

    def close(self):
        self._cur.close()

class PostgresConnectionWrapper:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return PostgresCursorWrapper(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

def is_postgres():
    return bool(os.environ.get('DATABASE_URL'))

def get_db():
    db_url = os.environ.get('DATABASE_URL')
    if db_url:
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        import psycopg2
        raw_conn = psycopg2.connect(db_url)
        return PostgresConnectionWrapper(raw_conn)
    else:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

def ensure_column(cursor, table, column, col_type_sql, default_val=None):
    """Hem PostgreSQL hem SQLite için tabloya eksik sütunu güvenle ekler."""
    use_pg = is_postgres()
    if use_pg:
        def_clause = f" DEFAULT {default_val}" if default_val is not None else ""
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type_sql}{def_clause}")
        except Exception as e:
            print(f"Postgres column migration note ({table}.{column}): {e}")
    else:
        try:
            cursor.execute(f"PRAGMA table_info({table})")
            cols = [c[1] for c in cursor.fetchall()]
            if column not in cols:
                def_clause = f" DEFAULT {default_val}" if default_val is not None else ""
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type_sql}{def_clause}")
        except Exception as e:
            print(f"SQLite column migration note ({table}.{column}): {e}")

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    use_pg = is_postgres()
    pk_type = "SERIAL PRIMARY KEY" if use_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"

    # 1. KULLANICILAR TABLOSU
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS users (
        id {pk_type},
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT DEFAULT 'izleyici',
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # 2. ROL VE MODÜL YETKİLERİ TABLOSU
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS role_permissions (
        id {pk_type},
        role TEXT NOT NULL,
        module TEXT NOT NULL,
        can_view INTEGER DEFAULT 1,
        can_edit INTEGER DEFAULT 0,
        UNIQUE(role, module)
    )
    ''')

    # Varsayılan Yetkileri Doldur
    for r in ROLES_LIST:
        for mod, _ in MODULES_LIST:
            is_admin_patron = r in ('admin', 'patron', 'genel müdür')
            can_e = 1 if is_admin_patron else 0
            if r == 'fabrika müdürü': can_e = 1
            elif r == 'imalat müdürü' and mod in ('projeler', 'kesim_takip', 'imalat_plan', 'imalat_takip', 'on_imalat_plan'): can_e = 1
            elif r == 'imalat mühendisi' and mod in ('kesim_takip', 'imalat_takip', 'imalat_plan'): can_e = 1
            elif r == 'iş hazırlama' and mod in ('projeler', 'kesim_takip', 'siparis_takip'): can_e = 1
            elif r == 'satınalma' and mod in ('siparis_takip',): can_e = 1
            elif r == 'kalite mühendisi' and mod in ('kalite_kontrol',): can_e = 1
            elif r == 'formen' and mod in ('kesim_takip', 'imalat_takip'): can_e = 1
            elif r == 'usta' and mod in ('kesim_takip',): can_e = 1

            cursor.execute('''
            INSERT OR IGNORE INTO role_permissions (role, module, can_view, can_edit)
            VALUES (?, ?, 1, ?)
            ''', (r, mod, can_e))

    # 3. AKTİVİTE / DENETİM GÜNLÜĞÜ (AUDIT LOG) TABLOSU
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS activity_logs (
        id {pk_type},
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

    # 4. PROJELER TABLOSU
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS projects (
        id {pk_type},
        code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        customer TEXT,
        site_location TEXT,
        start_date TEXT,
        cutting_start_date TEXT,
        fitup_start_date TEXT,
        fitup_end_date TEXT,
        welding_end_date TEXT,
        welding_cleaning_end_date TEXT,
        paint_end_date TEXT,
        delivery_date TEXT,
        target_tonnage REAL DEFAULT 0,
        color TEXT DEFAULT '#3b82f6',
        pos_prefix TEXT DEFAULT '',
        status TEXT DEFAULT 'Aktif',
        order_status TEXT DEFAULT 'Bekliyor',
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    ensure_column(cursor, "projects", "cutting_start_date", "TEXT", "''")
    ensure_column(cursor, "projects", "fitup_start_date", "TEXT", "''")
    ensure_column(cursor, "projects", "fitup_end_date", "TEXT", "''")
    ensure_column(cursor, "projects", "welding_end_date", "TEXT", "''")
    ensure_column(cursor, "projects", "welding_cleaning_end_date", "TEXT", "''")
    ensure_column(cursor, "projects", "paint_end_date", "TEXT", "''")
    ensure_column(cursor, "projects", "color", "TEXT", "'#3b82f6'")
    ensure_column(cursor, "projects", "pos_prefix", "TEXT", "''")
    ensure_column(cursor, "projects", "order_status", "TEXT", "'Bekliyor'")
    ensure_column(cursor, "projects", "status", "TEXT", "'Aktif'")

    # 5. ASSEMBLIES (MONTAJ / MARKA LİSTESİ) TABLOSU
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS assemblies (
        id {pk_type},
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        assembly_pos TEXT NOT NULL,
        description TEXT,
        profile_type TEXT,
        quantity INTEGER NOT NULL DEFAULT 1,
        unit_weight REAL NOT NULL DEFAULT 0,
        total_weight REAL NOT NULL DEFAULT 0,
        material_grade TEXT DEFAULT 'S275JR',
        fab_fitup_qty INTEGER DEFAULT 0,
        fab_welding_qty INTEGER DEFAULT 0,
        fab_cleaning_qty INTEGER DEFAULT 0,
        fab_done_qty INTEGER DEFAULT 0,
        qa_pending_qty INTEGER DEFAULT 0,
        qa_approved_qty INTEGER DEFAULT 0,
        qa_rejected_qty INTEGER DEFAULT 0,
        paint_status TEXT DEFAULT 'BEKLIYOR',
        paint_sandblast_qty INTEGER DEFAULT 0,
        paint_paint_qty INTEGER DEFAULT 0,
        paint_galv_qty INTEGER DEFAULT 0,
        paint_done_qty INTEGER DEFAULT 0,
        paint_ral TEXT,
        paint_dft TEXT,
        shipped_qty INTEGER DEFAULT 0,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(project_id, assembly_pos)
    )
    ''')

    # 6. ASSEMBLY_PARTS (MONTAJ ELEMAN / PARÇA İLİŞKİSİ) TABLOSU
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS assembly_parts (
        id {pk_type},
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        assembly_id INTEGER,
        assembly_pos TEXT NOT NULL,
        part_pos TEXT NOT NULL,
        description TEXT,
        profile_type TEXT,
        quantity_per_assembly INTEGER DEFAULT 1,
        total_quantity INTEGER DEFAULT 1,
        length REAL DEFAULT 0,
        unit_weight REAL DEFAULT 0,
        total_weight REAL DEFAULT 0,
        material_grade TEXT DEFAULT 'S275JR'
    )
    ''')

    # 7. PARTS (TEK PARÇA / POZ LİSTESİ) TABLOSU
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS parts (
        id {pk_type},
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
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
        fab_status TEXT DEFAULT 'BEKLIYOR',
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    ensure_column(cursor, "parts", "cut_quantity", "INTEGER", "0")
    ensure_column(cursor, "parts", "remaining_quantity", "INTEGER", "0")
    ensure_column(cursor, "parts", "length", "REAL", "0")
    ensure_column(cursor, "parts", "is_profile", "INTEGER", "0")
    ensure_column(cursor, "parts", "machine_type", "TEXT", "''")
    ensure_column(cursor, "parts", "operator_name", "TEXT", "''")
    ensure_column(cursor, "parts", "notes", "TEXT", "''")

    # 8. SİPARİŞ VERİLEN MALZEMELER (ORDERED)
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS material_orders_ordered (
        id {pk_type},
        project_id INTEGER,
        project_code TEXT,
        material TEXT NOT NULL,
        thickness REAL DEFAULT 0,
        width REAL DEFAULT 0,
        length REAL DEFAULT 0,
        quantity INTEGER DEFAULT 1,
        weight REAL DEFAULT 0,
        supplier TEXT,
        approval_status TEXT DEFAULT 'Onaylandı',
        approved_by TEXT DEFAULT 'Sistem',
        approved_date TEXT DEFAULT '',
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    ensure_column(cursor, "material_orders_ordered", "approval_status", "TEXT", "'Onaylandı'")
    ensure_column(cursor, "material_orders_ordered", "approved_by", "TEXT", "'Sistem'")
    ensure_column(cursor, "material_orders_ordered", "approved_date", "TEXT", "''")

    # 9. SİPARİŞ GELEN MALZEMELER (RECEIVED)
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS material_orders_received (
        id {pk_type},
        project_id INTEGER,
        project_code TEXT,
        material TEXT NOT NULL,
        thickness REAL DEFAULT 0,
        width REAL DEFAULT 0,
        length REAL DEFAULT 0,
        quantity INTEGER DEFAULT 1,
        weight REAL DEFAULT 0,
        received_date TEXT,
        waybill_no TEXT,
        notes TEXT,
        invoice_photo_url TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    ensure_column(cursor, "material_orders_received", "invoice_photo_url", "TEXT", "''")

    # 10. MAKİNELER TABLOSU
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS machines (
        id {pk_type},
        name TEXT UNIQUE NOT NULL,
        code TEXT DEFAULT '',
        type TEXT DEFAULT 'Kesim',
        capacity_ton_day REAL DEFAULT 10.0,
        hourly_rate REAL DEFAULT 0.0,
        operator_name TEXT DEFAULT '',
        status TEXT DEFAULT 'Aktif',
        notes TEXT DEFAULT '',
        is_active INTEGER DEFAULT 1
    )
    ''')

    ensure_column(cursor, "machines", "code", "TEXT", "''")
    ensure_column(cursor, "machines", "type", "TEXT", "'Kesim'")
    ensure_column(cursor, "machines", "capacity_ton_day", "REAL", "10.0")
    ensure_column(cursor, "machines", "hourly_rate", "REAL", "0.0")
    ensure_column(cursor, "machines", "operator_name", "TEXT", "''")
    ensure_column(cursor, "machines", "status", "TEXT", "'Aktif'")
    ensure_column(cursor, "machines", "notes", "TEXT", "''")
    ensure_column(cursor, "machines", "is_active", "INTEGER", "1")

    cursor.execute("SELECT COUNT(*) as count FROM machines")
    if cursor.fetchone()['count'] == 0:
        default_machines = [
            ("Ajan 260A Plazma", "PLZ-1", "Plazma", 12.0, 0, "Ali Kesici", "Aktif"),
            ("Ermaksan 4kW Sac Lazer", "LZR-1", "Sac Lazer", 15.0, 0, "Mehmet Lazer", "Aktif"),
            ("Bystronic 10kW Profil Lazer", "PLZR-1", "Profil Lazer", 18.0, 0, "Hakan Profil", "Aktif"),
            ("Kasto Şerit Testere 1", "TST-1", "Bant Testere", 8.0, 0, "Mustafa Testere", "Aktif"),
            ("Messer Oksijen Kesim", "OKS-1", "Oksijen Kesim", 10.0, 0, "Kemal Alev", "Aktif"),
            ("Hol 3 - Çatım & Kaynak 1", "CTM-1", "Çatım & Kaynak", 20.0, 0, "Ahmet Usta", "Aktif"),
            ("Boyahane - Kumlama & Boya", "BYA-1", "Yüzey İşlem", 25.0, 0, "Hasan Boyacı", "Aktif")
        ]
        cursor.executemany("INSERT OR IGNORE INTO machines (name, code, type, capacity_ton_day, hourly_rate, operator_name, status) VALUES (?, ?, ?, ?, ?, ?, ?)", default_machines)

    # 11. ÖN İMALAT PLAN / MAKİNE TAKVİMİ
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS machine_plans (
        id {pk_type},
        machine_name TEXT NOT NULL,
        date TEXT NOT NULL,
        project_id INTEGER,
        project_code TEXT,
        plan_type TEXT DEFAULT 'Planlanan',
        status TEXT DEFAULT 'Planlandı',
        stoppage_reason TEXT,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(machine_name, date, plan_type)
    )
    ''')

    # 12. HIZLI KESİLENLER GİRİŞİ LOGLARI
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS cutting_entries (
        id {pk_type},
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # 13. KALİTE KONTROL HAVUZU (QA INSPECTIONS)
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS qa_inspections (
        id {pk_type},
        project_id INTEGER NOT NULL,
        assembly_id INTEGER,
        assembly_pos TEXT NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1,
        inspector_id INTEGER,
        inspector_name TEXT,
        inspection_date TEXT,
        status TEXT DEFAULT 'Bekliyor',
        defect_type TEXT,
        comments TEXT,
        workstation TEXT,
        photo_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    ensure_column(cursor, "qa_inspections", "workstation", "TEXT", "''")
    ensure_column(cursor, "qa_inspections", "photo_url", "TEXT", "''")

    # 14. BOYA / YÜZEY İŞLEM KAYITLARI
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS paint_records (
        id {pk_type},
        project_id INTEGER NOT NULL,
        assembly_id INTEGER,
        assembly_pos TEXT NOT NULL,
        process_type TEXT NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1,
        ral_code TEXT,
        dft_micron TEXT,
        lot_no TEXT,
        completion_date TEXT,
        operator_name TEXT,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # 15. SEVKİYATLAR TABLOSU
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS shipments (
        id {pk_type},
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
        accounting_status TEXT DEFAULT 'Bekliyor',
        accounting_processed_by TEXT,
        accounting_processed_at TIMESTAMP,
        accounting_invoice_no TEXT,
        accounting_notes TEXT
    )
    ''')

    ensure_column(cursor, "shipments", "accounting_status", "TEXT", "'Bekliyor'")
    ensure_column(cursor, "shipments", "accounting_processed_by", "TEXT", "NULL")
    ensure_column(cursor, "shipments", "accounting_processed_at", "TIMESTAMP", "NULL")
    ensure_column(cursor, "shipments", "accounting_invoice_no", "TEXT", "''")
    ensure_column(cursor, "shipments", "accounting_notes", "TEXT", "''")

    # 16. SEVKİYAT KALEMLERİ
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS shipment_items (
        id {pk_type},
        shipment_id INTEGER NOT NULL,
        assembly_id INTEGER,
        assembly_pos TEXT NOT NULL,
        description TEXT,
        quantity INTEGER NOT NULL DEFAULT 1,
        unit_weight REAL NOT NULL DEFAULT 0,
        total_weight REAL NOT NULL DEFAULT 0
    )
    ''')

    # 17. SİSTEM AYARLARI
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS system_settings (
        id {pk_type},
        app_title TEXT DEFAULT 'ORDUMAK ÇELİK İMALAT MES',
        app_subtitle TEXT DEFAULT 'İmalat, Montaj, Boya ve Sevkiyat Takip Sistemi',
        company_name TEXT DEFAULT 'ORDUMAK ÇELİK VE METAL İMALAT SAN. TİC. A.Ş.',
        company_sub_title TEXT DEFAULT 'Endüstriyel Çelik Konstrüksiyon & İmalat Tesisleri',
        company_address TEXT DEFAULT 'Dilovası İMES Organize Sanayi Bölgesi Kocaeli',
        company_phone TEXT DEFAULT '+90 (262) 555 01 23',
        company_email TEXT DEFAULT 'info@ordumak.com',
        company_tax_info TEXT DEFAULT 'Dilovası V.D. - 1234567890',
        company_logo_url TEXT DEFAULT '/static/img/ordumak_logo.png',
        company_website_url TEXT DEFAULT 'https://ordumak.com'
    )
    ''')

    ensure_column(cursor, "system_settings", "app_title", "TEXT", "'ORDUMAK ÇELİK İMALAT MES'")
    ensure_column(cursor, "system_settings", "app_subtitle", "TEXT", "'İmalat, Montaj, Boya ve Sevkiyat Takip Sistemi'")
    ensure_column(cursor, "system_settings", "company_logo_url", "TEXT", "'/static/img/ordumak_logo.png'")
    ensure_column(cursor, "system_settings", "company_website_url", "TEXT", "'https://ordumak.com'")

    cursor.execute('SELECT COUNT(*) as count FROM system_settings')
    if cursor.fetchone()['count'] == 0:
        cursor.execute('''
        INSERT INTO system_settings (app_title, app_subtitle, company_name, company_sub_title, company_address, company_phone, company_email, company_tax_info, company_logo_url, company_website_url)
        VALUES ('ORDUMAK ÇELİK İMALAT MES', 'İmalat, Montaj, Boya ve Sevkiyat Takip Sistemi', 'ORDUMAK ÇELİK VE METAL İMALAT SAN. TİC. A.Ş.', 'Endüstriyel Çelik Konstrüksiyon & İmalat Tesisleri', 'Dilovası İMES Organize Sanayi Bölgesi Kocaeli', '+90 (262) 555 01 23', 'info@ordumak.com', 'Dilovası V.D. - 1234567890', '/static/img/ordumak_logo.png', 'https://ordumak.com')
        ''')

    # 18. OPERATÖRLER TABLOSU
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS operators (
        id {pk_type},
        operator_name TEXT UNIQUE NOT NULL,
        role TEXT DEFAULT 'Operatör',
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

    # 19. VARSAYILAN KULLANICILARI OLUŞTUR
    cursor.execute("SELECT COUNT(*) as count FROM users WHERE username='admin'")
    if cursor.fetchone()['count'] == 0:
        cursor.execute('''
        INSERT INTO users (username, password_hash, full_name, role)
        VALUES ('admin', ?, 'Sistem Yöneticisi', 'admin')
        ''', (hash_password("admin123"),))

    # 20. CANLI SOHBET MESAJLARI TABLOSU
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS chat_messages (
        id {pk_type},
        channel TEXT NOT NULL DEFAULT 'genel',
        user_id INTEGER,
        username TEXT NOT NULL,
        full_name TEXT NOT NULL,
        message TEXT,
        photo_url TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # 21. TOPLANTILAR VE KARARLAR TABLOSU
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS meetings (
        id {pk_type},
        project_id INTEGER,
        title TEXT NOT NULL,
        meeting_date TEXT NOT NULL,
        meeting_time TEXT,
        location TEXT,
        organizer TEXT,
        attendees TEXT,
        summary TEXT,
        decisions TEXT,
        status TEXT NOT NULL DEFAULT 'Tamamlandı',
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # 22. TOPLANTI AKSİYON VE SORUMLULUK MADDELERİ
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS meeting_action_items (
        id {pk_type},
        meeting_id INTEGER NOT NULL,
        description TEXT NOT NULL,
        responsible_person TEXT,
        due_date TEXT,
        status TEXT NOT NULL DEFAULT 'Devam Ediyor',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # 23. SİSTEM VE ŞANTİYE DUYURULARI TABLOSU
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS announcements (
        id {pk_type},
        user_id INTEGER,
        username TEXT NOT NULL,
        full_name TEXT NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        priority TEXT NOT NULL DEFAULT 'Önemli',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # 24. CANLI BİLDİRİMLER TABLOSU (TOAST / PUSH)
    cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS notifications (
        id {pk_type},
        category TEXT NOT NULL,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        icon TEXT DEFAULT 'fa-bell',
        color TEXT DEFAULT 'blue',
        link_url TEXT DEFAULT '',
        user_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    conn.commit()
    conn.close()


def get_system_settings():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM system_settings LIMIT 1')
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else {
        'app_title': 'ORDUMAK ÇELİK İMALAT MES',
        'app_subtitle': 'İmalat, Montaj, Boya ve Sevkiyat Takip Sistemi',
        'company_name': 'ORDUMAK ÇELİK VE METAL İMALAT SAN. TİC. A.Ş.',
        'company_logo_url': '/static/img/ordumak_logo.png',
        'company_website_url': 'https://ordumak.com'
    }

def get_next_dispatch_no(project_id):
    """Proje bazlı sıralı sevkiyat numarası üretir (SEVK-1, SEVK-2, SEVK-3...)."""
    if not project_id:
        return "SEVK-1"
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM shipments WHERE project_id = ?", (project_id,))
    count = cursor.fetchone()['count'] or 0
    conn.close()
    return f"SEVK-{count + 1}"

def log_activity(action, entity_type=None, entity_id=None, details="", username="Sistem", user_id=None, ip_address=None):
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
        print(f"Log error: {e}")

def can_user_edit(user_role, module_name):
    """Kullanıcının belirtilen modülde düzenleme yetkisi var mı kontrol eder."""
    if not user_role:
        return False
    role_lower = str(user_role).lower().strip()
    if role_lower in ('admin', 'patron'):
        return True
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT can_edit FROM role_permissions WHERE role = ? AND module = ?", (role_lower, module_name))
    row = cursor.fetchone()
    conn.close()
    if row and row['can_edit'] == 1:
        return True
    return False

def get_customers():
    """Sistemdeki tüm kayıtlı müşterileri listeler."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT customer FROM projects WHERE customer IS NOT NULL AND customer != '' AND status != 'Arşiv' ORDER BY customer ASC")
    rows = cursor.fetchall()
    conn.close()
    return [r['customer'] for r in rows if r['customer']]

def set_project_status(project_id, status):
    """Projenin durumunu günceller (Aktif, Tamamlandı, Arşiv)."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE projects SET status = ? WHERE id = ?", (status, project_id))
    conn.commit()
    conn.close()

def get_global_metrics(customer=None):
    """Tüm projelerin (veya seçilen müşterinin) toplam metriklerini hesaplar."""
    init_db()
    conn = get_db()
    cursor = conn.cursor()

    cust_filter_proj = ""
    cust_params = []
    if customer and customer.strip():
        cust_filter_proj = " AND customer = ?"
        cust_params = [customer.strip()]

    cursor.execute(f"SELECT COUNT(*) as count FROM projects WHERE status != 'Arşiv'{cust_filter_proj}", cust_params)
    active_projects = cursor.fetchone()['count']

    # Proje ID listesi
    if customer and customer.strip():
        cursor.execute("SELECT id FROM projects WHERE status != 'Arşiv' AND customer = ?", (customer.strip(),))
        p_ids = [r['id'] for r in cursor.fetchall()]
        if not p_ids:
            p_ids = [-1]
        placeholders = ','.join(['?'] * len(p_ids))
        
        cursor.execute(f'''
        SELECT 
            COUNT(id) as total_parts_count,
            COALESCE(SUM(quantity), 0) as total_parts_qty,
            COALESCE(SUM(cut_quantity), 0) as total_parts_cut_qty,
            COALESCE(SUM(total_weight), 0) / 1000.0 as total_parts_tonnage,
            COALESCE(SUM((unit_weight * cut_quantity)), 0) / 1000.0 as total_cut_tonnage
        FROM parts WHERE project_id IN ({placeholders})
        ''', p_ids)
        parts_row = cursor.fetchone()

        cursor.execute(f'''
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
            COALESCE(SUM((unit_weight * (paint_sandblast_qty + paint_paint_qty + paint_galv_qty))), 0) / 1000.0 as paint_in_progress_tonnage,
            COALESCE(SUM((unit_weight * paint_done_qty)), 0) / 1000.0 as paint_completed_tonnage,
            COALESCE(SUM((unit_weight * shipped_qty)), 0) / 1000.0 as shipped_tonnage
        FROM assemblies WHERE project_id IN ({placeholders})
        ''', p_ids)
        ass_row = cursor.fetchone()

        cursor.execute(f"SELECT COALESCE(SUM(weight), 0) / 1000.0 as total_ordered_tonnage FROM material_orders_ordered WHERE project_id IN ({placeholders})", p_ids)
        ordered_tonnage = cursor.fetchone()['total_ordered_tonnage'] or 0.0

        cursor.execute(f"SELECT COALESCE(SUM(weight), 0) / 1000.0 as total_received_tonnage FROM material_orders_received WHERE project_id IN ({placeholders})", p_ids)
        received_tonnage = cursor.fetchone()['total_received_tonnage'] or 0.0

        cursor.execute(f"SELECT COUNT(*) as count FROM shipments WHERE project_id IN ({placeholders})", p_ids)
        total_shipments = cursor.fetchone()['count']
    else:
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
            COALESCE(SUM((unit_weight * (paint_sandblast_qty + paint_paint_qty + paint_galv_qty))), 0) / 1000.0 as paint_in_progress_tonnage,
            COALESCE(SUM((unit_weight * paint_done_qty)), 0) / 1000.0 as paint_completed_tonnage,
            COALESCE(SUM((unit_weight * shipped_qty)), 0) / 1000.0 as shipped_tonnage
        FROM assemblies
        ''')
        ass_row = cursor.fetchone()

        cursor.execute("SELECT COALESCE(SUM(weight), 0) / 1000.0 as total_ordered_tonnage FROM material_orders_ordered")
        ordered_tonnage = cursor.fetchone()['total_ordered_tonnage'] or 0.0

        cursor.execute("SELECT COALESCE(SUM(weight), 0) / 1000.0 as total_received_tonnage FROM material_orders_received")
        received_tonnage = cursor.fetchone()['total_received_tonnage'] or 0.0

        cursor.execute("SELECT COUNT(*) as count FROM shipments")
        total_shipments = cursor.fetchone()['count']

    conn.close()

    parts_stats = dict(parts_row) if parts_row else {
        'total_parts_count': 0, 'total_parts_qty': 0, 'total_parts_cut_qty': 0,
        'total_parts_tonnage': 0.0, 'total_cut_tonnage': 0.0
    }
    ass_stats = dict(ass_row) if ass_row else {
        'total_assemblies_count': 0, 'total_assemblies_qty': 0, 'total_assembly_tonnage': 0.0,
        'fitup_tonnage': 0.0, 'welding_tonnage': 0.0, 'cleaning_tonnage': 0.0, 'fab_completed_tonnage': 0.0,
        'qa_pending_tonnage': 0.0, 'qa_approved_tonnage': 0.0, 'paint_in_progress_tonnage': 0.0,
        'paint_completed_tonnage': 0.0, 'shipped_tonnage': 0.0
    }

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
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM projects WHERE id = ?', (project_id,))
    project = cursor.fetchone()
    if not project:
        conn.close()
        return None

    proj_dict = dict(project)

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

def get_machines(active_only=False):
    conn = get_db()
    cursor = conn.cursor()
    if active_only:
        cursor.execute("SELECT * FROM machines WHERE is_active = 1 ORDER BY type, name")
    else:
        cursor.execute("SELECT * FROM machines ORDER BY type, name")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_machine_by_id(machine_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM machines WHERE id = ?", (machine_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def add_machine(name, m_type='Kesim', code='', capacity_ton_day=10.0, hourly_rate=0.0, operator_name='', status='Aktif', notes=''):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO machines (name, code, type, capacity_ton_day, hourly_rate, operator_name, status, notes, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
    """, (name.strip(), code.strip(), m_type.strip(), float(capacity_ton_day or 10.0), float(hourly_rate or 0.0), operator_name.strip(), status.strip(), notes.strip()))
    m_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return m_id

def update_machine(machine_id, name, m_type='Kesim', code='', capacity_ton_day=10.0, hourly_rate=0.0, operator_name='', status='Aktif', notes='', is_active=1):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE machines
        SET name = ?, code = ?, type = ?, capacity_ton_day = ?, hourly_rate = ?, operator_name = ?, status = ?, notes = ?, is_active = ?
        WHERE id = ?
    """, (name.strip(), code.strip(), m_type.strip(), float(capacity_ton_day or 10.0), float(hourly_rate or 0.0), operator_name.strip(), status.strip(), notes.strip(), int(is_active), machine_id))
    conn.commit()
    conn.close()

def delete_machine(machine_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM machines WHERE id = ?", (machine_id,))
    conn.commit()
    conn.close()

def get_all_role_permissions():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT role, module, can_view, can_edit FROM role_permissions ORDER BY role, module")
    rows = cursor.fetchall()
    conn.close()
    # Organize as dict: {role: {module: can_edit}}
    perms = {}
    for r in rows:
        role = r['role']
        if role not in perms:
            perms[role] = {}
        perms[role][r['module']] = r['can_edit']
    return perms

def update_role_permission(role, module, can_edit):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO role_permissions (role, module, can_view, can_edit)
    VALUES (?, ?, 1, ?)
    ON CONFLICT(role, module) DO UPDATE SET can_edit = excluded.can_edit
    ''', (role, module, can_edit))
    conn.commit()
    conn.close()

def update_system_settings(app_title, app_subtitle, company_name=None, company_logo_url=None, company_website_url=None):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM system_settings LIMIT 1")
    row = cursor.fetchone()
    if row:
        cursor.execute('''
        UPDATE system_settings
        SET app_title = COALESCE(?, app_title),
            app_subtitle = COALESCE(?, app_subtitle),
            company_name = COALESCE(?, company_name),
            company_logo_url = COALESCE(?, company_logo_url),
            company_website_url = COALESCE(?, company_website_url)
        WHERE id = ?
        ''', (app_title, app_subtitle, company_name, company_logo_url, company_website_url, row['id']))
    conn.commit()
    conn.close()

def get_user_by_id(user_id):
    """Kullanıcı bilgilerini ID ile getirir."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, full_name, role, is_active, created_at FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_user_profile(user_id, username, full_name, new_password=None, role=None):
    """Kullanıcının kendi profil bilgilerini veya şifresini günceller."""
    conn = get_db()
    cursor = conn.cursor()
    if new_password and str(new_password).strip():
        pw_hash = hash_password(str(new_password).strip())
        if role and str(role).strip():
            cursor.execute("UPDATE users SET username = ?, full_name = ?, password_hash = ?, role = ? WHERE id = ?",
                           (username.strip(), full_name.strip(), pw_hash, role.strip(), user_id))
        else:
            cursor.execute("UPDATE users SET username = ?, full_name = ?, password_hash = ? WHERE id = ?",
                           (username.strip(), full_name.strip(), pw_hash, user_id))
    else:
        if role and str(role).strip():
            cursor.execute("UPDATE users SET username = ?, full_name = ?, role = ? WHERE id = ?",
                           (username.strip(), full_name.strip(), role.strip(), user_id))
        else:
            cursor.execute("UPDATE users SET username = ?, full_name = ? WHERE id = ?",
                           (username.strip(), full_name.strip(), user_id))
    conn.commit()
    conn.close()

def approve_material_order(order_id, user_name="Satınalma"):
    """Malzeme sipariş talebini onaylar."""
    conn = get_db()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("UPDATE material_orders_ordered SET approval_status = 'Onaylandı', approved_by = ?, approved_date = ? WHERE id = ?",
                   (user_name, now_str, order_id))
    conn.commit()
    conn.close()

def reject_material_order(order_id, user_name="Satınalma"):
    """Malzeme sipariş talebini reddeder."""
    conn = get_db()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("UPDATE material_orders_ordered SET approval_status = 'Reddedildi', approved_by = ?, approved_date = ? WHERE id = ?",
                   (user_name, now_str, order_id))
    conn.commit()
    conn.close()

def get_cutting_progress_analysis(project_id=None):
    """Projelerin Plaka ve Profil bazında kesim tamamlama oranlarını hesaplar."""
    conn = get_db()
    cursor = conn.cursor()

    if project_id:
        cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        projects = cursor.fetchall()
    else:
        cursor.execute("SELECT * FROM projects WHERE status != 'Arşiv' ORDER BY created_at DESC")
        projects = cursor.fetchall()

    analysis_data = []

    for p in projects:
        pid = p['id']
        cursor.execute("SELECT * FROM parts WHERE project_id = ?", (pid,))
        parts = [dict(r) for r in cursor.fetchall()]

        total_parts_count = len(parts)
        total_tonnage = 0.0
        total_cut_tonnage = 0.0

        plate_total_qty = 0
        plate_cut_qty = 0
        plate_total_kg = 0.0
        plate_cut_kg = 0.0

        profile_total_qty = 0
        profile_cut_qty = 0
        profile_total_kg = 0.0
        profile_cut_kg = 0.0

        # Poz sözlüğü (hızlı erişim için)
        parts_map = {pt['pos_no']: pt for pt in parts}

        # Makine loglarından gelen kesimleri analiz et
        cursor.execute("SELECT pos_no, cut_quantity, machine FROM cutting_entries WHERE project_id = ?", (pid,))
        cut_logs = [dict(r) for r in cursor.fetchall()]

        has_cutting_logs = len(cut_logs) > 0
        plate_log_cut_qty = 0
        plate_log_cut_kg = 0.0
        profile_log_cut_qty = 0
        profile_log_cut_kg = 0.0

        if has_cutting_logs:
            for clog in cut_logs:
                cpos = clog['pos_no']
                cqty = clog['cut_quantity'] or 0
                mach = str(clog['machine'] or '').lower()
                pt_match = parts_map.get(cpos)
                u_w = pt_match['unit_weight'] if pt_match and pt_match.get('unit_weight') else 0.0
                cut_w = cqty * u_w

                if 'plazma' in mach or 'sac lazer' in mach or 'oksijen' in mach or ('lazer' in mach and 'profil' not in mach):
                    plate_log_cut_qty += cqty
                    plate_log_cut_kg += cut_w
                elif 'profil' in mach or 'testere' in mach or 'boru' in mach:
                    profile_log_cut_qty += cqty
                    profile_log_cut_kg += cut_w
                else:
                    # Makine tipi belli değilse parçanın profil tipine bak
                    p_type = str(pt_match['profile_type'] if pt_match else '').upper()
                    if any(p_type.startswith(x) for x in ['PL', 'SAC', 'FLANŞ', 'GUSE', 'BERKİTME']) or 'PL' in p_type:
                        plate_log_cut_qty += cqty
                        plate_log_cut_kg += cut_w
                    else:
                        profile_log_cut_qty += cqty
                        profile_log_cut_kg += cut_w

        parts_breakdown = []

        for pt in parts:
            p_type = str(pt['profile_type'] or '').strip().upper()
            qty = pt['quantity'] or 0
            cut_qty = pt['cut_quantity'] or 0
            u_weight = pt['unit_weight'] or 0.0
            tot_weight = pt['total_weight'] or (qty * u_weight)
            cut_weight = cut_qty * u_weight

            total_tonnage += tot_weight / 1000.0
            total_cut_tonnage += cut_weight / 1000.0

            # Plaka vs Profil Ayrımı (Hedef / Toplam hesaplama için)
            is_plate = any(p_type.startswith(x) for x in ['PL', 'SAC', 'FLANŞ', 'GUSE', 'BERKİTME']) or 'PL' in p_type

            if is_plate:
                category = "Plaka / Sac"
                plate_total_qty += qty
                plate_total_kg += tot_weight
                if not has_cutting_logs:
                    plate_cut_qty += cut_qty
                    plate_cut_kg += cut_weight
            else:
                category = "Profil / Çelik"
                profile_total_qty += qty
                profile_total_kg += tot_weight
                if not has_cutting_logs:
                    profile_cut_qty += cut_qty
                    profile_cut_kg += cut_weight

            parts_breakdown.append({
                'id': pt['id'],
                'pos_no': pt['pos_no'],
                'name': pt['name'],
                'profile_type': pt['profile_type'],
                'category': category,
                'quantity': qty,
                'cut_quantity': cut_qty,
                'remaining_quantity': max(0, qty - cut_qty),
                'unit_weight': u_weight,
                'total_weight': round(tot_weight, 1),
                'cut_weight': round(cut_weight, 1),
                'cut_pct': round((cut_qty / qty) * 100, 1) if qty > 0 else 0.0,
                'material_grade': pt['material_grade']
            })

        if has_cutting_logs:
            plate_cut_qty = plate_log_cut_qty
            plate_cut_kg = plate_log_cut_kg
            profile_cut_qty = profile_log_cut_qty
            profile_cut_kg = profile_log_cut_kg
            total_cut_tonnage = (plate_cut_kg + profile_cut_kg) / 1000.0

        plate_pct = round((plate_cut_kg / plate_total_kg) * 100, 1) if plate_total_kg > 0 else 0.0
        profile_pct = round((profile_cut_kg / profile_total_kg) * 100, 1) if profile_total_kg > 0 else 0.0
        overall_pct = round((total_cut_tonnage / (total_tonnage or 0.0001)) * 100, 1) if total_tonnage > 0 else 0.0

        analysis_data.append({
            'project': dict(p),
            'total_parts_count': total_parts_count,
            'total_tonnage': round(total_tonnage, 2),
            'total_cut_tonnage': round(total_cut_tonnage, 2),
            'overall_cut_pct': min(100.0, overall_pct),
            'plate': {
                'total_qty': plate_total_qty,
                'cut_qty': plate_cut_qty,
                'remaining_qty': max(0, plate_total_qty - plate_cut_qty),
                'total_ton': round(plate_total_kg / 1000.0, 2),
                'cut_ton': round(plate_cut_kg / 1000.0, 2),
                'cut_pct': min(100.0, plate_pct)
            },
            'profile': {
                'total_qty': profile_total_qty,
                'cut_qty': profile_cut_qty,
                'remaining_qty': max(0, profile_total_qty - profile_cut_qty),
                'total_ton': round(profile_total_kg / 1000.0, 2),
                'cut_ton': round(profile_cut_kg / 1000.0, 2),
                'cut_pct': min(100.0, profile_pct)
            },
            'parts_breakdown': parts_breakdown
        })

    conn.close()
    return analysis_data


# =========================================================================
# CANLI BİLDİRİMLER SİSTEMİ (TOAST & PUSH)
# =========================================================================
def add_notification(category, title, message, icon='fa-bell', color='blue', link_url='', user_id=None):
    """Sistem geneline veya kullanıcıya canlı işlem bildirimi ekler."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO notifications (category, title, message, icon, color, link_url, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (category, title.strip(), message.strip(), icon, color, link_url or '', user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Notification insert error: {e}")

def get_recent_notifications(since_id=0, limit=15):
    """En son bildirimleri JSON formatı için döner."""
    conn = get_db()
    cursor = conn.cursor()
    if since_id and int(since_id) > 0:
        cursor.execute("SELECT * FROM notifications WHERE id > ? ORDER BY id DESC LIMIT ?", (int(since_id), limit))
    else:
        cursor.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


# =========================================================================
# KULLANICI YÖNETİMİ & DÜZENLEME (ADMIN / PATRON)
# =========================================================================
def admin_update_user(user_id, username, full_name, role, is_active, new_password=None):
    """Yöneticinin kullanıcı bilgilerini, rolünü, aktiflik durumunu ve şifresini güncellemesini sağlar."""
    conn = get_db()
    cursor = conn.cursor()
    if new_password and str(new_password).strip():
        pw_hash = hash_password(str(new_password).strip())
        cursor.execute("""
            UPDATE users
            SET username = ?, full_name = ?, role = ?, is_active = ?, password_hash = ?
            WHERE id = ?
        """, (username.strip(), full_name.strip(), role.strip(), int(is_active), pw_hash, user_id))
    else:
        cursor.execute("""
            UPDATE users
            SET username = ?, full_name = ?, role = ?, is_active = ?
            WHERE id = ?
        """, (username.strip(), full_name.strip(), role.strip(), int(is_active), user_id))
    conn.commit()
    conn.close()


# =========================================================================
# DUYURU VE UYARI SİSTEMİ
# =========================================================================
def get_active_announcements():
    """Sistemde yayında olan aktif duyuruları en yeniden eskiye döner."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM announcements WHERE is_active = 1 ORDER BY id DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def add_announcement(user_id, username, full_name, title, content, priority='Önemli'):
    """Yeni bir sistem/şantiye duyurusu ekler."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO announcements (user_id, username, full_name, title, content, priority, is_active)
        VALUES (?, ?, ?, ?, ?, ?, 1)
    """, (user_id, username, full_name, title.strip(), content.strip(), priority))
    conn.commit()
    conn.close()

def deactivate_announcement(announcement_id):
    """Duyuruyu yayından kaldırır."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE announcements SET is_active = 0 WHERE id = ?", (announcement_id,))
    conn.commit()
    conn.close()

def delete_announcement(announcement_id):
    """Duyuruyu tamamen siler."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
    conn.commit()
    conn.close()


# =========================================================================
# CANLI SOHBET & İLETİŞİM SİSTEMİ
# =========================================================================
def get_chat_messages(channel='genel', limit=100):
    """Belirtilen kanal veya modüle ait son sohbet mesajlarını döner."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM (
            SELECT * FROM chat_messages
            WHERE channel = ?
            ORDER BY id DESC
            LIMIT ?
        ) ORDER BY id ASC
    """, (channel, limit))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def save_chat_message(channel, user_id, username, full_name, message, photo_url=''):
    """Yeni sohbet mesajı ve isteğe bağlı fotoğraf ekler."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO chat_messages (channel, user_id, username, full_name, message, photo_url)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (channel or 'genel', user_id, username, full_name, message.strip() if message else '', photo_url or ''))
    conn.commit()
    conn.close()


# =========================================================================
# TOPLANTI VE KARAR TAKİP SİSTEMİ
# =========================================================================
def get_meetings(project_id=None):
    """Kayıtlı tüm toplantıları listeler; opsiyonel olarak proje filtresi uygular."""
    conn = get_db()
    cursor = conn.cursor()
    if project_id:
        cursor.execute("""
            SELECT m.*, p.code as project_code, p.name as project_name
            FROM meetings m
            LEFT JOIN projects p ON m.project_id = p.id
            WHERE m.project_id = ?
            ORDER BY m.meeting_date DESC, m.id DESC
        """, (project_id,))
    else:
        cursor.execute("""
            SELECT m.*, p.code as project_code, p.name as project_name
            FROM meetings m
            LEFT JOIN projects p ON m.project_id = p.id
            ORDER BY m.meeting_date DESC, m.id DESC
        """)
    rows = [dict(r) for r in cursor.fetchall()]
    
    # Her toplantı için aksiyon kalemlerini ekle
    for m in rows:
        cursor.execute("SELECT * FROM meeting_action_items WHERE meeting_id = ? ORDER BY id ASC", (m['id'],))
        m['actions'] = [dict(a) for a in cursor.fetchall()]
    
    conn.close()
    return rows

def get_meeting_by_id(meeting_id):
    """Belirli bir toplantının detaylarını ve aksiyon kalemlerini döner."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT m.*, p.code as project_code, p.name as project_name
        FROM meetings m
        LEFT JOIN projects p ON m.project_id = p.id
        WHERE m.id = ?
    """, (meeting_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None
    
    meeting = dict(row)
    cursor.execute("SELECT * FROM meeting_action_items WHERE meeting_id = ? ORDER BY id ASC", (meeting_id,))
    meeting['actions'] = [dict(a) for a in cursor.fetchall()]
    conn.close()
    return meeting

def add_meeting(project_id, title, meeting_date, meeting_time, location, organizer, attendees, summary, decisions, status, created_by):
    """Yeni toplantı kaydı oluşturur."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO meetings (project_id, title, meeting_date, meeting_time, location, organizer, attendees, summary, decisions, status, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (project_id or None, title.strip(), meeting_date, meeting_time or '', location or '', organizer or '', attendees or '', summary or '', decisions or '', status or 'Tamamlandı', created_by or ''))
    meeting_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return meeting_id

def update_meeting(meeting_id, project_id, title, meeting_date, meeting_time, location, organizer, attendees, summary, decisions, status):
    """Toplantı kaydını günceller."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE meetings
        SET project_id = ?, title = ?, meeting_date = ?, meeting_time = ?, location = ?,
            organizer = ?, attendees = ?, summary = ?, decisions = ?, status = ?
        WHERE id = ?
    """, (project_id or None, title.strip(), meeting_date, meeting_time or '', location or '', organizer or '', attendees or '', summary or '', decisions or '', status or 'Tamamlandı', meeting_id))
    conn.commit()
    conn.close()

def delete_meeting(meeting_id):
    """Toplantı ve bağlı aksiyonlarını siler."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM meeting_action_items WHERE meeting_id = ?", (meeting_id,))
    cursor.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
    conn.commit()
    conn.close()

def get_meeting_action_items(meeting_id):
    """Toplantıya ait aksiyon maddelerini listeler."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM meeting_action_items WHERE meeting_id = ? ORDER BY id ASC", (meeting_id,))
    items = [dict(a) for a in cursor.fetchall()]
    conn.close()
    return items

def add_meeting_action_item(meeting_id, description, responsible_person, due_date, status='Devam Ediyor'):
    """Toplantıya yeni bir aksiyon/görev maddesi ekler."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO meeting_action_items (meeting_id, description, responsible_person, due_date, status)
        VALUES (?, ?, ?, ?, ?)
    """, (meeting_id, description.strip(), responsible_person or '', due_date or '', status or 'Devam Ediyor'))
    action_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return action_id

def update_action_item_status(item_id, status):
    """Aksiyon maddesinin durumunu günceller (Tamamlandı, Devam Ediyor, Beklemede, İptal)."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE meeting_action_items SET status = ? WHERE id = ?", (status, item_id))
    conn.commit()
    conn.close()

def delete_meeting_action_item(item_id):
    """Aksiyon maddesini siler."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM meeting_action_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()


# =========================================================================
# 25. MUHASEBE & İRSALİYE TAKİP MODÜLÜ VERİTABANI FONKSİYONLARI
# =========================================================================
def get_shipments_with_accounting(project_id=None, accounting_status=None, search=None, customer=None):
    """Muhasebe ve sevkiyat takip listesi için irsaliyeleri filtreli getirir."""
    conn = get_db()
    cursor = conn.cursor()
    
    query = """
        SELECT s.*, p.code as project_code, p.name as project_name, p.customer, p.site_location
        FROM shipments s
        LEFT JOIN projects p ON s.project_id = p.id
        WHERE 1=1
    """
    params = []
    
    if project_id and str(project_id).isdigit():
        query += " AND s.project_id = ?"
        params.append(int(project_id))
        
    if accounting_status and accounting_status != 'Tümü':
        if accounting_status == 'Bekliyor':
            query += " AND (s.accounting_status = 'Bekliyor' OR s.accounting_status IS NULL OR s.accounting_status = '')"
        else:
            query += " AND s.accounting_status = ?"
            params.append(accounting_status)
        
    if customer and customer.strip():
        query += " AND p.customer = ?"
        params.append(customer.strip())
        
    if search and search.strip():
        s_term = f"%{search.strip()}%"
        query += " AND (s.dispatch_no LIKE ? OR s.vehicle_plate LIKE ? OR s.driver_name LIKE ? OR s.accounting_invoice_no LIKE ? OR p.name LIKE ? OR p.customer LIKE ?)"
        params.extend([s_term, s_term, s_term, s_term, s_term, s_term])
        
    query += " ORDER BY s.id DESC"
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def update_shipment_accounting(shipment_id, accounting_status, accounting_processed_by, accounting_invoice_no=None, accounting_notes=None):
    """İrsaliyenin muhasebe işleme durumunu günceller."""
    conn = get_db()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if accounting_status == 'İşlendi' else None
    
    cursor.execute("""
        UPDATE shipments
        SET accounting_status = ?,
            accounting_processed_by = ?,
            accounting_processed_at = ?,
            accounting_invoice_no = ?,
            accounting_notes = ?
        WHERE id = ?
    """, (accounting_status, accounting_processed_by, now_str, accounting_invoice_no or '', accounting_notes or '', shipment_id))
    conn.commit()
    conn.close()

def get_accounting_summary_stats():
    """Muhasebe takip modülü için özet metrikleri hesaplar."""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) as total, COALESCE(SUM(total_tonnage), 0) as total_tonnage FROM shipments")
    tot_row = cursor.fetchone()
    total_count = (tot_row['total'] if tot_row else 0) or 0
    total_tonnage = round((tot_row['total_tonnage'] if tot_row else 0.0) or 0.0, 2)
    
    cursor.execute("SELECT COUNT(*) as processed, COALESCE(SUM(total_tonnage), 0) as processed_tonnage FROM shipments WHERE accounting_status = 'İşlendi'")
    proc_row = cursor.fetchone()
    processed_count = (proc_row['processed'] if proc_row else 0) or 0
    processed_tonnage = round((proc_row['processed_tonnage'] if proc_row else 0.0) or 0.0, 2)
    
    cursor.execute("SELECT COUNT(*) as pending, COALESCE(SUM(total_tonnage), 0) as pending_tonnage FROM shipments WHERE accounting_status != 'İşlendi' OR accounting_status IS NULL OR accounting_status = ''")
    pend_row = cursor.fetchone()
    pending_count = (pend_row['pending'] if pend_row else 0) or 0
    pending_tonnage = round((pend_row['pending_tonnage'] if pend_row else 0.0) or 0.0, 2)
    
    conn.close()
    return {
        'total_count': total_count,
        'total_tonnage': total_tonnage,
        'processed_count': processed_count,
        'processed_tonnage': processed_tonnage,
        'pending_count': pending_count,
        'pending_tonnage': pending_tonnage,
        'completion_pct': round((processed_count / total_count * 100), 1) if total_count > 0 else 0
    }



