// Genel Yardımcı JavaScript Fonksiyonları

// Sayı Formatı: 1234.56 -> 1.234,56
function formatNumber(num, decimals = 2) {
    if (num === null || num === undefined || isNaN(num)) return '0,00';
    return Number(num).toLocaleString('tr-TR', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
    });
}

// Modal Kontrolleri
function openModal(id) {
    const modal = document.getElementById(id);
    if (modal) {
        modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
    }
}

function closeModal(id) {
    const modal = document.getElementById(id);
    if (modal) {
        modal.classList.add('hidden');
        document.body.style.overflow = '';
    }
}

// ESC Tuşu ile Modalları Kapatma
document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
        document.querySelectorAll('[id$="Modal"]').forEach(m => {
            if (!m.classList.contains('hidden')) {
                closeModal(m.id);
            }
        });
    }
});

// Otomatik Bildirim Kapatma (5 saniye)
document.addEventListener('DOMContentLoaded', function () {
    const alerts = document.querySelectorAll('.animate-fade-in');
    alerts.forEach(al => {
        setTimeout(() => {
            al.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
            al.style.opacity = '0';
            al.style.transform = 'translateY(-10px)';
            setTimeout(() => al.remove(), 500);
        }, 6000);
    });
});
