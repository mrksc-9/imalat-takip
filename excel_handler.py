import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# =========================================================================
# 1. AKILLI SAYI VE FORMAT AYRIŞTIRICI (PARSE_NUMBER)
# =========================================================================
def parse_number(val, default=0.0):
    """
    Sayısal değerleri (nokta/virgül karmaşası '105.1' vs '105,1', binlik ayracı '1.250,50' vs '1,250.50',
    birim ekleri 'kg', 'ton', 'mm', 'm2' vb.) güvenli ve standart bir float sayıya dönüştürür.
    """
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    
    s = str(val).strip()
    if not s or s.lower() in ('-', 'none', 'null', 'nan', 'n/a', ''):
        return default
        
    # Birim eklerini ve gereksiz karakterleri temizle
    for unit in ['kg', 'ton', 'm2', 'mm', 'mt', 'm', 'adet', 'pcs', 'tl', '$', '€']:
        if s.lower().endswith(unit):
            s = s[:len(s)-len(unit)].strip()
            
    s = s.replace(' ', '')
    if not s:
        return default

    # Hem nokta hem virgül içeriyorsa binlik ayracı tespit et
    if '.' in s and ',' in s:
        if s.rfind(',') > s.rfind('.'):
            # Avrupa / Türkiye formatı: 1.250,50 -> 1250.50
            s = s.replace('.', '').replace(',', '.')
        else:
            # Amerikan / Standart format: 1,250.50 -> 1250.50
            s = s.replace(',', '')
    elif ',' in s:
        # Tek virgül varsa ondalık ayracıdır: 105,1 -> 105.1
        s = s.replace(',', '.')
        
    try:
        return float(s)
    except (ValueError, TypeError):
        return default

def safe_int(val, default=1):
    """Sayısal değeri güvenli bir pozitif tamsayıya (int) dönüştürür."""
    try:
        num = parse_number(val, float(default))
        res = int(round(num))
        return res if res > 0 else default
    except Exception:
        return default

def safe_float(val, default=0.0):
    """Geriye dönük uyumluluk takma adı (alias)"""
    return parse_number(val, default)

# =========================================================================
# 2. AKILLI TEKLA STRUCTURES EXCEL OKUYUCU
# =========================================================================
def parse_tekla_excel(file_stream):
    """
    Tekla Structures Excel raporlarını (Sarı başlıklı Montajlar ve altındaki Beyaz satırlı Parçalar dahil)
    ve standart poz listelerini otomatik ayrıştırır.
    Montaj Listesi (Assemblies), Montaj Parça Listesi (Assembly Parts) ve Tek Parça Listesi (Parts) üretir.
    """
    wb = openpyxl.load_workbook(file_stream, data_only=True)
    
    parsed_assemblies = []
    parsed_assembly_parts = []
    parsed_parts = []
    
    # Yardımcı: Sütun İsim Eşleştirici
    def match_col(headers, keywords):
        for idx, h in enumerate(headers):
            for kw in keywords:
                if kw in h:
                    return idx
        return None

    # Tüm çalışma sayfalarını (sheets) tara
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        if ws.max_row < 2:
            continue
            
        # İlk dolu satırı başlık olarak tespit et
        header_row_idx = 1
        headers = []
        for r in range(1, min(15, ws.max_row + 1)):
            row_vals = [str(c.value or '').strip().lower() for c in ws[r]]
            joined = ' '.join(row_vals)
            if any(k in joined for k in ['assembly', 'montaj', 'poz', 'pos', 'mark', 'profil', 'adet', 'qty']):
                header_row_idx = r
                headers = row_vals
                break
                
        if not headers:
            headers = [str(c.value or '').strip().lower() for c in ws[1]]
            header_row_idx = 1

        # Sütun indekslerini tespit et
        ass_pos_idx = match_col(headers, ['assembly adı', 'assembly mark', 'assembly pos', 'montaj no', 'montaj markası', 'montaj adı', 'montaj', 'main part', 'marka', 'ana poz'])
        part_pos_idx = match_col(headers, ['tek parça', 'parça no', 'parça poz', 'part mark', 'part pos', 'pos no', 'poz no', 'poz', 'pos', 'mark'])
        desc_idx = match_col(headers, ['tanım', 'açıklama', 'description', 'parça adı', 'eleman adı', 'name', 'ad'])
        prof_idx = match_col(headers, ['profil', 'kesit', 'section', 'profile', 'malzeme cinsi', 'tip', 'material type'])
        qty_idx = match_col(headers, ['adet', 'miktar', 'sayı', 'qty', 'quantity', 'count', 'adet/sayı'])
        len_idx = match_col(headers, ['uzunluk', 'boy', 'length', 'len', 'uzunluk (mm)', 'boy (mm)'])
        unit_wt_idx = match_col(headers, ['birim ağırlık', 'birim kg', 'unit weight', 'weight', 'tek ağırlık', 'kg/m'])
        tot_wt_idx = match_col(headers, ['toplam ağırlık', 'toplam kg', 'toplam tonaj', 'total weight', 'toplam'])
        grade_idx = match_col(headers, ['kalite', 'çelik kalitesi', 'grade', 'material', 'malzeme kalitesi'])

        # Varsayılanlar
        if ass_pos_idx is None and part_pos_idx is None:
            ass_pos_idx = 0
            part_pos_idx = 1 if len(headers) > 1 else 0
        elif ass_pos_idx is None:
            ass_pos_idx = part_pos_idx
        elif part_pos_idx is None:
            part_pos_idx = ass_pos_idx

        current_assembly = None
        current_assembly_qty = 1
        current_assembly_desc = 'İmalat Elemanı'
        current_assembly_profile = '-'
        current_assembly_grade = 'S275JR'

        # Sayfa içeriğini satır satır oku
        for row in ws.iter_rows(min_row=header_row_idx + 1, values_only=True):
            if not row or not any(row):
                continue

            raw_ass = str(row[ass_pos_idx] or '').strip() if ass_pos_idx is not None and ass_pos_idx < len(row) and row[ass_pos_idx] is not None else ''
            raw_part = str(row[part_pos_idx] or '').strip() if part_pos_idx is not None and part_pos_idx < len(row) and row[part_pos_idx] is not None else ''
            raw_desc = str(row[desc_idx] or '').strip() if desc_idx is not None and desc_idx < len(row) and row[desc_idx] is not None else ''
            raw_prof = str(row[prof_idx] or '').strip() if prof_idx is not None and prof_idx < len(row) and row[prof_idx] is not None else '-'
            raw_qty = row[qty_idx] if qty_idx is not None and qty_idx < len(row) else 1
            raw_len = row[len_idx] if len_idx is not None and len_idx < len(row) else 0.0
            raw_uwt = row[unit_wt_idx] if unit_wt_idx is not None and unit_wt_idx < len(row) else 0.0
            raw_twt = row[tot_wt_idx] if tot_wt_idx is not None and tot_wt_idx < len(row) else 0.0
            raw_grade = str(row[grade_idx] or 'S275JR').strip() if grade_idx is not None and grade_idx < len(row) and row[grade_idx] is not None else 'S275JR'

            parsed_qty = safe_int(raw_qty, 1)
            parsed_len = parse_number(raw_len, 0.0)
            parsed_uwt = parse_number(raw_uwt, 0.0)
            parsed_twt = parse_number(raw_twt, 0.0)

            # Toplam ağırlık 0 ise ve birim ağırlık varsa hesapla
            if parsed_twt == 0.0 and parsed_uwt > 0:
                parsed_twt = round(parsed_qty * parsed_uwt, 2)

            # --- DURUM 1: Tekla Hiyerarşik Yapı ---
            # Kolon A dolu -> Yeni Montaj Başlığı (Sarı Satır)
            if raw_ass and (not raw_part or raw_part == raw_ass or ass_pos_idx != part_pos_idx):
                current_assembly = raw_ass
                current_assembly_qty = parsed_qty
                current_assembly_desc = raw_desc or (raw_part if raw_part != raw_ass else 'İmalat Elemanı') or 'İmalat Elemanı'
                current_assembly_profile = raw_prof if raw_prof != '-' else '-'
                current_assembly_grade = raw_grade or 'S275JR'

                parsed_assemblies.append({
                    'assembly_pos': current_assembly,
                    'description': current_assembly_desc,
                    'profile_type': current_assembly_profile,
                    'quantity': current_assembly_qty,
                    'unit_weight': round(parsed_uwt, 2),
                    'total_weight': round(parsed_twt if parsed_twt > 0 else (current_assembly_qty * parsed_uwt), 2),
                    'material_grade': current_assembly_grade
                })

                # Eğer aynı satırda Poz ve Profil de farklı bir parça olarak verilmişse
                if raw_part and raw_part != raw_ass and raw_prof != '-':
                    tot_q = parsed_qty
                    parsed_assembly_parts.append({
                        'assembly_pos': current_assembly,
                        'part_pos': raw_part,
                        'description': raw_desc or current_assembly_desc,
                        'profile_type': raw_prof,
                        'quantity_per_assembly': 1,
                        'total_quantity': tot_q,
                        'length': round(parsed_len, 1),
                        'unit_weight': round(parsed_uwt, 2),
                        'total_weight': round(parsed_twt, 2),
                        'material_grade': raw_grade
                    })
                    parsed_parts.append({
                        'pos_no': raw_part,
                        'name': raw_desc or current_assembly_desc,
                        'profile_type': raw_prof,
                        'quantity': tot_q,
                        'length': round(parsed_len, 1),
                        'unit_weight': round(parsed_uwt, 2),
                        'total_weight': round(parsed_twt, 2),
                        'material_grade': raw_grade
                    })

            # Kolon A boş AMA Kolon B (Poz) dolu -> current_assembly'nin Alt Parçası (Beyaz Satır)
            elif not raw_ass and raw_part:
                parent_ass = current_assembly or raw_part
                qty_per_ass = parsed_qty
                tot_qty = qty_per_ass * (current_assembly_qty if current_assembly else 1)
                calc_tot_wt = round(tot_qty * parsed_uwt, 2) if parsed_uwt > 0 else parsed_twt

                parsed_assembly_parts.append({
                    'assembly_pos': parent_ass,
                    'part_pos': raw_part,
                    'description': raw_desc or 'Montaj Parçası',
                    'profile_type': raw_prof,
                    'quantity_per_assembly': qty_per_ass,
                    'total_quantity': tot_qty,
                    'length': round(parsed_len, 1),
                    'unit_weight': round(parsed_uwt, 2),
                    'total_weight': round(calc_tot_wt, 2),
                    'material_grade': raw_grade
                })

                parsed_parts.append({
                    'pos_no': raw_part,
                    'name': raw_desc or 'Montaj Parçası',
                    'profile_type': raw_prof,
                    'quantity': tot_qty,
                    'length': round(parsed_len, 1),
                    'unit_weight': round(parsed_uwt, 2),
                    'total_weight': round(calc_tot_wt, 2),
                    'material_grade': raw_grade
                })

            # Düz satır (Hem ass hem part dolu ve aynı sütun veya tekil)
            elif raw_ass and raw_part and ass_pos_idx == part_pos_idx:
                parsed_assemblies.append({
                    'assembly_pos': raw_ass,
                    'description': raw_desc or 'İmalat Elemanı',
                    'profile_type': raw_prof,
                    'quantity': parsed_qty,
                    'unit_weight': round(parsed_uwt, 2),
                    'total_weight': round(parsed_twt, 2),
                    'material_grade': raw_grade
                })
                parsed_parts.append({
                    'pos_no': raw_part,
                    'name': raw_desc or 'İmalat Elemanı',
                    'profile_type': raw_prof,
                    'quantity': parsed_qty,
                    'length': round(parsed_len, 1),
                    'unit_weight': round(parsed_uwt, 2),
                    'total_weight': round(parsed_twt, 2),
                    'material_grade': raw_grade
                })
                parsed_assembly_parts.append({
                    'assembly_pos': raw_ass,
                    'part_pos': raw_part,
                    'description': raw_desc or 'İmalat Elemanı',
                    'profile_type': raw_prof,
                    'quantity_per_assembly': 1,
                    'total_quantity': parsed_qty,
                    'length': round(parsed_len, 1),
                    'unit_weight': round(parsed_uwt, 2),
                    'total_weight': round(parsed_twt, 2),
                    'material_grade': raw_grade
                })

    # Tekil liste oluşturma (Aynı assembly_pos veya pos_no olanları birleştir/özetle)
    unique_assemblies = {}
    for a in parsed_assemblies:
        k = a['assembly_pos']
        if k not in unique_assemblies:
            unique_assemblies[k] = dict(a)
        else:
            unique_assemblies[k]['quantity'] += a['quantity']
            unique_assemblies[k]['total_weight'] = round(unique_assemblies[k]['total_weight'] + a['total_weight'], 2)

    unique_parts = {}
    for p in parsed_parts:
        k = p['pos_no']
        if k not in unique_parts:
            unique_parts[k] = dict(p)
        else:
            unique_parts[k]['quantity'] += p['quantity']
            unique_parts[k]['total_weight'] = round(unique_parts[k]['total_weight'] + p['total_weight'], 2)

    return {
        'assemblies': list(unique_assemblies.values()),
        'assembly_parts': parsed_assembly_parts,
        'parts': list(unique_parts.values())
    }

def parse_assemblies_file(file_stream):
    """Sadece Montaj / Assembly Listesi içeren Excel dosyasını ayrıştırır."""
    res = parse_tekla_excel(file_stream)
    return res['assemblies']

def parse_assembly_parts_file(file_stream):
    """Sadece Montaj Parça Listesi içeren Excel dosyasını ayrıştırır."""
    res = parse_tekla_excel(file_stream)
    if res['assembly_parts']:
        return res['assembly_parts']
    # If not detected as assembly_parts, convert assemblies or parts
    ap_list = []
    for a in res['assemblies']:
        ap_list.append({
            'assembly_pos': a['assembly_pos'],
            'part_pos': a['assembly_pos'],
            'description': a.get('description', ''),
            'profile_type': a.get('profile_type', ''),
            'quantity_per_assembly': 1,
            'total_quantity': a.get('quantity', 1),
            'length': 0.0,
            'unit_weight': a.get('unit_weight', 0.0),
            'total_weight': a.get('total_weight', 0.0),
            'material_grade': a.get('material_grade', 'S275JR')
        })
    return ap_list

def parse_parts_file(file_stream):
    """Sadece Tek Parça / Poz Listesi içeren Excel dosyasını ayrıştırır."""
    res = parse_tekla_excel(file_stream)
    return res['parts']

def parse_clipboard_table(text, target_type='assemblies'):
    """
    Excel veya Tekla'dan kopyalanan tablo metnini (Tab veya noktalı virgül / virgül ayrılmış) ayrıştırır.
    target_type: 'assemblies' | 'assembly_parts' | 'parts'
    """
    if not text or not text.strip():
        return []

    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return []

    # Sütun ayırıcıyı belirle (Tab, noktalı virgül veya virgül)
    first_line = lines[0]
    delimiter = '\t'
    if '\t' in first_line:
        delimiter = '\t'
    elif ';' in first_line:
        delimiter = ';'
    elif ',' in first_line and not any(c.isdigit() for c in first_line.split(',')[0]):
        delimiter = ','

    rows = []
    for line in lines:
        parts = [p.strip() for p in line.split(delimiter)]
        if any(parts):
            rows.append(parts)

    if not rows:
        return []

    # İlk satır başlık mı kontrol et
    headers = [c.lower() for c in rows[0]]
    has_header = any(k in ' '.join(headers) for k in ['poz', 'pos', 'mark', 'profil', 'adet', 'qty', 'montaj', 'assembly', 'tanım', 'name', 'weight', 'ağırlık'])
    
    data_rows = rows[1:] if has_header else rows

    results = []

    if target_type == 'assemblies':
        for r in data_rows:
            if not r or not any(r): continue
            # Standart kolonlar: Poz/Marka, Tanım, Profil, Adet, Birim Kg, Toplam Kg, Kalite
            pos = r[0] if len(r) > 0 else ''
            if not pos: continue
            desc = r[1] if len(r) > 1 and r[1] else 'İmalat Elemanı'
            prof = r[2] if len(r) > 2 and r[2] else '-'
            qty = safe_int(r[3] if len(r) > 3 else 1, 1)
            u_wt = parse_number(r[4] if len(r) > 4 else 0.0)
            t_wt = parse_number(r[5] if len(r) > 5 else 0.0)
            if t_wt == 0.0 and u_wt > 0:
                t_wt = round(qty * u_wt, 2)
            grade = r[6] if len(r) > 6 and r[6] else 'S275JR'

            results.append({
                'assembly_pos': pos,
                'description': desc,
                'profile_type': prof,
                'quantity': qty,
                'unit_weight': round(u_wt, 2),
                'total_weight': round(t_wt, 2),
                'material_grade': grade
            })

    elif target_type == 'assembly_parts':
        for r in data_rows:
            if not r or not any(r): continue
            # Standart kolonlar: Montaj Poz, Parça Poz, Tanım, Profil, Adet/Montaj, Toplam Adet, Boy, Birim Kg, Toplam Kg, Kalite
            ass_pos = r[0] if len(r) > 0 else ''
            part_pos = r[1] if len(r) > 1 else ass_pos
            if not ass_pos and not part_pos: continue
            if not ass_pos: ass_pos = part_pos
            if not part_pos: part_pos = ass_pos

            desc = r[2] if len(r) > 2 and r[2] else 'Montaj Parçası'
            prof = r[3] if len(r) > 3 and r[3] else '-'
            q_ass = safe_int(r[4] if len(r) > 4 else 1, 1)
            tot_q = safe_int(r[5] if len(r) > 5 else q_ass, q_ass)
            length = parse_number(r[6] if len(r) > 6 else 0.0)
            u_wt = parse_number(r[7] if len(r) > 7 else 0.0)
            t_wt = parse_number(r[8] if len(r) > 8 else 0.0)
            if t_wt == 0.0 and u_wt > 0:
                t_wt = round(tot_q * u_wt, 2)
            grade = r[9] if len(r) > 9 and r[9] else 'S275JR'

            results.append({
                'assembly_pos': ass_pos,
                'part_pos': part_pos,
                'description': desc,
                'profile_type': prof,
                'quantity_per_assembly': q_ass,
                'total_quantity': tot_q,
                'length': round(length, 1),
                'unit_weight': round(u_wt, 2),
                'total_weight': round(t_wt, 2),
                'material_grade': grade
            })

    elif target_type == 'parts':
        for r in data_rows:
            if not r or not any(r): continue
            # Standart kolonlar: Poz No, Tanım, Profil, Adet, Boy, Birim Kg, Toplam Kg, Kalite
            pos = r[0] if len(r) > 0 else ''
            if not pos: continue
            name = r[1] if len(r) > 1 and r[1] else 'Poz Parçası'
            prof = r[2] if len(r) > 2 and r[2] else '-'
            qty = safe_int(r[3] if len(r) > 3 else 1, 1)
            length = parse_number(r[4] if len(r) > 4 else 0.0)
            u_wt = parse_number(r[5] if len(r) > 5 else 0.0)
            t_wt = parse_number(r[6] if len(r) > 6 else 0.0)
            if t_wt == 0.0 and u_wt > 0:
                t_wt = round(qty * u_wt, 2)
            grade = r[7] if len(r) > 7 and r[7] else 'S275JR'

            results.append({
                'pos_no': pos,
                'name': name,
                'profile_type': prof,
                'quantity': qty,
                'length': round(length, 1),
                'unit_weight': round(u_wt, 2),
                'total_weight': round(t_wt, 2),
                'material_grade': grade
            })

    return results

# Geriye uyumluluk için eski fonksiyon adı
def parse_excel_parts(file_stream):
    res = parse_tekla_excel(file_stream)
    return res['parts']


# =========================================================================
# 2. ÖRNEK EXCEL ŞABLONU OLUŞTURUCU
# =========================================================================
def generate_template_excel():
    """Kullanıcının doldurması için 3 sayfalı zengin Tekla & İmalat Excel şablonu üretir."""
    wb = openpyxl.Workbook()
    
    # 1. Sayfa: Montaj Listesi (Assemblies)
    ws1 = wb.active
    ws1.title = "1-Montaj Listesi (Assembly)"

    header_font = Font(name='Segoe UI', size=11, bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='1E3A8A', end_color='1E3A8A', fill_type='solid') # Koyu Mavi
    align_center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    align_left = Alignment(horizontal='left', vertical='center')
    align_right = Alignment(horizontal='right', vertical='center')

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )

    headers_ass = ["Montaj / Marka No *", "Eleman Açıklaması *", "Ana Profil Kesiti *", "Adet *", "Birim Ağırlık (kg) *", "Toplam Ağırlık (kg)", "Çelik Kalitesi"]
    for c_idx, h in enumerate(headers_ass, 1):
        cell = ws1.cell(row=1, column=c_idx, value=h)
        cell.font = header_font; cell.fill = header_fill; cell.alignment = align_center; cell.border = thin_border

    sample_assemblies = [
        ["C-101", "Ana Taşıyıcı Kolon", "HEB 300", 12, 1250.0, "=D2*E2", "S275JR"],
        ["C-102", "Rüzgar Kolonu", "HEA 240", 8, 850.0, "=D3*E3", "S275JR"],
        ["B-201", "Ana Çatı Kirişi", "IPE 360", 16, 680.0, "=D4*E4", "S355JR"],
        ["TR-301", "Çatı Makası", "2x L 80x80x8", 10, 420.0, "=D5*E5", "S235JR"],
        ["P-401", "Aşık Profili", "C 200x75x2.5", 60, 45.0, "=D6*E6", "S235JR"]
    ]

    for r_idx, r_data in enumerate(sample_assemblies, 2):
        for c_idx, val in enumerate(r_data, 1):
            cell = ws1.cell(row=r_idx, column=c_idx, value=val)
            cell.font = Font(name='Segoe UI', size=10); cell.border = thin_border
            if c_idx in (1, 7): cell.alignment = align_center
            elif c_idx in (4, 5, 6): cell.alignment = align_right
            else: cell.alignment = align_left

    # 2. Sayfa: Tek Parça / Poz Listesi (Single Parts)
    ws2 = wb.create_sheet(title="2-Tek Parça Listesi (Pozlar)")
    headers_parts = ["Poz No *", "Parça Tanımı *", "Profil / Sac Kesiti *", "Adet *", "Uzunluk (mm)", "Birim Ağırlık (kg) *", "Toplam Ağırlık (kg)", "Malzeme Kalitesi"]
    for c_idx, h in enumerate(headers_parts, 1):
        cell = ws2.cell(row=1, column=c_idx, value=h)
        cell.font = header_font; cell.fill = PatternFill(start_color='0F766E', end_color='0F766E', fill_type='solid') # Teal
        cell.alignment = align_center; cell.border = thin_border

    sample_parts = [
        ["p1", "Kolon Gövde Profili", "HEB 300", 12, 8500, 1000.0, "=D2*F2", "S275JR"],
        ["p2", "Taban Flanş Plakası", "PL 25mm", 12, 450, 45.2, "=D3*F3", "S355JR"],
        ["p3", "Berkitme Levhası", "PL 12mm", 48, 280, 5.3, "=D4*F4", "S275JR"],
        ["p4", "Bayrak Plakası", "PL 10mm", 32, 200, 3.1, "=D5*F5", "S235JR"],
        ["p5", "Kiriş Gövde", "IPE 360", 16, 12000, 685.0, "=D6*F6", "S355JR"]
    ]

    for r_idx, r_data in enumerate(sample_parts, 2):
        for c_idx, val in enumerate(r_data, 1):
            cell = ws2.cell(row=r_idx, column=c_idx, value=val)
            cell.font = Font(name='Segoe UI', size=10); cell.border = thin_border
            if c_idx in (1, 8): cell.alignment = align_center
            elif c_idx in (4, 5, 6, 7): cell.alignment = align_right
            else: cell.alignment = align_left

    # Sütun genişliklerini otomatik ayarla
    for ws in [ws1, ws2]:
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 4, 15)
        ws.row_dimensions[1].height = 28

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

# =========================================================================
# 3. TEDARİKÇİ MALZEME TEKLİF İSTEK FORMU (RFQ EXCEL)
# =========================================================================
def export_material_rfq_excel(project_code, ordered_rows, supplier_name=""):
    """Sipariş modülündeki malzemeler için resmi teklif istek Excel formu oluşturur."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Malzeme Teklif Listesi"

    header_font = Font(name='Segoe UI', size=11, bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='1E3A8A', end_color='1E3A8A', fill_type='solid')
    data_font = Font(name='Segoe UI', size=10)
    bold_font = Font(name='Segoe UI', size=10, bold=True)
    kpi_fill = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )

    # Başlık Alanı
    ws.merge_cells('A1:F1')
    ws['A1'] = "MALZEME VE PROFİL TEKLİF İSTEK FORMU"
    ws['A1'].font = Font(name='Segoe UI', size=15, bold=True, color='1E3A8A')
    ws['A1'].alignment = Alignment(horizontal='left', vertical='center')
    ws.row_dimensions[1].height = 30

    info_rows = [
        ("Firma:", supplier_name or "Tedarikçi Firma", "Proje Kodu:", project_code),
        ("Tarih:", io.openpyxl if False else "", "Konu:", "Hammadde / Profil Fiyat Teklifi")
    ]
    
    ws['A2'] = "Firma:"; ws['A2'].font = bold_font; ws['B2'] = supplier_name or "-"
    ws['D2'] = "Proje Kodu:"; ws['D2'].font = bold_font; ws['E2'] = project_code
    
    table_start_row = 5
    headers = ["Malzeme / Kesit", "Kalınlık (mm)", "En (mm)", "Boy (mm)", "Adet", "Toplam Ağırlık (kg)"]
    for c_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=table_start_row, column=c_idx, value=h)
        cell.font = header_font; cell.fill = header_fill; cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = thin_border
    ws.row_dimensions[table_start_row].height = 25

    total_weight = 0.0
    for idx, r in enumerate(ordered_rows, 1):
        row_num = table_start_row + idx
        m = r.get('material', '')
        kal = r.get('thickness', 0)
        en = r.get('width', 0)
        boy = r.get('length', 0)
        adet = r.get('quantity', 0)
        ag = r.get('weight', 0.0)
        total_weight += ag

        vals = [
            m,
            kal if kal > 0 else "-",
            en if en > 0 else "-",
            boy if boy > 0 else "-",
            adet,
            round(ag, 2)
        ]

        for c_idx, val in enumerate(vals, 1):
            cell = ws.cell(row=row_num, column=c_idx, value=val)
            cell.font = data_font; cell.border = thin_border
            if c_idx in (2, 3, 4): cell.alignment = Alignment(horizontal='center', vertical='center')
            elif c_idx in (5, 6): cell.alignment = Alignment(horizontal='right', vertical='center')
            else: cell.alignment = Alignment(horizontal='left', vertical='center')

    # Toplam Satırı
    tot_row = table_start_row + len(ordered_rows) + 1
    ws.cell(row=tot_row, column=4, value="GENEL TOPLAM:").font = bold_font
    ws.cell(row=tot_row, column=4).alignment = Alignment(horizontal='right', vertical='center')
    
    tot_cell = ws.cell(row=tot_row, column=6, value=round(total_weight, 2))
    tot_cell.font = bold_font; tot_cell.alignment = Alignment(horizontal='right', vertical='center')

    for c in range(1, 7):
        ws.cell(row=tot_row, column=c).fill = kpi_fill
        ws.cell(row=tot_row, column=c).border = thin_border

    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 14)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

# =========================================================================
# 4. PROJE DETAYLI İMALAT & TONAJ İCMAL RAPORU EXCEL
# =========================================================================
def export_project_report_excel(project_summary, assemblies_list, parts_list):
    """Bir projenin Montaj, Parça ve Aşama durumlarını içeren zengin Excel raporu üretir."""
    wb = openpyxl.Workbook()
    
    header_font = Font(name='Segoe UI', size=10, bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='1E3A8A', end_color='1E3A8A', fill_type='solid')
    data_font = Font(name='Segoe UI', size=10)
    bold_font = Font(name='Segoe UI', size=10, bold=True)
    kpi_fill = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')
    done_fill = PatternFill(start_color='DCFCE7', end_color='DCFCE7', fill_type='solid')
    wait_fill = PatternFill(start_color='FEF3C7', end_color='FEF3C7', fill_type='solid')

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )

    # 1. Sayfa: Montaj / Assembly Listesi
    ws1 = wb.active
    ws1.title = "Montaj Durum İcmali"

    ws1.merge_cells('A1:K1')
    ws1['A1'] = f"İMALAT & MONTAJ İCMALİ - {project_summary['name'].upper()} ({project_summary['code']})"
    ws1['A1'].font = Font(name='Segoe UI', size=14, bold=True, color='1E3A8A')
    ws1['A1'].alignment = Alignment(horizontal='left', vertical='center')
    ws1.row_dimensions[1].height = 30

    info_rows = [
        ("Proje Kodu:", project_summary['code'], "Müşteri:", project_summary.get('customer') or '-'),
        ("Toplam Tonaj:", f"{project_summary['total_tonnage']} Ton", "Şantiye:", project_summary.get('site_location') or '-'),
        ("İmalatı Biten:", f"{project_summary['fab_completed_tonnage']} Ton (%{project_summary['fab_pct']})", "Boyanan:", f"{project_summary['paint_completed_tonnage']} Ton (%{project_summary['paint_pct']})"),
        ("Sevk Edilen:", f"{project_summary['shipped_tonnage']} Ton (%{project_summary['ship_pct']})", "Atölye Stoğu:", f"{project_summary['factory_stock_tonnage']} Ton")
    ]

    for r_idx, (k1, v1, k2, v2) in enumerate(info_rows, 3):
        ws1.cell(row=r_idx, column=1, value=k1).font = bold_font
        ws1.cell(row=r_idx, column=2, value=v1).font = data_font
        ws1.cell(row=r_idx, column=4, value=k2).font = bold_font
        ws1.cell(row=r_idx, column=5, value=v2).font = data_font
        for c in range(1, 6): ws1.cell(row=r_idx, column=c).fill = kpi_fill

    table_start_row = 8
    headers1 = ["Sıra", "Montaj No", "Tanım", "Profil", "Toplam Adet", "Birim Kg", "Toplam Kg", "Çatım", "Kaynak", "Boya", "Sevk"]
    for c_idx, h in enumerate(headers1, 1):
        cell = ws1.cell(row=table_start_row, column=c_idx, value=h)
        cell.font = header_font; cell.fill = header_fill; cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = thin_border
    ws1.row_dimensions[table_start_row].height = 25

    for idx, a in enumerate(assemblies_list, 1):
        r = table_start_row + idx
        vals = [
            idx,
            a['assembly_pos'],
            a['description'],
            a['profile_type'],
            a['quantity'],
            a['unit_weight'],
            a['total_weight'],
            f"{a.get('fab_fitup_qty', 0)} / {a['quantity']}",
            f"{a.get('fab_welding_qty', 0)} / {a['quantity']}",
            f"{a.get('paint_done_qty', 0)} / {a['quantity']}",
            f"{a.get('shipped_qty', 0)} / {a['quantity']}"
        ]
        for c_idx, val in enumerate(vals, 1):
            cell = ws1.cell(row=r, column=c_idx, value=val)
            cell.font = data_font; cell.border = thin_border
            if c_idx in (1, 2, 8, 9, 10, 11): cell.alignment = Alignment(horizontal='center', vertical='center')
            elif c_idx in (5, 6, 7): cell.alignment = Alignment(horizontal='right', vertical='center')
            else: cell.alignment = Alignment(horizontal='left', vertical='center')

    # 2. Sayfa: Parça Listesi (Single Parts)
    ws2 = wb.create_sheet(title="Parça Listesi & Kesim")
    headers2 = ["Sıra", "Poz No", "Parça Tanımı", "Profil Kesiti", "Toplam Adet", "Kesilen Adet", "Kalan Adet", "Uzunluk (mm)", "Birim Kg", "Toplam Kg", "Kalite"]
    for c_idx, h in enumerate(headers2, 1):
        cell = ws2.cell(row=1, column=c_idx, value=h)
        cell.font = header_font; cell.fill = PatternFill(start_color='0F766E', end_color='0F766E', fill_type='solid')
        cell.alignment = Alignment(horizontal='center', vertical='center'); cell.border = thin_border
    ws2.row_dimensions[1].height = 25

    for idx, p in enumerate(parts_list, 1):
        r = 1 + idx
        kalan = max(0, p['quantity'] - p.get('cut_quantity', 0))
        vals = [
            idx,
            p['pos_no'],
            p['name'],
            p['profile_type'],
            p['quantity'],
            p.get('cut_quantity', 0),
            kalan,
            p.get('length', 0),
            p['unit_weight'],
            p['total_weight'],
            p['material_grade']
        ]
        for c_idx, val in enumerate(vals, 1):
            cell = ws2.cell(row=r, column=c_idx, value=val)
            cell.font = data_font; cell.border = thin_border
            if c_idx in (1, 2, 11): cell.alignment = Alignment(horizontal='center', vertical='center')
            elif c_idx in (5, 6, 7, 8, 9, 10): cell.alignment = Alignment(horizontal='right', vertical='center')
            else: cell.alignment = Alignment(horizontal='left', vertical='center')
            if c_idx == 7 and kalan == 0: cell.fill = done_fill

    for ws in [ws1, ws2]:
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

# =========================================================================
# 5. SEVKİYAT İRSALİYESİ / ÇEKİ LİSTESİ EXCEL ÇIKTISI
# =========================================================================
def export_shipment_excel(shipment, items, settings):
    """Sevk İrsaliyesi / Çeki Listesini resmi formatta Excel olarak oluşturur."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sevk İrsaliyesi"

    header_font = Font(name='Segoe UI', size=10, bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='1E3A8A', end_color='1E3A8A', fill_type='solid')
    data_font = Font(name='Segoe UI', size=10)
    bold_font = Font(name='Segoe UI', size=10, bold=True)
    kpi_fill = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )

    # Başlık ve Firma Bilgisi
    ws.merge_cells('A1:F1')
    ws['A1'] = settings.get('company_name', 'ÇELİK VE METAL İMALAT SANAYİ')
    ws['A1'].font = Font(name='Segoe UI', size=13, bold=True, color='1E3A8A')
    
    ws.merge_cells('A2:F2')
    ws['A2'] = "SEVKİYAT İRSALİYESİ & ÇEKİ LİSTESİ (PACKING LIST)"
    ws['A2'].font = Font(name='Segoe UI', size=12, bold=True, color='0F172A')

    ws['A4'] = "İrsaliye No:"; ws['A4'].font = bold_font; ws['B4'] = shipment.get('dispatch_no', '-')
    ws['D4'] = "Sevk Tarihi:"; ws['D4'].font = bold_font; ws['E4'] = shipment.get('dispatch_date', '-')
    
    ws['A5'] = "Araç Plakası:"; ws['A5'].font = bold_font; ws['B5'] = shipment.get('vehicle_plate', '-')
    ws['D5'] = "Şoför / İletişim:"; ws['D5'].font = bold_font; ws['E5'] = f"{shipment.get('driver_name', '-')} ({shipment.get('driver_phone', '-')})"
    
    ws['A6'] = "Teslim Yeri:"; ws['A6'].font = bold_font; ws['B6'] = shipment.get('destination', '-')
    ws['D6'] = "Nakliye Firması:"; ws['D6'].font = bold_font; ws['E6'] = shipment.get('carrier_company', '-')

    table_start_row = 8
    headers = ["Sıra", "Montaj / Marka No", "Açıklama / Eleman", "Sevk Edilen Adet", "Birim Ağırlık (kg)", "Toplam Ağırlık (kg)"]
    for c_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=table_start_row, column=c_idx, value=h)
        cell.font = header_font; cell.fill = header_fill; cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = thin_border
    ws.row_dimensions[table_start_row].height = 25

    total_qty = 0
    total_weight = 0.0

    for idx, it in enumerate(items, 1):
        r = table_start_row + idx
        qty = it.get('quantity', 1)
        u_wt = it.get('unit_weight', 0.0)
        t_wt = it.get('total_weight', qty * u_wt)
        total_qty += qty
        total_weight += t_wt

        vals = [idx, it.get('assembly_pos', '-'), it.get('description', '-'), qty, round(u_wt, 2), round(t_wt, 2)]
        for c_idx, val in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c_idx, value=val)
            cell.font = data_font; cell.border = thin_border
            if c_idx in (1, 2): cell.alignment = Alignment(horizontal='center', vertical='center')
            elif c_idx in (4, 5, 6): cell.alignment = Alignment(horizontal='right', vertical='center')
            else: cell.alignment = Alignment(horizontal='left', vertical='center')

    # Toplam Satırı
    tot_row = table_start_row + len(items) + 1
    ws.cell(row=tot_row, column=3, value="GENEL TOPLAM:").font = bold_font
    ws.cell(row=tot_row, column=3).alignment = Alignment(horizontal='right', vertical='center')
    
    ws.cell(row=tot_row, column=4, value=total_qty).font = bold_font
    ws.cell(row=tot_row, column=4).alignment = Alignment(horizontal='right', vertical='center')

    ws.cell(row=tot_row, column=6, value=round(total_weight, 2)).font = bold_font
    ws.cell(row=tot_row, column=6).alignment = Alignment(horizontal='right', vertical='center')

    for c in range(1, 7):
        ws.cell(row=tot_row, column=c).fill = kpi_fill
        ws.cell(row=tot_row, column=c).border = thin_border

    # İmza Alanları
    sign_row = tot_row + 3
    ws.cell(row=sign_row, column=1, value="Teslim Eden (Fabrika)").font = bold_font
    ws.cell(row=sign_row, column=3, value="Taşıyan (Şoför)").font = bold_font
    ws.cell(row=sign_row, column=5, value="Teslim Alan (Şantiye)").font = bold_font

    ws.cell(row=sign_row + 1, column=1, value="İmza: ..................")
    ws.cell(row=sign_row + 1, column=3, value="İmza: ..................")
    ws.cell(row=sign_row + 1, column=5, value="İmza: ..................")

    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 14)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output
