import os
import re
import math
import socket
import traceback
from datetime import datetime, timedelta
import calendar
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_file, session, g
)

from database import (
    get_db, init_db, log_activity, hash_password,
    get_global_metrics, get_project_summary
)
from excel_handler import (
    parse_tekla_excel, parse_excel_parts, generate_template_excel,
    export_material_rfq_excel, export_project_report_excel, export_shipment_excel
)
from seed_data import seed_demo_data

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static')
)
app.secret_key = os.environ.get('SECRET_KEY', 'celik_imalat_takip_gizli_anahtar_2026_super_secure')

@app.errorhandler(500)
def internal_error(error):
    err_trace = traceback.format_exc()
    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>500 Sunucu Hatası Detayı</title></head>
    <body style="font-family: monospace; background: #0f172a; color: #f87171; padding: 25px; line-height: 1.5;">
        <h2 style="color: #ef4444;">⚠️ 500 Dahili Sunucu Hatası Detayı:</h2>
        <pre style="background: #1e293b; padding: 15px; border-radius: 8px; color: #e2e8f0; overflow-x: auto; white-space: pre-wrap;">{err_trace}</pre>
    </body>
    </html>
    """, 500


# Otomatik IP Tespiti
def get_local_ip():
    """Bilgisayarın yerel ağdaki (Wi-Fi / Ethernet) IP adresini otomatik bulur."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

# Format ve Sayı Yardımcıları
def clean_float(val_str):
    if not val_str: return 0.0
    try:
        return float(str(val_str).strip().replace(',', '.').replace(' ', '').replace('kg', '').replace('ton', ''))
    except ValueError:
        return 0.0

def clean_int(val_str):
    if not val_str: return 0
    try:
        return int(float(str(val_str).strip().replace(' ', '')))
    except ValueError:
        return 0

def eval_math(val_str):
    if not val_str: return 0
    s = str(val_str).strip().replace(',', '.')
    if not s: return 0
    if any(op in s for op in ['+', '-', '*', '/']):
        if re.match(r'^[\d\.\+\-\*\/\(\)\s]+$', s):
            try:
                res = eval(s, {"__builtins__": None}, {})
                return int(float(res))
            except:
                pass
    return clean_int(val_str)

def fmt_num(val, decimals=2):
    if val is None: return "0,00"
    try:
        f_val = float(val)
        return f"{f_val:.{decimals}f}".replace('.', ',')
    except:
        return str(val)

# Jinja Template Filtreleri ve Değişkenleri
@app.template_filter('format_num')
def jinja_fmt_num(val, decimals=2):
    return fmt_num(val, decimals)

@app.template_filter('format_date')
def jinja_fmt_date(val_str):
    if not val_str: return "-"
    for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%d.%m.%Y'):
        try:
            dt = datetime.strptime(str(val_str).split('.')[0], fmt)
            return dt.strftime('%d.%m.%Y')
        except:
            pass
    return str(val_str)

@app.context_processor
def inject_global_vars():
    user = session.get('user')
    return {
        'local_ip': get_local_ip(),
        'current_user': user,
        'now': datetime.now()
    }

# Oturum Kontrol Dekoratörü
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            # Oturum yoksa otomatik izleyici veya login yönlendirmesi
            # Kullanıcı deneyimini kesmemek için varsayılan misafir kullanıcı atanabilir veya login'e yönlendirilir
            pass
        return f(*args, **kwargs)
    return decorated_function

_db_initialized = False

@app.before_request
def ensure_db_ready():
    global _db_initialized
    if not _db_initialized:
        try:
            init_db()
            seed_demo_data()
            _db_initialized = True
        except Exception as e:
            print(f"Veritabani hazirlama uyarisi: {e}")

# Baslangicta da calistir
with app.app_context():
    try:
        init_db()
        seed_demo_data()
        _db_initialized = True
    except Exception as e:
        print(f"Veritabanı başlatma uyarısı: {e}")


# =========================================================================
# 0. KULLANICI GİRİŞİ, ÇIKIŞI VE YETKİ
# =========================================================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ? AND is_active = 1", (username,))
        user_row = cursor.fetchone()
        conn.close()

        if user_row and user_row['password_hash'] == hash_password(password):
            user_data = dict(user_row)
            del user_data['password_hash']
            session['user'] = user_data
            
            log_activity(
                action="Giriş Yapıldı",
                entity_type="users",
                entity_id=user_data['id'],
                details=f"{user_data['full_name']} ({user_data['role']}) sisteme giriş yaptı.",
                username=user_data['username'],
                user_id=user_data['id'],
                ip_address=request.remote_addr
            )
            
            flash(f"Hoş geldiniz, {user_data['full_name']}!", "success")
            return redirect(url_for('index'))
        else:
            flash("Geçersiz kullanıcı adı veya şifre!", "danger")

    return render_template('login.html')

@app.route('/logout')
def logout():
    user = session.get('user')
    if user:
        log_activity(
            action="Çıkış Yapıldı",
            entity_type="users",
            entity_id=user.get('id'),
            details=f"{user.get('full_name')} sistemden çıkış yaptı.",
            username=user.get('username'),
            user_id=user.get('id'),
            ip_address=request.remote_addr
        )
    session.pop('user', None)
    flash("Başarıyla çıkış yapıldı.", "info")
    return redirect(url_for('login'))


# =========================================================================
# 1. KONTROL PANELİ (DASHBOARD)
# =========================================================================
@app.route('/')
def index():
    metrics = get_global_metrics()
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Projelerin aşama durumlarıyla listesi
    cursor.execute("SELECT * FROM projects WHERE status != 'Arşiv' ORDER BY id DESC")
    projects_raw = cursor.fetchall()
    projects = []
    for p in projects_raw:
        summary = get_project_summary(p['id'])
        if summary:
            projects.append(summary)

    # Son Sevkiyatlar
    cursor.execute('''
    SELECT s.*, p.name as project_name, p.code as project_code
    FROM shipments s
    LEFT JOIN projects p ON s.project_id = p.id
    ORDER BY s.id DESC LIMIT 5
    ''')
    recent_shipments = [dict(r) for r in cursor.fetchall()]

    # Son Aktivite Günlüğü
    cursor.execute('SELECT * FROM activity_logs ORDER BY id DESC LIMIT 8')
    recent_activities = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return render_template('dashboard.html',
                           metrics=metrics,
                           projects=projects,
                           recent_shipments=recent_shipments,
                           recent_activities=recent_activities)


# =========================================================================
# 2. PROJE TAKİP & TEKLA LİSTELERİ
# =========================================================================
@app.route('/projeler')
def projeler():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE status != 'Arşiv' ORDER BY id DESC")
    projects_raw = cursor.fetchall()
    projects = []
    for p in projects_raw:
        summary = get_project_summary(p['id'])
        if summary: projects.append(summary)
    conn.close()
    metrics = get_global_metrics()
    return render_template('projeler.html', projects=projects, metrics=metrics)

@app.route('/projeler/<int:project_id>')
def proje_detay(project_id):
    project = get_project_summary(project_id)
    if not project:
        flash("Proje bulunamadı!", "danger")
        return redirect(url_for('projeler'))

    conn = get_db()
    cursor = conn.cursor()

    # 1. Montaj Listesi (Assemblies)
    cursor.execute('SELECT * FROM assemblies WHERE project_id = ? ORDER BY assembly_pos ASC', (project_id,))
    assemblies = [dict(r) for r in cursor.fetchall()]

    # 2. Montaj Parça Listesi (Assembly Parts)
    cursor.execute('SELECT * FROM assembly_parts WHERE project_id = ? ORDER BY assembly_pos ASC, part_pos ASC', (project_id,))
    assembly_parts = [dict(r) for r in cursor.fetchall()]

    # 3. Tek Parça / Poz Listesi (Parts)
    cursor.execute('SELECT * FROM parts WHERE project_id = ? ORDER BY pos_no ASC', (project_id,))
    parts = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return render_template('proje_detay.html',
                           project=project,
                           assemblies=assemblies,
                           assembly_parts=assembly_parts,
                           parts=parts)

@app.route('/api/projeler', methods=['POST'])
def api_proje_ekle():
    code = request.form.get('code', '').strip().upper()
    name = request.form.get('name', '').strip()
    customer = request.form.get('customer', '').strip()
    site_location = request.form.get('site_location', '').strip()
    start_date = request.form.get('start_date', '')
    cutting_start_date = request.form.get('cutting_start_date', '')
    delivery_date = request.form.get('delivery_date', '')
    target_tonnage = clean_float(request.form.get('target_tonnage', 0))
    color = request.form.get('color', '#3b82f6')
    pos_prefix = request.form.get('pos_prefix', '').strip()
    notes = request.form.get('notes', '').strip()

    if not code or not name:
        flash("Proje Kodu ve Proje Adı zorunludur!", "warning")
        return redirect(url_for('projeler'))

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO projects (code, name, customer, site_location, start_date, cutting_start_date, delivery_date, target_tonnage, color, pos_prefix, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (code, name, customer, site_location, start_date, cutting_start_date, delivery_date, target_tonnage, color, pos_prefix, notes))
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()

        u = session.get('user', {})
        log_activity(
            action="Proje Oluşturuldu",
            entity_type="projects",
            entity_id=new_id,
            details=f"'{code} - {name}' projesi oluşturuldu.",
            username=u.get('username', 'Kullanıcı'),
            user_id=u.get('id'),
            ip_address=request.remote_addr
        )

        flash(f"'{name}' projesi başarıyla oluşturuldu.", "success")
        return redirect(url_for('proje_detay', project_id=new_id))
    except Exception as e:
        conn.close()
        flash(f"Proje eklenirken hata: {str(e)}", "danger")
        return redirect(url_for('projeler'))

@app.route('/api/projeler/<int:project_id>/tekla-yukle', methods=['POST'])
def api_tekla_yukle(project_id):
    """Tekla Structures Excel dosyasını (Montaj, Montaj Parça ve Tek Parça) ayrıştırıp kaydeder."""
    if 'excel_file' not in request.files:
        flash("Lütfen bir Excel dosyası seçin.", "warning")
        return redirect(url_for('proje_detay', project_id=project_id))

    file = request.files['excel_file']
    if file.filename == '':
        flash("Dosya seçilmedi.", "warning")
        return redirect(url_for('proje_detay', project_id=project_id))

    try:
        parsed_data = parse_tekla_excel(file.stream)
        assemblies = parsed_data.get('assemblies', [])
        assembly_parts = parsed_data.get('assembly_parts', [])
        parts = parsed_data.get('parts', [])

        conn = get_db()
        cursor = conn.cursor()

        # 1. Assemblies Kaydet
        for a in assemblies:
            cursor.execute('''
            INSERT INTO assemblies (project_id, assembly_pos, description, profile_type, quantity, unit_weight, total_weight, material_grade)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, assembly_pos) DO UPDATE SET
                description = excluded.description,
                profile_type = excluded.profile_type,
                quantity = excluded.quantity,
                unit_weight = excluded.unit_weight,
                total_weight = excluded.total_weight,
                material_grade = excluded.material_grade
            ''', (project_id, a['assembly_pos'], a['description'], a['profile_type'], a['quantity'], a['unit_weight'], a['total_weight'], a['material_grade']))

        # 2. Assembly Parts Kaydet
        for ap in assembly_parts:
            # Assembly ID bul
            cursor.execute("SELECT id FROM assemblies WHERE project_id = ? AND assembly_pos = ?", (project_id, ap['assembly_pos']))
            ass_row = cursor.fetchone()
            ass_id = ass_row['id'] if ass_row else None

            cursor.execute('''
            INSERT INTO assembly_parts (project_id, assembly_id, assembly_pos, part_pos, description, profile_type, quantity_per_assembly, total_quantity, length, unit_weight, total_weight, material_grade)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (project_id, ass_id, ap['assembly_pos'], ap['part_pos'], ap['description'], ap['profile_type'], ap['quantity_per_assembly'], ap['total_quantity'], ap['length'], ap['unit_weight'], ap['total_weight'], ap['material_grade']))

        # 3. Tek Parça (Parts) Listesini Kaydet
        for p in parts:
            cursor.execute('''
            INSERT INTO parts (project_id, pos_no, name, profile_type, quantity, length, unit_weight, total_weight, material_grade)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (project_id, p['pos_no'], p['name'], p['profile_type'], p['quantity'], p.get('length', 0), p['unit_weight'], p['total_weight'], p['material_grade']))

        conn.commit()
        conn.close()

        u = session.get('user', {})
        log_activity(
            action="Tekla Excel Yüklendi",
            entity_type="projects",
            entity_id=project_id,
            details=f"{len(assemblies)} Montaj, {len(parts)} Poz Tekla dosyasından aktarıldı.",
            username=u.get('username', 'Kullanıcı'),
            user_id=u.get('id'),
            ip_address=request.remote_addr
        )

        flash(f"Harika! Tekla Structures dosyasından {len(assemblies)} montaj markası ve {len(parts)} parça pozu başarıyla aktarıldı.", "success")
    except Exception as e:
        flash(f"Excel okunurken hata oluştu: {str(e)}", "danger")

    return redirect(url_for('proje_detay', project_id=project_id))

@app.route('/api/projeler/<int:project_id>/sil', methods=['POST'])
def api_proje_sil(project_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT code, name FROM projects WHERE id = ?", (project_id,))
    p_row = cursor.fetchone()
    p_info = f"{p_row['code']} - {p_row['name']}" if p_row else f"ID: {project_id}"
    
    cursor.execute('DELETE FROM projects WHERE id = ?', (project_id,))
    conn.commit()
    conn.close()

    u = session.get('user', {})
    log_activity(
        action="Proje Silindi",
        entity_type="projects",
        entity_id=project_id,
        details=f"'{p_info}' projesi ve bağlı tüm kayıtlar silindi.",
        username=u.get('username', 'Kullanıcı'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )

    flash("Proje ve bağlı tüm veriler silindi.", "info")
    return redirect(url_for('projeler'))


# =========================================================================
# 3. SİPARİŞ TAKİP & PROFİL AĞIRLIK HESAPLAMA MOTORU
# =========================================================================
@app.route('/siparis-takip')
def siparis_takip():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, code, name FROM projects WHERE status != 'Arşiv' ORDER BY name ASC")
    projects = [dict(r) for r in cursor.fetchall()]

    selected_project_id = request.args.get('proje_id', '')
    
    # Sipariş Verilen ve Gelenleri al
    query_ver = "SELECT * FROM material_orders_ordered WHERE 1=1"
    query_gel = "SELECT * FROM material_orders_received WHERE 1=1"
    params = []
    
    if selected_project_id:
        query_ver += " AND project_id = ?"
        query_gel += " AND project_id = ?"
        params.append(selected_project_id)
        
    query_ver += " ORDER BY id DESC"
    query_gel += " ORDER BY id DESC"
    
    cursor.execute(query_ver, params)
    ordered_items = [dict(r) for r in cursor.fetchall()]
    
    cursor.execute(query_gel, params)
    received_items = [dict(r) for r in cursor.fetchall()]

    # Kalan Malzeme Özeti Hesapla
    ordered_map = {}
    for o in ordered_items:
        key = (o['material'].upper().strip(), o['thickness'], o['width'], o['length'])
        if key not in ordered_map:
            ordered_map[key] = {'material': o['material'], 'thickness': o['thickness'], 'width': o['width'], 'length': o['length'], 'ordered_qty': 0, 'ordered_weight': 0.0, 'received_qty': 0, 'received_weight': 0.0}
        ordered_map[key]['ordered_qty'] += o['quantity']
        ordered_map[key]['ordered_weight'] += o['weight']

    for g in received_items:
        key = (g['material'].upper().strip(), g['thickness'], g['width'], g['length'])
        if key not in ordered_map:
            ordered_map[key] = {'material': g['material'], 'thickness': g['thickness'], 'width': g['width'], 'length': g['length'], 'ordered_qty': 0, 'ordered_weight': 0.0, 'received_qty': 0, 'received_weight': 0.0}
        ordered_map[key]['received_qty'] += g['quantity']
        ordered_map[key]['received_weight'] += g['weight']

    remaining_items = []
    tot_ordered_w = 0.0
    tot_received_w = 0.0
    tot_remaining_w = 0.0

    for k, v in ordered_map.items():
        kalan_qty = max(0, v['ordered_qty'] - v['received_qty'])
        kalan_w = max(0.0, v['ordered_weight'] - v['received_weight'])
        fazla_qty = max(0, v['received_qty'] - v['ordered_qty'])
        
        tot_ordered_w += v['ordered_weight']
        tot_received_w += v['received_weight']
        tot_remaining_w += kalan_w

        remaining_items.append({
            'material': v['material'],
            'thickness': v['thickness'],
            'width': v['width'],
            'length': v['length'],
            'ordered_qty': v['ordered_qty'],
            'received_qty': v['received_qty'],
            'remaining_qty': kalan_qty,
            'surplus_qty': fazla_qty,
            'remaining_weight': round(kalan_w, 2)
        })

    conn.close()
    return render_template('siparis_takip.html',
                           projects=projects,
                           selected_project_id=selected_project_id,
                           ordered_items=ordered_items,
                           received_items=received_items,
                           remaining_items=remaining_items,
                           tot_ordered_ton=round(tot_ordered_w / 1000.0, 2),
                           tot_received_ton=round(tot_received_w / 1000.0, 2),
                           tot_remaining_ton=round(tot_remaining_w / 1000.0, 2))

@app.route('/api/siparis-takip/hesapla-agirlik', methods=['POST'])
def api_hesapla_profil_agirlik():
    """Girilen profil cinsi, et kalınlığı, en, boy ve adede göre otomatik ağırlık hesaplar."""
    data = request.get_json() or {}
    m_raw = str(data.get('material', '')).strip().upper()
    kal = clean_float(data.get('thickness', 0))
    en = clean_float(data.get('width', 0))
    boy = clean_float(data.get('length', 0))
    adet = clean_int(data.get('quantity', 1))
    if adet <= 0: adet = 1

    agirlik = 0.0
    birim_agirlik = 0.0

    is_sac = "SAC" in m_raw or "PL" in m_raw or "PLAKA" in m_raw
    is_structural = any(p in m_raw for p in ["UNP", "HEA", "HEB", "IPE", "NPU", "NPI", "INP"])

    rect_match = re.search(r'RHS(\d+)[Xx](\d+)', m_raw)
    sq_match = re.search(r'(?:SHS|RHS)(\d+)', m_raw)
    pipe_match = re.search(r'[ØD](\d+[\.,]?\d*)', m_raw)
    angle_match = re.search(r'\bL(\d+)(?:[Xx](\d+))?(?:[Xx](\d+))?', m_raw)
    struct_match = re.search(r'(?:UNP|HEA|HEB|IPE|NPU|INP|NPI)\s*(\d+)', m_raw)

    if rect_match:
        a = float(rect_match.group(1)); b = float(rect_match.group(2))
        t = kal if kal > 0 else 4.0
        r_dis = 2.0 * t; r_ic = max(0.0, r_dis - t)
        net_alan = max(0.0, (2.0 * t * (a + b - 2.0 * t)) - ((4.0 - math.pi) * (r_dis**2 - r_ic**2)))
        birim_agirlik = net_alan * 0.00785
    elif sq_match and not rect_match:
        a = float(sq_match.group(1))
        t = kal if kal > 0 else 4.0
        r_dis = 2.0 * t; r_ic = max(0.0, r_dis - t)
        net_alan = max(0.0, (2.0 * t * (2.0 * a - 2.0 * t)) - ((4.0 - math.pi) * (r_dis**2 - r_ic**2)))
        birim_agirlik = net_alan * 0.00785
    elif pipe_match or "BORU" in m_raw:
        d_val = float(pipe_match.group(1).replace(',', '.')) if pipe_match else 33.7
        t = kal if kal > 0 else 3.2
        birim_agirlik = math.pi * (d_val - t) * t * 0.00785
    elif angle_match or "KÖŞEBENT" in m_raw or m_raw.startswith('L'):
        a_val = float(angle_match.group(1)) if angle_match else 50.0
        t = kal if kal > 0 else (float(angle_match.group(2)) if angle_match and angle_match.group(2) else 5.0)
        birim_agirlik = (2.0 * a_val - t) * t * 0.00785
    elif struct_match or is_structural:
        size_val = int(struct_match.group(1)) if struct_match else 100
        if "UNP" in m_raw or "NPU" in m_raw:
            unp_w = {80: 8.64, 100: 10.6, 120: 13.4, 140: 16.0, 160: 18.8, 180: 22.0, 200: 25.3, 220: 29.4, 240: 33.2, 260: 37.9, 300: 46.2}
            birim_agirlik = unp_w.get(size_val, size_val * 0.13)
        elif "HEA" in m_raw:
            hea_w = {100: 16.7, 120: 19.9, 140: 24.7, 160: 30.4, 180: 35.5, 200: 42.3, 220: 50.5, 240: 60.3, 260: 68.2, 300: 88.3, 360: 112, 400: 125}
            birim_agirlik = hea_w.get(size_val, size_val * 0.22)
        elif "HEB" in m_raw:
            heb_w = {100: 20.4, 120: 26.7, 140: 33.7, 160: 42.6, 180: 51.2, 200: 61.3, 220: 71.5, 240: 83.2, 260: 93.0, 300: 117, 360: 142, 400: 155}
            birim_agirlik = heb_w.get(size_val, size_val * 0.28)
        elif "IPE" in m_raw or "INP" in m_raw or "NPI" in m_raw:
            ipe_w = {80: 6.0, 100: 8.1, 120: 10.4, 140: 12.9, 160: 15.8, 180: 18.8, 200: 22.4, 220: 26.2, 240: 30.7, 270: 36.1, 300: 42.2, 360: 57.1, 400: 66.3}
            birim_agirlik = ipe_w.get(size_val, size_val * 0.15)
    elif is_sac:
        if kal > 0 and en > 0 and boy > 0:
            agirlik = (kal * en * boy * 7.85 / 1000000.0) * adet

    if agirlik == 0.0 and birim_agirlik > 0 and boy > 0:
        agirlik = birim_agirlik * (boy / 1000.0) * adet

    return jsonify({
        'status': 'success',
        'unit_weight_per_m': round(birim_agirlik, 3),
        'total_weight': round(agirlik, 2)
    })

@app.route('/api/siparis-takip/verilen-ekle', methods=['POST'])
def api_siparis_verilen_ekle():
    project_id = request.form.get('project_id') or None
    material = request.form.get('material', '').strip().upper()
    thickness = clean_float(request.form.get('thickness', 0))
    width = clean_float(request.form.get('width', 0))
    length = clean_float(request.form.get('length', 0))
    quantity = clean_int(request.form.get('quantity', 1))
    weight = clean_float(request.form.get('weight', 0))
    supplier = request.form.get('supplier', '').strip()
    notes = request.form.get('notes', '').strip()

    conn = get_db()
    cursor = conn.cursor()
    p_code = ""
    if project_id:
        cursor.execute("SELECT code FROM projects WHERE id = ?", (project_id,))
        r = cursor.fetchone()
        if r: p_code = r['code']

    cursor.execute('''
    INSERT INTO material_orders_ordered (project_id, project_code, material, thickness, width, length, quantity, weight, supplier, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (project_id, p_code, material, thickness, width, length, quantity, weight, supplier, notes))
    conn.commit()
    conn.close()

    flash("Sipariş verilen malzeme listeye eklendi.", "success")
    return redirect(url_for('siparis_takip', proje_id=project_id or ''))

@app.route('/api/siparis-takip/gelen-ekle', methods=['POST'])
def api_siparis_gelen_ekle():
    project_id = request.form.get('project_id') or None
    material = request.form.get('material', '').strip().upper()
    thickness = clean_float(request.form.get('thickness', 0))
    width = clean_float(request.form.get('width', 0))
    length = clean_float(request.form.get('length', 0))
    quantity = clean_int(request.form.get('quantity', 1))
    weight = clean_float(request.form.get('weight', 0))
    received_date = request.form.get('received_date') or datetime.now().strftime("%Y-%m-%d")
    waybill_no = request.form.get('waybill_no', '').strip()
    notes = request.form.get('notes', '').strip()

    conn = get_db()
    cursor = conn.cursor()
    p_code = ""
    if project_id:
        cursor.execute("SELECT code FROM projects WHERE id = ?", (project_id,))
        r = cursor.fetchone()
        if r: p_code = r['code']

    cursor.execute('''
    INSERT INTO material_orders_received (project_id, project_code, material, thickness, width, length, quantity, weight, received_date, waybill_no, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (project_id, p_code, material, thickness, width, length, quantity, weight, received_date, waybill_no, notes))
    conn.commit()
    conn.close()

    flash("Gelen malzeme başarıyla kaydedildi.", "success")
    return redirect(url_for('siparis_takip', proje_id=project_id or ''))

@app.route('/api/siparis-takip/teklif-excel')
def api_siparis_teklif_excel():
    project_id = request.args.get('proje_id', '')
    supplier = request.args.get('firma', 'Tedarikçi Firma')
    
    conn = get_db()
    cursor = conn.cursor()
    p_code = "GENEL"
    if project_id:
        cursor.execute("SELECT code FROM projects WHERE id = ?", (project_id,))
        r = cursor.fetchone()
        if r: p_code = r['code']
        cursor.execute("SELECT * FROM material_orders_ordered WHERE project_id = ?", (project_id,))
    else:
        cursor.execute("SELECT * FROM material_orders_ordered")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    excel_stream = export_material_rfq_excel(p_code, rows, supplier)
    return send_file(
        excel_stream,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f"Malzeme_Teklif_Istek_{p_code}.xlsx"
    )


# =========================================================================
# 4. ÖN İMALAT PLAN (MAKİNE TAKVİMİ)
# =========================================================================
@app.route('/on-imalat-plan')
def on_imalat_plan():
    year = int(request.args.get('yil', datetime.now().year))
    month = int(request.args.get('ay', datetime.now().month))
    
    if month > 12: month = 1; year += 1
    elif month < 1: month = 12; year -= 1

    days_in_month = calendar.monthrange(year, month)[1]

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM machines WHERE is_active = 1 ORDER BY id ASC")
    machines = [r['name'] for r in cursor.fetchall()]

    cursor.execute("SELECT id, code, name, color FROM projects WHERE status != 'Arşiv' ORDER BY name ASC")
    projects = [dict(r) for r in cursor.fetchall()]

    # Ayın planlarını çek
    month_start = f"{year}-{month:02d}-01"
    month_end = f"{year}-{month:02d}-{days_in_month:02d}"

    cursor.execute("SELECT * FROM machine_plans WHERE date BETWEEN ? AND ?", (month_start, month_end))
    plans_raw = cursor.fetchall()
    
    # (machine_name, date, plan_type) -> plan_dict
    plans_matrix = {}
    for p in plans_raw:
        k = (p['machine_name'], p['date'], p['plan_type'])
        plans_matrix[k] = dict(p)

    # Durma sebepleri
    cursor.execute('''
    SELECT * FROM machine_plans
    WHERE stoppage_reason IS NOT NULL AND stoppage_reason != '' AND date BETWEEN ? AND ?
    ORDER BY date DESC
    ''', (month_start, month_end))
    stoppages = [dict(r) for r in cursor.fetchall()]

    conn.close()

    # Gün detayları (Haftanın günleri, renkler)
    days = []
    day_names = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
    for d in range(1, days_in_month + 1):
        dt = datetime(year, month, d)
        w = dt.weekday()
        days.append({
            'day': d,
            'date_str': f"{year}-{month:02d}-{d:02d}",
            'day_name': day_names[w],
            'is_weekend': w in (5, 6),
            'is_saturday': w == 5,
            'is_sunday': w == 6
        })

    return render_template('on_imalat_plan.html',
                           year=year,
                           month=month,
                           days=days,
                           machines=machines,
                           projects=projects,
                           plans_matrix=plans_matrix,
                           stoppages=stoppages)

@app.route('/api/on-imalat-plan/kaydet', methods=['POST'])
def api_plan_kaydet():
    machine_name = request.form.get('machine_name')
    date = request.form.get('date')
    plan_type = request.form.get('plan_type', 'Planlanan')
    project_code = request.form.get('project_code', '').strip()
    status = request.form.get('status', 'Planlandı')
    stoppage_reason = request.form.get('stoppage_reason', '').strip()

    conn = get_db()
    cursor = conn.cursor()

    if status == 'Temizle' or (not project_code and not stoppage_reason):
        cursor.execute("DELETE FROM machine_plans WHERE machine_name = ? AND date = ? AND plan_type = ?", (machine_name, date, plan_type))
    else:
        cursor.execute("SELECT id FROM projects WHERE code = ?", (project_code,))
        p_row = cursor.fetchone()
        p_id = p_row['id'] if p_row else None

        cursor.execute('''
        INSERT INTO machine_plans (machine_name, date, project_id, project_code, plan_type, status, stoppage_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(machine_name, date, plan_type) DO UPDATE SET
            project_id = excluded.project_id,
            project_code = excluded.project_code,
            status = excluded.status,
            stoppage_reason = excluded.stoppage_reason
        ''', (machine_name, date, p_id, project_code, plan_type, status, stoppage_reason))

    conn.commit()
    conn.close()

    return jsonify({'status': 'success'})


# =========================================================================
# 5. KESİM TAKİP (HIZLI KESİLENLER GİRİŞİ & PARÇA LİSTESİ GÜNCELLEME)
# =========================================================================
@app.route('/kesim-takip')
def kesim_takip():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, code, name, pos_prefix FROM projects WHERE status != 'Arşiv' ORDER BY name ASC")
    projects = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT name FROM machines WHERE is_active = 1 ORDER BY id ASC")
    machines = [r['name'] for r in cursor.fetchall()]

    cursor.execute("SELECT operator_name FROM operators WHERE is_active = 1 AND role IN ('Operatör', 'Kaynakçı') ORDER BY operator_name ASC")
    operators = [r['operator_name'] for r in cursor.fetchall()]

    cursor.execute("SELECT operator_name FROM operators WHERE is_active = 1 AND role = 'Yardımcı' ORDER BY operator_name ASC")
    helpers = [r['operator_name'] for r in cursor.fetchall()]

    # Son Kesim Logları
    cursor.execute('SELECT * FROM cutting_entries ORDER BY id DESC LIMIT 50')
    cutting_logs = [dict(r) for r in cursor.fetchall()]

    # Gün Bazlı Özet
    cursor.execute('''
    SELECT cut_date, COUNT(id) as entry_count, SUM(cut_quantity) as total_pieces
    FROM cutting_entries
    GROUP BY cut_date ORDER BY cut_date DESC LIMIT 10
    ''')
    daily_summary = [dict(r) for r in cursor.fetchall()]

    # Makine Bazlı Özet
    cursor.execute('''
    SELECT machine, COUNT(id) as entry_count, SUM(cut_quantity) as total_pieces
    FROM cutting_entries
    GROUP BY machine ORDER BY total_pieces DESC
    ''')
    machine_summary = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return render_template('kesim_takip.html',
                           projects=projects,
                           machines=machines,
                           operators=operators,
                           helpers=helpers,
                           cutting_logs=cutting_logs,
                           daily_summary=daily_summary,
                           machine_summary=machine_summary,
                           today_str=datetime.now().strftime("%Y-%m-%d"))

@app.route('/api/kesim-takip/poz-bilgisi')
def api_kesim_poz_bilgisi():
    """Seçilen proje ve poz no için profil, toplam adet ve kalan adedi döndürür."""
    project_id = request.args.get('proje_id')
    pos_no = request.args.get('pos_no', '').strip()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM parts WHERE project_id = ? AND pos_no = ?", (project_id, pos_no))
    row = cursor.fetchone()
    conn.close()

    if row:
        r_dict = dict(row)
        kalan = max(0, r_dict['quantity'] - r_dict['cut_quantity'])
        return jsonify({
            'status': 'found',
            'profile': r_dict['profile_type'],
            'total_qty': r_dict['quantity'],
            'cut_qty': r_dict['cut_quantity'],
            'remaining_qty': kalan,
            'unit_weight': r_dict['unit_weight']
        })
    return jsonify({'status': 'not_found'})

@app.route('/api/kesim-takip/kaydet', methods=['POST'])
def api_kesim_kaydet():
    """Hızlı kesilenler girişini kaydeder ve Proje Parça Listesindeki kesilen_adet değerini anında artırır."""
    project_id = request.form.get('project_id')
    pos_no = request.form.get('pos_no', '').strip()
    profile = request.form.get('profile', '').strip()
    cut_qty_raw = request.form.get('cut_quantity', '1')
    cut_quantity = eval_math(cut_qty_raw)
    cut_date = request.form.get('cut_date') or datetime.now().strftime("%Y-%m-%d")
    machine = request.form.get('machine', '').strip()
    operator = request.form.get('operator', '').strip()
    helper = request.form.get('helper', '').strip()
    shift = request.form.get('shift', 'Gündüz')
    notes = request.form.get('notes', '').strip()

    if not project_id or not pos_no or cut_quantity <= 0:
        flash("Lütfen proje, poz no ve geçerli bir kesilen adet girin!", "warning")
        return redirect(url_for('kesim_takip'))

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT code FROM projects WHERE id = ?", (project_id,))
    p_row = cursor.fetchone()
    p_code = p_row['code'] if p_row else ""

    # Parça listesini güncelle
    cursor.execute("SELECT id, quantity, cut_quantity FROM parts WHERE project_id = ? AND pos_no = ?", (project_id, pos_no))
    part_row = cursor.fetchone()

    is_overcut = False
    if part_row:
        new_cut_total = part_row['cut_quantity'] + cut_quantity
        cursor.execute("UPDATE parts SET cut_quantity = ?, remaining_quantity = ? WHERE id = ?",
                       (new_cut_total, max(0, part_row['quantity'] - new_cut_total), part_row['id']))
        if new_cut_total > part_row['quantity']:
            is_overcut = True

    # Kesim logunu ekle
    u = session.get('user', {})
    cursor.execute('''
    INSERT INTO cutting_entries (project_id, project_code, pos_no, profile, cut_quantity, cut_date, machine, operator, helper, shift, user_id, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (project_id, p_code, pos_no, profile, cut_quantity, cut_date, machine, operator, helper, shift, u.get('id'), notes))
    conn.commit()
    conn.close()

    log_activity(
        action="Kesim Girişi Yapıldı",
        entity_type="cutting_entries",
        entity_id=project_id,
        details=f"{p_code} - Poz '{pos_no}' için {cut_quantity} adet kesim işlendi ({machine} - {operator}).",
        username=u.get('username', 'Kullanıcı'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )

    if is_overcut:
        flash(f"Dikkat: '{pos_no}' pozu için girilen toplam kesim ({new_cut_total}), proje hedef miktarını ({part_row['quantity']}) aştı!", "warning")
    else:
        flash(f"'{pos_no}' pozu için {cut_quantity} adet kesim başarıyla işlendi ve parça listesi güncellendi.", "success")

    return redirect(url_for('kesim_takip'))


# =========================================================================
# 6. İMALAT PLAN (PROJE BİTİŞ TARİHLERİ & AŞAMA PLANLAMA)
# =========================================================================
@app.route('/imalat-plan')
def imalat_plan():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE status != 'Arşiv' ORDER BY delivery_date ASC")
    projects_raw = cursor.fetchall()
    projects = []
    for p in projects_raw:
        summary = get_project_summary(p['id'])
        if summary: projects.append(summary)
    conn.close()
    return render_template('imalat_plan.html', projects=projects, today_str=datetime.now().strftime("%Y-%m-%d"))

@app.route('/api/imalat-plan/guncelle', methods=['POST'])
def api_imalat_plan_guncelle():
    project_id = request.form.get('project_id')
    start_date = request.form.get('start_date')
    cutting_start_date = request.form.get('cutting_start_date')
    fitup_end_date = request.form.get('fitup_end_date')
    welding_end_date = request.form.get('welding_end_date')
    paint_end_date = request.form.get('paint_end_date')
    delivery_date = request.form.get('delivery_date')

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE projects SET
        start_date = ?,
        cutting_start_date = ?,
        fitup_end_date = ?,
        welding_end_date = ?,
        paint_end_date = ?,
        delivery_date = ?
    WHERE id = ?
    ''', (start_date, cutting_start_date, fitup_end_date, welding_end_date, paint_end_date, delivery_date, project_id))
    conn.commit()
    conn.close()

    flash("Proje imalat planı ve hedef bitiş tarihleri güncellendi.", "success")
    return redirect(url_for('imalat_plan'))


# =========================================================================
# 7. İMALAT TAKİP (ÇATIM, KAYNAK, TEMİZLİK & KALİTEYE GÖNDER)
# =========================================================================
@app.route('/imalat-takip')
def imalat_takip():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, code, name FROM projects WHERE status != 'Arşiv' ORDER BY name ASC")
    projects = [dict(r) for r in cursor.fetchall()]

    selected_project_id = request.args.get('proje_id', '')

    query = '''
    SELECT a.*, p.code as project_code, p.name as project_name
    FROM assemblies a
    JOIN projects p ON a.project_id = p.id
    WHERE 1=1
    '''
    params = []
    if selected_project_id:
        query += " AND a.project_id = ?"
        params.append(selected_project_id)

    query += " ORDER BY a.assembly_pos ASC"
    cursor.execute(query, params)
    assemblies = [dict(r) for r in cursor.fetchall()]

    metrics = get_global_metrics()
    conn.close()
    return render_template('imalat_takip.html',
                           assemblies=assemblies,
                           projects=projects,
                           selected_project_id=selected_project_id,
                           metrics=metrics)

@app.route('/api/imalat-takip/asama-guncelle', methods=['POST'])
def api_imalat_asama_guncelle():
    """Çatım, Kaynak ve Temizlik aşamalarındaki kısmi adetleri günceller."""
    assembly_id = request.form.get('assembly_id')
    fitup_qty = clean_int(request.form.get('fab_fitup_qty', 0))
    welding_qty = clean_int(request.form.get('fab_welding_qty', 0))
    cleaning_qty = clean_int(request.form.get('fab_cleaning_qty', 0))
    done_qty = clean_int(request.form.get('fab_done_qty', 0))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE assemblies SET
        fab_fitup_qty = ?,
        fab_welding_qty = ?,
        fab_cleaning_qty = ?,
        fab_done_qty = ?
    WHERE id = ?
    ''', (fitup_qty, welding_qty, cleaning_qty, done_qty, assembly_id))
    conn.commit()
    conn.close()

    flash("İmalat aşama adetleri güncellendi.", "success")
    return redirect(request.referrer or url_for('imalat_takip'))

@app.route('/api/imalat-takip/kaliteye-gonder', methods=['POST'])
def api_imalat_kaliteye_gonder():
    """İmalatı biten montajları Kalite Kontrol havuzuna gönderir."""
    assembly_id = request.form.get('assembly_id')
    send_qty = clean_int(request.form.get('quantity', 1))
    comments = request.form.get('comments', '').strip()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM assemblies WHERE id = ?", (assembly_id,))
    ass = cursor.fetchone()

    if not ass or send_qty <= 0:
        flash("Geçersiz işlem!", "warning")
        return redirect(request.referrer or url_for('imalat_takip'))

    # assemblies tablosunda qa_pending_qty artır
    new_pending = ass['qa_pending_qty'] + send_qty
    cursor.execute("UPDATE assemblies SET qa_pending_qty = ? WHERE id = ?", (new_pending, assembly_id))

    # qa_inspections kaydı oluştur
    u = session.get('user', {})
    cursor.execute('''
    INSERT INTO qa_inspections (project_id, assembly_id, assembly_pos, quantity, comments)
    VALUES (?, ?, ?, ?, ?)
    ''', (ass['project_id'], assembly_id, ass['assembly_pos'], send_qty, comments))

    conn.commit()
    conn.close()

    log_activity(
        action="Kalite Kontrole Gönderildi",
        entity_type="assemblies",
        entity_id=assembly_id,
        details=f"Marka '{ass['assembly_pos']}' için {send_qty} adet kalite muayenesine sevk edildi.",
        username=u.get('username', 'Kullanıcı'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )

    flash(f"'{ass['assembly_pos']}' markasından {send_qty} adet başarıyla Kalite Kontrol sayfasına gönderildi.", "success")
    return redirect(request.referrer or url_for('imalat_takip'))


# =========================================================================
# 8. KALİTE KONTROL (QA / QC DENETİM & ONAY/RED)
# =========================================================================
@app.route('/kalite-kontrol')
def kalite_kontrol():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, code, name FROM projects WHERE status != 'Arşiv' ORDER BY name ASC")
    projects = [dict(r) for r in cursor.fetchall()]

    selected_project_id = request.args.get('proje_id', '')

    query = '''
    SELECT q.*, p.code as project_code, p.name as project_name, a.profile_type, a.description
    FROM qa_inspections q
    JOIN projects p ON q.project_id = p.id
    LEFT JOIN assemblies a ON q.assembly_id = a.id
    WHERE 1=1
    '''
    params = []
    if selected_project_id:
        query += " AND q.project_id = ?"
        params.append(selected_project_id)

    query += " ORDER BY q.id DESC"
    cursor.execute(query, params)
    inspections = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return render_template('kalite_kontrol.html',
                           inspections=inspections,
                           projects=projects,
                           selected_project_id=selected_project_id,
                           today_str=datetime.now().strftime("%Y-%m-%d"))

@app.route('/api/kalite-kontrol/karar', methods=['POST'])
def api_kalite_karar():
    inspection_id = request.form.get('inspection_id')
    decision = request.form.get('decision') # 'Onaylandı', 'Reddedildi'
    defect_type = request.form.get('defect_type', '').strip()
    comments = request.form.get('comments', '').strip()
    certificate_no = request.form.get('certificate_no', '').strip()
    inspection_date = request.form.get('inspection_date') or datetime.now().strftime("%Y-%m-%d")

    u = session.get('user', {})
    inspector_name = u.get('full_name', 'Kalite Kontrolcü')

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM qa_inspections WHERE id = ?", (inspection_id,))
    qa = cursor.fetchone()
    if not qa:
        flash("Kayıt bulunamadı!", "warning")
        return redirect(url_for('kalite_kontrol'))

    cursor.execute('''
    UPDATE qa_inspections SET
        status = ?,
        defect_type = ?,
        comments = ?,
        certificate_no = ?,
        inspection_date = ?,
        inspector_id = ?,
        inspector_name = ?
    WHERE id = ?
    ''', (decision, defect_type, comments, certificate_no, inspection_date, u.get('id'), inspector_name, inspection_id))

    # Assemblies tablosundaki adetleri güncelle
    if qa['assembly_id']:
        cursor.execute("SELECT * FROM assemblies WHERE id = ?", (qa['assembly_id'],))
        ass = cursor.fetchone()
        if ass:
            qty = qa['quantity']
            new_pending = max(0, ass['qa_pending_qty'] - qty)
            if decision == 'Onaylandı':
                new_approved = ass['qa_approved_qty'] + qty
                # Boya için hazır hale getir
                new_paint_sandblast = ass['paint_sandblast_qty'] + qty
                cursor.execute('''
                UPDATE assemblies SET
                    qa_pending_qty = ?,
                    qa_approved_qty = ?,
                    paint_sandblast_qty = ?
                WHERE id = ?
                ''', (new_pending, new_approved, new_paint_sandblast, qa['assembly_id']))
            else:
                new_rejected = ass['qa_rejected_qty'] + qty
                cursor.execute('''
                UPDATE assemblies SET
                    qa_pending_qty = ?,
                    qa_rejected_qty = ?
                WHERE id = ?
                ''', (new_pending, new_rejected, qa['assembly_id']))

    conn.commit()
    conn.close()

    log_activity(
        action=f"Kalite Kararı: {decision}",
        entity_type="qa_inspections",
        entity_id=inspection_id,
        details=f"Marka '{qa['assembly_pos']}' ({qa['quantity']} Adet) -> {decision}. Not: {comments}",
        username=u.get('username', 'Kullanıcı'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )

    flash(f"Kalite denetim kararı ({decision}) başarıyla kaydedildi.", "success")
    return redirect(url_for('kalite_kontrol'))


# =========================================================================
# 9. BOYA & YÜZEY İŞLEM TAKİBİ
# =========================================================================
@app.route('/boya-takip')
def boya_takip():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, code, name FROM projects WHERE status != 'Arşiv' ORDER BY name ASC")
    projects = [dict(r) for r in cursor.fetchall()]

    selected_project_id = request.args.get('proje_id', '')

    query = '''
    SELECT a.*, p.code as project_code, p.name as project_name
    FROM assemblies a
    JOIN projects p ON a.project_id = p.id
    WHERE (a.qa_approved_qty > 0 OR a.paint_done_qty > 0 OR a.paint_status != 'BEKLIYOR')
    '''
    params = []
    if selected_project_id:
        query += " AND a.project_id = ?"
        params.append(selected_project_id)

    query += " ORDER BY a.assembly_pos ASC"
    cursor.execute(query, params)
    assemblies = [dict(r) for r in cursor.fetchall()]

    # Boya Logları
    cursor.execute('''
    SELECT pr.*, p.code as project_code, p.name as project_name
    FROM paint_records pr
    JOIN projects p ON pr.project_id = p.id
    ORDER BY pr.id DESC LIMIT 30
    ''')
    paint_logs = [dict(r) for r in cursor.fetchall()]

    metrics = get_global_metrics()
    conn.close()
    return render_template('boya_takip.html',
                           assemblies=assemblies,
                           projects=projects,
                           selected_project_id=selected_project_id,
                           paint_logs=paint_logs,
                           metrics=metrics)

@app.route('/api/boya-takip/kaydet', methods=['POST'])
def api_boya_kaydet():
    assembly_id = request.form.get('assembly_id')
    process_type = request.form.get('process_type', 'Boya') # 'Kumlama', 'Boya', 'Galvaniz', 'Tamamlandı'
    quantity = clean_int(request.form.get('quantity', 1))
    ral_code = request.form.get('ral_code', '').strip()
    dft_micron = request.form.get('dft_micron', '').strip()
    lot_no = request.form.get('lot_no', '').strip()
    completion_date = request.form.get('completion_date') or datetime.now().strftime("%Y-%m-%d")
    notes = request.form.get('notes', '').strip()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM assemblies WHERE id = ?", (assembly_id,))
    ass = cursor.fetchone()

    if not ass or quantity <= 0:
        flash("Geçersiz işlem!", "warning")
        return redirect(url_for('boya_takip'))

    # Boya kaydını logla
    u = session.get('user', {})
    cursor.execute('''
    INSERT INTO paint_records (project_id, assembly_id, assembly_pos, process_type, quantity, ral_code, dft_micron, lot_no, completion_date, operator_name, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (ass['project_id'], assembly_id, ass['assembly_pos'], process_type, quantity, ral_code, dft_micron, lot_no, completion_date, u.get('full_name'), notes))

    # Assemblies adetlerini güncelle
    if process_type == 'Tamamlandı' or process_type == 'Boya':
        new_done = ass['paint_done_qty'] + quantity
        cursor.execute('''
        UPDATE assemblies SET
            paint_done_qty = ?,
            paint_status = 'TAMAMLANDI',
            paint_ral = COALESCE(?, paint_ral),
            paint_dft = COALESCE(?, paint_dft)
        WHERE id = ?
        ''', (new_done, ral_code or None, dft_micron or None, assembly_id))
    elif process_type == 'Kumlama':
        cursor.execute("UPDATE assemblies SET paint_sandblast_qty = paint_sandblast_qty + ?, paint_status = 'KUMLAMADA' WHERE id = ?", (quantity, assembly_id))
    elif process_type == 'Galvaniz':
        cursor.execute("UPDATE assemblies SET paint_galv_qty = paint_galv_qty + ?, paint_done_qty = paint_done_qty + ?, paint_status = 'GALVANIZDE' WHERE id = ?", (quantity, quantity, assembly_id))

    conn.commit()
    conn.close()

    log_activity(
        action=f"Yüzey İşlem: {process_type}",
        entity_type="paint_records",
        entity_id=assembly_id,
        details=f"Marka '{ass['assembly_pos']}' ({quantity} Adet) -> {process_type} tamamlandı (RAL: {ral_code}, DFT: {dft_micron}).",
        username=u.get('username', 'Kullanıcı'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )

    flash(f"'{ass['assembly_pos']}' için {process_type} işlemi ({quantity} Adet) başarıyla kaydedildi.", "success")
    return redirect(url_for('boya_takip'))


# =========================================================================
# 10. SEVKİYAT TAKİP & A4 İRSALİYE / ÇEKİ LİSTESİ
# =========================================================================
@app.route('/sevk')
def sevk():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT s.*, p.code as project_code, p.name as project_name, p.customer
    FROM shipments s
    LEFT JOIN projects p ON s.project_id = p.id
    ORDER BY s.id DESC
    ''')
    shipments = [dict(r) for r in cursor.fetchall()]

    # Sevkiyata Hazır Mamuller (Boyası bitmiş, sevk edilmeyen adetleri olanlar)
    cursor.execute('''
    SELECT a.*, p.code as project_code, p.name as project_name, (a.paint_done_qty - a.shipped_qty) as ready_qty
    FROM assemblies a
    JOIN projects p ON a.project_id = p.id
    WHERE (a.paint_done_qty - a.shipped_qty) > 0
    ORDER BY p.name ASC, a.assembly_pos ASC
    ''')
    ready_assemblies = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT id, code, name, site_location FROM projects WHERE status != 'Arşiv' ORDER BY name ASC")
    projects = [dict(r) for r in cursor.fetchall()]

    metrics = get_global_metrics()
    conn.close()
    return render_template('sevk.html',
                           shipments=shipments,
                           ready_assemblies=ready_assemblies,
                           projects=projects,
                           metrics=metrics,
                           today_str=datetime.now().strftime("%Y-%m-%d"))

@app.route('/sevk/<int:shipment_id>')
def sevk_detay(shipment_id):
    """A4 Yazdırılabilir Sevk İrsaliyesi / Çeki Listesi Görünümü"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
    SELECT s.*, p.name as project_name, p.code as project_code, p.customer, p.site_location
    FROM shipments s
    LEFT JOIN projects p ON s.project_id = p.id
    WHERE s.id = ?
    ''', (shipment_id,))
    shipment = cursor.fetchone()
    if not shipment:
        conn.close()
        flash("Sevkiyat kaydı bulunamadı!", "danger")
        return redirect(url_for('sevk'))

    shipment_dict = dict(shipment)

    # Kalemler
    cursor.execute('SELECT * FROM shipment_items WHERE shipment_id = ? ORDER BY id ASC', (shipment_id,))
    items = [dict(r) for r in cursor.fetchall()]

    # Firma Ayarları
    cursor.execute('SELECT * FROM system_settings LIMIT 1')
    settings = dict(cursor.fetchone() or {})

    conn.close()
    return render_template('sevk_fisi.html', shipment=shipment_dict, items=items, settings=settings)

@app.route('/api/sevk/olustur', methods=['POST'])
def api_sevk_olustur():
    """Kısmi adetleri seçerek yeni sevkiyat irsaliyesi oluşturur."""
    project_id = request.form.get('project_id')
    dispatch_no = request.form.get('dispatch_no', '').strip().upper()
    vehicle_plate = request.form.get('vehicle_plate', '').strip().upper()
    driver_name = request.form.get('driver_name', '').strip()
    driver_phone = request.form.get('driver_phone', '').strip()
    carrier_company = request.form.get('carrier_company', '').strip()
    dispatch_date = request.form.get('dispatch_date') or datetime.now().strftime("%Y-%m-%d")
    destination = request.form.get('destination', '').strip()
    notes = request.form.get('notes', '').strip()

    # Seçilen montaj ID'leri ve sevk adetleri
    assembly_ids = request.form.getlist('assembly_ids')

    if not dispatch_no or not vehicle_plate or not assembly_ids:
        flash("İrsaliye No, Araç Plakası ve en az bir sevk kalemi seçilmelidir!", "warning")
        return redirect(url_for('sevk'))

    conn = get_db()
    cursor = conn.cursor()

    total_qty = 0
    total_weight = 0.0
    items_to_insert = []

    for a_id in assembly_ids:
        qty_val = clean_int(request.form.get(f'qty_{a_id}', 0))
        if qty_val <= 0: continue

        cursor.execute("SELECT * FROM assemblies WHERE id = ?", (a_id,))
        ass = cursor.fetchone()
        if not ass: continue

        u_wt = ass['unit_weight']
        tot_wt = qty_val * u_wt

        total_qty += qty_val
        total_weight += tot_wt

        items_to_insert.append((a_id, ass['assembly_pos'], ass['description'], qty_val, u_wt, tot_wt))

    if not items_to_insert:
        flash("Lütfen sevk edilecek geçerli miktarlar girin!", "warning")
        return redirect(url_for('sevk'))

    total_tonnage = round(total_weight / 1000.0, 2)
    u = session.get('user', {})

    # Sevkiyatı kaydet
    cursor.execute('''
    INSERT INTO shipments (project_id, dispatch_no, vehicle_plate, driver_name, driver_phone, carrier_company, dispatch_date, destination, total_quantity, total_tonnage, notes, created_by)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (project_id, dispatch_no, vehicle_plate, driver_name, driver_phone, carrier_company, dispatch_date, destination, total_qty, total_tonnage, notes, u.get('full_name', 'Sevkiyatçı')))
    shipment_id = cursor.lastrowid

    # Kalemleri kaydet ve assemblies shipped_qty güncelle
    for it in items_to_insert:
        a_id, a_pos, desc, q, u_w, t_w = it
        cursor.execute('''
        INSERT INTO shipment_items (shipment_id, assembly_id, assembly_pos, description, quantity, unit_weight, total_weight)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (shipment_id, a_id, a_pos, desc, q, u_w, t_w))

        cursor.execute("UPDATE assemblies SET shipped_qty = shipped_qty + ? WHERE id = ?", (q, a_id))

    conn.commit()
    conn.close()

    log_activity(
        action="Sevkiyat Oluşturuldu",
        entity_type="shipments",
        entity_id=shipment_id,
        details=f"'{dispatch_no}' ({vehicle_plate}) ile {total_qty} Adet ({total_tonnage} Ton) sevk edildi.",
        username=u.get('username', 'Kullanıcı'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )

    flash(f"'{dispatch_no}' numaralı sevkiyat ({total_tonnage} Ton) başarıyla oluşturuldu!", "success")
    return redirect(url_for('sevk_detay', shipment_id=shipment_id))

@app.route('/api/sevk/<int:shipment_id>/excel')
def api_sevk_excel(shipment_id):
    """Sevk İrsaliyesi / Çeki Listesini Excel olarak indirir."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,))
    shipment = dict(cursor.fetchone() or {})

    cursor.execute("SELECT * FROM shipment_items WHERE shipment_id = ?", (shipment_id,))
    items = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT * FROM system_settings LIMIT 1")
    settings = dict(cursor.fetchone() or {})
    conn.close()

    excel_stream = export_shipment_excel(shipment, items, settings)
    return send_file(
        excel_stream,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f"Sevk_Irsaliyesi_{shipment.get('dispatch_no', shipment_id)}.xlsx"
    )

@app.route('/api/sevk/<int:shipment_id>/sil', methods=['POST'])
def api_sevk_sil(shipment_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT assembly_id, quantity FROM shipment_items WHERE shipment_id = ?", (shipment_id,))
    items = cursor.fetchall()
    for it in items:
        if it['assembly_id']:
            cursor.execute("UPDATE assemblies SET shipped_qty = MAX(0, shipped_qty - ?) WHERE id = ?", (it['quantity'], it['assembly_id']))

    cursor.execute("DELETE FROM shipments WHERE id = ?", (shipment_id,))
    conn.commit()
    conn.close()

    flash("Sevkiyat iptal edildi ve parçalar tekrar sevk bekliyor havuzuna alındı.", "info")
    return redirect(url_for('sevk'))


# =========================================================================
# 11. KULLANICI YÖNETİMİ & DENETİM GÜNLÜĞÜ (AUDIT LOG)
# =========================================================================
@app.route('/kullanicilar')
def kullanicilar():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, full_name, role, is_active, created_at FROM users ORDER BY id ASC")
    users = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return render_template('kullanicilar.html', users=users)

@app.route('/api/kullanicilar/ekle', methods=['POST'])
def api_kullanici_ekle():
    username = request.form.get('username', '').strip().lower()
    password = request.form.get('password', '').strip()
    full_name = request.form.get('full_name', '').strip()
    role = request.form.get('role', 'İzleyici')

    if not username or not password or not full_name:
        flash("Kullanıcı adı, şifre ve ad soyad zorunludur!", "warning")
        return redirect(url_for('kullanicilar'))

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO users (username, password_hash, full_name, role)
        VALUES (?, ?, ?, ?)
        ''', (username, hash_password(password), full_name, role))
        conn.commit()
        conn.close()
        flash(f"'{full_name}' ({role}) kullanıcısı başarıyla eklendi.", "success")
    except Exception as e:
        conn.close()
        flash(f"Kullanıcı eklenemedi (Kullanıcı adı zaten var olabilir): {str(e)}", "danger")

    return redirect(url_for('kullanicilar'))

@app.route('/api/kullanicilar/<int:user_id>/durum', methods=['POST'])
def api_kullanici_durum(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    flash("Kullanıcı durumu güncellendi.", "info")
    return redirect(url_for('kullanicilar'))

@app.route('/aktivite-loglari')
def aktivite_loglari():
    """Her kullanıcının yaptığı işlemleri şeffaf gösteren Denetim Günlüğü."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM activity_logs ORDER BY id DESC LIMIT 200')
    logs = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return render_template('aktivite_loglari.html', logs=logs)


# =========================================================================
# 12. AĞ, TELEFON, TABLET & UZAK ERİŞİM REHBERİ
# =========================================================================
@app.route('/ag-rehberi')
def ag_rehberi():
    return render_template('ag_rehberi.html', local_ip=get_local_ip())


# =========================================================================
# 13. RAPORLAR & SİSTEM AYARLARI
# =========================================================================
@app.route('/raporlar')
def raporlar():
    metrics = get_global_metrics()
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM projects WHERE status != 'Arşiv' ORDER BY id DESC")
    projects_raw = cursor.fetchall()
    projects = []
    for p in projects_raw:
        s = get_project_summary(p['id'])
        if s: projects.append(s)

    cursor.execute('SELECT * FROM system_settings LIMIT 1')
    settings = dict(cursor.fetchone() or {})
    conn.close()

    return render_template('raporlar.html', metrics=metrics, projects=projects, settings=settings)

@app.route('/api/ayarlar/guncelle', methods=['POST'])
def api_ayarlar_guncelle():
    company_name = request.form.get('company_name')
    company_sub_title = request.form.get('company_sub_title')
    company_address = request.form.get('company_address')
    company_phone = request.form.get('company_phone')
    company_email = request.form.get('company_email')
    company_tax_info = request.form.get('company_tax_info')

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE system_settings
    SET company_name = ?, company_sub_title = ?, company_address = ?, company_phone = ?, company_email = ?, company_tax_info = ?
    WHERE id = 1
    ''', (company_name, company_sub_title, company_address, company_phone, company_email, company_tax_info))
    conn.commit()
    conn.close()

    flash("Firma bilgileri ve sistem ayarları güncellendi.", "success")
    return redirect(url_for('raporlar'))

@app.route('/api/excel-sablon')
def api_excel_sablon():
    excel_stream = generate_template_excel()
    return send_file(
        excel_stream,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='Tekla_Imalat_Sablonu.xlsx'
    )


# =========================================================================
# SUNUCU ÇALIŞTIRMA
# =========================================================================
if __name__ == '__main__':
    local_ip = get_local_ip()
    print("=" * 70)
    print("   🏗️ İMALAT, MONTAJ, BOYA VE SEVKİYAT TAKİP SİSTEMİ BAŞLATILDI")
    print("=" * 70)
    print(f" -> Bu bilgisayardan açmak için : http://localhost:5000")
    print(f" -> Telefon / Tablet / Yerel Ağ : http://{local_ip}:5000")
    print("=" * 70)
    print(" Bilgi: Evde, işyerinde veya sahada telefonunuzun tarayıcısına")
    print(f" 'http://{local_ip}:5000' yazarak anlık işlem yapabilirsiniz.")
    print("=" * 70)
    app.run(host='0.0.0.0', port=5000, debug=False)
