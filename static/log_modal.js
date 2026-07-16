(function () {
    const STORAGE_KEY_PREFIX = 'crm_page_log_history';
    const MAX_HISTORY = 8000;
    let button = null;
    let overlay = null;
    let body = null;
    let historyLoaded = false;
    let serverHistoryLoaded = false;
    let serverHistoryPromise = null;
    let logHistory = [];
    let seenKeys = new Set();

    function canonicalLogKey(key) {
        const value = String(key || '');
        const bulkLogin = /^bulk-login-(?:query|transfer|all):([^:]+):(.+)$/.exec(value);
        return bulkLogin ? `job:${bulkLogin[1]}:${bulkLogin[2]}` : value;
    }

    function stableLogKey(row) {
        if (!row) return '';
        const context = row.context && typeof row.context === 'object' ? row.context : {};
        const explicit = canonicalLogKey(row.key || context.log_key);
        if (explicit) return explicit;
        const jobId = row.job_id || context.job_id;
        const logId = row.log_id !== undefined ? row.log_id : context.log_id;
        if (jobId && logId !== undefined && logId !== null) {
            return `job:${jobId}:${logId}`;
        }
        return row.id ? `event:${row.id}` : '';
    }

    function logTimestamp(row) {
        const context = row && row.context && typeof row.context === 'object' ? row.context : {};
        const value = String((row && row.created_at) || context.created_at || '');
        if (!value) return 0;
        const parsed = Date.parse(value.includes('T') ? value : value.replace(' ', 'T'));
        return Number.isFinite(parsed) ? parsed : 0;
    }

    function normalizeLogRow(row) {
        const context = row && row.context && typeof row.context === 'object' ? row.context : {};
        const createdAt = String((row && row.created_at) || context.created_at || '');
        const normalized = {
            ...(row || {}),
            time: String((row && row.time) || (createdAt.length >= 19 ? createdAt.slice(11, 19) : '')),
            created_at: createdAt,
            message: String((row && row.message) || ''),
            level: normalizeLevel((row && row.level) || 'dim'),
        };
        normalized.key = stableLogKey(normalized);
        return normalized;
    }

    function sameVisibleLog(left, right) {
        if (left.message !== right.message || left.level !== right.level) return false;
        if (left.time === right.time) return true;
        const leftTimestamp = logTimestamp(left);
        const rightTimestamp = logTimestamp(right);
        return leftTimestamp > 0
            && rightTimestamp > 0
            && Math.abs(leftTimestamp - rightTimestamp) <= 5000;
    }

    function mergeLogRows(existingRows, incomingRows) {
        const merged = [];
        const indexes = new Map();

        function mergeOne(value, preferIncoming) {
            const row = normalizeLogRow(value);
            const key = stableLogKey(row);
            if (key && indexes.has(key)) {
                const index = indexes.get(key);
                merged[index] = preferIncoming ? {...merged[index], ...row, key} : merged[index];
                return;
            }
            if (key && key.startsWith('job:')) {
                const fallbackIndex = merged.findIndex(item => !stableLogKey(item) && sameVisibleLog(item, row));
                if (fallbackIndex >= 0) {
                    merged[fallbackIndex] = {...merged[fallbackIndex], ...row, key};
                    indexes.set(key, fallbackIndex);
                    return;
                }
            }
            if (key) indexes.set(key, merged.length);
            merged.push(row);
        }

        (existingRows || []).forEach(row => mergeOne(row, false));
        (incomingRows || []).forEach(row => mergeOne(row, true));
        merged.sort((left, right) => logTimestamp(right) - logTimestamp(left));
        return merged;
    }

    function currentLogScope() {
        const path = window.location.pathname || '/';
        if (path === '/' || path === '/index.html') return '/';
        return path.replace(/\/+$/, '') || '/';
    }

    function storageKey() {
        return `${STORAGE_KEY_PREFIX}:${currentLogScope()}`;
    }

    function currentLogCategory() {
        const categories = {
            '/': 'results',
            '/crm': 'crm',
            '/transfer': 'transfer',
            '/accounts': 'accounts',
            '/product-library': 'product-library',
        };
        return categories[currentLogScope()] || '';
    }

    function ensureLogModal() {
        if (button && overlay && body) return;

        button = document.createElement('button');
        button.type = 'button';
        button.className = 'global-log-button';
        button.textContent = '查看日志';
        button.addEventListener('click', openGlobalLogModal);

        overlay = document.createElement('div');
        overlay.className = 'global-log-overlay';
        overlay.innerHTML = `
            <div class="global-log-modal" role="dialog" aria-modal="true" aria-label="详细日志">
                <div class="global-log-head">
                    <h3>详细日志</h3>
                    <div class="global-log-actions">
                        <button type="button" class="global-log-small" data-action="clear">清空</button>
                        <button type="button" class="global-log-close" data-action="close" aria-label="关闭">×</button>
                    </div>
                </div>
                <div class="global-log-body"><div class="dim">等待操作...</div></div>
            </div>
        `;
        body = overlay.querySelector('.global-log-body');
        overlay.addEventListener('click', event => {
            if (event.target === overlay || event.target.dataset.action === 'close') closeGlobalLogModal();
            if (event.target.dataset.action === 'clear') clearGlobalLog();
        });

        document.body.appendChild(button);
        document.body.appendChild(overlay);
        loadHistory();
        renderHistory();
        loadServerHistory();
    }

    function openGlobalLogModal() {
        ensureLogModal();
        overlay.classList.add('show');
        loadServerHistory();
    }

    function closeGlobalLogModal() {
        if (overlay) overlay.classList.remove('show');
    }

    function clearGlobalLog() {
        ensureLogModal();
        logHistory = [];
        seenKeys = new Set();
        persistHistory();
        body.innerHTML = '<div class="dim">等待操作...</div>';
    }

    function normalizeLevel(level) {
        return ['info', 'success', 'error', 'warn', 'dim'].includes(level) ? level : 'dim';
    }

    function loadHistory() {
        if (historyLoaded) return;
        historyLoaded = true;
        try {
            const rows = JSON.parse(sessionStorage.getItem(storageKey()) || '[]');
            logHistory = Array.isArray(rows) ? mergeLogRows([], rows).slice(0, MAX_HISTORY) : [];
        } catch (e) {
            logHistory = [];
        }
        seenKeys = new Set(logHistory.map(stableLogKey).filter(Boolean));
    }

    function persistHistory() {
        try {
            sessionStorage.setItem(storageKey(), JSON.stringify(logHistory.slice(0, MAX_HISTORY)));
        } catch (e) {}
    }

    function loadServerHistory() {
        const category = currentLogCategory();
        if (!category || serverHistoryLoaded) return serverHistoryPromise;
        if (serverHistoryPromise) return serverHistoryPromise;
        serverHistoryPromise = fetch(`/api/logs?category=${encodeURIComponent(category)}&limit=500`, {
            credentials: 'same-origin',
        })
            .then(response => response.ok ? response.json() : null)
            .then(payload => {
                if (payload && payload.success && Array.isArray(payload.logs)) {
                    replaceGlobalLogRows(payload.logs);
                }
                serverHistoryLoaded = true;
            })
            .catch(() => {})
            .finally(() => {
                serverHistoryPromise = null;
            });
        return serverHistoryPromise;
    }

    function renderHistory() {
        ensureLogModal();
        body.innerHTML = '';
        if (!logHistory.length) {
            body.innerHTML = '<div class="dim">等待操作...</div>';
            return;
        }
        for (const row of logHistory) {
            appendLogLine(row.message, row.level, row.time, false);
        }
        body.scrollTop = 0;
    }

    function appendLogLine(message, level, time, prepend=true) {
        if (body.textContent.trim() === '等待操作...') body.innerHTML = '';
        const line = document.createElement('div');
        line.className = normalizeLevel(level || 'dim');
        line.textContent = `[${time || new Date().toLocaleTimeString()}] ${message || ''}`;
        if (prepend) body.insertBefore(line, body.firstChild);
        else body.appendChild(line);
        while (body.children.length > 8000) {
            body.removeChild(body.lastChild);
        }
        body.scrollTop = 0;
    }

    function appendGlobalLog(message, level, time, key, createdAt) {
        ensureLogModal();
        loadHistory();
        const stamp = time || new Date().toLocaleTimeString();
        const normalizedLevel = normalizeLevel(level || 'dim');
        const dedupeKey = canonicalLogKey(key);
        if (dedupeKey && seenKeys.has(dedupeKey)) return;
        const entry = {
            time: stamp,
            created_at: createdAt || new Date().toISOString(),
            message: String(message || ''),
            level: normalizedLevel,
            key: dedupeKey,
        };
        logHistory = mergeLogRows(logHistory, [entry]).slice(0, MAX_HISTORY);
        seenKeys = new Set(logHistory.map(stableLogKey).filter(Boolean));
        persistHistory();
        renderHistory();
    }

    function appendGlobalLogRow(row) {
        if (!row) return;
        replaceGlobalLogRows([row]);
    }

    function replaceGlobalLogRows(rows) {
        ensureLogModal();
        if (!rows || !rows.length) return;
        loadHistory();
        logHistory = mergeLogRows(logHistory, rows).slice(0, MAX_HISTORY);
        seenKeys = new Set(logHistory.map(stableLogKey).filter(Boolean));
        persistHistory();
        renderHistory();
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {stableLogKey, mergeLogRows};
    }

    if (typeof window !== 'undefined') {
        window.globalLogAppend = appendGlobalLog;
        window.globalLogAppendRow = appendGlobalLogRow;
        window.globalLogReplaceRows = replaceGlobalLogRows;
        window.globalLogClear = clearGlobalLog;
        window.openGlobalLogModal = openGlobalLogModal;
        window.closeGlobalLogModal = closeGlobalLogModal;
    }

    if (typeof document !== 'undefined') {
        document.addEventListener('DOMContentLoaded', ensureLogModal);
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape') closeGlobalLogModal();
        });
    }
})();
