// İmalat Takip Sistemi - İstemci Tarafı JavaScript Yardımcıları

console.log("İmalat, Boya ve Sevkiyat Takip Sistemi devrede.");

// Global Toast Bildirimi Fonksiyonu
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `fixed bottom-5 right-5 z-50 p-4 rounded-xl shadow-2xl flex items-center gap-3 text-xs font-semibold border animate-fade-in ${
        type === 'success' ? 'bg-emerald-950 text-emerald-300 border-emerald-800' :
        type === 'danger' ? 'bg-rose-950 text-rose-300 border-rose-800' :
        'bg-slate-900 text-blue-300 border-slate-700'
    }`;
    
    toast.innerHTML = `
        <i class="fa-solid ${type === 'success' ? 'fa-circle-check text-emerald-400' : 'fa-circle-info'} text-base"></i>
        <span>${message}</span>
    `;

    document.body.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 4000);
}
