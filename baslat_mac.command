#!/bin/bash
# Mac OS (MacBook) Başlatıcı
cd "$(dirname "$0")"
echo "========================================================"
echo "  🏗️  ÇELİK İMALAT, MONTAJ VE SEVKİYAT TAKİP SİSTEMİ (MAC)"
echo "========================================================"
echo ""
echo "  Gerekli kütüphaneler kontrol ediliyor..."
pip3 install flask openpyxl --quiet 2>/dev/null || pip install flask openpyxl --quiet 2>/dev/null

echo "  Tarayıcı açılıyor: http://localhost:5000"
open http://localhost:5000 &
python3 app.py || python app.py
