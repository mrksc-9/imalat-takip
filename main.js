// =========================================================================
// GENEL YARDIMCI JAVASCRIPT FONKSİYONLARI & MODAL MOTORU
// =========================================================================

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
        modal.classList.add('flex');
        document.body.style.overflow = 'hidden';
    }
}

function closeModal(id) {
    const modal = document.getElementById(id);
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
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

// Otomatik Bildirim / Alert Kapatma (6 saniye)
function initAutoDismissAlerts() {
    const alerts = document.querySelectorAll('.animate-fade-in');
    alerts.forEach(al => {
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


// =========================================================================
// 🚀 INSTANT CLICK PROGRESS & RESPONSIVE NAVIGATION MOTORU
// =========================================================================
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

    // Tıklanan iç linklerde anında görsel geri bildirim ver
    document.addEventListener('click', function (e) {
        const a = e.target.closest('a');
        if (!a) return;
        const href = a.getAttribute('href');
        if (!href || href.startsWith('#') || href.startsWith('javascript:') || href.startsWith('mailto:') || href.startsWith('tel:') || a.getAttribute('target') === '_blank' || a.hasAttribute('download')) {
            return;
        }
        showProgressBar();
    });

    window.addEventListener('beforeunload', function () {
        showProgressBar();
    });
})();

