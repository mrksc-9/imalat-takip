# 🏗️ İmalat, Boya ve Sevkiyat Takip Sistemi

Çelik konstrüksiyon, makine ve metal imalat fabrikaları için geliştirilmiş; **imalat**, **boya** ve **şantiye sevkiyatı** süreçlerini parça ve tonaj bazında anlık olarak takip eden, Excel entegrasyonlu modern yönetim programıdır.

---

## 🚀 Hızlı Başlangıç

Programı çalıştırmak için **`baslat.bat`** dosyasına çift tıklamanız yeterlidir.
Tarayıcınız otomatik olarak açılacak ve sistem `http://127.0.0.1:5000` adresinde başlayacaktır.

---

## 🌟 Temel Özellikler

1. **📊 Canlı Tonaj Kontrol Paneli (Dashboard)**:
   - Toplam Proje Tonajı (Ton)
   - İmalatı Biten Tonaj ve % İlerleme Oranı
   - Boyanan Tonaj ve % İlerleme Oranı
   - Şantiyeye Sevk Edilen Tonaj ve % İlerleme Oranı
   - Atölyede / Fabrikada Bekleyen Stok Tonajı
   - İnteraktif Pasta ve Çubuk Grafikleri

2. **🏗️ Projeler ve Poz / Parça Yönetimi**:
   - Yeni Proje oluşturma (Müşteri, Şantiye Yeri, Hedef Tonaj, Termin Tarihi)
   - Poz Listesi girişi (Poz No, Parça Tanımı, Profil Cinsi, Adet, Birim Kg, Toplam Tonaj, Çelik Kalitesi)
   - **Excel'den Toplu Poz Yükleme**: Tekla Structures, AutoCAD veya Excel parça listelerini tek tıkla sisteme aktarma
   - Boş Excel şablonu indirme ve proje icmal raporunu Excel olarak dışa aktarma

3. **⚙️ 1. İmalat Aşaması Takibi**:
   - Kesim, Delik, Montaj ve Kaynak durumlarının yönetimi
   - Tek tek veya toplu olarak "İmalatı Tamamlandı" işaretleme

4. **🎨 2. Boya & Kumlama Takibi**:
   - Boyahaneye parça sevk etme
   - RAL Boya Kodu ve Kuru Film Kalınlığı (DFT mikron) değerlerini kaydetme
   - Boyanan tonajın anlık hesaplanması

5. **🚚 3. Sevkiyat & Lojistik Paneli**:
   - Araç Plakası, Şoför Adı, Telefonu ve Nakliye Firması ile Sevkiyat İrsaliyesi oluşturma
   - Sadece boyası bitmiş parçalardan seçim yaparak araç yükleme listesi hazırlama
   - Canlı yükleme tonajı hesaplayıcı

6. **📄 Yazdırılabilir A4 Sevk İrsaliyesi / Çeki Listesi**:
   - Tek tıkla yazdırılabilir veya PDF kaydedilebilir resmi sevk formu
   - Firma bilgileri, teslimat yeri, poz ve ağırlık dökümü, teslim eden / teslim alan imza kutuları

---

## 📱 Yerel Ağ (Tablet / Telefon) Erişimi

Program bilgisayarınızda çalışırken aynı Wi-Fi / yerel ağa bağlı atölyedeki tablet veya cep telefonlarından da erişilebilir:
`http://[BILGISAYAR_IP_ADRESINIZ]:5000`

---

## 🛠️ Klasör Yapısı

- `app.py`: Web sunucusu ve API motoru
- `database.py`: SQLite veritabanı yönetimi ve tonaj hesaplayıcı
- `excel_handler.py`: Excel içe/dışa aktarma modülü
- `seed_data.py`: Gerçekçi örnek endüstriyel demo verileri
- `baslat.bat`: Windows tek tıkla çalıştırma aracı
- `templates/`: HTML şablonları (Dashboard, Projeler, İmalat, Boya, Sevk, A4 Fiş, Raporlar)
- `static/`: Stil ve JavaScript dosyaları
