import os
import re
import math
import socket
import json
import hashlib
import urllib.request
import urllib.parse
import threading
import traceback
import functools
import gzip
from datetime import datetime, timedelta
import calendar
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_file, session, g, send_from_directory, abort
)

from database import (
    get_isg_records, add_isg_record, update_isg_record, delete_isg_record,
    get_shuttle_routes, add_shuttle_route, update_shuttle_route, delete_shuttle_route, add_shuttle_passenger, delete_shuttle_passenger,
    get_consumables, add_consumable, update_consumable, delete_consumable, record_consumable_transaction, get_consumable_transactions,
    get_anonymous_reports, add_anonymous_report, update_anonymous_report_status, delete_anonymous_report,
    get_system_feature_requests, add_system_feature_request, update_system_feature_request, delete_system_feature_request,
    get_db, init_db, run_schema_migrations, ensure_column, log_activity, hash_password,
    get_global_metrics, get_project_summary, get_all_projects_summary, get_customers, set_project_status,
    get_system_settings, update_system_settings, invalidate_app_cache,
    get_machines, get_machine_by_id, add_machine, update_machine, delete_machine,
    get_all_role_permissions, update_role_permission,
    can_user_edit, get_next_dispatch_no,
    get_user_by_id, update_user_profile,
    approve_material_order, reject_material_order,
    get_cutting_progress_analysis,
    admin_update_user, delete_user,
    get_active_announcements, add_announcement, deactivate_announcement, delete_announcement,
    get_chat_messages, save_chat_message, clear_chat_messages, delete_chat_message,
    mark_chat_messages_as_read, get_chat_users_with_unread, get_unread_chat_summary,
    get_meetings, get_meeting_by_id, add_meeting, update_meeting, delete_meeting,
    get_meeting_action_items, add_meeting_action_item, update_action_item_status, delete_meeting_action_item,
    add_notification, get_recent_notifications,
    save_push_subscription, get_push_subscriptions, delete_push_subscription,
    get_shipments_with_accounting, update_shipment_accounting, get_accounting_summary_stats,
    get_all_roles, get_roles_list, add_custom_role, delete_custom_role,
    get_design_tasks, get_design_task_by_id, save_design_task, update_design_task, delete_design_task, get_design_summary_stats,
    get_cutting_batches, get_cutting_batch_details, update_cutting_batch_items, delete_cutting_batch,
    get_istanbul_now, get_istanbul_now_str,
    ROLES_LIST, MODULES_LIST
)
from excel_handler import (
    parse_tekla_excel, parse_excel_parts, generate_template_excel,
    export_material_rfq_excel, export_project_report_excel, export_shipment_excel,
    parse_assemblies_file, parse_assembly_parts_file, parse_parts_file, parse_clipboard_table
)
from seed_data import seed_demo_data

import jinja2

# =========================================================================
# FLASK UYGULAMA YAPILANDIRMASI
# =========================================================================
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
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=15)

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

def send_web_push_notification(title, message, url="/", icon="/static/img/ordumak_logo.png"):
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

_original_add_notification = add_notification

def add_notification(category, title, message, icon='fa-bell', color='blue', link_url='', user_id=None):
    """Hem veritabanı notification tablosuna yazar hem de kayıtlı tüm cihazlara arka plan Web Push bildirimi iletir."""
    res = _original_add_notification(category, title, message, icon=icon, color=color, link_url=link_url, user_id=user_id)
    try:
        send_web_push_notification(title=title, message=message, url=link_url or '/', icon='/static/img/ordumak_logo.png')
    except Exception as e:
        print(f"Push dispatch note: {e}")
    return res


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
@functools.lru_cache(maxsize=1)
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
        'now': get_istanbul_now()
    }

_db_initialized = False

@app.before_request
def ensure_db_and_auth():
    global _db_initialized
    if not _db_initialized:
        try:
            init_db()
            run_schema_migrations()
            seed_demo_data()
            _db_initialized = True
        except Exception as e:
            print(f"Veritabani hazirlama uyarisi: {e}")
            try:
                run_schema_migrations()
            except Exception:
                pass

    # Zorunlu Giriş - Giriş yapmamış kullanıcıları login sayfasına yönlendir (Statik dosyalar, SW, Manifest, Logo ve Push API hariç)
    if (
        request.endpoint in ('login', 'static', 'service_worker', 'pwa_manifest', 'api_push_vapid_key', 'api_push_subscribe', 'serve_upload_fallback', 'serve_company_logo', 'serve_static_style_css', 'serve_static_main_js', 'serve_static_logo_svg', 'serve_static_logo_png')
        or (request.path and (
            request.path.startswith('/static/')
            or request.path.startswith('/css/')
            or request.path.startswith('/js/')
            or request.path.startswith('/img/')
            or request.path.endswith(('.css', '.js', '.png', '.svg', '.jpg', '.jpeg', '.webp', '.ico', '.json'))
            or request.path in ('/favicon.ico', '/apple-touch-icon.png', '/logo', '/api/company-logo', '/sw.js', '/manifest.json', '/style.css', '/main.js', '/ordumak_logo.svg', '/ordumak_logo.png', '/api/push/vapid-public-key', '/api/push/subscribe')
        ))
    ):
        return
    if 'user' not in session:
        return redirect(url_for('login'))

def fetch_company_logo_from_website(website_url):
    """
    Kullanıcının girdiği firma web sitesinden (örn. https://ordumak.com.tr)
    kurumsal logoyu otomatik olarak tespit edip indirir ve static/uploads/ altına kaydeder.
    """
    if not website_url:
        return None
    
    website_url = website_url.strip()
    if not website_url.startswith(('http://', 'https://')):
        website_url = 'https://' + website_url
        
    try:
        parsed = urllib.parse.urlparse(website_url)
        domain = parsed.netloc or parsed.path
        domain = domain.replace('www.', '').strip('/')
    except Exception:
        return None

    if not domain:
        return None
    
    upload_dir = os.path.join(BASE_DIR, 'static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    domain_hash = hashlib.md5(domain.lower().encode('utf-8')).hexdigest()[:8]
    target_filename = f"web_logo_{domain_hash}.png"
    target_path = os.path.join(upload_dir, target_filename)

    # 1. Aşama: Web sitesinin ana sayfasını çekip <img> logo, og:image veya icon etiketlerini ara
    try:
        req = urllib.request.Request(
            website_url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            
            candidates = []
            
            # a) <img ... src="...logo...">
            for m in re.finditer(r'<img[^>]+(?:class|id|alt)=[\'"][^\'"]*logo[^\'"]*[\'"][^>]+src=[\'"]([^\'"]+)[\'"]', html, re.I):
                candidates.append(m.group(1))
            for m in re.finditer(r'<img[^>]+src=[\'"]([^\'"]*logo[^\'"]*\.(?:png|svg|webp|jpg|jpeg))[\'"]', html, re.I):
                candidates.append(m.group(1))
                
            # b) <meta property="og:image" content="...">
            for m in re.finditer(r'<meta[^>]+property=[\'"]og:image[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html, re.I):
                candidates.append(m.group(1))
            for m in re.finditer(r'<meta[^>]+content=[\'"]([^\'"]+)[\'"][^>]+property=[\'"]og:image[\'"]', html, re.I):
                candidates.append(m.group(1))
                
            # c) <link rel="apple-touch-icon" href="..."> veya <link rel="icon" href="...">
            for m in re.finditer(r'<link[^>]+rel=[\'"](?:apple-touch-icon|icon|shortcut icon)[\'"][^>]+href=[\'"]([^\'"]+)[\'"]', html, re.I):
                candidates.append(m.group(1))
                
            # Adayları indirip test et
            for cand in candidates:
                cand_url = urllib.parse.urljoin(website_url, cand)
                try:
                    img_req = urllib.request.Request(cand_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                    with urllib.request.urlopen(img_req, timeout=4) as img_resp:
                        data = img_resp.read()
                        if len(data) > 500: # En az 500 bayt geçerli görsel
                            ext = os.path.splitext(urllib.parse.urlparse(cand_url).path)[1].lower()
                            if not ext or ext not in ('.png', '.svg', '.webp', '.jpg', '.jpeg'):
                                ext = '.png'
                            saved_name = f"web_logo_{domain_hash}{ext}"
                            saved_path = os.path.join(upload_dir, saved_name)
                            with open(saved_path, 'wb') as f:
                                f.write(data)
                            return f"/static/uploads/{saved_name}"
                except Exception:
                    continue
    except Exception as e:
        print(f"Web sitesi HTML tarama uyarısı ({website_url}): {e}")
        
    # 2. Aşama: Google High-Res Favicon / Brand Logo API (256px)
    try:
        google_fav_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=256"
        req = urllib.request.Request(google_fav_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = resp.read()
            if len(data) > 500:
                with open(target_path, 'wb') as f:
                    f.write(data)
                return f"/static/uploads/{target_filename}"
    except Exception as e:
        print(f"Google Favicon API uyarısı ({domain}): {e}")
        
    return None

EMBEDDED_STYLE_CSS = """
.custom-scrollbar::-webkit-scrollbar { width: 6px; height: 6px; }
.custom-scrollbar::-webkit-scrollbar-track { background: rgba(15, 23, 42, 0.6); }
.custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(51, 65, 85, 0.8); border-radius: 9999px; }
.custom-scrollbar::-webkit-scrollbar-thumb:hover { background: rgba(71, 85, 105, 1); }
@keyframes fadeIn { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }
@keyframes scaleUp { from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: scale(1); } }
.animate-fade-in { animation: fadeIn 0.25s ease-out forwards; }
.animate-scale-up { animation: scaleUp 0.2s cubic-bezier(0.16, 1, 0.3, 1) forwards; }
tbody tr { transition: background-color 0.15s ease; }
input:focus, select:focus, textarea:focus { box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.4); }
""".strip()

EMBEDDED_MAIN_JS = """
function formatNumber(num, decimals = 2) {
    if (num === null || num === undefined || isNaN(num)) return '0,00';
    return Number(num).toLocaleString('tr-TR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}
function openModal(id) {
    const modal = document.getElementById(id);
    if (modal) { modal.classList.remove('hidden'); modal.classList.add('flex'); document.body.style.overflow = 'hidden'; }
}
function closeModal(id) {
    const modal = document.getElementById(id);
    if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); document.body.style.overflow = ''; }
}
document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
        document.querySelectorAll('[id$="Modal"]').forEach(m => { if (!m.classList.contains('hidden')) closeModal(m.id); });
    }
});
function initAutoDismissAlerts() {
    document.querySelectorAll('.animate-fade-in').forEach(al => {
        setTimeout(() => {
            if (al && al.parentElement) {
                al.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
                al.style.opacity = '0';
                al.style.transform = 'translateY(-10px)';
                setTimeout(() => { if (al && al.parentElement) al.remove(); }, 500);
            }
        }, 6000);
    });
}
document.addEventListener('DOMContentLoaded', initAutoDismissAlerts);
(function () {
    function showProgressBar() {
        let bar = document.getElementById('turboProgressBar');
        if (!bar) {
            bar = document.createElement('div');
            bar.id = 'turboProgressBar';
            bar.className = 'fixed top-0 left-0 h-[3px] bg-gradient-to-r from-blue-500 via-indigo-500 to-emerald-400 z-[999999] transition-all duration-200 pointer-events-none shadow-sm shadow-blue-500/50';
            bar.style.width = '0%';
            document.body.appendChild(bar);
        }
        bar.style.opacity = '1';
        bar.style.width = '30%';
        setTimeout(() => { if (bar) bar.style.width = '70%'; }, 50);
        setTimeout(() => { if (bar) bar.style.width = '90%'; }, 200);
    }
    document.addEventListener('click', function (e) {
        const a = e.target.closest('a');
        if (!a) return;
        const href = a.getAttribute('href');
        if (!href || href.startsWith('#') || href.startsWith('javascript:') || href.startsWith('mailto:') || href.startsWith('tel:') || a.getAttribute('target') === '_blank' || a.hasAttribute('download')) return;
        showProgressBar();
    });
    window.addEventListener('beforeunload', showProgressBar);
})();
""".strip()

EMBEDDED_LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 80" width="320" height="80"><defs><linearGradient id="blueGrad" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#2563EB"/><stop offset="100%" stop-color="#1E3A8A"/></linearGradient><linearGradient id="orangeGrad" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#F59E0B"/><stop offset="100%" stop-color="#D97706"/></linearGradient></defs><g transform="translate(10, 10)"><polygon points="30,4 56,19 56,49 30,64 4,49 4,19" fill="none" stroke="url(#blueGrad)" stroke-width="5" stroke-linejoin="round"/><polygon points="30,14 46,24 46,44 30,54 14,44 14,24" fill="url(#blueGrad)" opacity="0.15"/><path d="M22 22 L38 22 M30 22 L30 46 M22 46 L38 46" stroke="url(#orangeGrad)" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/></g><text x="80" y="44" font-family="system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif" font-size="28" font-weight="900" letter-spacing="1.5" fill="#FFFFFF">ORDU<tspan fill="#3B82F6">MAK</tspan></text><text x="82" y="62" font-family="system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif" font-size="9" font-weight="700" letter-spacing="3.5" fill="#94A3B8">ÇELİK VE METAL İMALAT</text></svg>"""

EMBEDDED_SW_JS = """
const CACHE_NAME = 'ordumak-mes-v2';
self.addEventListener('install', (event) => { self.skipWaiting(); });
self.addEventListener('activate', (event) => { event.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') return;
    event.respondWith(
        fetch(event.request).catch(() => caches.match(event.request))
    );
});
""".strip()

@app.route('/api/company-logo')
@app.route('/logo')
def serve_company_logo():
    """
    Firma logosunu dinamik ve akıllı sunar:
    1. Yüklenmiş logo diskte varsa onu sunar.
    2. Diskte yoksa veya web sitesi tanımlıysa web sitesinden logoyu indirip sunar.
    3. Hiçbiri yoksa varsayılan kurumsal logoyu (SVG/PNG) 200 OK ile sunar (Asla 404 vermez).
    """
    settings = get_system_settings()
    custom_url = settings.get('company_logo_url')
    website_url = settings.get('company_website_url')
    
    # 1. Diskte var olan özel logo
    if custom_url and custom_url.startswith('/static/'):
        rel_path = custom_url.replace('/static/', '', 1).lstrip('/')
        disk_path = os.path.join(BASE_DIR, 'static', rel_path)
        if os.path.exists(disk_path):
            resp = send_file(disk_path)
            resp.headers['Cache-Control'] = 'public, max-age=86400'
            return resp
            
    # 2. Web sitesinden otomatik çekilmiş logo kontrolü
    if website_url:
        try:
            parsed = urllib.parse.urlparse(website_url if website_url.startswith(('http://', 'https://')) else f"https://{website_url}")
            domain = (parsed.netloc or parsed.path).replace('www.', '').strip('/')
            if domain:
                domain_hash = hashlib.md5(domain.lower().encode('utf-8')).hexdigest()[:8]
                for ext in ('.png', '.svg', '.webp', '.jpg', '.jpeg'):
                    cached_file = os.path.join(BASE_DIR, 'static', 'uploads', f"web_logo_{domain_hash}{ext}")
                    if os.path.exists(cached_file):
                        resp = send_file(cached_file)
                        resp.headers['Cache-Control'] = 'public, max-age=86400'
                        return resp
                # Eğer diskte yoksa webden çek
                web_logo_url = fetch_company_logo_from_website(website_url)
                if web_logo_url:
                    rel_p = web_logo_url.replace('/static/', '', 1).lstrip('/')
                    d_p = os.path.join(BASE_DIR, 'static', rel_p)
                    if os.path.exists(d_p):
                        resp = send_file(d_p)
                        resp.headers['Cache-Control'] = 'public, max-age=86400'
                        return resp
        except Exception as e:
            print(f"Company logo web serve note: {e}")

    # 3. Varsayılan Logo (Gömülü / Default)
    png_path = os.path.join(BASE_DIR, 'static', 'img', 'ordumak_logo.png')
    svg_path = os.path.join(BASE_DIR, 'static', 'img', 'ordumak_logo.svg')
    if os.path.exists(png_path):
        resp = send_file(png_path, mimetype='image/png')
        resp.headers['Cache-Control'] = 'public, max-age=86400'
        return resp
    if os.path.exists(svg_path):
        resp = send_file(svg_path, mimetype='image/svg+xml')
        resp.headers['Cache-Control'] = 'public, max-age=86400'
        return resp
        
    return EMBEDDED_LOGO_SVG, 200, {'Content-Type': 'image/svg+xml', 'Cache-Control': 'public, max-age=86400'}

@app.route('/static/uploads/<path:filename>')
@app.route('/uploads/<path:filename>')
def serve_upload_fallback(filename):
    """Yüklenen logolar veya fotoğraflar sunucu diskinde yoksa logoya yönlendirir (404 döngüsünü engeller)."""
    upload_dir = os.path.join(BASE_DIR, 'static', 'uploads')
    target_path = os.path.join(upload_dir, filename)
    if os.path.exists(target_path):
        return send_from_directory(upload_dir, filename)
    return serve_company_logo()

@app.route('/style.css')
@app.route('/static/style.css')
@app.route('/static/css/style.css')
@app.route('/css/style.css')
def serve_static_style_css():
    css_path = os.path.join(BASE_DIR, 'static', 'css', 'style.css')
    if os.path.exists(css_path):
        return send_file(css_path, mimetype='text/css')
    return EMBEDDED_STYLE_CSS, 200, {'Content-Type': 'text/css'}

@app.route('/main.js')
@app.route('/static/main.js')
@app.route('/static/js/main.js')
@app.route('/static/js/app.js')
@app.route('/js/main.js')
def serve_static_main_js():
    js_path = os.path.join(BASE_DIR, 'static', 'js', 'main.js')
    if os.path.exists(js_path):
        return send_file(js_path, mimetype='application/javascript')
    return EMBEDDED_MAIN_JS, 200, {'Content-Type': 'application/javascript'}

@app.route('/ordumak_logo.svg')
@app.route('/static/ordumak_logo.svg')
@app.route('/static/img/ordumak_logo.svg')
@app.route('/img/ordumak_logo.svg')
def serve_static_logo_svg():
    svg_path = os.path.join(BASE_DIR, 'static', 'img', 'ordumak_logo.svg')
    if os.path.exists(svg_path):
        return send_file(svg_path, mimetype='image/svg+xml')
    return EMBEDDED_LOGO_SVG, 200, {'Content-Type': 'image/svg+xml'}

@app.route('/ordumak_logo.png')
@app.route('/static/ordumak_logo.png')
@app.route('/static/img/ordumak_logo.png')
@app.route('/img/ordumak_logo.png')
def serve_static_logo_png():
    png_path = os.path.join(BASE_DIR, 'static', 'img', 'ordumak_logo.png')
    if os.path.exists(png_path):
        return send_file(png_path, mimetype='image/png')
    return serve_company_logo()

@app.route('/sw.js')
@app.route('/static/sw.js')
def service_worker():
    """Service Worker dosyasını doğru headerlar ile sunar."""
    sw_path = os.path.join(BASE_DIR, 'static', 'sw.js')
    if os.path.exists(sw_path):
        resp = send_file(sw_path, mimetype='application/javascript')
        resp.headers['Service-Worker-Allowed'] = '/'
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return resp
    resp = app.response_class(EMBEDDED_SW_JS, mimetype='application/javascript')
    resp.headers['Service-Worker-Allowed'] = '/'
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route('/favicon.ico')
@app.route('/static/favicon.ico')
def serve_favicon():
    """Masaüstü ve tarayıcı sekme ikonu (Favicon)."""
    ico_path = os.path.join(BASE_DIR, 'static', 'favicon.ico')
    if os.path.exists(ico_path):
        resp = send_file(ico_path, mimetype='image/x-icon')
    else:
        resp = send_file(os.path.join(BASE_DIR, 'static', 'img', 'ordumak_icon_192.png'), mimetype='image/png')
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp

@app.route('/apple-touch-icon.png')
@app.route('/apple-touch-icon-precomposed.png')
@app.route('/static/apple-touch-icon.png')
def serve_apple_touch_icon():
    """iOS Safari, Mac ve Mobil Masaüstü İkonu."""
    icon_path = os.path.join(BASE_DIR, 'static', 'img', 'apple-touch-icon.png')
    if not os.path.exists(icon_path):
        icon_path = os.path.join(BASE_DIR, 'static', 'img', 'ordumak_icon_192.png')
    resp = send_file(icon_path, mimetype='image/png')
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp

@app.route('/manifest.json')
@app.route('/static/manifest.json')
def pwa_manifest():
    """PWA mobil ana ekrana ekleme ve masaüstü görev çubuğu kısayol manifest verisi döner."""
    settings = get_system_settings()
    company_title = settings.get('company_name', 'ORDUMAK DEMİR ÇELİK A.Ş.')
    manifest = {
        "name": company_title,
        "short_name": "ORDUMAK MES",
        "description": settings.get('app_subtitle', 'İmalat, Montaj, Boya ve Sevkiyat Takip Portalı'),
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#020617",
        "theme_color": "#020617",
        "icons": [
            {
                "src": "/api/company-logo",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable"
            },
            {
                "src": "/api/company-logo",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable"
            }
        ]
    }
    return jsonify(manifest)

@app.errorhandler(404)
def handle_404(e):
    req_path = request.path.lower()
    if req_path.endswith(('.png', '.svg', '.jpg', '.jpeg', '.webp', '.ico', '.gif')) or '/static/uploads/' in req_path or '/static/img/' in req_path or 'logo' in req_path:
        return serve_company_logo()
    if req_path.endswith('.css') or 'style' in req_path:
        return EMBEDDED_STYLE_CSS, 200, {'Content-Type': 'text/css'}
    if req_path.endswith('.js') or 'main' in req_path or 'sw.js' in req_path:
        if 'sw.js' in req_path:
            resp = app.response_class(EMBEDDED_SW_JS, mimetype='application/javascript')
            resp.headers['Service-Worker-Allowed'] = '/'
            return resp
        return EMBEDDED_MAIN_JS, 200, {'Content-Type': 'application/javascript'}
    if req_path.endswith('manifest.json'):
        return pwa_manifest()
    return render_template('404.html') if os.path.exists(os.path.join(BASE_DIR, 'templates', '404.html')) else ("Sayfa Bulunamadı", 404)

@app.after_request
def add_cache_and_compression(response):
    """Statik dosyalar ve sayfalar için yüksek hızlı önbellekleme ve Gzip sıkıştırması."""
    # 1. Önbellekleme Başlıkları
    if request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    elif not response.headers.get('Cache-Control'):
        response.headers['Cache-Control'] = 'no-cache'

    # 2. Dahili Gzip Sıkıştırma (Ağ gecikmesini %90-95 azaltır)
    accept_encoding = request.headers.get('Accept-Encoding', '')
    if (
        'gzip' in accept_encoding.lower()
        and response.status_code < 300
        and response.content_type
        and any(ct in response.content_type for ct in ('text/', 'application/json', 'application/javascript'))
        and not response.direct_passthrough
        and len(response.get_data()) > 400
        and 'Content-Encoding' not in response.headers
    ):
        try:
            compressed = gzip.compress(response.get_data(), compresslevel=6)
            response.set_data(compressed)
            response.headers['Content-Encoding'] = 'gzip'
            response.headers['Content-Length'] = len(compressed)
        except Exception:
            pass

    return response


# Baslangicta da calistir
with app.app_context():
    try:
        init_db()
        run_schema_migrations()
        seed_demo_data()
        _db_initialized = True
    except Exception as e:
        print(f"Veritabanı başlatma uyarısı: {e}")
        try:
            run_schema_migrations()
        except Exception:
            pass


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
            
            # Beni Hatırla Seçeneği (15 Günlük Oturum)
            remember_me = request.form.get('remember_me')
            if remember_me:
                session.permanent = True
            else:
                session.permanent = False
            
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
    
    # Projelerin aşama durumlarıyla listesi (Aktif olanlar - Tek toplu sorgu ile ultra hızlı)
    projects = get_all_projects_summary(
        customer=selected_customer if selected_customer else None,
        exclude_status=('Arşiv', 'Tamamlandı')
    )
    
    conn = get_db()
    cursor = conn.cursor()

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
    
    # Aktif ve Biten Projeleri N+1 sorgu yapmadan tek seferde toplu getir
    active_projects = get_all_projects_summary(exclude_status=('Arşiv', 'Tamamlandı'))
    finished_projects = get_all_projects_summary(status_filter='Tamamlandı')

    metrics = get_global_metrics()
    return render_template('projeler.html',
                           active_projects=active_projects,
                           finished_projects=finished_projects,
                           active_tab=tab,
                           projects=active_projects if tab == 'aktif' else finished_projects,
                           metrics=metrics)

# =========================================================================
# 2.1 DİZAYN & 3D MODELLEME MODÜLÜ
# =========================================================================
@app.route('/dizayn-modelleme')
def dizayn_modelleme():
    stage_filter = request.args.get('stage', 'Tümü')
    status_filter = request.args.get('status', 'Tümü')
    project_id_arg = request.args.get('project_id')
    search = request.args.get('search', '').strip()
    
    selected_project_id = int(project_id_arg) if project_id_arg and project_id_arg.isdigit() else None
    
    tasks = get_design_tasks(
        project_id=selected_project_id,
        stage_type=stage_filter,
        status=status_filter,
        search=search
    )
    stats = get_design_summary_stats()
    
    # Proje listesi (dropdown için)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, code, name FROM projects WHERE status != 'Arşiv' ORDER BY name ASC")
    projects_list = [dict(r) for r in cursor.fetchall()]
    
    # Dizayn personelleri / Kullanıcılar
    cursor.execute("SELECT id, username, full_name, role FROM users WHERE is_active = 1 ORDER BY full_name ASC")
    users_list = [dict(r) for r in cursor.fetchall()]
    conn.close()
    
    cur_user = session.get('user', {})
    user_role = cur_user.get('role', 'izleyici')
    can_edit = can_user_edit(user_role, 'dizayn_modelleme') or user_role in ('admin', 'patron', 'dizayn', 'imalat müdürü')
    
    return render_template('dizayn_modelleme.html',
                           tasks=tasks,
                           stats=stats,
                           projects=projects_list,
                           users=users_list,
                           selected_stage=stage_filter,
                           selected_status=status_filter,
                           selected_project_id=selected_project_id,
                           search=search,
                           can_edit=can_edit)

@app.route('/api/dizayn/gorev-ekle', methods=['POST'])
def api_dizayn_gorev_ekle():
    cur_user = session.get('user', {})
    user_role = cur_user.get('role', 'izleyici')
    if not (can_user_edit(user_role, 'dizayn_modelleme') or user_role in ('admin', 'patron', 'dizayn', 'imalat müdürü')):
        flash("Yetkisiz işlem! Dizayn ve Modelleme ekleme yetkiniz yok.", "danger")
        return redirect(url_for('dizayn_modelleme'))
        
    project_id_raw = request.form.get('project_id', '').strip()
    custom_project_name = request.form.get('custom_project_name', '').strip()
    custom_project_code = request.form.get('custom_project_code', '').strip()
    
    project_id = int(project_id_raw) if project_id_raw and project_id_raw.isdigit() else None
    
    project_name = ''
    project_code = ''
    
    if project_id:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, code, name FROM projects WHERE id = ?", (project_id,))
        p_row = cursor.fetchone()
        conn.close()
        if p_row:
            project_name = p_row['name']
            project_code = p_row['code'] or ''
    else:
        project_name = custom_project_name
        project_code = custom_project_code
        
    if not project_name:
        flash("Lütfen bir proje seçin veya proje adı girin!", "warning")
        return redirect(url_for('dizayn_modelleme'))
        
    stage_type = request.form.get('stage_type', 'Modelleme').strip()
    status = request.form.get('status', 'Devam Ediyor').strip()
    lead_designer = request.form.get('lead_designer', '').strip()
    designer_user_id_raw = request.form.get('designer_user_id', '').strip()
    designer_user_id = int(designer_user_id_raw) if designer_user_id_raw and designer_user_id_raw.isdigit() else None
    
    # Designer name'i user_id'den al eğer varsa
    if designer_user_id and not lead_designer:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT full_name FROM users WHERE id = ?", (designer_user_id,))
        u_row = cursor.fetchone()
        conn.close()
        if u_row:
            lead_designer = u_row['full_name']
            
    start_date = request.form.get('start_date', '').strip()
    target_date = request.form.get('target_date', '').strip()
    estimated_days = request.form.get('estimated_days', '7').strip()
    progress_percent = request.form.get('progress_percent', '0').strip()
    tekla_version = request.form.get('tekla_version', 'Tekla 2024').strip()
    revision_no = request.form.get('revision_no', 'Rev 0').strip()
    description = request.form.get('description', '').strip()
    revision_notes = request.form.get('revision_notes', '').strip()
    
    task_id = save_design_task(
        project_id=project_id,
        project_code=project_code,
        project_name=project_name,
        stage_type=stage_type,
        status=status,
        lead_designer=lead_designer,
        designer_user_id=designer_user_id,
        start_date=start_date,
        target_date=target_date,
        actual_end_date=None,
        estimated_days=int(estimated_days or 7),
        progress_percent=int(progress_percent or 0),
        tekla_version=tekla_version,
        revision_no=revision_no,
        description=description,
        revision_notes=revision_notes
    )
    
    log_activity(
        action="Dizayn Görevi Eklendi",
        entity_type="project_design_tasks",
        entity_id=task_id,
        details=f"'{project_name}' projesi için yeni dizayn/modelleme görevi eklendi ({stage_type} - {revision_no}).",
        username=cur_user.get('username', 'Kullanıcı'),
        user_id=cur_user.get('id')
    )
    flash(f"'{project_name}' için Dizayn & 3D Modelleme görevi başarıyla oluşturuldu.", "success")
    return redirect(url_for('dizayn_modelleme'))

@app.route('/api/dizayn/<int:task_id>/guncelle', methods=['POST'])
def api_dizayn_gorev_guncelle(task_id):
    cur_user = session.get('user', {})
    user_role = cur_user.get('role', 'izleyici')
    if not (can_user_edit(user_role, 'dizayn_modelleme') or user_role in ('admin', 'patron', 'dizayn', 'imalat müdürü')):
        flash("Yetkisiz işlem! Dizayn ve Modelleme güncelleme yetkiniz yok.", "danger")
        return redirect(url_for('dizayn_modelleme'))
        
    stage_type = request.form.get('stage_type', '').strip()
    status = request.form.get('status', '').strip()
    lead_designer = request.form.get('lead_designer', '').strip()
    designer_user_id_raw = request.form.get('designer_user_id', '').strip()
    designer_user_id = int(designer_user_id_raw) if designer_user_id_raw and designer_user_id_raw.isdigit() else None
    
    if designer_user_id and not lead_designer:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT full_name FROM users WHERE id = ?", (designer_user_id,))
        u_row = cursor.fetchone()
        conn.close()
        if u_row:
            lead_designer = u_row['full_name']
            
    start_date = request.form.get('start_date', '').strip()
    target_date = request.form.get('target_date', '').strip()
    actual_end_date = request.form.get('actual_end_date', '').strip()
    estimated_days = request.form.get('estimated_days', '7').strip()
    progress_percent = request.form.get('progress_percent', '0').strip()
    tekla_version = request.form.get('tekla_version', 'Tekla 2024').strip()
    revision_no = request.form.get('revision_no', 'Rev 0').strip()
    description = request.form.get('description', '').strip()
    revision_notes = request.form.get('revision_notes', '').strip()
    
    # Eğer durum Tamamlandı yapıldıysa ve bitiş tarihi verilmediyse bugünü ata
    if status == 'Tamamlandı' and not actual_end_date:
        actual_end_date = get_istanbul_now_str('%Y-%m-%d')
        if int(progress_percent or 0) < 100:
            progress_percent = '100'
            
    success = update_design_task(
        task_id=task_id,
        stage_type=stage_type,
        status=status,
        lead_designer=lead_designer,
        designer_user_id=designer_user_id,
        start_date=start_date,
        target_date=target_date,
        actual_end_date=actual_end_date,
        estimated_days=int(estimated_days or 7),
        progress_percent=int(progress_percent or 0),
        tekla_version=tekla_version,
        revision_no=revision_no,
        description=description,
        revision_notes=revision_notes
    )
    
    if success:
        log_activity(
            action="Dizayn Görevi Güncellendi",
            entity_type="project_design_tasks",
            entity_id=task_id,
            details=f"Dizayn görevi güncellendi (Aşama: {stage_type}, Durum: {status}, Rev: {revision_no}).",
            username=cur_user.get('username', 'Kullanıcı'),
            user_id=cur_user.get('id')
        )
        flash("Dizayn & 3D Modelleme görevi başarıyla güncellendi.", "success")
    else:
        flash("Görev güncellenirken bir hata oluştu.", "danger")
        
    return redirect(url_for('dizayn_modelleme'))

@app.route('/api/dizayn/<int:task_id>/sil', methods=['POST'])
def api_dizayn_gorev_sil(task_id):
    cur_user = session.get('user', {})
    user_role = cur_user.get('role', 'izleyici')
    if user_role not in ('admin', 'patron', 'dizayn'):
        flash("Yetkisiz işlem! Yalnızca Yönetici, Patron veya Dizayn sorumlusu görev silebilir.", "danger")
        return redirect(url_for('dizayn_modelleme'))
        
    task = get_design_task_by_id(task_id)
    p_name = task.get('project_name', '') if task else ''
    
    delete_design_task(task_id)
    log_activity(
        action="Dizayn Görevi Silindi",
        entity_type="project_design_tasks",
        entity_id=task_id,
        details=f"'{p_name}' projesine ait dizayn görevi silindi.",
        username=cur_user.get('username', 'Kullanıcı'),
        user_id=cur_user.get('id')
    )
    flash(f"'{p_name}' dizayn görevi başarıyla silindi.", "success")
    return redirect(url_for('dizayn_modelleme'))

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
    name = request.form.get('name', '').strip()
    code = request.form.get('code', '').strip().upper()
    customer = request.form.get('customer', '').strip()
    site_location = request.form.get('site_location', '').strip()
    start_date = request.form.get('start_date', '')
    cutting_start_date = request.form.get('cutting_start_date', '')
    delivery_date = request.form.get('delivery_date', '')
    target_tonnage = clean_float(request.form.get('target_tonnage', 0))
    color = request.form.get('color', '#3b82f6')
    pos_prefix = request.form.get('pos_prefix', '').strip()
    notes = request.form.get('notes', '').strip()

    if not name:
        flash("Proje Adı zorunludur!", "warning")
        return redirect(url_for('projeler'))

    if not code:
        import unicodedata
        tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
        clean_name = name.translate(tr_map)
        clean_code = re.sub(r'[^A-Za-z0-9]', '', clean_name).upper()[:10]
        if not clean_code:
            clean_code = f"PRJ-{int(datetime.now().timestamp()) % 10000}"
        
        conn_check = get_db()
        c_check = conn_check.cursor()
        c_check.execute("SELECT id FROM projects WHERE code = ?", (clean_code,))
        if c_check.fetchone():
            clean_code = f"{clean_code[:7]}-{int(datetime.now().timestamp()) % 1000}"
        conn_check.close()
        code = clean_code

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

        flash(f"'{name}' projesi başarıyla oluşturuldu (Proje Kodu: {code}).", "success")
        return redirect(url_for('proje_detay', project_id=new_id))
    except Exception as e:
        conn.close()
        flash(f"Proje eklenirken hata: {str(e)}", "danger")
        return redirect(url_for('projeler'))

@app.route('/api/projeler/<int:project_id>/tonaj-guncelle', methods=['POST'])
def api_proje_tonaj_guncelle(project_id):
    """Proje toplam tonajını manuel olarak güncelleme/revize etme."""
    target_tonnage = clean_float(request.form.get('target_tonnage', 0.0))
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT code, name FROM projects WHERE id = ?", (project_id,))
    p_row = cursor.fetchone()
    if not p_row:
        conn.close()
        flash("Proje bulunamadı!", "danger")
        return redirect(url_for('projeler'))
    
    cursor.execute("UPDATE projects SET target_tonnage = ? WHERE id = ?", (target_tonnage, project_id))
    conn.commit()
    conn.close()
    
    u = session.get('user', {})
    log_activity(
        action="Proje Tonajı Güncellendi",
        entity_type="projects",
        entity_id=project_id,
        details=f"'{p_row['name']}' projesinin tonajı {target_tonnage:.2f} Ton olarak güncellendi.",
        username=u.get('username', 'Kullanıcı'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )
    flash(f"Proje tonajı başarıyla güncellendi: {target_tonnage:.2f} Ton", "success")
    return redirect(url_for('proje_detay', project_id=project_id))

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


@app.route('/api/projeler/<int:project_id>/listeleri-temizle', methods=['POST'])
def api_proje_listeleri_temizle(project_id):
    """Yanlış liste yüklenmesi durumunda projedeki parça ve montaj listelerini güvenle sıfırlar."""
    target = request.form.get('target', 'all')
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        if target in ('all', 'assemblies'):
            cursor.execute("DELETE FROM assemblies WHERE project_id = ?", (project_id,))
        if target in ('all', 'assembly_parts'):
            cursor.execute("DELETE FROM assembly_parts WHERE project_id = ?", (project_id,))
        if target in ('all', 'parts'):
            cursor.execute("DELETE FROM parts WHERE project_id = ?", (project_id,))
            
        conn.commit()
        
        u = session.get('user', {})
        username = u.get('username') if isinstance(u, dict) else str(u or 'Kullanıcı')
        user_id = u.get('id') if isinstance(u, dict) else None
        
        log_activity(
            action="Proje Listeleri Temizlendi",
            entity_type="projects",
            entity_id=project_id,
            details=f"Proje ID {project_id} için yüklenmiş listeler ({target}) temizlendi.",
            username=username,
            user_id=user_id,
            ip_address=request.remote_addr
        )
        flash("Proje listeleri başarıyla temizlendi. Şimdi doğru listenizi yükleyebilirsiniz.", "success")
    except Exception as e:
        try: conn.rollback()
        except Exception: pass
        flash(f"Liste temizlenirken hata: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for('proje_detay', project_id=project_id))


@app.route('/api/projeler/<int:project_id>/sil', methods=['POST'])
def api_proje_sil(project_id):
    conn = get_db()
    cursor = conn.cursor()
    p_info = f"ID: {project_id}"
    try:
        cursor.execute("SELECT code, name FROM projects WHERE id = ?", (project_id,))
        p_row = cursor.fetchone()
        p_info = f"{p_row['code']} - {p_row['name']}" if p_row else f"ID: {project_id}"
        p_name = p_row['name'] if p_row else ''
        
        # 1. Kesim kayıtlarını koru: Proje silinse bile kesim tonajı ve operatör performans logları silinmesin
        try:
            cursor.execute('UPDATE cutting_entries SET project_id = NULL, project_name = ? WHERE project_id = ?', (p_name, project_id))
        except Exception as e:
            try:
                conn.rollback()
                ensure_column(cursor, "cutting_entries", "project_name", "TEXT", "''", conn=conn)
                cursor.execute('UPDATE cutting_entries SET project_id = NULL, project_name = ? WHERE project_id = ?', (p_name, project_id))
            except Exception:
                try:
                    conn.rollback()
                    cursor.execute('UPDATE cutting_entries SET project_id = NULL WHERE project_id = ?', (project_id,))
                except Exception:
                    pass

        # 2. İlgili alt tabloları temizle
        for tbl in ('parts', 'assemblies', 'assembly_parts', 'qa_inspections', 'material_orders_received', 'material_orders_ordered', 'shipments'):
            try:
                cursor.execute(f'DELETE FROM {tbl} WHERE project_id = ?', (project_id,))
            except Exception:
                pass

        cursor.execute('DELETE FROM projects WHERE id = ?', (project_id,))
        conn.commit()
    except Exception as e:
        try: conn.rollback()
        except Exception: pass
        print(f"api_proje_sil exception note: {e}")
    finally:
        conn.close()

    u = session.get('user', {})
    username = u.get('username') if isinstance(u, dict) else str(u or 'Kullanıcı')
    user_id = u.get('id') if isinstance(u, dict) else None

    log_activity(
        action="Proje Silindi",
        entity_type="projects",
        entity_id=project_id,
        details=f"'{p_info}' projesi silindi (kesim performans kayıtları korundu).",
        username=username,
        user_id=user_id,
        ip_address=request.remote_addr
    )

    flash("Proje başarıyla silindi (kesim performans kayıtları korundu).", "info")
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

    # Özet Sorguları (Tonaj ve Adet)
    try:
        cursor.execute('''
        SELECT cut_date, COUNT(id) as entry_count, SUM(cut_quantity) as total_pieces, ROUND(COALESCE(SUM(cut_tonnage), 0), 2) as total_tonnage
        FROM cutting_entries
        GROUP BY cut_date ORDER BY cut_date DESC LIMIT 15
        ''')
        daily_summary = [dict(r) for r in cursor.fetchall()]

        cursor.execute('''
        SELECT machine, COUNT(id) as entry_count, SUM(cut_quantity) as total_pieces, ROUND(COALESCE(SUM(cut_tonnage), 0), 2) as total_tonnage
        FROM cutting_entries
        WHERE machine IS NOT NULL AND machine != ''
        GROUP BY machine ORDER BY total_tonnage DESC, total_pieces DESC
        ''')
        machine_summary = [dict(r) for r in cursor.fetchall()]

        cursor.execute('''
        SELECT operator, COUNT(id) as entry_count, SUM(cut_quantity) as total_pieces, ROUND(COALESCE(SUM(cut_tonnage), 0), 2) as total_tonnage
        FROM cutting_entries
        WHERE operator IS NOT NULL AND operator != ''
        GROUP BY operator ORDER BY total_tonnage DESC, total_pieces DESC
        ''')
        operator_summary = [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        print(f"kesim_takip sorgu kurtarma başlatılıyor: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        run_schema_migrations()
        try:
            cursor.execute('''
            SELECT cut_date, COUNT(id) as entry_count, SUM(cut_quantity) as total_pieces, ROUND(COALESCE(SUM(cut_tonnage), 0), 2) as total_tonnage
            FROM cutting_entries
            GROUP BY cut_date ORDER BY cut_date DESC LIMIT 15
            ''')
            daily_summary = [dict(r) for r in cursor.fetchall()]

            cursor.execute('''
            SELECT machine, COUNT(id) as entry_count, SUM(cut_quantity) as total_pieces, ROUND(COALESCE(SUM(cut_tonnage), 0), 2) as total_tonnage
            FROM cutting_entries
            WHERE machine IS NOT NULL AND machine != ''
            GROUP BY machine ORDER BY total_tonnage DESC, total_pieces DESC
            ''')
            machine_summary = [dict(r) for r in cursor.fetchall()]

            cursor.execute('''
            SELECT operator, COUNT(id) as entry_count, SUM(cut_quantity) as total_pieces, ROUND(COALESCE(SUM(cut_tonnage), 0), 2) as total_tonnage
            FROM cutting_entries
            WHERE operator IS NOT NULL AND operator != ''
            GROUP BY operator ORDER BY total_tonnage DESC, total_pieces DESC
            ''')
            operator_summary = [dict(r) for r in cursor.fetchall()]
        except Exception:
            daily_summary, machine_summary, operator_summary = [], [], []

    # Kesim Giriş Grupları & Paketleri (Giriş 1, Giriş 2...)
    cutting_batches = get_cutting_batches(limit=100)

    conn.close()
    return render_template('kesim_takip.html',
                           projects=projects,
                           machines=machines,
                           operators=operators,
                           helpers=helpers,
                           cutting_batches=cutting_batches,
                           cutting_logs=cutting_logs,
                           daily_summary=daily_summary,
                           machine_summary=machine_summary,
                           operator_summary=operator_summary,
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
    """Çoklu poz kesim girişini 'Giriş #X' grubu altında tek seferde kaydeder ve parça listesini günceller."""
    data = request.get_json() or {}
    project_id = data.get('project_id')
    cut_date = data.get('cut_date') or datetime.now().strftime("%Y-%m-%d")
    machine = data.get('machine', '').strip()
    operator = data.get('operator', '').strip()
    helper = data.get('helper', '').strip()
    shift = data.get('shift', 'Gündüz')
    notes = data.get('notes', '').strip()
    items = data.get('items', [])

    if not project_id or not items:
        return jsonify({'status': 'error', 'message': 'Proje ve en az bir kesim satırı gereklidir.'}), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT code, name FROM projects WHERE id = ?", (project_id,))
    p_row = cursor.fetchone()
    p_code = p_row['code'] if p_row else ""
    p_name = p_row['name'] if p_row else ""

    u = session.get('user', {})
    saved_count = 0

    # 1. Proje için bir sonraki Giriş No'yu belirle (Örn: Giriş #1, Giriş #2)
    cursor.execute("SELECT COALESCE(MAX(batch_no), 0) + 1 as next_no FROM cutting_batches WHERE project_id = ?", (project_id,))
    b_row = cursor.fetchone()
    next_batch_no = b_row['next_no'] if b_row else 1
    batch_code = f"Giriş #{next_batch_no}"

    # 2. cutting_batches kaydını oluştur
    cursor.execute('''
    INSERT INTO cutting_batches (batch_no, batch_code, project_id, project_code, project_name, cut_date, machine, operator, helper, shift, total_items, total_quantity, total_tonnage, notes, user_id, created_by)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?)
    ''', (next_batch_no, batch_code, project_id, p_code, p_name, cut_date, machine, operator, helper, shift, notes, u.get('id'), u.get('username', 'Kullanıcı')))

    batch_id = cursor.lastrowid
    if not batch_id:
        cursor.execute("SELECT id FROM cutting_batches WHERE project_id = ? AND batch_no = ? ORDER BY id DESC LIMIT 1", (project_id, next_batch_no))
        br = cursor.fetchone()
        batch_id = br['id'] if br else None

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
        row_notes = str(it.get('notes', '')).strip() or notes

        if not pos_no or cut_quantity <= 0:
            continue

        # Parça listesini güncelle ve birim ağırlığı al (Poz prefixli veya prefixsiz aranır)
        cursor.execute("""
        SELECT id, pos_no, quantity, cut_quantity, profile_type, unit_weight 
        FROM parts 
        WHERE project_id = ? AND (
            pos_no = ? 
            OR pos_no = ? 
            OR pos_no = ? 
            OR LOWER(pos_no) = LOWER(?)
            OR LOWER(pos_no) = LOWER(?)
        )
        ORDER BY CASE WHEN pos_no = ? THEN 1 WHEN pos_no = ? THEN 2 ELSE 3 END
        LIMIT 1
        """, (project_id, pos_no, raw_pos, (prefix + raw_pos) if prefix else raw_pos, pos_no, raw_pos, pos_no, raw_pos))
        part_row = cursor.fetchone()
        if not part_row and profile:
            cursor.execute("""
            SELECT id, pos_no, quantity, cut_quantity, profile_type, unit_weight 
            FROM parts 
            WHERE project_id = ? AND profile_type = ? AND (pos_no LIKE ? OR pos_no LIKE ?)
            LIMIT 1
            """, (project_id, profile, f"%{raw_pos}%", f"%{pos_no}%"))
            part_row = cursor.fetchone()
        unit_weight = 0.0
        if part_row:
            new_cut_total = part_row['cut_quantity'] + cut_quantity
            cursor.execute("UPDATE parts SET cut_quantity = ?, remaining_quantity = ? WHERE id = ?",
                           (new_cut_total, max(0, part_row['quantity'] - new_cut_total), part_row['id']))
            if not profile:
                profile = part_row['profile_type']
            unit_weight = float(part_row['unit_weight'] or 0.0)

        cut_tonnage = round((cut_quantity * unit_weight) / 1000.0, 4)

        # Log kaydet (batch_id ile bağlı)
        cursor.execute('''
        INSERT INTO cutting_entries (batch_id, project_id, project_code, project_name, pos_no, profile, cut_quantity, unit_weight, cut_tonnage, cut_date, machine, operator, helper, shift, user_id, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (batch_id, project_id, p_code, p_name, pos_no, profile, cut_quantity, unit_weight, cut_tonnage, cut_date, row_machine, row_operator, helper, shift, u.get('id'), row_notes))
        saved_count += 1

    # 3. Batch özet toplamlarını güncelle
    if batch_id:
        cursor.execute('''
        SELECT COUNT(id) as cnt, COALESCE(SUM(cut_quantity), 0) as tot_qty, COALESCE(SUM(cut_tonnage), 0.0) as tot_ton
        FROM cutting_entries WHERE batch_id = ?
        ''', (batch_id,))
        b_stat = cursor.fetchone()
        if b_stat and b_stat['cnt'] > 0:
            cursor.execute('''
            UPDATE cutting_batches
            SET total_items = ?, total_quantity = ?, total_tonnage = ?
            WHERE id = ?
            ''', (b_stat['cnt'], b_stat['tot_qty'], round(b_stat['tot_ton'], 4), batch_id))
        else:
            cursor.execute("DELETE FROM cutting_batches WHERE id = ?", (batch_id,))

    conn.commit()
    conn.close()
    invalidate_app_cache()

    if saved_count > 0:
        log_activity(
            action="Toplu Kesim Girişi",
            entity_type="cutting_batches",
            entity_id=batch_id or project_id,
            details=f"{p_code} projesi için {batch_code} oluşturuldu ({saved_count} satır kesim kaydı, {machine} - {operator}).",
            username=u.get('username', 'Kullanıcı'),
            user_id=u.get('id'),
            ip_address=request.remote_addr
        )

    return jsonify({
        'status': 'success',
        'saved_count': saved_count,
        'batch_id': batch_id,
        'batch_code': batch_code,
        'message': f"{p_code} için {saved_count} poz kesimi '{batch_code}' olarak kaydedildi ve ana listeye işlendi."
    })

@app.route('/api/kesim-takip/batch/<int:batch_id>/detay')
def api_kesim_batch_detay(batch_id):
    """Belirli bir kesim giriş grubunun (Giriş 1 vb.) detaylarını ve poz satırlarını döndürür."""
    batch_data = get_cutting_batch_details(batch_id)
    if not batch_data:
        return jsonify({'status': 'error', 'message': 'Kesim giriş grubu bulunamadı.'}), 404
    return jsonify({'status': 'success', 'batch': batch_data})

@app.route('/api/kesim-takip/batch/<int:batch_id>/guncelle', methods=['POST'])
def api_kesim_batch_guncelle(batch_id):
    """Kesim giriş paketindeki poz miktarlarını günceller ve ana parçalar ile eşitler."""
    u = session.get('user', {})
    user_role = u.get('role', '')
    if not (can_user_edit(user_role, 'kesim_takip') or can_user_edit(user_role, 'kesim') or user_role in ('admin', 'patron', 'genel müdür', 'imalat müdürü', 'imalat mühendisi', 'formen', 'usta', 'iş hazırlama')):
        return jsonify({'status': 'error', 'message': 'Bu işlem için düzenleme yetkiniz bulunmamaktadır.'}), 403

    data = request.get_json() or {}
    items = data.get('items', [])
    if not items:
        return jsonify({'status': 'error', 'message': 'Güncellenecek poz bilgisi gönderilmedi.'}), 400

    success, message = update_cutting_batch_items(batch_id, items, user_info=u)
    if success:
        log_activity(
            action="Kesim Girişi Düzenlendi",
            entity_type="cutting_batches",
            entity_id=batch_id,
            details=f"Kesim Giriş #{batch_id} poz miktarları güncellendi.",
            username=u.get('username', 'Kullanıcı'),
            user_id=u.get('id'),
            ip_address=request.remote_addr
        )
        return jsonify({'status': 'success', 'message': message})
    return jsonify({'status': 'error', 'message': message}), 400

@app.route('/api/kesim-takip/batch/<int:batch_id>/sil', methods=['POST'])
def api_kesim_batch_sil(batch_id):
    """Kesim giriş paketini tamamen geri alır ve parçaların kesilen adetlerini otomatik düşer."""
    u = session.get('user', {})
    user_role = u.get('role', '')
    if not (can_user_edit(user_role, 'kesim_takip') or can_user_edit(user_role, 'kesim') or user_role in ('admin', 'patron', 'genel müdür', 'imalat müdürü', 'imalat mühendisi', 'formen', 'usta', 'iş hazırlama')):
        return jsonify({'status': 'error', 'message': 'Bu işlem için silme yetkiniz bulunmamaktadır.'}), 403

    success, message = delete_cutting_batch(batch_id, user_info=u)
    if success:
        log_activity(
            action="Kesim Girişi Geri Alındı / Silindi",
            entity_type="cutting_batches",
            entity_id=batch_id,
            details=f"Kesim Giriş #{batch_id} silindi ve adetler parçalardan geri düşüldü.",
            username=u.get('username', 'Kullanıcı'),
            user_id=u.get('id'),
            ip_address=request.remote_addr
        )
        return jsonify({'status': 'success', 'message': message})
    return jsonify({'status': 'error', 'message': message}), 400

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

    cursor.execute("SELECT code, name FROM projects WHERE id = ?", (project_id,))
    p_row = cursor.fetchone()
    p_code = p_row['code'] if p_row else ""
    p_name = p_row['name'] if p_row else ""

    # Parça listesini güncelle ve birim ağırlığı al
    cursor.execute("SELECT id, quantity, cut_quantity, profile_type, unit_weight FROM parts WHERE project_id = ? AND pos_no = ?", (project_id, pos_no))
    part_row = cursor.fetchone()

    unit_weight = 0.0
    is_overcut = False
    if part_row:
        new_cut_total = part_row['cut_quantity'] + cut_quantity
        cursor.execute("UPDATE parts SET cut_quantity = ?, remaining_quantity = ? WHERE id = ?",
                       (new_cut_total, max(0, part_row['quantity'] - new_cut_total), part_row['id']))
        if not profile:
            profile = part_row['profile_type']
        unit_weight = float(part_row['unit_weight'] or 0.0)
        if new_cut_total > part_row['quantity']:
            is_overcut = True

    cut_tonnage = round((cut_quantity * unit_weight) / 1000.0, 4)

    # 1. Giriş Numarasını bul ve Batch oluştur
    cursor.execute("SELECT COALESCE(MAX(batch_no), 0) + 1 as next_no FROM cutting_batches WHERE project_id = ?", (project_id,))
    b_row = cursor.fetchone()
    next_batch_no = b_row['next_no'] if b_row else 1
    batch_code = f"Giriş #{next_batch_no}"

    u = session.get('user', {})
    cursor.execute('''
    INSERT INTO cutting_batches (batch_no, batch_code, project_id, project_code, project_name, cut_date, machine, operator, helper, shift, total_items, total_quantity, total_tonnage, notes, user_id, created_by)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
    ''', (next_batch_no, batch_code, project_id, p_code, p_name, cut_date, machine, operator, helper, shift, cut_quantity, cut_tonnage, notes, u.get('id'), u.get('username', 'Kullanıcı')))

    batch_id = cursor.lastrowid
    if not batch_id:
        cursor.execute("SELECT id FROM cutting_batches WHERE project_id = ? AND batch_no = ? ORDER BY id DESC LIMIT 1", (project_id, next_batch_no))
        br = cursor.fetchone()
        batch_id = br['id'] if br else None

    # Kesim logunu ekle
    cursor.execute('''
    INSERT INTO cutting_entries (batch_id, project_id, project_code, project_name, pos_no, profile, cut_quantity, unit_weight, cut_tonnage, cut_date, machine, operator, helper, shift, user_id, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (batch_id, project_id, p_code, p_name, pos_no, profile, cut_quantity, unit_weight, cut_tonnage, cut_date, machine, operator, helper, shift, u.get('id'), notes))
    conn.commit()
    conn.close()
    invalidate_app_cache()

    log_activity(
        action="Kesim Girişi Yapıldı",
        entity_type="cutting_entries",
        entity_id=project_id,
        details=f"{p_code} - Poz '{pos_no}' için {cut_quantity} adet kesim ({cut_tonnage:.3f} Ton) işlendi ({machine} - {operator}).",
        username=u.get('username', 'Kullanıcı'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )

    if is_overcut:
        flash(f"Dikkat: '{pos_no}' pozu için girilen toplam kesim ({new_cut_total}), proje hedef miktarını ({part_row['quantity']}) aştı!", "warning")
    else:
        flash(f"'{pos_no}' pozu için {cut_quantity} adet ({cut_tonnage:.3f} Ton) kesim başarıyla işlendi ({batch_code}).", "success")

    return redirect(url_for('kesim_takip'))


# =========================================================================
# 6. İMALAT PLAN (PROJE BİTİŞ TARİHLERİ & AŞAMA PLANLAMA)
# =========================================================================
@app.route('/imalat-plan')
def imalat_plan():
    projects = get_all_projects_summary(exclude_status=('Arşiv',))
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

@app.route('/api/kalite-kontrol/boyaya-sevk', methods=['POST'])
def api_kalite_boyaya_sevk():
    """Kaliteden onaylanan montajları boyahaneye / yüzey korumaya sevk eder."""
    inspection_id = request.form.get('inspection_id')
    quantity = int(request.form.get('quantity', 1))
    process_type = request.form.get('process_type', 'Kumlama') # 'Kumlama', 'Boya', 'Galvaniz'
    notes = request.form.get('notes', '').strip()

    u = session.get('user', {})
    operator_name = u.get('full_name', 'Kalite & Sevk')

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM qa_inspections WHERE id = ?", (inspection_id,))
    qa = cursor.fetchone()
    if not qa or not qa['assembly_id']:
        conn.close()
        flash("Kalite kaydı veya montaj bilgisi bulunamadı.", "warning")
        return redirect(url_for('kalite_kontrol'))

    cursor.execute("SELECT * FROM assemblies WHERE id = ?", (qa['assembly_id'],))
    ass = cursor.fetchone()
    if not ass:
        conn.close()
        flash("Montaj kaydı bulunamadı.", "warning")
        return redirect(url_for('kalite_kontrol'))

    # Hedef istasyon ve durum güncellemesi
    new_status = 'KUMLAMADA' if process_type == 'Kumlama' else ('BOYADA' if process_type == 'Boya' else 'GALVANIZDE')
    
    if process_type == 'Kumlama':
        new_sandblast = ass['paint_sandblast_qty'] + quantity
        cursor.execute('''
        UPDATE assemblies SET
            paint_sandblast_qty = ?,
            paint_status = ?
        WHERE id = ?
        ''', (new_sandblast, new_status, qa['assembly_id']))
    elif process_type == 'Boya':
        new_paint = ass['paint_paint_qty'] + quantity
        cursor.execute('''
        UPDATE assemblies SET
            paint_paint_qty = ?,
            paint_status = ?
        WHERE id = ?
        ''', (new_paint, new_status, qa['assembly_id']))
    else:
        new_galv = ass['paint_galv_qty'] + quantity
        cursor.execute('''
        UPDATE assemblies SET
            paint_galv_qty = ?,
            paint_status = ?
        WHERE id = ?
        ''', (new_galv, new_status, qa['assembly_id']))

    # Paint record kaydı ekle
    now_str = get_istanbul_now_str()
    cursor.execute('''
    INSERT INTO paint_records (project_id, assembly_id, assembly_pos, process_type, quantity, completion_date, operator_name, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (ass['project_id'], qa['assembly_id'], qa['assembly_pos'], f"Boyaya Sevk ({process_type})", quantity, now_str, operator_name, notes))

    conn.commit()
    conn.close()

    log_activity(
        action="Boyaya Sevk Edildi",
        entity_type="assemblies",
        entity_id=qa['assembly_id'],
        details=f"Marka '{qa['assembly_pos']}' ({quantity} Adet) -> {process_type} istasyonuna sevk edildi. Not: {notes}",
        username=u.get('username', 'Kullanıcı'),
        user_id=u.get('id'),
        ip_address=request.remote_addr
    )

    add_notification(
        category='boya_sevk',
        title="Boyahaneye Yeni Sevk",
        message=f"Marka '{qa['assembly_pos']}' ({quantity} Adet) kalite kontrol onayından sonra {process_type} istasyonuna sevk edildi.",
        icon='fa-paint-roller',
        color='purple',
        link_url=url_for('boya_takip')
    )

    flash(f"'{qa['assembly_pos']}' ({quantity} Adet) başarıyla Boya Takip ({process_type}) istasyonuna sevk edildi.", "success")
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
    if process_type in ('Tamamlandı', 'Boya'):
        new_done = ass['paint_done_qty'] + quantity
        cursor.execute('''
        UPDATE assemblies SET
            paint_done_qty = ?,
            paint_status = 'TAMAMLANDI',
            paint_ral = COALESCE(?, paint_ral),
            paint_dft = COALESCE(?, paint_dft)
        WHERE id = ?
        ''', (new_done, ral_code or None, dft_micron or None, assembly_id))
        add_notification(
            category='boya_tamam',
            title='Boya Tamamlandı & Sevke Hazır',
            message=f"Marka '{ass['assembly_pos']}' ({quantity} Adet) boyandı ve sevkiyat havuzuna hazırlandı.",
            icon='fa-truck-ramp-box',
            color='purple',
            link_url=url_for('sevk')
        )
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
    return redirect(request.referrer or url_for('boya_takip'))


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
    roles_list = get_roles_list()
    all_role_objects = get_all_roles()
    
    return render_template('kullanicilar.html',
                           users=users,
                           roles=roles_list,
                           role_objects=all_role_objects,
                           modules=MODULES_LIST,
                           permissions=role_perms)

@app.route('/api/roller/ekle', methods=['POST'])
def api_roller_ekle():
    cur_user = session.get('user', {})
    if cur_user.get('role') not in ('admin', 'patron'):
        flash("Yetkisiz işlem! Yalnızca Yönetici ve Patron yeni rol tanımlayabilir.", "danger")
        return redirect(url_for('kullanicilar'))
        
    role_name = request.form.get('role_name', '').strip().lower()
    description = request.form.get('description', '').strip()
    
    if not role_name:
        flash("Rol adı zorunludur!", "warning")
        return redirect(url_for('kullanicilar'))
        
    success, msg = add_custom_role(role_name, description)
    if success:
        log_activity(
            action="Yeni Rol Eklendi",
            entity_type="roles",
            details=f"Sisteme yeni rol eklendi: '{role_name}'",
            username=cur_user.get('username', 'Kullanıcı'),
            user_id=cur_user.get('id')
        )
        flash(msg, "success")
    else:
        flash(msg, "danger")
        
    return redirect(url_for('kullanicilar'))

@app.route('/api/roller/sil', methods=['POST'])
def api_roller_sil():
    cur_user = session.get('user', {})
    if cur_user.get('role') not in ('admin', 'patron'):
        flash("Yetkisiz işlem! Yalnızca Yönetici ve Patron rol silebilir.", "danger")
        return redirect(url_for('kullanicilar'))
        
    role_name = request.form.get('role_name', '').strip().lower()
    if not role_name:
        flash("Silinecek rol belirtilmedi!", "warning")
        return redirect(url_for('kullanicilar'))
        
    success, msg = delete_custom_role(role_name)
    if success:
        log_activity(
            action="Rol Silindi",
            entity_type="roles",
            details=f"Sistem rolü silindi: '{role_name}'",
            username=cur_user.get('username', 'Kullanıcı'),
            user_id=cur_user.get('id')
        )
        flash(msg, "success")
    else:
        flash(msg, "danger")
        
    return redirect(url_for('kullanicilar'))

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
    all_roles = get_roles_list()
    for r in all_roles:
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

@app.route('/api/kullanici/<int:user_id>/sil', methods=['POST'])
@app.route('/api/kullanicilar/<int:user_id>/sil', methods=['POST'])
def api_kullanici_sil(user_id):
    """Admin veya Patronun kullanıcıyı kalıcı olarak silmesini sağlar."""
    cur_user = session.get('user', {})
    if cur_user.get('role') not in ('admin', 'patron'):
        flash("Yetkisiz işlem! Yalnızca Yönetici veya Patron kullanıcı silebilir.", "danger")
        return redirect(url_for('kullanicilar'))

    if cur_user.get('id') == user_id:
        flash("Kendi oturum açtığınız hesabı silemezsiniz!", "danger")
        return redirect(url_for('kullanicilar'))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, full_name FROM users WHERE id = ?", (user_id,))
    target_user = cursor.fetchone()
    conn.close()

    if not target_user:
        flash("Kullanıcı bulunamadı!", "warning")
        return redirect(url_for('kullanicilar'))

    if target_user['username'] == 'admin':
        flash("Ana 'admin' hesabı sistem güvenliği için silinemez!", "danger")
        return redirect(url_for('kullanicilar'))

    delete_user(user_id)
    log_activity(
        action="Kullanıcı Silindi",
        entity_type="users",
        entity_id=user_id,
        details=f"'{target_user['full_name']}' ({target_user['username']}) kullanıcısı sistemden kalıcı olarak silindi.",
        username=cur_user.get('username'),
        user_id=cur_user.get('id'),
        ip_address=request.remote_addr
    )
    flash(f"'{target_user['full_name']}' kullanıcısı başarıyla silindi.", "success")
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
    auto_fetch_web = request.form.get('auto_fetch_web_logo') == '1'

    company_logo_url = None
    if 'company_logo' in request.files and request.files['company_logo'].filename != '':
        logo_file = request.files['company_logo']
        ext = os.path.splitext(logo_file.filename)[1].lower()
        filename = f"logo_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        os.makedirs(os.path.join(BASE_DIR, 'static', 'uploads'), exist_ok=True)
        logo_file.save(os.path.join(BASE_DIR, 'static', 'uploads', filename))
        company_logo_url = f"/static/uploads/{filename}"
    elif company_website_url and (auto_fetch_web or not request.form.get('keep_manual_logo')):
        # Firma web sitesinden logoyu otomatik çek
        web_logo = fetch_company_logo_from_website(company_website_url)
        if web_logo:
            company_logo_url = web_logo

    update_system_settings(app_title, app_subtitle, company_name, company_logo_url, company_website_url)
    flash("Firma başlık, web sitesi ve logo ayarları başarıyla güncellendi.", "success")
    return redirect(url_for('raporlar'))

@app.route('/api/ayarlar/fetch-logo-from-web', methods=['POST'])
def api_ayarlar_fetch_logo_from_web():
    """AJAX ile firma web sitesinden anlık logo çekip önizleme döner."""
    cur_user = session.get('user', {})
    if cur_user.get('role') not in ('admin', 'patron'):
        return jsonify({'status': 'error', 'message': 'Yetkisiz işlem!'}), 403

    data = request.get_json(silent=True) or {}
    website_url = data.get('website_url') or request.form.get('website_url', '')
    if not website_url:
        settings = get_system_settings()
        website_url = settings.get('company_website_url', '')

    if not website_url:
        return jsonify({'status': 'error', 'message': 'Lütfen önce geçerli bir web sitesi adresi girin.'}), 400

    logo_url = fetch_company_logo_from_website(website_url)
    if logo_url:
        settings = get_system_settings()
        update_system_settings(
            settings.get('app_title'),
            settings.get('app_subtitle'),
            settings.get('company_name'),
            logo_url,
            website_url
        )
        return jsonify({
            'status': 'success',
            'message': 'Logo web sitesinden başarıyla çekildi ve sisteme uygulandı!',
            'logo_url': logo_url
        })
    else:
        return jsonify({
            'status': 'error',
            'message': 'Web sitesinden logo otomatik alınamadı. Lütfen adresi kontrol edin veya manuel yükleyin.'
        }), 400

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
    try:
        get_system_settings.cache_clear()
    except Exception:
        pass
    invalidate_app_cache()

    flash("Firma bilgileri ve sistem ayarları başarıyla güncellendi.", "success")
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

@app.route('/api/canli-denetim/temizle', methods=['POST'])
def api_canli_denetim_temizle():
    """Yalnızca yönetici / yetkili kullanıcıların denetim günlüğünü temizlemesini sağlar."""
    u = session.get('user')
    user_role = u.get('role') if isinstance(u, dict) else session.get('role', 'izleyici')
    
    if user_role not in ('admin', 'patron', 'genel müdür'):
        flash("Bu işlem için yönetici yetkisi gereklidir.", "danger")
        return redirect(url_for('canli_denetim'))
        
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM activity_logs")
    conn.commit()
    conn.close()
    
    username = u.get('username') if isinstance(u, dict) else str(u or 'Yönetici')
    user_id = u.get('id') if isinstance(u, dict) else None
    
    log_activity(
        action="Denetim Günlüğü Temizlendi",
        entity_type="activity_logs",
        details="Tüm geçmiş canlı denetim kayıtları yönetici tarafından temizlendi.",
        username=username,
        user_id=user_id,
        ip_address=request.remote_addr
    )
    
    flash("Canlı denetim günlüğü başarıyla temizlendi.", "success")
    return redirect(url_for('canli_denetim'))


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
# 19. CANLI SOHBET & İLETİŞİM MERKEZİ (BİRİM KANALLARI & BİREBİR DM)
# =========================================================================
CHAT_CHANNELS = [
    {
        'id': 'genel',
        'name': 'Genel Fabrika & Duyurular',
        'short_name': '#genel',
        'icon': 'fa-solid fa-globe',
        'color': 'blue',
        'badge_class': 'bg-blue-500/10 text-blue-400 border-blue-500/30',
        'desc': 'Tüm birimler ortak koordinasyon ve genel fabrika duyuruları'
    },
    {
        'id': 'kesim',
        'name': 'Kesim & Ön İmalat',
        'short_name': '#kesim',
        'icon': 'fa-solid fa-scissors',
        'color': 'amber',
        'badge_class': 'bg-amber-500/10 text-amber-400 border-amber-500/30',
        'desc': 'CNC Plazma, Sac Lazer, Profil Lazer, Testere ve Oksijen Kesim'
    },
    {
        'id': 'imalat',
        'name': 'İmalat & Çatım-Kaynak',
        'short_name': '#imalat',
        'icon': 'fa-solid fa-toolbox',
        'color': 'orange',
        'badge_class': 'bg-orange-500/10 text-orange-400 border-orange-500/30',
        'desc': 'Çatım holleri, kaynak istasyonları, tesviye ve montaj atölyesi'
    },
    {
        'id': 'kalite',
        'name': 'Kalite Kontrol (QA/QC)',
        'short_name': '#kalite',
        'icon': 'fa-solid fa-clipboard-check',
        'color': 'teal',
        'badge_class': 'bg-teal-500/10 text-teal-400 border-teal-500/30',
        'desc': 'Kaynak muayenesi, boya mikron ölçümü, NDT ve kalite onayları'
    },
    {
        'id': 'boya',
        'name': 'Boya & Yüzey İşlem',
        'short_name': '#boya',
        'icon': 'fa-solid fa-paint-roller',
        'color': 'purple',
        'badge_class': 'bg-purple-500/10 text-purple-400 border-purple-500/30',
        'desc': 'Kumlama (SA 2.5), Astar, Son Kat Epoksi/Poliüretan ve Galvaniz'
    },
    {
        'id': 'sevkiyat',
        'name': 'Sevkiyat & Lojistik',
        'short_name': '#sevkiyat',
        'icon': 'fa-solid fa-truck-moving',
        'color': 'emerald',
        'badge_class': 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
        'desc': 'Yükleme koordinasyonu, sevk irsaliyeleri ve nakliye takibi'
    },
    {
        'id': 'muhasebe',
        'name': 'Muhasebe & Satınalma',
        'short_name': '#muhasebe',
        'icon': 'fa-solid fa-file-invoice-dollar',
        'color': 'indigo',
        'badge_class': 'bg-indigo-500/10 text-indigo-400 border-indigo-500/30',
        'desc': 'Fatura, irsaliye kayıtları, malzeme tedarik ve satınalma'
    },
    {
        'id': 'yonetim',
        'name': 'Yönetim & Mühendislik',
        'short_name': '#yonetim',
        'icon': 'fa-solid fa-user-tie',
        'color': 'rose',
        'badge_class': 'bg-rose-500/10 text-rose-400 border-rose-500/30',
        'desc': 'Yöneticiler, Proje Müdürleri, İmalat Mühendisleri ve İş Hazırlama'
    }
]

@app.route('/sohbet')
def sohbet():
    """Tam sayfa kurumsal sohbet ve iletişim merkezi."""
    cur_u = session.get('user', {})
    if not cur_u:
        return redirect(url_for('login', next=request.path))

    initial_channel = request.args.get('channel', 'genel')
    initial_partner_id = request.args.get('partner_id')
    if initial_partner_id and str(initial_partner_id).isdigit():
        initial_partner_id = int(initial_partner_id)
    else:
        initial_partner_id = None

    users = get_chat_users_with_unread(current_user_id=cur_u.get('id'))

    return render_template(
        'sohbet.html',
        chat_channels=CHAT_CHANNELS,
        users=users,
        initial_channel=initial_channel,
        initial_partner_id=initial_partner_id
    )

@app.route('/api/chat/messages')
@app.route('/api/chat/<channel>')
def api_chat_get(channel=None):
    """Kanal bazlı veya birebir DM sohbet mesajlarını JSON döner."""
    cur_u = session.get('user', {})
    user_id = cur_u.get('id') if cur_u else None

    req_channel = channel or request.args.get('channel') or 'genel'
    partner_id = request.args.get('partner_id') or request.args.get('dm_user_id')

    if partner_id and str(partner_id).isdigit() and int(partner_id) > 0 and user_id:
        p_id = int(partner_id)
        msgs = get_chat_messages(user_id=user_id, partner_id=p_id, limit=150)
        # Mesajları okundu olarak işaretle
        mark_chat_messages_as_read(current_user_id=user_id, partner_id=p_id)
    else:
        msgs = get_chat_messages(channel=req_channel, limit=150)

    return jsonify(msgs)

@app.route('/api/chat/send', methods=['POST'])
def api_chat_send():
    """Yeni sohbet mesajı ve görsel kaydeder (Kanal veya Birebir DM)."""
    u = session.get('user', {})
    if not u:
        return jsonify({'status': 'error', 'message': 'Oturum açılmamış.'}), 401

    channel = request.form.get('channel', 'genel')
    receiver_id = request.form.get('receiver_id')
    message = request.form.get('message', '').strip()
    photo_url = ""

    if receiver_id and str(receiver_id).isdigit() and int(receiver_id) > 0:
        receiver_id = int(receiver_id)
    else:
        receiver_id = None

    if 'photo' in request.files:
        photo = request.files['photo']
        if photo and photo.filename != '':
            ext = os.path.splitext(photo.filename)[1].lower()
            if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                prefix = f"dm_{receiver_id}" if receiver_id else f"chat_{channel}"
                filename = f"{prefix}_{int(datetime.now().timestamp())}_{photo.filename}"
                save_dir = os.path.join(BASE_DIR, 'static', 'uploads', 'chat')
                os.makedirs(save_dir, exist_ok=True)
                photo.save(os.path.join(save_dir, filename))
                photo_url = f"/static/uploads/chat/{filename}"

    if not message and not photo_url:
        return jsonify({'status': 'error', 'message': 'Boş mesaj gönderilemez.'}), 400

    msg_id = save_chat_message(
        channel=channel,
        user_id=u.get('id'),
        username=u.get('username'),
        full_name=u.get('full_name'),
        message=message,
        photo_url=photo_url,
        receiver_id=receiver_id
    )

    if receiver_id:
        notif_title = f"💬 Özel Mesaj: {u.get('full_name', 'Personel')}"
        notif_url = f"/sohbet?partner_id={u.get('id')}"
    else:
        notif_title = f"💬 #{channel} - {u.get('full_name', 'Personel')}"
        notif_url = f"/sohbet?channel={channel}"

    send_web_push_notification(
        title=notif_title,
        message=message[:100] if message else "📷 Fotoğraf paylaştı",
        url=notif_url
    )

    return jsonify({
        'status': 'success',
        'message_id': msg_id,
        'photo_url': photo_url,
        'receiver_id': receiver_id,
        'channel': channel
    })

@app.route('/api/chat/mark-read', methods=['POST'])
def api_chat_mark_read():
    """Kullanıcıdan gelen mesajları okundu olarak işaretler."""
    u = session.get('user', {})
    if not u:
        return jsonify({'status': 'error', 'message': 'Oturum açılmamış.'}), 401

    data = request.get_json(silent=True) or {}
    partner_id = data.get('partner_id') or request.form.get('partner_id')
    if partner_id and str(partner_id).isdigit():
        mark_chat_messages_as_read(current_user_id=u.get('id'), partner_id=int(partner_id))
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': 'partner_id gerekli'}), 400

@app.route('/api/chat/unread-summary')
def api_chat_unread_summary():
    """Mevcut kullanıcının toplam ve kullanıcı bazlı okunmamış mesaj sayılarını döner."""
    u = session.get('user', {})
    if not u:
        return jsonify({'total_unread': 0, 'by_user': {}})
    summary = get_unread_chat_summary(current_user_id=u.get('id'))
    return jsonify(summary)

@app.route('/api/chat/users')
def api_chat_users():
    """Aktif kullanıcı listesini ve okunmamış DM sayılarını JSON döner."""
    u = session.get('user', {})
    cur_id = u.get('id') if u else None
    users = get_chat_users_with_unread(current_user_id=cur_id)
    return jsonify(users)

@app.route('/api/chat/clear', methods=['POST'])
def api_chat_clear():
    """Belirli bir sohbet kanalındaki veya DM konuşmasındaki mesajları temizler."""
    u = session.get('user', {})
    if not u:
        return jsonify({'status': 'error', 'message': 'Oturum açılmamış.'}), 401

    data = request.get_json(silent=True) or {}
    channel = data.get('channel') or request.form.get('channel')
    partner_id = data.get('partner_id') or request.form.get('partner_id')

    if partner_id and str(partner_id).isdigit() and int(partner_id) > 0:
        p_id = int(partner_id)
        clear_chat_messages(user_id=u.get('id'), partner_id=p_id)
        log_activity(
            action="DM Sohbeti Temizlendi",
            entity_type="chat_messages",
            details=f"Kullanıcı ID {p_id} ile olan özel sohbet mesajları temizlendi.",
            username=u.get('username'),
            user_id=u.get('id'),
            ip_address=request.remote_addr
        )
        return jsonify({'status': 'success', 'message': 'Özel sohbet geçmişi temizlendi.'})
    else:
        chan = channel or 'genel'
        clear_chat_messages(channel=chan)
        log_activity(
            action="Sohbet Temizlendi",
            entity_type="chat_messages",
            details=f"#{chan} kanalındaki sohbet mesajları temizlendi.",
            username=u.get('username'),
            user_id=u.get('id'),
            ip_address=request.remote_addr
        )
        return jsonify({'status': 'success', 'message': f"#{chan} kanalındaki mesajlar temizlendi."})

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
        
    u = session.get('user')
    user_id = None
    if isinstance(u, dict):
        user_id = u.get('id')
    elif isinstance(u, int):
        user_id = u
    elif isinstance(u, str) and u.isdigit():
        user_id = int(u)
        
    save_push_subscription(
        user_id=user_id,
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

@app.route('/ai-muhendis')
def ai_muhendis():
    return redirect(url_for('index'))



# =========================================================================
# SUNUCU ÇALIŞTIRMA
# =========================================================================


# =========================================================================
# 22. İSG & SAHA UYGUNSUZLUK MODÜLÜ
# =========================================================================
@app.route('/isg-takip')
def isg_takip():
    """İSG uygunsuzluk, ramak kala ve saha güvenlik bildirimleri sayfası."""
    status_filter = request.args.get('status', '')
    records = get_isg_records(status=status_filter if status_filter else None)
    return render_template('isg_takip.html', records=records, selected_status=status_filter)

@app.route('/api/isg/ekle', methods=['POST'])
def api_isg_ekle():
    title = request.form.get('title', '').strip()
    category = request.form.get('category', 'Uygunsuzluk')
    location = request.form.get('location', '').strip()
    description = request.form.get('description', '').strip()
    priority = request.form.get('priority', 'Orta')
    status = request.form.get('status', 'Açık')
    corrective_action = request.form.get('corrective_action', '').strip()
    assigned_to = request.form.get('assigned_to', '').strip()
    
    cur_user = session.get('user', {})
    reported_by = cur_user.get('full_name') or cur_user.get('username') or 'Saha Personeli'
    
    photo_url = ''
    if 'photo' in request.files and request.files['photo'].filename != '':
        pfile = request.files['photo']
        ext = os.path.splitext(pfile.filename)[1].lower()
        fname = f"isg_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        upload_dir = os.path.join(BASE_DIR, 'static', 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        pfile.save(os.path.join(upload_dir, fname))
        photo_url = f"/static/uploads/{fname}"
        
    add_isg_record(title, category, location, description, photo_url, priority, status, corrective_action, assigned_to, reported_by)
    flash("İSG bildirimi başarıyla kaydedildi.", "success")
    return redirect(url_for('isg_takip'))

@app.route('/api/isg/<int:record_id>/guncelle', methods=['POST'])
def api_isg_guncelle(record_id):
    status = request.form.get('status', 'Açık')
    corrective_action = request.form.get('corrective_action', '').strip()
    assigned_to = request.form.get('assigned_to', '').strip()
    update_isg_record(record_id, status, corrective_action, assigned_to)
    flash("İSG kaydı güncellendi.", "success")
    return redirect(url_for('isg_takip'))

@app.route('/api/isg/<int:record_id>/sil', methods=['POST'])
def api_isg_sil(record_id):
    delete_isg_record(record_id)
    flash("İSG kaydı silindi.", "success")
    return redirect(url_for('isg_takip'))

# =========================================================================
# 23. SERVİS GÜZERGAHLARI VE YOLCU LİSTESİ MODÜLÜ
# =========================================================================
@app.route('/servis-guzergah')
def servis_guzergah():
    """Fabrika servis hatları, şoförler, duraklar ve yolcu listesi."""
    shuttles = get_shuttle_routes()
    return render_template('servis_guzergah.html', shuttles=shuttles)

@app.route('/api/servis/ekle', methods=['POST'])
def api_servis_ekle():
    name = request.form.get('name', '').strip()
    driver_name = request.form.get('driver_name', '').strip()
    driver_phone = request.form.get('driver_phone', '').strip()
    plate_number = request.form.get('plate_number', '').strip()
    capacity = int(request.form.get('capacity', 16) or 16)
    route_stops = request.form.get('route_stops', '').strip()
    notes = request.form.get('notes', '').strip()
    
    add_shuttle_route(name, driver_name, driver_phone, plate_number, capacity, route_stops, notes)
    flash(f"'{name}' servis hattı başarıyla eklendi.", "success")
    return redirect(url_for('servis_guzergah'))

@app.route('/api/servis/<int:shuttle_id>/guncelle', methods=['POST'])
def api_servis_guncelle(shuttle_id):
    name = request.form.get('name', '').strip()
    driver_name = request.form.get('driver_name', '').strip()
    driver_phone = request.form.get('driver_phone', '').strip()
    plate_number = request.form.get('plate_number', '').strip()
    capacity = int(request.form.get('capacity', 16) or 16)
    route_stops = request.form.get('route_stops', '').strip()
    notes = request.form.get('notes', '').strip()
    
    update_shuttle_route(shuttle_id, name, driver_name, driver_phone, plate_number, capacity, route_stops, notes)
    flash(f"'{name}' servisi güncellendi.", "success")
    return redirect(url_for('servis_guzergah'))

@app.route('/api/servis/<int:shuttle_id>/sil', methods=['POST'])
def api_servis_sil(shuttle_id):
    delete_shuttle_route(shuttle_id)
    flash("Servis hattı silindi.", "success")
    return redirect(url_for('servis_guzergah'))

@app.route('/api/servis/<int:shuttle_id>/yolcu-ekle', methods=['POST'])
def api_servis_yolcu_ekle(shuttle_id):
    person_name = request.form.get('person_name', '').strip()
    department = request.form.get('department', '').strip()
    boarding_stop = request.form.get('boarding_stop', '').strip()
    phone = request.form.get('phone', '').strip()
    
    if person_name:
        add_shuttle_passenger(shuttle_id, person_name, department, boarding_stop, phone)
        flash(f"'{person_name}' servise eklendi.", "success")
    return redirect(url_for('servis_guzergah'))

@app.route('/api/servis/yolcu/<int:passenger_id>/sil', methods=['POST'])
def api_servis_yolcu_sil(passenger_id):
    delete_shuttle_passenger(passenger_id)
    flash("Yolcu servisten çıkarıldı.", "success")
    return redirect(url_for('servis_guzergah'))

# =========================================================================
# 24. DEPO & SARF MALZEME STOK TAKİBİ VE ÇIKIŞ İŞLEMLERİ
# =========================================================================
@app.route('/depo-sarf')
def depo_sarf():
    """Depo sarf malzeme stok durumu, kritik stok uyarıları ve sarf çıkış/giriş paneli."""
    cat_filter = request.args.get('category', '')
    crit_only = request.args.get('critical') == '1'
    
    consumables = get_consumables(category=cat_filter if cat_filter else None, critical_only=crit_only)
    transactions = get_consumable_transactions(limit=60)
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, code, name FROM projects WHERE status != 'Arşiv' ORDER BY name ASC")
    projects = [dict(r) for r in cursor.fetchall()]
    
    # Kritik stoktaki ürün sayısı
    cursor.execute("SELECT COUNT(*) as count FROM inventory_consumables WHERE current_qty <= critical_level")
    critical_count = cursor.fetchone()['count'] or 0
    conn.close()
    
    return render_template('depo_sarf.html',
                           consumables=consumables,
                           transactions=transactions,
                           projects=projects,
                           critical_count=critical_count,
                           selected_category=cat_filter,
                           critical_only=crit_only)

@app.route('/api/depo/sarf-ekle', methods=['POST'])
def api_depo_sarf_ekle():
    code = request.form.get('code', '').strip()
    name = request.form.get('name', '').strip()
    category = request.form.get('category', 'Kaynak & Montaj')
    unit = request.form.get('unit', 'Adet')
    current_qty = float(request.form.get('current_qty', 0) or 0)
    critical_level = float(request.form.get('critical_level', 10) or 10)
    shelf_location = request.form.get('shelf_location', '').strip()
    notes = request.form.get('notes', '').strip()
    
    add_consumable(code, name, category, unit, current_qty, critical_level, shelf_location, notes)
    flash(f"'{name}' sarf malzemesi başarıyla eklendi.", "success")
    return redirect(url_for('depo_sarf'))

@app.route('/api/depo/sarf/<int:material_id>/guncelle', methods=['POST'])
def api_depo_sarf_guncelle(material_id):
    code = request.form.get('code', '').strip()
    name = request.form.get('name', '').strip()
    category = request.form.get('category', 'Kaynak & Montaj')
    unit = request.form.get('unit', 'Adet')
    critical_level = float(request.form.get('critical_level', 10) or 10)
    shelf_location = request.form.get('shelf_location', '').strip()
    notes = request.form.get('notes', '').strip()
    
    update_consumable(material_id, code, name, category, unit, critical_level, shelf_location, notes)
    flash(f"'{name}' sarf malzemesi güncellendi.", "success")
    return redirect(url_for('depo_sarf'))

@app.route('/api/depo/sarf/<int:material_id>/sil', methods=['POST'])
def api_depo_sarf_sil(material_id):
    delete_consumable(material_id)
    flash("Sarf malzemesi ve hareketleri silindi.", "success")
    return redirect(url_for('depo_sarf'))

@app.route('/api/depo/sarf-cikis', methods=['POST'])
def api_depo_sarf_cikis():
    material_id = int(request.form.get('material_id', 0))
    qty = float(request.form.get('qty', 1) or 1)
    recipient_person = request.form.get('recipient_person', '').strip()
    project_id = request.form.get('project_id')
    p_id = int(project_id) if project_id and project_id.isdigit() else None
    machine_name = request.form.get('machine_name', '').strip()
    foreman_name = request.form.get('foreman_name', '').strip()
    notes = request.form.get('notes', '').strip()
    
    cur_user = session.get('user', {})
    created_by = cur_user.get('full_name') or cur_user.get('username') or 'Depocu'
    
    ok, msg = record_consumable_transaction(
        material_id=material_id,
        trans_type='Çıkış',
        qty=qty,
        recipient_person=recipient_person,
        project_id=p_id,
        machine_name=machine_name,
        foreman_name=foreman_name,
        notes=notes,
        created_by=created_by
    )
    if ok:
        flash(msg, "success")
    else:
        flash(msg, "danger")
    return redirect(url_for('depo_sarf'))

@app.route('/api/depo/sarf-giris', methods=['POST'])
def api_depo_sarf_giris():
    material_id = int(request.form.get('material_id', 0))
    qty = float(request.form.get('qty', 1) or 1)
    notes = request.form.get('notes', '').strip()
    
    cur_user = session.get('user', {})
    created_by = cur_user.get('full_name') or cur_user.get('username') or 'Depocu'
    
    ok, msg = record_consumable_transaction(
        material_id=material_id,
        trans_type='Giriş',
        qty=qty,
        notes=notes,
        created_by=created_by
    )
    if ok:
        flash(f"Stok girişi kaydedildi. {msg}", "success")
    else:
        flash(msg, "danger")
    return redirect(url_for('depo_sarf'))

# =========================================================================
# 25. %100 ANONİM SAHA & İŞYERİ BİLDİRİM KUTUSU
# =========================================================================
@app.route('/anonim-bildirim')
def anonim_bildirim():
    """Kaytarma, duyum ve uygunsuz davranışların %100 anonim paylaşıldığı alan."""
    reports = get_anonymous_reports()
    return render_template('anonim_bildirim.html', reports=reports)

@app.route('/api/anonim-bildirim/gonder', methods=['POST'])
def api_anonim_bildirim_gonder():
    """%100 Kimliksiz Bildirim Uç Noktası."""
    title = request.form.get('title', '').strip()
    category = request.form.get('category', 'Genel')
    description = request.form.get('description', '').strip()
    
    if not title or not description:
        flash("Lütfen başlık ve açıklama alanlarını doldurun.", "warning")
        return redirect(url_for('anonim_bildirim'))
        
    media_url = ''
    media_type = 'image'
    if 'media_file' in request.files and request.files['media_file'].filename != '':
        mfile = request.files['media_file']
        ext = os.path.splitext(mfile.filename)[1].lower()
        if ext in ('.mp4', '.mov', '.avi', '.webm', '.mkv'):
            media_type = 'video'
        else:
            media_type = 'image'
            
        fname = f"anon_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.urandom(4).hex()}{ext}"
        upload_dir = os.path.join(BASE_DIR, 'static', 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        mfile.save(os.path.join(upload_dir, fname))
        media_url = f"/static/uploads/{fname}"
        
    add_anonymous_report(title, category, description, media_url, media_type)
    flash("🛡️ Bildiriminiz %100 Anonim olarak iletildi. Kimlik veya IP bilginiz kesinlikle kaydedilmemiştir.", "success")
    return redirect(url_for('anonim_bildirim'))

@app.route('/api/anonim-bildirim/<int:report_id>/durum', methods=['POST'])
def api_anonim_bildirim_durum(report_id):
    status = request.form.get('status', 'İncelendi')
    update_anonymous_report_status(report_id, status)
    flash("Bildirim durumu güncellendi.", "success")
    return redirect(url_for('anonim_bildirim'))

@app.route('/api/anonim-bildirim/<int:report_id>/sil', methods=['POST'])
def api_anonim_bildirim_sil(report_id):
    cur_user = session.get('user', {})
    if cur_user.get('role') not in ('admin', 'patron'):
        flash("Yetkisiz işlem!", "danger")
        return redirect(url_for('anonim_bildirim'))
    delete_anonymous_report(report_id)
    flash("Bildirim silindi.", "success")
    return redirect(url_for('anonim_bildirim'))

# =========================================================================
# 26. SİSTEM GELİŞTİRME & ANONİM GERİ BİLDİRİM KUTUSU (ADMİN YÖNETİR)
# =========================================================================
@app.route('/sistem-istek')
def sistem_istek():
    """Site düzenleme ve geliştirme talepleri sayfası."""
    cur_user = session.get('user', {})
    is_admin = cur_user.get('role') in ('admin', 'patron')
    requests_list = get_system_feature_requests() if is_admin else []
    return render_template('sistem_istek.html', requests_list=requests_list, is_admin=is_admin)

@app.route('/api/sistem-istek/gonder', methods=['POST'])
def api_sistem_istek_gonder():
    title = request.form.get('title', '').strip()
    category = request.form.get('category', 'Yeni Özellik')
    description = request.form.get('description', '').strip()
    
    if not title or not description:
        flash("Lütfen başlık ve açıklama girin.", "warning")
        return redirect(url_for('sistem_istek'))
        
    add_system_feature_request(title, category, description)
    flash("💡 Sistem geliştirme öneriniz / geri bildiriminiz başarıyla iletildi. Teşekkür ederiz!", "success")
    return redirect(url_for('sistem_istek'))

@app.route('/api/sistem-istek/<int:req_id>/durum', methods=['POST'])
def api_sistem_istek_durum(req_id):
    cur_user = session.get('user', {})
    if cur_user.get('role') not in ('admin', 'patron'):
        flash("Yetkisiz işlem!", "danger")
        return redirect(url_for('sistem_istek'))
    status = request.form.get('status', 'İncelendi')
    admin_notes = request.form.get('admin_notes', '').strip()
    update_system_feature_request(req_id, status, admin_notes)
    flash("Geliştirme talebi durumu güncellendi.", "success")
    return redirect(url_for('sistem_istek'))

@app.route('/api/sistem-istek/<int:req_id>/sil', methods=['POST'])
def api_sistem_istek_sil(req_id):
    cur_user = session.get('user', {})
    if cur_user.get('role') not in ('admin', 'patron'):
        flash("Yetkisiz işlem!", "danger")
        return redirect(url_for('sistem_istek'))
    delete_system_feature_request(req_id)
    flash("Talep silindi.", "success")
    return redirect(url_for('sistem_istek'))

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
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
