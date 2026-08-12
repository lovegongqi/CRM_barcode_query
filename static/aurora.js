(function () {
    const NAV = {
        '/crm': ['⌕', '查询'],
        '/': ['⌕', '查询'],
        '/results': ['▤', '结果'],
        '/transfer': ['⇄', '移库'],
        '/inbound': ['⇩', '入库'],
        '/product-library': ['≋', '匹配'],
        '/accounts': ['⚙', '设置']
    };

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, char => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        })[char]);
    }

    function navMeta(anchor) {
        const path = new URL(anchor.href, location.origin).pathname;
        if (path === '/' && anchor.textContent.includes('结果')) return ['▤', '结果'];
        return NAV[path] || ['•', anchor.textContent.trim()];
    }

    function enhanceNavigation() {
        document.querySelectorAll('.page-nav a').forEach(anchor => {
            if (anchor.dataset.auroraEnhanced) return;
            const original = anchor.textContent.trim();
            const [glyph, fallback] = navMeta(anchor);
            anchor.dataset.auroraEnhanced = '1';
            anchor.setAttribute('aria-label', original || fallback);
            anchor.title = original || fallback;
            anchor.innerHTML = `<span class="aurora-nav-glyph" aria-hidden="true">${escapeHtml(glyph)}</span><span class="aurora-nav-label">${escapeHtml(fallback)}</span>`;
        });
    }

    function ensureDialog() {
        let dialog = document.getElementById('auroraLogDialog');
        if (dialog) return dialog;
        dialog = document.createElement('div');
        dialog.id = 'auroraLogDialog';
        dialog.className = 'aurora-log-dialog';
        dialog.setAttribute('role', 'dialog');
        dialog.setAttribute('aria-modal', 'true');
        dialog.setAttribute('aria-labelledby', 'auroraLogTitle');
        dialog.innerHTML = `
            <section class="aurora-log-panel">
                <div class="aurora-log-titlebar">
                    <div><strong id="auroraLogTitle">详细日志</strong><small id="auroraLogSubtitle"></small></div>
                    <button class="aurora-log-close" type="button" aria-label="关闭日志">×</button>
                </div>
                <div class="aurora-log-meta" id="auroraLogMeta"></div>
                <div class="aurora-log-lines" id="auroraLogLines"></div>
            </section>`;
        dialog.addEventListener('click', event => {
            if (event.target === dialog || event.target.closest('.aurora-log-close')) closeAuroraLog();
        });
        document.body.appendChild(dialog);
        return dialog;
    }

    function normalizeLog(row) {
        if (typeof row === 'string') return {message: row, time: '', level: 'dim'};
        return {
            message: row && (row.message || row.text || row.status) || '',
            time: row && (row.time || row.timestamp) || '',
            level: row && (row.level || row.state) || 'dim'
        };
    }

    function renderAuroraLog(dialog, title, logs, meta, subtitle) {
        const rows = Array.isArray(logs) ? logs : (logs ? [logs] : []);
        document.getElementById('auroraLogTitle').textContent = title || '详细日志';
        document.getElementById('auroraLogSubtitle').textContent = subtitle || '实时任务时间线';
        document.getElementById('auroraLogMeta').innerHTML = Object.entries(meta || {})
            .filter(([, value]) => value !== '' && value != null)
            .map(([key, value]) => `<span>${escapeHtml(key)}：${escapeHtml(value)}</span>`).join('');
        document.getElementById('auroraLogLines').innerHTML = (rows.length ? rows : [{message: '暂无详细日志', level: 'dim'}])
            .map(raw => {
                const row = normalizeLog(raw);
                const level = ['success', 'warn', 'error'].includes(row.level) ? row.level : '';
                return `<div class="aurora-log-line ${level}"><span class="aurora-log-time">${escapeHtml(row.time)}</span>${escapeHtml(row.message)}</div>`;
            }).join('');
    }

    window.openAuroraLog = function (title, logs, meta, subtitle, liveKey='') {
        const dialog = ensureDialog();
        dialog.dataset.liveKey = String(liveKey || '');
        renderAuroraLog(dialog, title, logs, meta, subtitle);
        dialog.classList.add('show');
        document.body.style.overflow = 'hidden';
        dialog.querySelector('.aurora-log-close').focus();
    };

    window.refreshAuroraLog = function (liveKey, title, logs, meta, subtitle) {
        const dialog = document.getElementById('auroraLogDialog');
        if (!dialog || !dialog.classList.contains('show')) return false;
        if (dialog.dataset.liveKey !== String(liveKey || '')) return false;
        renderAuroraLog(dialog, title, logs, meta, subtitle);
        return true;
    };

    window.closeAuroraLog = function () {
        const dialog = document.getElementById('auroraLogDialog');
        if (!dialog) return;
        dialog.classList.remove('show');
        dialog.dataset.liveKey = '';
        document.body.style.overflow = '';
    };

    window.auroraStatusClass = function (level) {
        const value = String(level || '').toLowerCase();
        if (/success|done|完成|成功|已结/.test(value)) return 'success';
        if (/error|fail|失败/.test(value)) return 'error';
        if (/warn|retry|重试|等待/.test(value)) return 'warn';
        if (/queue|排队|未开始/.test(value)) return 'dim';
        return '';
    };

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeAuroraLog();
    });
    document.addEventListener('DOMContentLoaded', () => {
        enhanceNavigation();
        ensureDialog();
    });
})();
