import os
import re
import math
import socket
import json
import threading
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
    get_global_metrics, get_project_summary, get_customers, set_project_status,
    get_system_settings, update_system_settings,
    get_machines, get_machine_by_id, add_machine, update_machine, delete_machine,
    get_all_role_permissions, update_role_permission,
    can_user_edit, get_next_dispatch_no,
    get_user_by_id, update_user_profile,
    approve_material_order, reject_material_order,
    get_cutting_progress_analysis,
    admin_update_user,
    get_active_announcements, add_announcement, deactivate_announcement, delete_announcement,
    get_chat_messages, save_chat_message, clear_chat_messages, delete_chat_message,
    get_meetings, get_meeting_by_id, add_meeting, update_meeting, delete_meeting,
    get_meeting_action_items, add_meeting_action_item, update_action_item_status, delete_meeting_action_item,
    add_notification, get_recent_notifications,
    save_push_subscription, get_push_subscriptions, delete_push_subscription,
    get_shipments_with_accounting, update_shipment_accounting, get_accounting_summary_stats,
    ROLES_LIST, MODULES_LIST
)
from excel_handler import (
    parse_tekla_excel, parse_excel_parts, generate_template_excel,
    export_material_rfq_excel, export_project_report_excel, export_shipment_excel,
    parse_assemblies_file, parse_assembly_parts_file, parse_parts_file, parse_clipboard_table
)
from seed_data import seed_demo_data

import jinja2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static')
)

# Şablonları hem 'templates/' klasöründe hem de ana dizinde bulacak akıllı yükleyici:
app.jinja_loader = jinja2.ChoiceLoader([
    jinja2.FileSystemLoader(os.path.join(BASE_DIR, 'templates')),
    jinja2.FileSystemLoader(BASE_DIR),
    jinja2.FileSystemLoader(os.path.join(os.getcwd(), 'templates')),
    jinja2.FileSystemLoader(os.getcwd()),
    jinja2.FileSystemLoader('/opt/render/project/src/templates'),
    jinja2.FileSystemLoader('/opt/render/project/src')
])

app.secret_key = os.environ.get('SECRET_KEY', 'celik_imalat_takip_gizli_anahtar_2026_super_secure')

# =========================================================================
# WEB PUSH (VAPID) BİLDİRİM MOTORU
# =========================================================================
try:
    from pywebpush import webpush, WebPushException
except ImportError:
    webpush = None
    WebPushException = Exception

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "BPBRMOLSjWIA_FowmaSo7BfrXTrLyiT9rkkznIDQ11yH_XB7gqiGwqMYPt7-fVde7wA1nvDL7L8Lz211HeNt8NM")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "6iJCT6-yECbniensUcfe2x_mxGcXfMVxvHWkK3skmBA")
VAPID_CLAIMS = {"sub": "mailto:admin@ordumak.com.tr"}

def send_web_push_notification(title, message, url="/", icon="/static/img/ordumak_logo.svg"):
    """Kayıtlı tüm mobil ve masaüstü tarayıcılara arka planda Web Push bildirimi gönderir."""
    def _send_task():
        if not webpush:
            return
        try:
            subscriptions = get_push_subscriptions()
            if not subscriptions:
                return
            payload_data = json.dumps({
                "title": title,
                "body": message,
                "icon": icon,
                "badge": icon,
                "url": url
            })
            for sub in subscriptions:
                subscription_info = {
                    "endpoint": sub["endpoint"],
                    "keys": {
                        "p256dh": sub["p256dh"],
                        "auth": sub["auth"]
                    }
                }
                try:
                    webpush(
                        subscription_info=subscription_info,
                        data=payload_data,
                        vapid_private_key=VAPID_PRIVATE_KEY,
                        vapid_claims=VAPID_CLAIMS
                    )
                except WebPushException as ex:
                    if hasattr(ex, 'response') and ex.response is not None and ex.response.status_code in (404, 410):
                        delete_push_subscription(sub["endpoint"])
                    else:
                        print(f"WebPush sending note: {ex}")
                except Exception as e:
                    print(f"WebPush item error: {e}")
        except Exception as e:
            print(f"WebPush background thread error: {e}")

    threading.Thread(target=_send_task, daemon=True).start()


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
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%d.%m.%Y %H:%M:%S', '%d.%m.%Y'):
        try:
            dt = datetime.strptime(str(val_str).split('.')[0], fmt)
            return dt.strftime('%d.%m.%Y')
        except:
            pass
    return str(val_str)

@app.template_filter('format_datetime')
def jinja_fmt_datetime(val_str):
    if not val_str: return "-"
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%d.%m.%Y %H:%M:%S', '%d.%m.%Y'):
        try:
            dt = datetime.strptime(str(val_str).split('.')[0], fmt)
            return dt.strftime('%d.%m.%Y %H:%M:%S')
        except:
            pass
    return str(val_str)

@app.context_processor
def inject_global_vars():
    user = session.get('user')
    user_role = user.get('role', 'izleyici') if user else 'izleyici'
    settings = get_system_settings()

    def user_can_edit(module_name):
        if not user:
            return False
        return can_user_edit(user.get('role'), module_name)

    active_announcements = []
    try:
        active_announcements = get_active_announcements()
    except Exception:
        pass

    return {
        'local_ip': get_local_ip(),
        'current_user': user,
        'user_role': user_role,
        'system_settings': settings,
        'can_user_edit': user_can_edit,
        'active_announcements': active_announcements,
        'now': datetime.now()
    }

_db_initialized = False

@app.before_request
def ensure_db_and_auth():
    global _db_initialized
    if not _db_initialized:
        try:
            init_db()
            seed_demo_data()
            _db_initialized = True
        except Exception as e:
            print(f"Veritabani hazirlama uyarisi: {e}")

    # Zorunlu Giriş - Giriş yapmamış kullanıcıları login sayfasına yönlendir (Statik dosyalar, SW, Manifest ve Push API hariç)
    if request.endpoint in ('login', 'static', 'service_worker', 'pwa_manifest', 'api_push_vapid_key', 'api_push_subscribe') or (request.path and (request.path.startswith('/static/') or request.path in ('/sw.js', '/manifest.json', '/api/push/vapid-public-key', '/api/push/subscribe'))):
        return
    if 'user' not in session:
        return redirect(url_for('login'))


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
    selected_customer = request.args.get('musteri', '').strip()
    customers = get_customers()
    metrics = get_global_metrics(customer=selected_customer if selected_customer else None)
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Projelerin aşama durumlarıyla listesi (Aktif olanlar)
    if selected_customer:
        cursor.execute("SELECT * FROM projects WHERE status != 'Arşiv' AND status != 'Tamamlandı' AND customer = ? ORDER BY id DESC", (selected_customer,))
    else:
        cursor.execute("SELECT * FROM projects WHERE status != 'Arşiv' AND status != 'Tamamlandı' ORDER BY id DESC")
    projects_raw = cursor.fetchall()
    projects = []
    for p in projects_raw:
        summary = get_project_summary(p['id'])
        if summary:
            projects.append(summary)

    # Son Sevkiyatlar
    if selected_customer:
        cursor.execute('''
        SELECT s.*, p.name as project_name, p.code as project_code
        FROM shipments s
        LEFT JOIN projects p ON s.project_id = p.id
        WHERE p.customer = ?
        ORDER BY s.id DESC LIMIT 5
        ''', (selected_customer,))
    else:
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
                           customers=customers,
                           selected_customer=selected_customer,
                           recent_shipments=recent_shipments,
                           recent_activities=recent_activities)


# =========================================================================
# 2. PROJE TAKİP & TEKLA LİSTELERİ
# =========================================================================
@app.route('/projeler')
def projeler():
    tab = request.args.get('tab', 'aktif')
    conn = get_db()
    cursor = conn.cursor()
    
    # Aktif Projeler
    cursor.execute("SELECT * FROM projects WHERE status != 'Arşiv' AND status != 'Tamamlandı' ORDER BY id DESC")
    active_raw = cursor.fetchall()
    active_projects = []
    for p in active_raw:
        summary = get_project_summary(p['id'])
        if summary: active_projects.append(summary)
        
    # Biten Projeler
    cursor.execute("SELECT * FROM projects WHERE status = 'Tamamlandı' ORDER BY id DESC")
    finished_raw = cursor.fetchall()
    finished_projects = []
    for p in finished_raw:
        summary = get_project_summary(p['id'])
        if summary: finished_projects.append(summary)

    conn.close()
    metrics = get_global_metrics()
    return render_template('projeler.html',
                           active_projects=active_projects,
                           finished_projects=finished_projects,
                           active_tab=tab,
                           projects=active_projects if tab == 'aktif' else finished_projects,
                           metrics=metrics)

@app.route('/api/projeler/<int:project_id>/durum', methods=['POST'])
def api_proje_durum(project_id):
    """Projenin durumunu değiştirir (Örn: Tamamlandı / Aktif)."""
    status = request.form.get('status', 'Tamamlandı').strip()
    set_project_status(project_id, status)
    
    u = session.get('user', {})
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT code, name FROM projects WHERE id = ?", (project_id,))
    p_row = cursor.fetchone()
    conn.close()
    p_name = f"{p_row['code']} - {p_row['name']}" if p_row else f"Proje #{project_id}"
    
    log_activity(
        action=f"Proje Durumu: {status}",
        entity_type="projects",
        entity_id=project_id,
        details=f"'{p_name}' projesinin durumu '{status}' olarak güncellendi.",
        username=u.get('username', 'Kullanıcı'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )
    
    add_notification(
        category='proje_durum',
        title=f"Proje Durumu: {status}",
        message=f"'{p_name}' projesi '{status}' olarak işaretlendi.",
        icon='fa-flag-checkered' if status == 'Tamamlandı' else 'fa-play',
        color='emerald' if status == 'Tamamlandı' else 'blue',
        link_url=url_for('projeler', tab='biten' if status == 'Tamamlandı' else 'aktif')
    )
    
    flash(f"'{p_name}' projesi başarıyla '{status}' olarak güncellendi.", "success")
    return redirect(url_for('projeler', tab='biten' if status == 'Tamamlandı' else 'aktif'))

@app.route('/api/projeler/<int:project_id>/guncelle', methods=['POST'])
def api_proje_guncelle(project_id):
    """Proje temel bilgilerini günceller."""
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
    status = request.form.get('status', 'Aktif').strip()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE projects
        SET code = ?, name = ?, customer = ?, site_location = ?, start_date = ?,
            cutting_start_date = ?, delivery_date = ?, target_tonnage = ?, color = ?,
            pos_prefix = ?, notes = ?, status = ?
        WHERE id = ?
    """, (code, name, customer, site_location, start_date, cutting_start_date, delivery_date, target_tonnage, color, pos_prefix, notes, status, project_id))
    conn.commit()
    conn.close()

    flash(f"'{name}' projesi bilgileri güncellendi.", "success")
    return redirect(url_for('projeler', tab='biten' if status == 'Tamamlandı' else 'aktif'))

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

@app.route('/api/projeler/<int:project_id>/assembly-yukle', methods=['POST'])
def api_assembly_yukle(project_id):
    """Montaj Listesi (Assemblies) için Excel veya Kopyala-Yapıştır Tablo içe aktarma."""
    pasted_text = request.form.get('pasted_text', '').strip()
    excel_file = request.files.get('excel_file')
    
    assemblies = []
    try:
        if pasted_text:
            assemblies = parse_clipboard_table(pasted_text, 'assemblies')
        elif excel_file and excel_file.filename != '':
            assemblies = parse_assemblies_file(excel_file.stream)
        else:
            flash("Lütfen bir Excel dosyası seçin veya tabloyu yapıştırın.", "warning")
            return redirect(url_for('proje_detay', project_id=project_id))

        if not assemblies:
            flash("Okunabilir montaj verisi bulunamadı.", "warning")
            return redirect(url_for('proje_detay', project_id=project_id))

        conn = get_db()
        cursor = conn.cursor()
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
            ''', (project_id, a['assembly_pos'], a.get('description', 'İmalat Elemanı'), a.get('profile_type', '-'), a.get('quantity', 1), a.get('unit_weight', 0.0), a.get('total_weight', 0.0), a.get('material_grade', 'S275JR')))
        conn.commit()
        conn.close()

        u = session.get('user', {})
        log_activity(
            action="Montaj Listesi Yüklendi",
            entity_type="assemblies",
            entity_id=project_id,
            details=f"{len(assemblies)} adet montaj markası sisteme aktarıldı.",
            username=u.get('username', 'Kullanıcı'),
            user_id=u.get('id'),
            ip_address=request.remote_addr
        )

        flash(f"Başarılı! {len(assemblies)} montaj markası başarıyla yüklendi/güncellendi.", "success")
    except Exception as e:
        flash(f"Montaj listesi aktarılırken hata: {str(e)}", "danger")

    return redirect(url_for('proje_detay', project_id=project_id))

@app.route('/api/projeler/<int:project_id>/assembly-parts-yukle', methods=['POST'])
def api_assembly_parts_yukle(project_id):
    """Montaj Parça Listesi (Assembly Parts) için Excel veya Kopyala-Yapıştır Tablo içe aktarma."""
    pasted_text = request.form.get('pasted_text', '').strip()
    excel_file = request.files.get('excel_file')
    
    assembly_parts = []
    try:
        if pasted_text:
            assembly_parts = parse_clipboard_table(pasted_text, 'assembly_parts')
        elif excel_file and excel_file.filename != '':
            assembly_parts = parse_assembly_parts_file(excel_file.stream)
        else:
            flash("Lütfen bir Excel dosyası seçin veya tabloyu yapıştırın.", "warning")
            return redirect(url_for('proje_detay', project_id=project_id))

        if not assembly_parts:
            flash("Okunabilir montaj parça verisi bulunamadı.", "warning")
            return redirect(url_for('proje_detay', project_id=project_id))

        conn = get_db()
        cursor = conn.cursor()
        for ap in assembly_parts:
            cursor.execute("SELECT id FROM assemblies WHERE project_id = ? AND assembly_pos = ?", (project_id, ap['assembly_pos']))
            ass_row = cursor.fetchone()
            ass_id = ass_row['id'] if ass_row else None

            cursor.execute('''
            INSERT INTO assembly_parts (project_id, assembly_id, assembly_pos, part_pos, description, profile_type, quantity_per_assembly, total_quantity, length, unit_weight, total_weight, material_grade)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (project_id, ass_id, ap['assembly_pos'], ap['part_pos'], ap.get('description', ''), ap.get('profile_type', '-'), ap.get('quantity_per_assembly', 1), ap.get('total_quantity', 1), ap.get('length', 0.0), ap.get('unit_weight', 0.0), ap.get('total_weight', 0.0), ap.get('material_grade', 'S275JR')))
        conn.commit()
        conn.close()

        u = session.get('user', {})
        log_activity(
            action="Montaj Parça Listesi Yüklendi",
            entity_type="assembly_parts",
            entity_id=project_id,
            details=f"{len(assembly_parts)} adet montaj parçası aktarıldı.",
            username=u.get('username', 'Kullanıcı'),
            user_id=u.get('id'),
            ip_address=request.remote_addr
        )

        flash(f"Başarılı! {len(assembly_parts)} montaj parça kaydı sisteme aktarıldı.", "success")
    except Exception as e:
        flash(f"Montaj parçaları aktarılırken hata: {str(e)}", "danger")

    return redirect(url_for('proje_detay', project_id=project_id))

@app.route('/api/projeler/<int:project_id>/parts-yukle', methods=['POST'])
def api_parts_yukle(project_id):
    """Tek Parça / Poz Listesi (Parts) için Excel veya Kopyala-Yapıştır Tablo içe aktarma."""
    pasted_text = request.form.get('pasted_text', '').strip()
    excel_file = request.files.get('excel_file')
    
    parts = []
    try:
        if pasted_text:
            parts = parse_clipboard_table(pasted_text, 'parts')
        elif excel_file and excel_file.filename != '':
            parts = parse_parts_file(excel_file.stream)
        else:
            flash("Lütfen bir Excel dosyası seçin veya tabloyu yapıştırın.", "warning")
            return redirect(url_for('proje_detay', project_id=project_id))

        if not parts:
            flash("Okunabilir parça verisi bulunamadı.", "warning")
            return redirect(url_for('proje_detay', project_id=project_id))

        conn = get_db()
        cursor = conn.cursor()
        for p in parts:
            cursor.execute('''
            INSERT INTO parts (project_id, pos_no, name, profile_type, quantity, length, unit_weight, total_weight, material_grade)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (project_id, p['pos_no'], p.get('name', 'Poz Parçası'), p.get('profile_type', '-'), p.get('quantity', 1), p.get('length', 0.0), p.get('unit_weight', 0.0), p.get('total_weight', 0.0), p.get('material_grade', 'S275JR')))
        conn.commit()
        conn.close()

        u = session.get('user', {})
        log_activity(
            action="Tek Parça Listesi Yüklendi",
            entity_type="parts",
            entity_id=project_id,
            details=f"{len(parts)} adet parça pozu sisteme aktarıldı.",
            username=u.get('username', 'Kullanıcı'),
            user_id=u.get('id'),
            ip_address=request.remote_addr
        )

        flash(f"Başarılı! {len(parts)} adet parça pozu sisteme başarıyla aktarıldı.", "success")
    except Exception as e:
        flash(f"Parça listesi aktarılırken hata: {str(e)}", "danger")

    return redirect(url_for('proje_detay', project_id=project_id))

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
    active_tab = request.args.get('tab', 'onaylanan') # 'talepler' or 'onaylanan'
    
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
    all_ordered_items = [dict(r) for r in cursor.fetchall()]
    
    cursor.execute(query_gel, params)
    received_items = [dict(r) for r in cursor.fetchall()]

    # Ayrıştır: Satınalma Onay Bekleyen Talepler vs Onaylanmış Kesin Siparişler
    pending_requests = [o for o in all_ordered_items if o.get('approval_status') == 'Onay Bekliyor']
    approved_orders = [o for o in all_ordered_items if o.get('approval_status') != 'Onay Bekliyor' and o.get('approval_status') != 'Reddedildi']
    rejected_requests = [o for o in all_ordered_items if o.get('approval_status') == 'Reddedildi']

    # Kalan Malzeme Özeti YALNIZCA Onaylanmış Siparişler üzerinden Hesaplanır
    ordered_map = {}
    for o in approved_orders:
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

    tot_pending_w = sum(p['weight'] for p in pending_requests)

    conn.close()
    return render_template('siparis_takip.html',
                           projects=projects,
                           selected_project_id=selected_project_id,
                           active_tab=active_tab,
                           pending_requests=pending_requests,
                           approved_orders=approved_orders,
                           rejected_requests=rejected_requests,
                           ordered_items=approved_orders,
                           received_items=received_items,
                           remaining_items=remaining_items,
                           tot_pending_ton=round(tot_pending_w / 1000.0, 2),
                           tot_ordered_ton=round(tot_ordered_w / 1000.0, 2),
                           tot_received_ton=round(tot_received_w / 1000.0, 2),
                           tot_remaining_ton=round(tot_remaining_w / 1000.0, 2))

@app.route('/api/siparis-takip/talep-ekle', methods=['POST'])
@app.route('/api/siparis-takip/verilen-ekle', methods=['POST'])
def api_siparis_verilen_ekle():
    """Yeni malzeme sipariş talebi oluşturur (Satınalma onayı için havuza gönderilir)."""
    project_id = request.form.get('project_id') or None
    material = request.form.get('material', '').strip().upper()
    thickness = clean_float(request.form.get('thickness', 0))
    width = clean_float(request.form.get('width', 0))
    length = clean_float(request.form.get('length', 0))
    quantity = clean_int(request.form.get('quantity', 1))
    weight = clean_float(request.form.get('weight', 0))
    supplier = request.form.get('supplier', '').strip()
    notes = request.form.get('notes', '').strip()
    
    # Kullanıcı rolüne göre doğrudan onaylı mı yoksa onay bekliyor mu?
    u = session.get('user', {})
    user_role = u.get('role', '')
    is_auto_approved = user_role in ('admin', 'patron', 'satınalma')
    approval_status = 'Onaylandı' if is_auto_approved else 'Onay Bekliyor'
    approved_by = u.get('full_name', 'Yetkili') if is_auto_approved else ''
    approved_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if is_auto_approved else ''

    conn = get_db()
    cursor = conn.cursor()
    p_code = ""
    if project_id:
        cursor.execute("SELECT code FROM projects WHERE id = ?", (project_id,))
        r = cursor.fetchone()
        if r: p_code = r['code']

    cursor.execute('''
    INSERT INTO material_orders_ordered (project_id, project_code, material, thickness, width, length, quantity, weight, supplier, approval_status, approved_by, approved_date, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (project_id, p_code, material, thickness, width, length, quantity, weight, supplier, approval_status, approved_by, approved_date, notes))
    conn.commit()
    conn.close()

    if is_auto_approved:
        add_notification(
            category='siparis_onay',
            title='Yeni Sipariş Onaylandı',
            message=f"{p_code or 'Genel'} - {material} ({quantity} adet, {weight} kg) siparişi onaylandı.",
            icon='fa-circle-check',
            color='emerald',
            link_url=url_for('siparis_takip', tab='onaylanan')
        )
        flash(f"'{material}' siparişi onaylı olarak kaydedildi.", "success")
    else:
        add_notification(
            category='siparis_talep',
            title='Yeni Sipariş Talebi',
            message=f"{u.get('full_name', 'Personel')} {material} ({quantity} adet) için sipariş talebi açtı. Satınalma onayı bekleniyor.",
            icon='fa-cart-plus',
            color='amber',
            link_url=url_for('siparis_takip', tab='talepler')
        )
        flash(f"'{material}' malzeme sipariş talebi oluşturuldu, Satınalma onayına gönderildi.", "info")

    return redirect(url_for('siparis_takip', proje_id=project_id or '', tab='talepler' if not is_auto_approved else 'onaylanan'))

@app.route('/api/siparis-takip/<int:order_id>/onayla', methods=['POST'])
def api_siparis_talep_onayla(order_id):
    """Satınalma / Yönetici malzeme sipariş talebini onaylar."""
    u = session.get('user', {})
    approver_name = u.get('full_name', 'Satınalma Yetkilisi')
    approve_material_order(order_id, approver_name)
    add_notification(
        category='siparis_onay',
        title='Sipariş Talebi Onaylandı',
        message=f"Talep #{order_id} Satınalma ({approver_name}) tarafından ONAYLANDI ve onaylanan siparişlere aktarıldı.",
        icon='fa-circle-check',
        color='emerald',
        link_url=url_for('siparis_takip', tab='onaylanan')
    )
    flash("Malzeme sipariş talebi ONAYLANDI ve onaylanan siparişler havuzuna aktarıldı.", "success")
    return redirect(request.referrer or url_for('siparis_takip', tab='onaylanan'))

@app.route('/api/siparis-takip/<int:order_id>/reddet', methods=['POST'])
def api_siparis_talep_reddet(order_id):
    """Satınalma / Yönetici malzeme sipariş talebini reddeder."""
    u = session.get('user', {})
    approver_name = u.get('full_name', 'Satınalma Yetkilisi')
    reject_material_order(order_id, approver_name)
    add_notification(
        category='siparis_red',
        title='Sipariş Talebi Reddedildi',
        message=f"Talep #{order_id} Satınalma ({approver_name}) tarafından REDDEDİLDİ.",
        icon='fa-circle-xmark',
        color='rose',
        link_url=url_for('siparis_takip', tab='talepler')
    )
    flash("Malzeme sipariş talebi REDDEDİLDİ.", "warning")
    return redirect(request.referrer or url_for('siparis_takip', tab='talepler'))

@app.route('/api/siparis-takip/verilen-guncelle', methods=['POST'])
def api_siparis_verilen_guncelle():
    """Sipariş verilen malzemenin bilgilerini günceller."""
    item_id = request.form.get('id')
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
    cursor.execute('''
    UPDATE material_orders_ordered
    SET material = ?, thickness = ?, width = ?, length = ?, quantity = ?, weight = ?, supplier = ?, notes = ?
    WHERE id = ?
    ''', (material, thickness, width, length, quantity, weight, supplier, notes, item_id))
    conn.commit()
    conn.close()

    flash("Sipariş verilen malzeme güncellendi.", "success")
    return redirect(request.referrer or url_for('siparis_takip'))

@app.route('/api/siparis-takip/verilen-sil', methods=['POST'])
def api_siparis_verilen_sil():
    """Sipariş verilen malzemeyi siler."""
    item_id = request.form.get('id')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM material_orders_ordered WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    flash("Sipariş kaydı silindi.", "info")
    return redirect(request.referrer or url_for('siparis_takip'))

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
    invoice_photo_url = ""

    if 'invoice_photo' in request.files:
        photo = request.files['invoice_photo']
        if photo and photo.filename != '':
            ext = os.path.splitext(photo.filename)[1].lower()
            if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                filename = f"inv_{int(datetime.now().timestamp())}_{photo.filename}"
                save_dir = os.path.join(BASE_DIR, 'static', 'uploads', 'invoices')
                os.makedirs(save_dir, exist_ok=True)
                photo.save(os.path.join(save_dir, filename))
                invoice_photo_url = f"/static/uploads/invoices/{filename}"

    conn = get_db()
    cursor = conn.cursor()
    p_code = ""
    if project_id:
        cursor.execute("SELECT code FROM projects WHERE id = ?", (project_id,))
        r = cursor.fetchone()
        if r: p_code = r['code']

    cursor.execute('''
    INSERT INTO material_orders_received (project_id, project_code, material, thickness, width, length, quantity, weight, received_date, waybill_no, notes, invoice_photo_url)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (project_id, p_code, material, thickness, width, length, quantity, weight, received_date, waybill_no, notes, invoice_photo_url))
    conn.commit()
    conn.close()

    add_notification(
        category='malzeme_geldi',
        title='Malzeme Teslim Alındı',
        message=f"{p_code or 'Genel'} - {material} ({quantity} Adet, {weight} kg) irsaliye #{waybill_no} ile fabrikaya teslim alındı.",
        icon='fa-boxes-packing',
        color='emerald',
        link_url=url_for('siparis_takip', proje_id=project_id or '')
    )

    flash("Gelen malzeme ve irsaliye görseli başarıyla kaydedildi.", "success")
    return redirect(url_for('siparis_takip', proje_id=project_id or ''))

@app.route('/api/siparis-takip/gelen-guncelle', methods=['POST'])
def api_siparis_gelen_guncelle():
    """Gelen malzemenin bilgilerini günceller."""
    item_id = request.form.get('id')
    material = request.form.get('material', '').strip().upper()
    thickness = clean_float(request.form.get('thickness', 0))
    width = clean_float(request.form.get('width', 0))
    length = clean_float(request.form.get('length', 0))
    quantity = clean_int(request.form.get('quantity', 1))
    weight = clean_float(request.form.get('weight', 0))
    received_date = request.form.get('received_date')
    waybill_no = request.form.get('waybill_no', '').strip()
    notes = request.form.get('notes', '').strip()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE material_orders_received
    SET material = ?, thickness = ?, width = ?, length = ?, quantity = ?, weight = ?, received_date = ?, waybill_no = ?, notes = ?
    WHERE id = ?
    ''', (material, thickness, width, length, quantity, weight, received_date, waybill_no, notes, item_id))
    conn.commit()
    conn.close()

    flash("Gelen malzeme kaydı güncellendi.", "success")
    return redirect(request.referrer or url_for('siparis_takip'))

@app.route('/api/siparis-takip/gelen-sil', methods=['POST'])
def api_siparis_gelen_sil():
    """Gelen malzeme kaydını siler."""
    item_id = request.form.get('id')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM material_orders_received WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    flash("Gelen malzeme kaydı silindi.", "info")
    return redirect(request.referrer or url_for('siparis_takip'))

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
# 4. ÖN İMALAT PLAN (MAKİNE TAKVİMİ & MAKİNE YÖNETİMİ)
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

    all_machines = get_machines()

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
                           all_machines=all_machines,
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

@app.route('/api/makineler/ekle', methods=['POST'])
def api_makine_ekle():
    """Yeni makine / istasyon ekler."""
    name = request.form.get('name', '').strip()
    m_type = request.form.get('type', 'Kesim').strip()
    code = request.form.get('code', '').strip()
    capacity = float(request.form.get('capacity_ton_day') or 10.0)
    hourly_rate = float(request.form.get('hourly_rate') or 0.0)
    operator = request.form.get('operator_name', '').strip()
    status = request.form.get('status', 'Aktif').strip()
    notes = request.form.get('notes', '').strip()

    if name:
        add_machine(name, m_type, code, capacity, hourly_rate, operator, status, notes)
        flash(f"'{name}' tezgahı/istasyonu başarıyla eklendi.", "success")
    return redirect(request.referrer or url_for('on_imalat_plan'))

@app.route('/api/makineler/<int:machine_id>/duzenle', methods=['POST'])
def api_makine_duzenle(machine_id):
    """Mevcut makine / tezgah bilgilerini günceller."""
    name = request.form.get('name', '').strip()
    m_type = request.form.get('type', 'Kesim').strip()
    code = request.form.get('code', '').strip()
    capacity = float(request.form.get('capacity_ton_day') or 10.0)
    hourly_rate = float(request.form.get('hourly_rate') or 0.0)
    operator = request.form.get('operator_name', '').strip()
    status = request.form.get('status', 'Aktif').strip()
    notes = request.form.get('notes', '').strip()
    is_active = int(request.form.get('is_active', 1))

    if name:
        update_machine(machine_id, name, m_type, code, capacity, hourly_rate, operator, status, notes, is_active)
        flash(f"'{name}' tezgah bilgileri başarıyla güncellendi.", "success")
    return redirect(request.referrer or url_for('on_imalat_plan'))

@app.route('/api/makineler/<int:machine_id>/sil', methods=['POST'])
def api_makine_sil(machine_id):
    """Makine / istasyonu siler."""
    delete_machine(machine_id)
    flash("Tezgah silindi.", "info")
    return redirect(request.referrer or url_for('on_imalat_plan'))


# =========================================================================
# 5. KESİM GİRİŞİ (HIZLI KESİLENLER & ÇOKLU POZ GİRİŞİ)
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
            'name': r_dict['name'],
            'total_qty': r_dict['quantity'],
            'cut_qty': r_dict['cut_quantity'],
            'remaining_qty': kalan,
            'unit_weight': r_dict['unit_weight']
        })
    return jsonify({'status': 'not_found'})

@app.route('/api/kesim-takip/proje-pozlar')
def api_kesim_proje_pozlar():
    """Seçilen projenin tüm pozlarını liste halinde döndürür."""
    project_id = request.args.get('proje_id')
    if not project_id:
        return jsonify([])
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT pos_no, name, profile_type, quantity, cut_quantity, length, unit_weight FROM parts WHERE project_id = ? ORDER BY pos_no ASC", (project_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    for r in rows:
        r['remaining_quantity'] = max(0, r['quantity'] - r['cut_quantity'])
    return jsonify(rows)

@app.route('/api/kesim-takip/toplu-kaydet', methods=['POST'])
def api_kesim_toplu_kaydet():
    """Çoklu poz kesim girişini tek seferde kaydeder ve parça listesini günceller."""
    data = request.get_json() or {}
    project_id = data.get('project_id')
    cut_date = data.get('cut_date') or datetime.now().strftime("%Y-%m-%d")
    machine = data.get('machine', '').strip()
    operator = data.get('operator', '').strip()
    helper = data.get('helper', '').strip()
    shift = data.get('shift', 'Gündüz')
    items = data.get('items', [])

    if not project_id or not items:
        return jsonify({'status': 'error', 'message': 'Proje ve en az bir kesim satırı gereklidir.'}), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT code FROM projects WHERE id = ?", (project_id,))
    p_row = cursor.fetchone()
    p_code = p_row['code'] if p_row else ""

    u = session.get('user', {})
    saved_count = 0

    for it in items:
        prefix = str(it.get('prefix', '')).strip()
        raw_pos = str(it.get('pos_no', '')).strip()
        if prefix and not raw_pos.startswith(prefix):
            pos_no = prefix + raw_pos
        else:
            pos_no = raw_pos

        cut_quantity = eval_math(it.get('cut_quantity', 0))
        profile = str(it.get('profile', '')).strip()
        row_machine = str(it.get('machine', '')).strip() or machine
        row_operator = str(it.get('operator', '')).strip() or operator
        notes = str(it.get('notes', '')).strip()

        if not pos_no or cut_quantity <= 0:
            continue

        # Parça listesini güncelle
        cursor.execute("SELECT id, quantity, cut_quantity, profile_type FROM parts WHERE project_id = ? AND pos_no = ?", (project_id, pos_no))
        part_row = cursor.fetchone()
        if part_row:
            new_cut_total = part_row['cut_quantity'] + cut_quantity
            cursor.execute("UPDATE parts SET cut_quantity = ?, remaining_quantity = ? WHERE id = ?",
                           (new_cut_total, max(0, part_row['quantity'] - new_cut_total), part_row['id']))
            if not profile:
                profile = part_row['profile_type']

        # Log kaydet
        cursor.execute('''
        INSERT INTO cutting_entries (project_id, project_code, pos_no, profile, cut_quantity, cut_date, machine, operator, helper, shift, user_id, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (project_id, p_code, pos_no, profile, cut_quantity, cut_date, machine, operator, helper, shift, u.get('id'), notes))
        saved_count += 1

    conn.commit()
    conn.close()

    if saved_count > 0:
        log_activity(
            action="Toplu Kesim Girişi",
            entity_type="cutting_entries",
            entity_id=project_id,
            details=f"{p_code} projesi için {saved_count} satır kesim kaydı işlendi ({machine} - {operator}).",
            username=u.get('username', 'Kullanıcı'),
            user_id=u.get('id'),
            ip_address=request.remote_addr
        )

    return jsonify({'status': 'success', 'saved_count': saved_count})

@app.route('/api/kesim-takip/kaydet', methods=['POST'])
def api_kesim_kaydet():
    """Hızlı tekli kesilenler girişini kaydeder."""
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
        flash(f"'{pos_no}' pozu için {cut_quantity} adet kesim başarıyla işlendi.", "success")

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
    fitup_start_date = request.form.get('fitup_start_date')
    fitup_end_date = request.form.get('fitup_end_date')
    welding_cleaning_end_date = request.form.get('welding_cleaning_end_date') or request.form.get('welding_end_date')
    paint_end_date = request.form.get('paint_end_date')
    delivery_date = request.form.get('delivery_date')

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE projects SET
        start_date = ?,
        cutting_start_date = ?,
        fitup_start_date = ?,
        fitup_end_date = ?,
        welding_cleaning_end_date = ?,
        paint_end_date = ?,
        delivery_date = ?
    WHERE id = ?
    ''', (start_date, cutting_start_date, fitup_start_date, fitup_end_date, welding_cleaning_end_date, paint_end_date, delivery_date, project_id))
    conn.commit()
    conn.close()

    flash("Proje imalat planı ve hedef aşama tarihleri güncellendi.", "success")
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
    """İmalatı biten montajları Kalite Kontrol havuzuna gönderir (İstasyon/Tezgah seçimiyle)."""
    assembly_id = request.form.get('assembly_id')
    send_qty = clean_int(request.form.get('quantity', 1))
    workstation = request.form.get('workstation', '').strip()
    comments = request.form.get('comments', '').strip()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM assemblies WHERE id = ?", (assembly_id,))
    ass = cursor.fetchone()

    if not ass or send_qty <= 0:
        flash("Geçersiz işlem!", "warning")
        return redirect(request.referrer or url_for('imalat_takip'))

    new_pending = ass['qa_pending_qty'] + send_qty
    cursor.execute("UPDATE assemblies SET qa_pending_qty = ? WHERE id = ?", (new_pending, assembly_id))

    u = session.get('user', {})
    cursor.execute('''
    INSERT INTO qa_inspections (project_id, assembly_id, assembly_pos, quantity, comments, workstation)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', (ass['project_id'], assembly_id, ass['assembly_pos'], send_qty, comments, workstation))

    conn.commit()
    conn.close()

    log_activity(
        action="Kalite Kontrole Gönderildi",
        entity_type="assemblies",
        entity_id=assembly_id,
        details=f"Marka '{ass['assembly_pos']}' için {send_qty} adet ({workstation or 'Tezgah'}) kalite muayenesine sevk edildi.",
        username=u.get('username', 'Kullanıcı'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )

    add_notification(
        category='qa_gonderildi',
        title='Kalite Kontrole Sevk',
        message=f"Marka '{ass['assembly_pos']}' ({send_qty} Adet) kalite kontrol havuzuna sevk edildi ({workstation or 'İstasyon'}).",
        icon='fa-clipboard-check',
        color='blue',
        link_url=url_for('kalite_kontrol')
    )

    flash(f"'{ass['assembly_pos']}' markasından {send_qty} adet başarıyla Kalite Kontrol sayfasına gönderildi.", "success")
    return redirect(request.referrer or url_for('imalat_takip'))


# =========================================================================
# 8. KALİTE KONTROL (QA / QC DENETİM & FOTOĞRAFLI ONAY/RED)
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
    inspection_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    photo_url = ""
    if 'photo' in request.files:
        photo = request.files['photo']
        if photo and photo.filename != '':
            ext = os.path.splitext(photo.filename)[1].lower()
            filename = f"qa_{inspection_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
            os.makedirs(os.path.join(BASE_DIR, 'static', 'uploads', 'qa'), exist_ok=True)
            save_path = os.path.join(BASE_DIR, 'static', 'uploads', 'qa', filename)
            photo.save(save_path)
            photo_url = f"/static/uploads/qa/{filename}"

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
        photo_url = CASE WHEN ? != '' THEN ? ELSE photo_url END,
        inspection_date = ?,
        inspector_id = ?,
        inspector_name = ?
    WHERE id = ?
    ''', (decision, defect_type, comments, photo_url, photo_url, inspection_date, u.get('id'), inspector_name, inspection_id))

    # Assemblies tablosundaki adetleri güncelle
    if qa['assembly_id']:
        cursor.execute("SELECT * FROM assemblies WHERE id = ?", (qa['assembly_id'],))
        ass = cursor.fetchone()
        if ass:
            qty = qa['quantity']
            new_pending = max(0, ass['qa_pending_qty'] - qty)
            if decision == 'Onaylandı':
                new_approved = ass['qa_approved_qty'] + qty
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
        details=f"Marka '{qa['assembly_pos']}' ({qa['quantity']} Adet) -> {decision}. {defect_type} - {comments}",
        username=u.get('username', 'Kullanıcı'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )

    add_notification(
        category='qa_karar',
        title=f"Kalite Kararı: {decision}",
        message=f"Marka '{qa['assembly_pos']}' ({qa['quantity']} Adet) kalite kontrol tarafından '{decision}' olarak sonuçlandırıldı.",
        icon='fa-shield-halved' if decision == 'Onaylandı' else 'fa-triangle-exclamation',
        color='teal' if decision == 'Onaylandı' else 'rose',
        link_url=url_for('kalite_kontrol')
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

    if process_type in ('Tamamlandı', 'Boya'):
        new_done = ass['paint_done_qty'] + quantity
        cursor.execute("UPDATE assemblies SET paint_done_qty = ? WHERE id = ?", (new_done, assembly_id))
        add_notification(
            category='boya_tamam',
            title='Boya Tamamlandı & Sevke Hazır',
            message=f"Marka '{ass['assembly_pos']}' ({quantity} Adet) boyandı ve sevkiyat havuzuna hazırlandı.",
            icon='fa-truck-ramp-box',
            color='purple',
            link_url=url_for('sevk')
        )

    conn.commit()
    conn.close()

    flash(f"'{ass['assembly_pos']}' için boya/yüzey işlem kaydı işlendi.", "success")
    return redirect(request.referrer or url_for('boya_takip'))

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

    if not dispatch_no:
        dispatch_no = get_next_dispatch_no(project_id)

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

    add_notification(
        category='sevk_cikis',
        title='Sevkiyat İrsaliyesi Kesildi',
        message=f"{dispatch_no} irsaliyesi ile {vehicle_plate} aracı ({total_qty} Parça, {total_tonnage} Ton) {destination or 'Şantiye'} yönüne sevk edildi.",
        icon='fa-truck-moving',
        color='emerald',
        link_url=url_for('sevk')
    )

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

@app.route('/api/sevk/siradaki-no')
def api_sevk_siradaki_no():
    """Seçilen proje için sıradaki bağımsız sevkiyat numarasını döndürür (SEVK-1, SEVK-2...)."""
    project_id = request.args.get('proje_id')
    next_no = get_next_dispatch_no(project_id)
    return jsonify({'status': 'success', 'dispatch_no': next_no})

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
            cursor.execute("UPDATE assemblies SET shipped_qty = CASE WHEN shipped_qty - ? < 0 THEN 0 ELSE shipped_qty - ? END WHERE id = ?", (it['quantity'], it['quantity'], it['assembly_id']))

    cursor.execute("DELETE FROM shipments WHERE id = ?", (shipment_id,))
    conn.commit()
    conn.close()

    flash("Sevkiyat iptal edildi ve parçalar tekrar sevk bekliyor havuzuna alındı.", "info")
    return redirect(url_for('sevk'))


# =========================================================================
# 10.5. MUHASEBE & İRSALİYE TAKİP MODÜLÜ
# =========================================================================
@app.route('/muhasebe-irsaliye')
def muhasebe_irsaliye():
    """Muhasebe ve Finans Ekibi için İrsaliye & Fatura Takip ve Onay Ekranı."""
    project_id = request.args.get('proje_id')
    accounting_status = request.args.get('durum', 'Tümü')
    search = request.args.get('q', '').strip()
    customer = request.args.get('musteri', '').strip()

    shipments = get_shipments_with_accounting(
        project_id=project_id,
        accounting_status=accounting_status,
        search=search,
        customer=customer
    )
    stats = get_accounting_summary_stats()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, code, name, customer FROM projects WHERE status != 'Arşiv' ORDER BY name ASC")
    projects = [dict(r) for r in cursor.fetchall()]
    customers = get_customers()
    conn.close()

    metrics = get_global_metrics(customer=customer)

    return render_template(
        'muhasebe_irsaliye.html',
        shipments=shipments,
        stats=stats,
        projects=projects,
        customers=customers,
        selected_project_id=project_id,
        selected_status=accounting_status,
        selected_customer=customer,
        search_query=search,
        metrics=metrics,
        today_str=datetime.now().strftime("%Y-%m-%d")
    )

@app.route('/api/muhasebe-irsaliye/<int:shipment_id>/islem', methods=['POST'])
def api_muhasebe_irsaliye_islem(shipment_id):
    """İrsaliyenin muhasebe işleme durumunu kaydeder/günceller ve bildirim üretir."""
    status = request.form.get('accounting_status', 'İşlendi').strip()
    invoice_no = request.form.get('accounting_invoice_no', '').strip()
    notes = request.form.get('accounting_notes', '').strip()
    
    u = session.get('user', {})
    user_name = u.get('full_name') or u.get('username') or 'Muhasebe Sorumlusu'

    # İrsaliye detayını çek
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT s.*, p.name as project_name FROM shipments s LEFT JOIN projects p ON s.project_id = p.id WHERE s.id = ?", (shipment_id,))
    shipment = cursor.fetchone()
    conn.close()

    if not shipment:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'status': 'error', 'message': 'İrsaliye bulunamadı'}), 404
        flash("İrsaliye bulunamadı!", "danger")
        return redirect(url_for('muhasebe_irsaliye'))

    dispatch_no = shipment['dispatch_no']
    project_name = shipment['project_name'] or 'Proje'

    update_shipment_accounting(
        shipment_id=shipment_id,
        accounting_status=status,
        accounting_processed_by=user_name if status == 'İşlendi' else None,
        accounting_invoice_no=invoice_no,
        accounting_notes=notes
    )

    if status == 'İşlendi':
        msg = f"'{dispatch_no}' numaralı irsaliye ({project_name}) muhasebe sistemine işlendi."
        if invoice_no:
            msg += f" (Resmi Fatura/İrsaliye No: {invoice_no})"
        add_notification(
            category='muhasebe_islem',
            title='İrsaliye Muhasebeye İşlendi',
            message=msg,
            icon='fa-file-invoice-dollar',
            color='indigo',
            link_url=url_for('muhasebe_irsaliye')
        )
        flash(f"'{dispatch_no}' numaralı irsaliye başarıyla 'Muhasebeye İşlendi' olarak kaydedildi.", "success")
    else:
        add_notification(
            category='muhasebe_bekliyor',
            title='İrsaliye Muhasebe Durumu Geri Alındı',
            message=f"'{dispatch_no}' numaralı irsaliye muhasebe bekleme havuzuna geri alındı.",
            icon='fa-clock-rotate-left',
            color='amber',
            link_url=url_for('muhasebe_irsaliye')
        )
        flash(f"'{dispatch_no}' numaralı irsaliye muhasebe bekleme durumuna geri alındı.", "info")

    log_activity(
        action=f"Muhasebe İrsaliye {status}",
        entity_type="shipments",
        entity_id=shipment_id,
        details=f"İrsaliye No: {dispatch_no}, Fatura No: {invoice_no or '-'}, Not: {notes or '-'}",
        username=u.get('username', 'Kullanıcı'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({
            'status': 'success',
            'accounting_status': status,
            'accounting_processed_by': user_name if status == 'İşlendi' else None,
            'accounting_invoice_no': invoice_no,
            'message': f"'{dispatch_no}' durumu güncellendi."
        })

    return redirect(request.referrer or url_for('muhasebe_irsaliye'))


# =========================================================================
# 11. KULLANICI YÖNETİMİ & YETKİ MATRİSİ & DENETİM GÜNLÜĞÜ
# =========================================================================
@app.route('/kullanicilar')
def kullanicilar():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, full_name, role, is_active, created_at FROM users ORDER BY id ASC")
    users = [dict(r) for r in cursor.fetchall()]
    conn.close()
    
    role_perms = get_all_role_permissions()
    return render_template('kullanicilar.html',
                           users=users,
                           roles=ROLES_LIST,
                           modules=MODULES_LIST,
                           permissions=role_perms)

@app.route('/api/kullanicilar/ekle', methods=['POST'])
def api_kullanici_ekle():
    cur_user = session.get('user', {})
    if cur_user.get('role') not in ('admin', 'patron'):
        flash("Yalnızca Sistem Yöneticisi veya Patron yeni kullanıcı ekleyebilir.", "danger")
        return redirect(url_for('kullanicilar'))

    username = request.form.get('username', '').strip().lower()
    password = request.form.get('password', '').strip()
    full_name = request.form.get('full_name', '').strip()
    role = request.form.get('role', 'izleyici').strip().lower()

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

@app.route('/api/kullanicilar/yetkiler-guncelle', methods=['POST'])
def api_kullanicilar_yetkiler_guncelle():
    """Rol Düzenleme Yetki Matrisini kaydeder."""
    cur_user = session.get('user', {})
    if cur_user.get('role') not in ('admin', 'patron'):
        flash("Yetkisiz işlem! Yalnızca Yönetici ve Patron yetkileri değiştirebilir.", "danger")
        return redirect(url_for('kullanicilar'))

    # Formdan tüm 'perm_{role}_{module}' alanlarını al
    for r in ROLES_LIST:
        for mod, _ in MODULES_LIST:
            field_name = f"perm_{r}_{mod}"
            can_e = 1 if request.form.get(field_name) == '1' else 0
            update_role_permission(r, mod, can_e)

    flash("Rol düzenleme yetki matrisi başarıyla güncellendi.", "success")
    return redirect(url_for('kullanicilar'))

@app.route('/api/kullanicilar/<int:user_id>/durum', methods=['POST'])
def api_kullanici_durum(user_id):
    cur_user = session.get('user', {})
    if cur_user.get('role') not in ('admin', 'patron'):
        flash("Yetkisiz işlem!", "danger")
        return redirect(url_for('kullanicilar'))

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
# 12. AĞ REHBERİ (YÖNLENDİRME)
# =========================================================================
@app.route('/ag-rehberi')
def ag_rehberi():
    return redirect(url_for('index'))


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

@app.route('/api/ayarlar/baslik-guncelle', methods=['POST'])
def api_ayarlar_baslik_guncelle():
    """Firma başlığı, alt başlık, logo ve web sitesi URL'sini günceller."""
    cur_user = session.get('user', {})
    if cur_user.get('role') not in ('admin', 'patron'):
        flash("Yalnızca Sistem Yöneticisi veya Patron sistem ayarlarını değiştirebilir.", "danger")
        return redirect(url_for('raporlar'))

    app_title = request.form.get('app_title', '').strip()
    app_subtitle = request.form.get('app_subtitle', '').strip()
    company_name = request.form.get('company_name', '').strip()
    company_website_url = request.form.get('company_website_url', '').strip()

    company_logo_url = None
    if 'company_logo' in request.files:
        logo_file = request.files['company_logo']
        if logo_file and logo_file.filename != '':
            ext = os.path.splitext(logo_file.filename)[1].lower()
            filename = f"logo_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
            os.makedirs(os.path.join(BASE_DIR, 'static', 'uploads'), exist_ok=True)
            logo_file.save(os.path.join(BASE_DIR, 'static', 'uploads', filename))
            company_logo_url = f"/static/uploads/{filename}"

    update_system_settings(app_title, app_subtitle, company_name, company_logo_url, company_website_url)
    flash("Firma başlık ve logo ayarları başarıyla güncellendi.", "success")
    return redirect(url_for('raporlar'))

@app.route('/api/ayarlar/guncelle', methods=['POST'])
def api_ayarlar_guncelle():
    cur_user = session.get('user', {})
    if cur_user.get('role') not in ('admin', 'patron'):
        flash("Yetkisiz işlem!", "danger")
        return redirect(url_for('raporlar'))

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

# =========================================================================
# 13. KESİM ANALİZİ (PLAKA & PROFİL KESİM İLERLEME TAKİBİ)
# =========================================================================
@app.route('/kesim-analiz')
def kesim_analiz():
    """Hangi işten plakadan yüzde kaç, profilden yüzde kaç kesildiğini gösteren analiz sayfası."""
    selected_project_id = request.args.get('proje_id', '')
    p_id = int(selected_project_id) if selected_project_id and selected_project_id.isdigit() else None
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, code, name FROM projects WHERE status != 'Arşiv' ORDER BY name ASC")
    projects = [dict(r) for r in cursor.fetchall()]
    conn.close()

    analysis_list = get_cutting_progress_analysis(project_id=p_id)

    return render_template('kesim_analiz.html',
                           projects=projects,
                           selected_project_id=selected_project_id,
                           analysis_list=analysis_list)


# =========================================================================
# 14. İMALAT PLANI TAKVİM ETKİNLİKLERİ API (CALENDAR JSON)
# =========================================================================
@app.route('/api/imalat-plan/takvim-etkinlikleri')
def api_imalat_plan_takvim_etkinlikleri():
    """Takvim bileşeni için projelerin aşama tarihlerini FullCalendar uyumlu JSON olarak döner."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE status != 'Arşiv'")
    projects = cursor.fetchall()
    conn.close()

    events = []
    for p in projects:
        p_code = p['code']
        p_name = p['name']
        color = p['color'] or '#3b82f6'

        # 1. Başlangıç / Kesim Başlama
        if p['cutting_start_date']:
            events.append({
                'id': f"cut_{p['id']}",
                'title': f"⚡ [{p_code}] Kesim Başlama",
                'start': p['cutting_start_date'],
                'color': '#eab308',
                'extendedProps': {'project_id': p['id'], 'stage': 'Kesim Başlama', 'project_name': p_name}
            })

        # 2. Çatım Başlama
        if p['fitup_start_date']:
            events.append({
                'id': f"fitup_s_{p['id']}",
                'title': f"🔨 [{p_code}] Çatım Başlama",
                'start': p['fitup_start_date'],
                'color': '#f97316',
                'extendedProps': {'project_id': p['id'], 'stage': 'Çatım Başlama', 'project_name': p_name}
            })

        # 3. Çatım Bitiş
        if p['fitup_end_date']:
            events.append({
                'id': f"fitup_e_{p['id']}",
                'title': f"📐 [{p_code}] Çatım Bitiş",
                'start': p['fitup_end_date'],
                'color': '#fb923c',
                'extendedProps': {'project_id': p['id'], 'stage': 'Çatım Bitiş', 'project_name': p_name}
            })

        # 4. Kaynak & Temizlik Bitiş
        w_date = p['welding_cleaning_end_date'] or p['welding_end_date']
        if w_date:
            events.append({
                'id': f"weld_{p['id']}",
                'title': f"🔥 [{p_code}] Kaynak & Temizlik Bitiş",
                'start': w_date,
                'color': '#ec4899',
                'extendedProps': {'project_id': p['id'], 'stage': 'Kaynak & Temizlik Bitiş', 'project_name': p_name}
            })

        # 5. Boya Bitiş
        if p['paint_end_date']:
            events.append({
                'id': f"paint_{p['id']}",
                'title': f"🎨 [{p_code}] Boya Bitiş",
                'start': p['paint_end_date'],
                'color': '#8b5cf6',
                'extendedProps': {'project_id': p['id'], 'stage': 'Boya Bitiş', 'project_name': p_name}
            })

        # 6. Teslimat / Sevkiyat Tarihi
        if p['delivery_date']:
            events.append({
                'id': f"deliv_{p['id']}",
                'title': f"🚚 [{p_code}] Teslimat / Montaj",
                'start': p['delivery_date'],
                'color': '#10b981',
                'extendedProps': {'project_id': p['id'], 'stage': 'Teslimat Tarihi', 'project_name': p_name}
            })

    return jsonify(events)


# =========================================================================
# 15. CANLI DENETİM & AUDIT LOG GÖRÜNTÜLEME
# =========================================================================
@app.route('/canli-denetim')
def canli_denetim():
    """Tüm kullanıcıların yaptığı işlemlerin anlık denetim günlüğü."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM activity_logs ORDER BY id DESC LIMIT 200")
    logs = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return render_template('canli_denetim.html', logs=logs)


# =========================================================================
# 16. KULLANICI / YÖNETİCİ PROFİL VE ŞİFRE DÜZENLEME
# =========================================================================
@app.route('/profil', methods=['GET', 'POST'])
def profil():
    """Giriş yapmış kullanıcının kendi profil bilgilerini ve şifresini güncelleme sayfası."""
    cur_u = session.get('user', {})
    user_id = cur_u.get('id')
    if not user_id:
        return redirect(url_for('login'))

    user_data = get_user_by_id(user_id)
    if not user_data:
        flash("Kullanıcı bilgisi bulunamadı.", "danger")
        return redirect(url_for('index'))

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        username = request.form.get('username', '').strip()
        current_password = request.form.get('current_password', '').strip()
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        role = request.form.get('role', '').strip() if cur_u.get('role') in ('admin', 'patron') else None

        if not username or not full_name:
            flash("Kullanıcı adı ve Ad Soyad alanları boş bırakılamaz.", "danger")
            return redirect(url_for('profil'))

        # Şifre değiştirilmek isteniyorsa kontrol et
        if new_password:
            if new_password != confirm_password:
                flash("Yeni şifreler birbiriyle eşleşmiyor!", "danger")
                return redirect(url_for('profil'))
            
            # Mevcut şifre kontrolü
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            conn.close()
            
            if row and row['password_hash'] != hash_password(current_password):
                flash("Mevcut şifrenizi hatalı girdiniz!", "danger")
                return redirect(url_for('profil'))
            
            update_user_profile(user_id, username, full_name, new_password=new_password, role=role)
            flash("Profiliniz ve şifreniz başarıyla güncellendi.", "success")
        else:
            update_user_profile(user_id, username, full_name, new_password=None, role=role)
            flash("Profil bilgileriniz başarıyla güncellendi.", "success")

        # Session'ı güncelle
        session['user']['full_name'] = full_name
        session['user']['username'] = username
        if role:
            session['user']['role'] = role

        log_activity(
            action="Profil Güncellendi",
            entity_type="users",
            entity_id=user_id,
            details=f"{username} kullanıcısı kendi profil bilgilerini güncelledi.",
            username=username,
            user_id=user_id,
            ip_address=request.remote_addr
        )

        return redirect(url_for('profil'))

    return render_template('profil.html', user=user_data, roles=ROLES_LIST)


# =========================================================================
# 17. KULLANICI DÜZENLEME (ADMIN & PATRON)
# =========================================================================
@app.route('/api/kullanicilar/guncelle', methods=['POST'])
def api_kullanicilar_guncelle():
    """Yöneticinin kullanıcı bilgilerini, rolünü, aktiflik durumunu ve şifresini düzenlemesini sağlar."""
    cur_user = session.get('user', {})
    if cur_user.get('role') not in ('admin', 'patron'):
        flash("Yetkisiz işlem! Yalnızca Yönetici ve Patron kullanıcı düzenleyebilir.", "danger")
        return redirect(url_for('kullanicilar'))

    user_id = request.form.get('user_id')
    full_name = request.form.get('full_name', '').strip()
    username = request.form.get('username', '').strip()
    role = request.form.get('role', '').strip()
    is_active = request.form.get('is_active', '1')
    new_password = request.form.get('new_password', '').strip()

    if not user_id or not username or not full_name:
        flash("Lütfen tüm zorunlu alanları doldurun.", "danger")
        return redirect(url_for('kullanicilar'))

    try:
        admin_update_user(user_id, username, full_name, role, is_active, new_password=new_password or None)
        log_activity(
            action="Kullanıcı Düzenlendi",
            entity_type="users",
            entity_id=user_id,
            details=f"{username} ({full_name}) kullanıcısı yönetici tarafından güncellendi. Yeni rol: {role}",
            username=cur_user.get('username'),
            user_id=cur_user.get('id'),
            ip_address=request.remote_addr
        )
        flash(f"'{full_name}' kullanıcısı başarıyla güncellendi.", "success")
    except Exception as e:
        flash(f"Kullanıcı güncellenirken hata oluştu: {str(e)}", "danger")

    return redirect(url_for('kullanicilar'))


# =========================================================================
# 18. TOPLANTI VE KARAR TAKİP MODÜLÜ
# =========================================================================
@app.route('/toplantilar')
def toplantilar():
    """Toplantı ve Karar Takip modülü."""
    selected_project_id = request.args.get('proje_id', '')
    p_id = int(selected_project_id) if selected_project_id and selected_project_id.isdigit() else None

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, code, name FROM projects WHERE status != 'Arşiv' ORDER BY name ASC")
    projects = [dict(r) for r in cursor.fetchall()]

    cursor.execute("SELECT id, username, full_name, role FROM users WHERE is_active = 1 ORDER BY full_name ASC")
    users = [dict(r) for r in cursor.fetchall()]
    conn.close()

    meetings_list = get_meetings(project_id=p_id)

    return render_template('toplantilar.html',
                           projects=projects,
                           users=users,
                           selected_project_id=selected_project_id,
                           meetings=meetings_list)

@app.route('/api/toplantilar/ekle', methods=['POST'])
def api_toplanti_ekle():
    u = session.get('user', {})
    project_id = request.form.get('project_id')
    p_id = int(project_id) if project_id and project_id.isdigit() else None
    title = request.form.get('title', '').strip()
    meeting_date = request.form.get('meeting_date')
    meeting_time = request.form.get('meeting_time', '')
    location = request.form.get('location', '')
    organizer = u.get('full_name', 'Sistem')
    
    # Katılımcılar listesi (checkbox veya metin)
    att_list = request.form.getlist('attendees')
    if att_list:
        attendees = ', '.join([a.strip() for a in att_list if a.strip()])
    else:
        attendees = request.form.get('attendees', '').strip()
        
    summary = request.form.get('summary', '')
    decisions = request.form.get('decisions', '')
    status = 'Tamamlandı'

    if not title or not meeting_date:
        flash("Toplantı başlığı ve tarihi zorunludur.", "danger")
        return redirect(url_for('toplantilar'))

    meeting_id = add_meeting(
        project_id=p_id,
        title=title,
        meeting_date=meeting_date,
        meeting_time=meeting_time,
        location=location,
        organizer=organizer,
        attendees=attendees,
        summary=summary,
        decisions=decisions,
        status=status,
        created_by=u.get('full_name', 'Yetkili')
    )

    log_activity(
        action="Toplantı Kaydedildi",
        entity_type="meetings",
        entity_id=meeting_id,
        details=f"Toplantı: '{title}' ({meeting_date})",
        username=u.get('username'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )

    flash("Toplantı kaydı başarıyla oluşturuldu.", "success")
    return redirect(url_for('toplantilar', proje_id=project_id or ''))

@app.route('/api/toplantilar/<int:meeting_id>/guncelle', methods=['POST'])
def api_toplanti_guncelle(meeting_id):
    project_id = request.form.get('project_id')
    p_id = int(project_id) if project_id and project_id.isdigit() else None
    title = request.form.get('title', '').strip()
    meeting_date = request.form.get('meeting_date')
    meeting_time = request.form.get('meeting_time', '')
    location = request.form.get('location', '')
    organizer = request.form.get('organizer', '')
    attendees = request.form.get('attendees', '')
    summary = request.form.get('summary', '')
    decisions = request.form.get('decisions', '')
    status = request.form.get('status', 'Tamamlandı')

    update_meeting(
        meeting_id=meeting_id,
        project_id=p_id,
        title=title,
        meeting_date=meeting_date,
        meeting_time=meeting_time,
        location=location,
        organizer=organizer,
        attendees=attendees,
        summary=summary,
        decisions=decisions,
        status=status
    )

    flash("Toplantı kaydı güncellendi.", "success")
    return redirect(url_for('toplantilar', proje_id=project_id or ''))

@app.route('/api/toplantilar/<int:meeting_id>/sil', methods=['POST'])
def api_toplanti_sil(meeting_id):
    delete_meeting(meeting_id)
    flash("Toplantı kaydı ve aksiyonları silindi.", "info")
    return redirect(url_for('toplantilar'))

@app.route('/api/toplantilar/<int:meeting_id>/aksiyon-ekle', methods=['POST'])
def api_toplanti_aksiyon_ekle(meeting_id):
    description = request.form.get('description', '').strip()
    responsible_person = request.form.get('responsible_person', '').strip()
    due_date = request.form.get('due_date') or ''
    status = request.form.get('status', 'Devam Ediyor')

    if not description:
        flash("Aksiyon tanımı boş olamaz.", "danger")
        return redirect(url_for('toplantilar'))

    add_meeting_action_item(
        meeting_id=meeting_id,
        description=description,
        responsible_person=responsible_person,
        due_date=due_date,
        status=status
    )

    flash("Aksiyon ve görev maddesi başarıyla eklendi.", "success")
    return redirect(request.referrer or url_for('toplantilar'))

@app.route('/api/toplantilar/aksiyon/<int:item_id>/durum-guncelle', methods=['POST'])
def api_toplanti_aksiyon_durum_guncelle(item_id):
    status = request.form.get('status', 'Devam Ediyor')
    update_action_item_status(item_id, status)
    flash("Aksiyon durumu güncellendi.", "success")
    return redirect(request.referrer or url_for('toplantilar'))

@app.route('/api/toplantilar/aksiyon/<int:item_id>/sil', methods=['POST'])
def api_toplanti_aksiyon_sil(item_id):
    delete_meeting_action_item(item_id)
    flash("Aksiyon maddesi silindi.", "info")
    return redirect(request.referrer or url_for('toplantilar'))


# =========================================================================
# 19. CANLI SOHBET & FOTOĞRAFLI İLETİŞİM SİSTEMİ
# =========================================================================
def generate_steel_ai_response(msg, user_name='Değerli Personel'):
    """ORDUMAK Akıllı Çelik İmalat & MES Yapay Zeka Danışmanı"""
    text = (msg or '').strip().lower()
    
    # 1. Canlı Fabrika ve Proje Durumu Analizi
    if any(k in text for k in ['fabrika', 'durum', 'özet', 'rapor', 'tonaj durumu', 'üretim durumu', 'neredeyiz', 'canlı']):
        try:
            m = get_global_metrics()
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT code, name, customer, target_tonnage, status FROM projects WHERE status != 'Tamamlandı' ORDER BY target_tonnage DESC LIMIT 5")
            active_projs = c.fetchall()
            conn.close()
            
            proj_txt = ""
            for p in active_projs:
                p_dict = dict(p) if hasattr(p, 'keys') else {'code': p[0], 'name': p[1], 'customer': p[2], 'target_tonnage': p[3]}
                proj_txt += f"  • **{p_dict.get('code')}** ({p_dict.get('name')}): `{p_dict.get('target_tonnage', 0):.1f} Ton`\n"
            
            return (
                f"🏭 **ORDUMAK Fabrika Canlı Üretim & Tonaj Brifingi:**\n\n"
                f"📊 **Genel İcmal:**\n"
                f"- **Toplam Taahhüt Edilen Tonaj:** `{m.get('total_tonnage', 0):.2f} Ton`\n"
                f"- **⚡ Kesilen Malzeme:** `{m.get('cut_tonnage', 0):.2f} Ton` (%{m.get('cut_pct', 0)})\n"
                f"- **🔨 İmalatı (Montaj/Kaynak) Biten:** `{m.get('fab_completed_tonnage', 0):.2f} Ton` (%{m.get('fab_pct', 0)})\n"
                f"- **🎨 Boyanan İmalat:** `{m.get('paint_completed_tonnage', 0):.2f} Ton` (%{m.get('paint_pct', 0)})\n"
                f"- **🚛 Sevk Edilen:** `{m.get('shipped_tonnage', 0):.2f} Ton` (%{m.get('ship_pct', 0)})\n"
                f"- **📦 Fabrika / Atölye İçi Stok:** `{m.get('factory_stock_tonnage', 0):.2f} Ton`\n\n"
                f"🏗️ **Öne Çıkan Aktif Projeler:**\n{proj_txt if proj_txt else '  • Aktif proje bulunmuyor.'}\n\n"
                f"💡 *Tavsiye:* Kesimden çıkan parçaların montaj holüne hızlı aktarımı için atölye içi vinç lojistiğini ve kalite kontrol onaylarını takip ediniz."
            )
        except Exception as e:
            print(f"AI metrics error: {e}")

    # 2. Ağırlık ve Geometri Formülleri
    if any(k in text for k in ['ağırlık', 'hesapla', 'tonaj', 'kg', 'formül', 'sac ağırlık', 'profil ağırlık']):
        return (
            f"📐 **Mühendislik Ağırlık Hesaplama Formülleri:**\n\n"
            f"• **Sac & Plaka:**\n"
            f"  `Ağırlık (kg) = Kalınlık (mm) × Genişlik (mm) × Boy (mm) × 0.00000785`\n"
            f"  *Örnek:* 12 mm × 1500 mm × 6000 mm = `12 × 1500 × 6000 × 7.85 / 10^6 = 847.8 kg`\n\n"
            f"• **Dairesel Boru:**\n"
            f"  `Ağırlık (kg/m) = (Dış Çap - Et Kalınlığı) × Et Kalınlığı × 0.02466`\n\n"
            f"• **Kutu Profil:**\n"
            f"  `Ağırlık (kg/m) = (Genişlik + Yükseklik - 2 × Et Kalınlığı) × 2 × Et Kalınlığı × 0.00785`\n\n"
            f"Profil ağırlıklarında ise standart çelik cetvelindeki metre ağırlığı (kg/m) boy ile çarpılır."
        )

    # 3. Cıvata ve Tork Değerleri
    if any(k in text for k in ['cıvata', 'civata', 'tork', 'nm', 'en 14399', '10.9', '8.8', 'hv', 'hr', 'öngerilme', 'sıkma']):
        return (
            f"🔩 **EN 14399 / ISO 898-1 Yapısal Cıvata Sıkma & Tork Standartları:**\n\n"
            f"| Çap | Kalite 8.8 Tork (Nm) | Kalite 10.9 Tork (Nm) | Min. Ön Germe (kN) |\n"
            f"| :--- | :---: | :---: | :---: |\n"
            f"| **M16** | 170 - 190 Nm | 240 - 260 Nm | ~90 kN |\n"
            f"| **M20** | 330 - 360 Nm | 470 - 510 Nm | ~140 kN |\n"
            f"| **M24** | 570 - 620 Nm | 810 - 870 Nm | ~205 kN |\n"
            f"| **M27** | 830 - 900 Nm | 1180 - 1280 Nm | ~265 kN |\n"
            f"| **M30** | 1130 - 1230 Nm | 1600 - 1750 Nm | ~325 kN |\n\n"
            f"⚠️ **Kritik Kurallar:**\n"
            f"1. Sürtünmeli birleşimlerde (Slip-Critical) temas yüzeyleri boyasız veya özel sürtünme katsayılı astar ile kaplanmalıdır.\n"
            f"2. Tork anahtarlarının kalibrasyon sertifikaları güncel olmalı, sıkma işlemi merkezden dışa doğru kademeli (%50 -> %100) yapılmalıdır."
        )

    # 4. Kaynak Standartları ve EN 1090
    if any(k in text for k in ['kaynak', 'en 1090', 'iso 5817', 'wps', 'pqr', 'wpqr', 'exc2', 'exc3', 'ön ısıtma', 'preheat', 'tav', 'elektrod', 'tel', 'sg2', 'gazaltı']):
        return (
            f"🛡️ **EN 1090-2 & EN ISO 5817 Kaynak Mühendisliği Şartları:**\n\n"
            f"1. **Uygulama Sınıfları (Execution Class):**\n"
            f"   • **EXC2:** Standart binalar, depolar. NDT oranı alın kaynaklarında min. %10, köşe kaynaklarında %5.\n"
            f"   • **EXC3:** Vinçli sanayi yapıları, köprüler, dinamik yükler. Kaynakçılar EN ISO 9606-1 onaylı, WPS/WPQR zorunludur. Alın kaynaklarında %100 VT + %20-%50 UT/MT.\n"
            f"2. **Ön Isıtma (Preheat - EN 1011-2):**\n"
            f"   • S355 kalite ve et kalınlığı `t > 20 mm` olan birleşimlerde çatlak riskini önlemek için min. **100°C - 150°C** ön ısıtma tavsiye edilir.\n"
            f"3. **Sarf Malzeme Seçimi:**\n"
            f"   • S235 / S275 için: `ER70S-6 (SG2)` veya `E7018 / E42 2 B`.\n"
            f"   • S355 için: `SG3` gazaltı teli veya `E7018-1` düşük hidrojenli bazik elektrot."
        )

    # 5. Boya, Kumlama ve Korozyon (ISO 12944)
    if any(k in text for k in ['boya', 'kumlama', 'sa 2.5', 'mikron', 'dft', 'iso 12944', 'c3', 'c4', 'c5', 'astar', 'epoksi', 'poliüretan', 'dew point', 'çiğ']):
        return (
            f"🎨 **ISO 12944 & ISO 8501 Çelik Yüzey Koruma Kılavuzu:**\n\n"
            f"1. **Yüzey Hazırlığı:**\n"
            f"   • Kumlama Derecesi: Min. **Sa 2.5** (Neredeyse beyaz metal - ISO 8501-1).\n"
            f"   • Yüzey Pürüzlülüğü: Orta (Medium G - Grit 40-75 µm).\n"
            f"2. **İklimsel Uygulama Şartları:**\n"
            f"   • Yüzey sıcaklığı, havanın Çiğ Noktası (Dew Point) sıcaklığından **en az 3°C yüksek** olmalıdır.\n"
            f"   • Bağıl nem (RH) **<%85** olmalıdır.\n"
            f"3. **Örnek C3 / C4 Dayanım Boya Katmanları:**\n"
            f"   • *1. Kat (Astar):* Çinko Fosfatlı / Epoksi Astar (`60-80 µm DFT`)\n"
            f"   • *2. Kat (Ara Kat):* Epoksi MIO (Micaceous Iron Oxide) (`80-100 µm DFT`)\n"
            f"   • *3. Kat (Son Kat):* Alifatik Poliüretan (`50-60 µm DFT`) -> Toplam `~200-240 µm DFT`."
        )

    # 6. Kesim, Fire ve Yerleşim (Nesting)
    if any(k in text for k in ['kesim', 'fire', 'lazer', 'plazma', 'nesting', 'testere', 'yerleşim', 'optimizasyon']):
        return (
            f"⚡ **Ön İmalat Kesim & Yerleşim (Nesting) Verimlilik Taktikleri:**\n\n"
            f"1. **Ortak Kenar Kesimi (Common Line Cutting):**\n"
            f"   • Dikdörtgen/kare flanş ve bayrak plakalarında ortak kenar kesimi yaparak delme (pierce) sayısını %40, kesim süresini %25 düşürebilirsiniz.\n"
            f"2. **Plazma / Lazer Boşlukları (Kerf & Margin):**\n"
            f"   • Plakalar arası mesafe sac kalınlığı `t` kadar (min. 5-8 mm), plaka kenarından ise min. 10 mm boşluk bırakılmalıdır.\n"
            f"3. **Profil Kesim Fire Önleme:**\n"
            f"   • 12 metre ve 6 metre standart boyları kombine ederek sipariş öncesi kesim simülasyonu yapın. Testere bıçak kalınlığı (3.5 mm) ve açı payı (10-15 mm) fire hesabına katılmalıdır."
        )

    # 7. Çelik Kaliteleri
    if any(k in text for k in ['s235', 's275', 's355', 'kalite', 'st37', 'st52', 'malzeme', 'akma']):
        return (
            f"🔩 **Yapısal Çelik Kaliteleri ve Mukavemet Değerleri (EN 10025-2):**\n\n"
            f"• **S235JR (Eski St37-2):** Akma Dayanımı fy = 235 MPa, Çekme fu = 360-510 MPa. Tali çelikler, aşık, kuşak, rüzgar gerdirmeleri.\n"
            f"• **S275JR (Eski St44-2):** Akma Dayanımı fy = 275 MPa, Çekme fu = 430-580 MPa. Standart ara kat kirişleri ve sundurmalar.\n"
            f"• **S355JR / J2 (Eski St52-3):** Akma Dayanımı fy = 355 MPa, Çekme fu = 470-630 MPa. Ağır yük taşıyan ana kolonlar, vinç kirişleri, kafes makaslar, flanş plakaları.\n"
            f"• *Darbe Enerjisi Notu:* `JR` = +20°C 27J, `J0` = 0°C 27J, `J2` = -20°C 27J darbe tokluğuna sahiptir."
        )

    # 8. Tekla ve İmalat Pozlama
    if any(k in text for k in ['tekla', 'poz', 'marka', 'assembly', 'part']):
        return (
            f"🏗️ **Tekla Structures & İmalat Entegrasyonu:**\n\n"
            f"• **Montaj Markası (Assembly / Marka):** Atölyede çatılıp kaynaklanan ve şantiyeye bağımsız giden komple elemandır (Örn: `K-101`, `KOL-1`, `MAKAS-3`).\n"
            f"• **Tekil Poz (Single Part / Poz):** CNC plazma/lazer veya testerede kesilen bağımsız parçadır (Örn: `p1`, `PL-12`, `flans-1`).\n"
            f"• *İpucu:* Kesim girişinde parça kodu yerine ana montaj markasını girerseniz, sistem otomatik olarak o markanın Tekla parça ağacını çözümler ve ağırlıkları otomatik doldurur."
        )

    return (
        f"🤖 Merhaba {user_name}! Ben **ORDUMAK AI Mühendisi & MES Asistanı**.\n\n"
        f"Aşağıdaki alanlarda teknik analiz ve hesaplama yapabilirim:\n"
        f"1. 🏭 **Canlı Fabrika Tonajı & Proje Durumu** (Kesim, imalat, boya ve sevk ilerlemeleri)\n"
        f"2. 🛡️ **EN 1090-2 EXC2/EXC3 & ISO 5817 Kaynak Şartları** (WPS, NDT, ön ısıtma)\n"
        f"3. 🔩 **EN 14399 8.8 / 10.9 Ön Germeli Cıvata Tork Değerleri** (Nm ve kN hesapları)\n"
        f"4. 🎨 **ISO 12944 Korozyon ve Boya Sistemleri** (Sa 2.5 kumlama, çiğ noktası, DFT mikron)\n"
        f"5. ⚡ **Kesim & Yerleşim (Nesting) Optimizasyonu** (Ortak kenar, plazma/lazer boşlukları)\n"
        f"6. 📐 **Çelik Ağırlık ve Mukavemet Formülleri** (Plaka, profil, boru tonajları)\n\n"
        f"Bana doğrudan bir soru sorabilir veya hesaplama yaptırabilirsiniz!"
    )

@app.route('/api/ai/sor', methods=['POST'])
def api_ai_sor():
    """ORDUMAK AI Mühendisi - Bağımsız Soru-Cevap API'si"""
    data = request.get_json() or {}
    question = data.get('question', '').strip()
    u = session.get('user', {})
    user_name = u.get('full_name', 'Mühendis')
    if not question:
        return jsonify({'status': 'error', 'message': 'Soru metni boş olamaz.'}), 400
    
    reply = generate_steel_ai_response(question, user_name)
    return jsonify({
        'status': 'success',
        'question': question,
        'answer': reply,
        'timestamp': datetime.now().strftime('%H:%M:%S')
    })

@app.route('/api/chat/<channel>')
def api_chat_get(channel):
    """Kanal bazlı sohbet mesajlarını JSON döner."""
    msgs = get_chat_messages(channel=channel, limit=100)
    return jsonify(msgs)

@app.route('/api/chat/send', methods=['POST'])
def api_chat_send():
    """Yeni sohbet mesajı ve görsel kaydeder; AI kanalı ise anında yanıt üretir."""
    u = session.get('user', {})
    if not u:
        return jsonify({'status': 'error', 'message': 'Oturum açılmamış.'}), 401

    channel = request.form.get('channel', 'genel')
    message = request.form.get('message', '').strip()
    photo_url = ""

    if 'photo' in request.files:
        photo = request.files['photo']
        if photo and photo.filename != '':
            ext = os.path.splitext(photo.filename)[1].lower()
            if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                filename = f"chat_{channel}_{int(datetime.now().timestamp())}_{photo.filename}"
                save_dir = os.path.join(BASE_DIR, 'static', 'uploads', 'chat')
                os.makedirs(save_dir, exist_ok=True)
                photo.save(os.path.join(save_dir, filename))
                photo_url = f"/static/uploads/chat/{filename}"

    if not message and not photo_url:
        return jsonify({'status': 'error', 'message': 'Boş mesaj gönderilemez.'}), 400

    save_chat_message(
        channel=channel,
        user_id=u.get('id'),
        username=u.get('username'),
        full_name=u.get('full_name'),
        message=message,
        photo_url=photo_url
    )

    # Arka planda Web Push bildirimi gönder
    send_web_push_notification(
        title=f"💬 {u.get('full_name', 'Personel')} (#{channel})",
        message=message[:100] if message else "📷 Fotoğraf paylaştı",
        url="/"
    )

    # Yapay Zeka Kanalı Yanıtı
    if channel in ('ai_ortak', 'ai_muhendis') or channel.startswith('ai_ozel') or '@ai' in message.lower():
        ai_reply = generate_steel_ai_response(message, u.get('full_name', 'Personel'))
        save_chat_message(
            channel=channel,
            user_id=0,
            username='ai_asistan',
            full_name='🤖 ORDUMAK AI Danışmanı',
            message=ai_reply,
            photo_url=""
        )

    return jsonify({'status': 'success', 'photo_url': photo_url})

@app.route('/api/chat/clear', methods=['POST'])
def api_chat_clear():
    """Belirli bir sohbet kanalındaki veya tüm kanallardaki mesajları temizler."""
    u = session.get('user', {})
    data = request.get_json(silent=True) or {}
    channel = data.get('channel') or request.form.get('channel') or 'genel'
    
    clear_chat_messages(channel=channel)
    log_activity(
        action="Sohbet Temizlendi",
        entity_type="chat_messages",
        details=f"#{channel} kanalındaki sohbet mesajları temizlendi.",
        username=u.get('username'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )
    return jsonify({'status': 'success', 'message': f"#{channel} kanalındaki mesajlar temizlendi."})

@app.route('/api/chat/message/<int:message_id>/delete', methods=['POST'])
def api_chat_message_delete(message_id):
    """Tekil bir sohbet mesajını siler."""
    delete_chat_message(message_id)
    return jsonify({'status': 'success', 'message': 'Mesaj silindi.'})

@app.route('/api/notifications/recent')
def api_notifications_recent():
    """En son canlı bildirimleri JSON formatında döner."""
    since_id = request.args.get('since_id', 0)
    notifs = get_recent_notifications(since_id=since_id, limit=10)
    return jsonify(notifs)

@app.route('/api/veritabani/yedek-indir')
def api_veritabani_yedek_indir():
    """Yöneticinin yerel SQLite veritabanını tek tıkla indirmesini sağlar."""
    cur_u = session.get('user', {})
    if cur_u.get('role') not in ('admin', 'patron'):
        flash("Yetkisiz erişim! Sadece Yöneticiler veritabanı yedeği indirebilir.", "danger")
        return redirect(url_for('raporlar'))
    db_file = os.path.join(BASE_DIR, 'imalat_takip.db')
    if os.path.exists(db_file):
        return send_file(
            db_file,
            as_attachment=True,
            download_name=f"imalat_takip_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        )
    flash("Yerel SQLite veritabanı dosyası bulunamadı (PostgreSQL kullanılıyor olabilir).", "warning")
    return redirect(url_for('raporlar'))

@app.route('/api/veritabani/geri-yukle', methods=['POST'])
def api_veritabani_geri_yukle():
    """Yedekten SQLite veritabanını güvenle geri yükler."""
    cur_u = session.get('user', {})
    if cur_u.get('role') not in ('admin', 'patron'):
        flash("Yetkisiz işlem! Sadece sistem yöneticileri veritabanını geri yükleyebilir.", "danger")
        return redirect(url_for('raporlar'))
    
    db_file = os.path.join(BASE_DIR, 'imalat_takip.db')
    restored = False
    
    # 1. Dosya yükleme kontrolü
    if 'backup_file' in request.files and request.files['backup_file'].filename != '':
        file = request.files['backup_file']
        if not file.filename.lower().endswith(('.db', '.sqlite', '.sqlite3')):
            flash("Geçersiz dosya formatı! Lütfen geçerli bir .db yedek dosyası yükleyin.", "danger")
            return redirect(url_for('raporlar'))
        
        # Mevcut veritabanının güvenlik yedeğini al
        if os.path.exists(db_file):
            safety_backup = os.path.join(BASE_DIR, f"imalat_takip_pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
            try:
                import shutil
                shutil.copy2(db_file, safety_backup)
            except Exception as e:
                print(f"Safety backup error: {e}")
        
        file.save(db_file)
        restored = True
    elif request.form.get('backup_path'):
        local_path = request.form.get('backup_path').strip()
        if not os.path.exists(local_path):
            flash(f"Belirtilen yedek dosya yolu bulunamadı: {local_path}", "danger")
            return redirect(url_for('raporlar'))
        
        if os.path.exists(db_file):
            safety_backup = os.path.join(BASE_DIR, f"imalat_takip_pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
            try:
                import shutil
                shutil.copy2(db_file, safety_backup)
            except Exception as e:
                print(f"Safety backup error: {e}")
        
        import shutil
        shutil.copy2(local_path, db_file)
        restored = True
    else:
        flash("Lütfen bir .db yedek dosyası seçin veya dosya yolu girin.", "warning")
        return redirect(url_for('raporlar'))
    
    if restored:
        try:
            init_db()
        except Exception as e:
            print(f"Init DB post-restore warning: {e}")
            
        log_activity(
            action="Veritabanı Geri Yüklendi",
            entity_type="system",
            details="Veritabanı yedekten başarıyla geri yüklendi.",
            username=cur_u.get('username'),
            user_id=cur_u.get('id'),
            ip_address=request.remote_addr
        )
        flash("Veritabanı yedekten başarıyla geri yüklendi ve tüm sistem verileri güncellendi!", "success")
    
    return redirect(url_for('raporlar'))

@app.route('/sw.js')
def service_worker():
    """Service Worker dosyasını doğru headerlar ile sunar."""
    sw_path = os.path.join(BASE_DIR, 'static', 'sw.js')
    if os.path.exists(sw_path):
        resp = send_file(sw_path, mimetype='application/javascript')
        resp.headers['Service-Worker-Allowed'] = '/'
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return resp
    return "/* Service worker not found */", 404

@app.route('/manifest.json')
def pwa_manifest():
    """PWA mobil ana ekrana ekleme manifest verisi döner."""
    settings = get_system_settings()
    logo_url = settings.get('company_logo_url') or '/static/img/ordumak_logo.svg'
    manifest = {
        "name": settings.get('app_title', 'ORDUMAK ÇELİK İMALAT MES'),
        "short_name": "İmalat MES",
        "description": settings.get('app_subtitle', 'İmalat, Montaj, Boya ve Sevkiyat Takip Portalı'),
        "start_url": "/",
        "display": "standalone",
        "background_color": "#020617",
        "theme_color": "#020617",
        "icons": [
            {
                "src": logo_url,
                "sizes": "192x192 512x512",
                "type": "image/png" if logo_url.endswith('.png') else "image/svg+xml",
                "purpose": "any maskable"
            }
        ]
    }
    return jsonify(manifest)


# =========================================================================
# 20. DUYURU VE UYARI SİSTEMİ
# =========================================================================
@app.route('/api/duyurular/ekle', methods=['POST'])
def api_duyuru_ekle():
    """Her kullanıcının duyuru paylaşabilmesini sağlar."""
    u = session.get('user', {})
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    priority = request.form.get('priority', 'Önemli').strip()

    if not title or not content:
        flash("Duyuru başlığı ve metni boş bırakılamaz.", "danger")
        return redirect(request.referrer or url_for('index'))

    add_announcement(
        user_id=u.get('id', 1),
        username=u.get('username', 'Kullanıcı'),
        full_name=u.get('full_name', 'Personel'),
        title=title,
        content=content,
        priority=priority
    )

    log_activity(
        action="Duyuru Yayınlandı",
        entity_type="announcements",
        details=f"Yeni duyuru: '{title}' ({priority})",
        username=u.get('username'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )

    flash("Duyurunuz başarıyla yayınlandı ve tüm personelin ekranına yansıtıldı.", "success")
    return redirect(request.referrer or url_for('index'))

@app.route('/api/duyurular/<int:announcement_id>/kapat', methods=['POST'])
def api_duyuru_kapat(announcement_id):
    """Duyuruyu kapatır/gizler."""
    deactivate_announcement(announcement_id)
    flash("Duyuru kapatıldı.", "info")
    return redirect(request.referrer or url_for('index'))

@app.route('/api/duyurular/<int:announcement_id>/sil', methods=['POST'])
def api_duyuru_sil(announcement_id):
    """Duyuruyu tamamen siler."""
    delete_announcement(announcement_id)
    flash("Duyuru silindi.", "info")
    return redirect(request.referrer or url_for('index'))


# =========================================================================
# 21. WEB PUSH BİLDİRİM SERVİSİ (PWA & MOBİL ARKA PLAN)
# =========================================================================
@app.route('/api/push/vapid-public-key', methods=['GET'])
def api_push_vapid_key():
    """Web Push VAPID genel anahtarını döner."""
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})

@app.route('/api/push/subscribe', methods=['POST'])
def api_push_subscribe():
    """Kullanıcı tarayıcısının veya mobil PWA uygulamasının push aboneliğini kaydeder."""
    data = request.get_json(silent=True) or {}
    endpoint = data.get('endpoint')
    keys = data.get('keys', {})
    p256dh = keys.get('p256dh')
    auth = keys.get('auth')
    
    if not endpoint or not p256dh or not auth:
        return jsonify({"status": "error", "message": "Geçersiz push abonelik verisi"}), 400
        
    u = session.get('user', {})
    save_push_subscription(
        user_id=u.get('id'),
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth
    )
    return jsonify({"status": "success", "message": "Abonelik başarıyla kaydedildi"})

@app.route('/api/push/test', methods=['POST'])
def api_push_test():
    """Tüm kayıtlı mobil ve masaüstü cihazlara test push bildirimi gönderir."""
    send_web_push_notification(
        title="🔔 ORDUMAK MES Test Bildirimi",
        message="Mobil ve masaüstü bildirim sistemi başarıyla bağlandı! Uygulama kapalıyken bile anlık bildirim alabilirsiniz.",
        url="/"
    )
    add_notification(
        category="sistem",
        title="Test Bildirimi Gönderildi",
        message="Web Push & Toast bildirim sistemi test edildi.",
        icon="fa-bell",
        color="emerald",
        link_url="/"
    )
    return jsonify({"status": "success", "message": "Test bildirimi gönderildi"})

# =========================================================================
# 22. ORDUMAK AI MÜHENDİSİ DANIŞMANLIK SAYFASI VE MOTORU
# =========================================================================
@app.route('/ai-muhendis')
def ai_muhendis():
    """ORDUMAK AI Mühendis danışmanlık ve analiz sayfası."""
    conn = get_db()
    cursor = conn.cursor()
    metrics = get_global_metrics()
    cursor.execute("SELECT * FROM projects WHERE status != 'Arşiv' ORDER BY id DESC")
    projects = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return render_template('ai_muhendis.html', metrics=metrics, projects=projects)

def generate_ai_engineer_response(prompt):
    return generate_steel_ai_response(prompt, session.get('user', {}).get('full_name', 'Mühendis'))

@app.route('/api/ai/ask', methods=['POST'])
def api_ai_ask():
    """AI Mühendis API uç noktası."""
    data = request.get_json(silent=True) or {}
    prompt = data.get('prompt', '').strip()
    if not prompt:
        return jsonify({"status": "error", "message": "Soru metni boş olamaz."}), 400
    u = session.get('user', {})
    answer = generate_steel_ai_response(prompt, u.get('full_name', 'Mühendis'))
    return jsonify({"status": "success", "answer": answer})



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
