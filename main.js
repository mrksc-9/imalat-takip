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
// 🚀 TURBO ULTRA-FAST NAVIGATION & IN-MEMORY PREFETCH ENGINE (0-15ms GEÇİŞ)
// =========================================================================
(function () {
    const pageCache = new Map();
    const prefetchedLinks = new Set();
    let isNavigating = false;

    function isEligibleLink(anchor) {
        if (!anchor || anchor.tagName !== 'A') return false;
        const href = anchor.getAttribute('href');
        if (!href) return false;
        if (href.startsWith('#') || href.startsWith('javascript:') || href.startsWith('mailto:') || href.startsWith('tel:')) return false;
        if (anchor.getAttribute('target') === '_blank' || anchor.hasAttribute('download') || anchor.hasAttribute('data-no-instant')) return false;
        if (href.startsWith('/api/') || href.startsWith('/static/') || href.startsWith('/logout') || href.endsWith('.xlsx') || href.endsWith('.pdf') || href.endsWith('.zip') || href.endsWith('.db')) return false;

        try {
            const url = new URL(anchor.href, window.location.origin);
            if (url.origin !== window.location.origin) return false;
            return true;
        } catch (e) {
            return false;
        }
    }

    function prefetchUrl(urlStr) {
        if (!urlStr || pageCache.has(urlStr) || isNavigating) return;
        
        // 1. Tarayıcı native prefetch linki ekle
        if (!prefetchedLinks.has(urlStr)) {
            prefetchedLinks.add(urlStr);
            try {
                const linkEl = document.createElement('link');
                linkEl.rel = 'prefetch';
                linkEl.href = urlStr;
                document.head.appendChild(linkEl);
            } catch (e) {}
        }

        // 2. RAM önbelleğe al
        fetch(urlStr, { headers: { 'X-Requested-With': 'Turbo-Prefetch' } })
            .then(res => {
                if (res.ok && (res.headers.get('content-type') || '').includes('text/html')) {
                    return res.text();
                }
                return null;
            })
            .then(html => {
                if (html) {
                    pageCache.set(urlStr, html);
                    if (pageCache.size > 30) {
                        const firstKey = pageCache.keys().next().value;
                        pageCache.delete(firstKey);
                    }
                }
            })
            .catch(() => {});
    }

    // Fare üzerine geldiğinde veya mobilde dokunulduğunda anında RAM'e önbelleğe al
    document.addEventListener('mouseover', function (e) {
        const a = e.target.closest('a');
        if (isEligibleLink(a)) {
            prefetchUrl(a.href);
        }
    }, { passive: true });

    document.addEventListener('touchstart', function (e) {
        const a = e.target.closest('a');
        if (isEligibleLink(a)) {
            prefetchUrl(a.href);
        }
    }, { passive: true });

    // Sayfa İçi Anlık Tıklama ve Geçiş Motoru
    document.addEventListener('click', function (e) {
        const a = e.target.closest('a');
        if (!isEligibleLink(a) || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.defaultPrevented) return;

        e.preventDefault();
        navigateTo(a.href, true);
    });

    function showProgressBar() {
        let bar = document.getElementById('turboProgressBar');
        if (!bar) {
            bar = document.createElement('div');
            bar.id = 'turboProgressBar';
            bar.className = 'fixed top-0 left-0 h-[3px] bg-gradient-to-r from-blue-500 via-indigo-500 to-emerald-400 z-[999999] transition-all duration-150 pointer-events-none shadow-sm shadow-blue-500/50';
            bar.style.width = '0%';
            document.body.appendChild(bar);
        }
        bar.style.opacity = '1';
        bar.style.width = '45%';
        setTimeout(() => { if (bar) bar.style.width = '85%'; }, 20);
    }

    function hideProgressBar() {
        const bar = document.getElementById('turboProgressBar');
        if (bar) {
            bar.style.width = '100%';
            setTimeout(() => {
                bar.style.opacity = '0';
                setTimeout(() => { if (bar) bar.style.width = '0%'; }, 150);
            }, 60);
        }
    }

    function executeDynamicScripts(container) {
        if (!container) return;
        const scripts = container.querySelectorAll('script');
        scripts.forEach(s => {
            if (s.src) {
                const newScript = document.createElement('script');
                newScript.src = s.src;
                document.head.appendChild(newScript);
                return;
            }

            let code = s.textContent.trim();
            if (!code) return;

            // Çoklu sayfa geçişlerinde let/const Identifier already declared hatasını önle
            const sanitizedCode = code.replace(/(?:^|\n)\s*(?:let|const)\s+([a-zA-Z0-9_$]+)\s*=/g, '\nvar $1 =');

            try {
                const runner = document.createElement('script');
                runner.textContent = `(function(){\n${sanitizedCode}\n})();`;
                document.body.appendChild(runner);
                setTimeout(() => runner.remove(), 50);
            } catch (err) {
                try {
                    (0, eval)(sanitizedCode);
                } catch (evalErr) {
                    console.warn('Script execution fallback note:', evalErr);
                }
            }
        });
    }

    async function navigateTo(urlStr, pushState = true) {
        if (isNavigating) return;
        isNavigating = true;
        showProgressBar();

        try {
            let html = pageCache.get(urlStr);
            if (!html) {
                const resp = await fetch(urlStr);
                if (!resp.ok || !(resp.headers.get('content-type') || '').includes('text/html')) {
                    window.location.href = urlStr;
                    return;
                }
                html = await resp.text();
                pageCache.set(urlStr, html);
            }

            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');

            // 1. Sayfa Başlığı
            if (doc.title) document.title = doc.title;

            // 2. Ana İçerik Değişimi (<main>)
            const newMain = doc.querySelector('main') || doc.querySelector('#mainContentArea');
            const curMain = document.querySelector('main') || document.querySelector('#mainContentArea');

            if (newMain && curMain) {
                curMain.innerHTML = newMain.innerHTML;
                if (newMain.className) curMain.className = newMain.className;
            } else {
                window.location.href = urlStr;
                return;
            }

            // 3. Üst Bar Sayfa Başlığı
            const newHeaderTitle = doc.querySelector('#pageHeaderTitle') || doc.querySelector('header h2');
            const curHeaderTitle = document.querySelector('#pageHeaderTitle') || document.querySelector('header h2');
            if (newHeaderTitle && curHeaderTitle) {
                curHeaderTitle.innerHTML = newHeaderTitle.innerHTML;
            }

            // 4. Sol Menü (Sidebar) Aktif Linklerini Eşitle
            const newNavLinks = doc.querySelectorAll('.nav-link');
            const curNavLinks = document.querySelectorAll('.nav-link');
            if (newNavLinks.length === curNavLinks.length) {
                curNavLinks.forEach((link, idx) => {
                    link.className = newNavLinks[idx].className;
                });
            }

            // 5. Browser Geçmişini (History) Güncelle
            if (pushState) {
                window.history.pushState({ url: urlStr }, '', urlStr);
            }

            // 6. Sayfa İçi Dinamik Scriptleri Güvenle Çalıştır
            executeDynamicScripts(curMain);

            // 7. Sayfa Başına Kaydır & Uyarıları Başlat
            window.scrollTo({ top: 0, behavior: 'instant' });
            initAutoDismissAlerts();

            // 8. Custom Event Yayınla
            document.dispatchEvent(new CustomEvent('turbo:load', { detail: { url: urlStr } }));

        } catch (err) {
            console.warn('Turbo navigation fallback to reload:', err);
            window.location.href = urlStr;
        } finally {
            isNavigating = false;
            hideProgressBar();
        }
    }

    // Tarayıcı İleri/Geri Tuşları
    window.addEventListener('popstate', function () {
        navigateTo(window.location.href, false);
    });

    window.turboNavigate = navigateTo;
    window.turboPrefetch = prefetchUrl;
    window.clearTurboCache = function () { pageCache.clear(); };
})();

