'use strict';

const INVENTORY_DEVICE_KEY = 'inventory_device_id';
let inventoryDeviceId = '';
let inventoryTask = null;
let lastInventoryVersion = null;
let inventoryPollPromise = null;
let inventoryPollQueuedForce = false;
let inventoryQueryGeneration = 0;
let inventoryPollTimer = null;
let inventorySearchTimer = null;
let currentCountItem = null;
let countDialogEditable = false;
let countMutationPending = false;
let countDialogRequestId = 0;
let countEntryDrafts = new Map();
let gyjLoginPollTimer = null;
let gyjLoginSession = 0;
let inventoryActiveTab = 'current';
let inventoryTabGeneration = 0;
let inventoryDifferenceState = 'open';
let inventoryDifferenceGeneration = 0;
let inventoryDifferenceSearchTimer = null;
let inventoryHistoryTasks = [];
let inventoryHistoryHasMore = false;
let currentSerialBarcode = '';
let currentSerialData = null;
let completionConfirmation = null;
let serialWorkspaceOpen = false;
let serialWorkspaceEditable = false;
let serialWorkspaceRequestId = 0;
let serialScanPending = false;
let serialScanQueueLength = 0;
let serialFinishPending = false;
let serialOperationQueue = Promise.resolve();
let serialOperationGeneration = 0;
let serialRenderedGeneration = 0;
let serialMutationFailures = new Map();
let inventoryCameraControls = null;
let inventoryCameraGeneration = 0;
let inventoryCameraLastSerial = '';
let inventoryCameraLastDecodedAt = 0;

function inventoryElement(id) {
    return document.getElementById(id);
}

function inventoryText(value, fallback = '—') {
    if (value === null || value === undefined || value === '') return fallback;
    return String(value);
}

function inventoryNode(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = inventoryText(text, '');
    return node;
}

function setInventoryNotice(message, kind = '') {
    const notice = inventoryElement('inventoryNotice');
    notice.textContent = message || '';
    notice.className = 'inventory-notice' + (kind ? ` is-${kind}` : '');
}

async function inventoryRequest(url, options = {}) {
    const requestOptions = {...options};
    const acceptFailureState = requestOptions.acceptFailureState === true;
    delete requestOptions.acceptFailureState;
    requestOptions.headers = {...(options.headers || {})};
    if (requestOptions.body && !requestOptions.headers['Content-Type']) {
        requestOptions.headers['Content-Type'] = 'application/json';
    }
    const response = await fetch(url, requestOptions);
    let data = {};
    try {
        data = await response.json();
    } catch (_error) {
        data = {};
    }
    if (!response.ok || (!acceptFailureState && data.success === false)) {
        const error = new Error(data.error || data.message || '请求失败，请稍后重试');
        error.status = response.status;
        error.data = data;
        throw error;
    }
    return data;
}

function inventoryPost(url, body) {
    return inventoryRequest(url, {method: 'POST', body: JSON.stringify(body)});
}

function inventoryDelete(url, body) {
    return inventoryRequest(url, {method: 'DELETE', body: JSON.stringify(body)});
}

function inventoryExpectedVersion(explicitVersion = null) {
    const value = explicitVersion === null || explicitVersion === undefined
        ? ((inventoryTask && inventoryTask.version) || lastInventoryVersion || 1)
        : explicitVersion;
    if (!Number.isInteger(value) || value < 1) {
        throw new Error('盘点任务版本已失效，请刷新后重试');
    }
    return value;
}

function acceptInventoryMutationVersion(data, updateActiveTask = true) {
    if (!data || !Number.isInteger(data.version) || data.version < 1) return data;
    if (updateActiveTask && inventoryTask) inventoryTask.version = data.version;
    if (updateActiveTask) lastInventoryVersion = data.version;
    return data;
}

async function inventoryMutationPost(
    url, body = {}, expectedVersion = null, updateActiveTask = true
) {
    const data = await inventoryPost(url, {
        ...body,
        expected_version: inventoryExpectedVersion(expectedVersion),
    });
    return acceptInventoryMutationVersion(data, updateActiveTask);
}

async function inventoryMutationDelete(
    url, body = {}, expectedVersion = null, updateActiveTask = true
) {
    const data = await inventoryDelete(url, {
        ...body,
        expected_version: inventoryExpectedVersion(expectedVersion),
    });
    return acceptInventoryMutationVersion(data, updateActiveTask);
}

function inventoryIsVersionConflict(error) {
    return Boolean(
        error && error.status === 409 && error.data
        && Number.isInteger(error.data.current_version)
    );
}

async function refreshInventoryAfterVersionConflict() {
    lastInventoryVersion = null;
    await pollInventoryTask({force: true});
}

function getInventoryDeviceId() {
    if (inventoryDeviceId) return inventoryDeviceId;
    try {
        inventoryDeviceId = localStorage.getItem(INVENTORY_DEVICE_KEY) || '';
    } catch (_error) {
        inventoryDeviceId = '';
    }
    if (!inventoryDeviceId) {
        try {
            if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
                inventoryDeviceId = crypto.randomUUID();
            }
        } catch (_error) {
            inventoryDeviceId = '';
        }
        try {
            if (!inventoryDeviceId && globalThis.crypto && typeof globalThis.crypto.getRandomValues === 'function') {
                const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
                bytes[6] = (bytes[6] & 0x0f) | 0x40;
                bytes[8] = (bytes[8] & 0x3f) | 0x80;
                const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0'));
                inventoryDeviceId = [
                    hex.slice(0, 4).join(''), hex.slice(4, 6).join(''),
                    hex.slice(6, 8).join(''), hex.slice(8, 10).join(''),
                    hex.slice(10, 16).join(''),
                ].join('-');
            }
        } catch (_error) {
            inventoryDeviceId = '';
        }
        if (!inventoryDeviceId) {
            inventoryDeviceId = `inventory-page-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
        }
        try {
            localStorage.setItem(INVENTORY_DEVICE_KEY, inventoryDeviceId);
        } catch (_error) {
            // The in-memory id remains stable for this page when storage is denied.
        }
    }
    return inventoryDeviceId;
}

function decimalParts(value) {
    const text = String(value === null || value === undefined ? '' : value).trim();
    const match = text.match(/^([0-9]+)(?:\.([0-9]*))?$/);
    if (!match) return null;
    const fraction = match[2] || '';
    return {digits: BigInt(match[1] + fraction), scale: fraction.length};
}

function decimalDifferenceText(actual, book) {
    const actualParts = decimalParts(actual);
    const bookParts = decimalParts(book);
    if (!actualParts || !bookParts) return null;
    const scale = Math.max(actualParts.scale, bookParts.scale);
    const actualInteger = actualParts.digits * (10n ** BigInt(scale - actualParts.scale));
    const bookInteger = bookParts.digits * (10n ** BigInt(scale - bookParts.scale));
    const difference = actualInteger - bookInteger;
    const negative = difference < 0n;
    let digits = (negative ? -difference : difference).toString().padStart(scale + 1, '0');
    let text;
    if (scale) {
        const whole = digits.slice(0, -scale) || '0';
        const fraction = digits.slice(-scale).replace(/0+$/, '');
        text = fraction ? `${whole}.${fraction}` : whole;
    } else {
        text = digits;
    }
    if (text === '0') return '0';
    return negative ? `-${text}` : text;
}

function decimalDirection(value) {
    const text = inventoryText(value, '0').trim();
    if (text === '0' || /^0(?:\.0+)?$/.test(text)) return 0;
    return text.startsWith('-') ? -1 : 1;
}

function inventoryBookQuantity(item) {
    if (!item) return null;
    for (const value of (
        [item.latest_book_qty, item.open_book_qty, item.book_quantity, item.initial_stock]
    )) {
        if (value !== null && value !== undefined && value !== '') return String(value);
    }
    return null;
}

function inventoryActualQuantity(item) {
    if (!item) return null;
    if (item.count_total !== null && item.count_total !== undefined) {
        return item.count_total;
    }
    if (item.completed_actual_qty !== null && item.completed_actual_qty !== undefined) {
        return item.completed_actual_qty;
    }
    return item.counted_quantity;
}

function inventoryIsCompleted(item) {
    return inventoryActualQuantity(item) !== null && inventoryActualQuantity(item) !== undefined;
}

function inventoryStateLabel(item) {
    const labels = {
        pending: '待盘点',
        matched: '数量一致',
        variance: decimalDirection(item.diff_qty) > 0 ? '盘盈' : '盘亏',
        serial_pending: '待序列号',
        serial_complete: '序列号已核对',
        data_error: '资料异常·不可盘',
    };
    return labels[item.state] || inventoryText(item.state, '待盘点');
}

function computedInventorySummary(items) {
    const result = {total: items.length, completed: 0, pending: 0, matched: 0, surplus: 0, deficit: 0, serial_pending: 0, data_error: 0};
    items.forEach((item) => {
        if (item.data_error) result.data_error += 1;
        else if (inventoryIsCompleted(item)) result.completed += 1;
        else result.pending += 1;
        if (item.state === 'matched') result.matched += 1;
        if (decimalDirection(item.diff_qty) > 0) result.surplus += 1;
        if (decimalDirection(item.diff_qty) < 0) result.deficit += 1;
        if (item.state === 'serial_pending') result.serial_pending += 1;
    });
    return result;
}

function summaryValue(summary, names, fallback) {
    for (const name of names) {
        if (summary && summary[name] !== null && summary[name] !== undefined) return summary[name];
    }
    return fallback;
}

function renderInventorySummary(task) {
    const root = inventoryElement('inventoryTaskSummary');
    root.replaceChildren();
    const computed = computedInventorySummary((task && task.items) || []);
    const summary = (task && task.summary) || {};
    const cards = [
        ['商品总数', summaryValue(summary, ['total', 'total_items'], computed.total), ''],
        ['已完成', summaryValue(summary, ['completed', 'completed_items'], computed.completed), 'is-good'],
        ['待盘点', summaryValue(summary, ['pending', 'pending_items'], computed.pending), 'is-warn'],
        ['数量一致', summaryValue(summary, ['matched', 'matched_items'], computed.matched), 'is-good'],
        ['盘盈', summaryValue(summary, ['surplus', 'surplus_items'], computed.surplus), 'is-warn'],
        ['盘亏', summaryValue(summary, ['deficit', 'deficit_items'], computed.deficit), 'is-bad'],
        ['待序列号', summaryValue(summary, ['serial_pending', 'serial_pending_items'], computed.serial_pending), 'is-warn'],
        ['资料异常', summaryValue(summary, ['data_error', 'data_error_items'], computed.data_error), 'is-bad'],
    ];
    cards.forEach(([label, value, className]) => {
        const card = inventoryNode('div', `inventory-summary-card ${className}`.trim());
        card.append(inventoryNode('span', '', label), inventoryNode('strong', '', value));
        root.append(card);
    });
}

function inventoryVisibleItems(items) {
    const filter = inventoryElement('inventoryFilters').value;
    if (filter === 'completed') return items.filter(inventoryIsCompleted);
    if (filter === 'variance') return items.filter((item) => decimalDirection(item.diff_qty) !== 0);
    return items;
}

function inventoryDetailText(item) {
    const parts = [
        ['规格', item.spec], ['型号', item.model], ['类别', item.category], ['单位', item.unit],
    ];
    return parts.map(([label, value]) => `${label} ${inventoryText(value)}`).join(' · ');
}

function inventoryMetric(label, value, className = '') {
    const metric = inventoryNode('div', `inventory-metric ${className}`.trim());
    metric.append(inventoryNode('span', '', label), inventoryNode('strong', '', value));
    return metric;
}

function replaceInventoryItem(item) {
    if (!inventoryTask || !Array.isArray(inventoryTask.items)) return;
    const index = inventoryTask.items.findIndex((row) => row.barcode === item.barcode);
    if (index >= 0) inventoryTask.items.splice(index, 1, item);
    else inventoryTask.items.push(item);
}

function renderInventoryItems(items) {
    const root = inventoryElement('inventoryItems');
    root.replaceChildren();
    const visible = inventoryVisibleItems(Array.isArray(items) ? items : []);
    if (!visible.length) {
        const query = inventoryElement('inventorySearch').value.trim();
        root.append(inventoryNode('div', 'inventory-empty', query ? '没有找到对应商品，请核对条码或名称。' : '当前范围内没有需要显示的商品。'));
        return;
    }
    visible.forEach((item) => {
        const completed = inventoryIsCompleted(item);
        const row = inventoryNode('article', 'inventory-item');
        row.dataset.barcode = inventoryText(item.barcode, '');
        row.setAttribute('aria-label', `${inventoryText(item.name, '未命名商品')}，${inventoryStateLabel(item)}`);

        const top = inventoryNode('div', 'inventory-item-top');
        const product = inventoryNode('div', 'inventory-item-product');
        product.append(
            inventoryNode('span', 'inventory-item-barcode', item.barcode),
            inventoryNode('strong', 'inventory-item-name', item.name),
        );
        const serialBadge = inventoryNode(
            'span', `inventory-serial-badge${item.has_serial ? '' : ' is-off'}`,
            item.data_error ? '序列号资料未知' : (item.has_serial ? '序列号商品' : '无序列号'),
        );
        product.append(serialBadge);
        top.append(product, inventoryNode('span', 'inventory-state-badge', inventoryStateLabel(item)));

        const metrics = inventoryNode('div', 'inventory-item-metrics');
        const direction = decimalDirection(item.diff_qty);
        metrics.append(
            inventoryMetric('GYJ 账面', inventoryBookQuantity(item)),
            inventoryMetric('实盘', inventoryActualQuantity(item)),
            inventoryMetric('差异', item.diff_qty, direction > 0 ? 'is-positive' : (direction < 0 ? 'is-negative' : '')),
        );

        const foot = inventoryNode('div', 'inventory-item-foot');
        foot.append(
            inventoryNode('span', '', completed ? '可继续追加或修改分次数量' : '尚未记录实盘数量'),
            inventoryNode('span', '', `更新时间：${inventoryText(item.updated_at)}`),
        );
        const actions = inventoryNode('div', 'inventory-item-actions');
        const count = inventoryNode(
            'button', 'btn btn-secondary', completed ? '修改数量' : '盘点数量'
        );
        count.type = 'button';
        count.disabled = Boolean(item.data_error);
        count.addEventListener('click', () => openCountItem(item.barcode));
        actions.append(count);
        if (item.state === 'serial_pending') {
            const serial = inventoryNode('button', 'btn btn-primary', '核对序列号');
            serial.type = 'button';
            serial.addEventListener('click', () => openSerialItem(item.barcode));
            actions.append(serial);
        }
        row.append(
            top,
            inventoryNode(
                'p', 'inventory-item-details',
                item.data_error
                    ? `资料异常，不可盘：${item.data_error}`
                    : inventoryDetailText(item),
            ),
            metrics,
            foot,
            actions,
        );
        root.append(row);
    });
}

function renderSerialQueue(task) {
    const section = inventoryElement('inventorySerialQueueRoot');
    const root = inventoryElement('inventorySerialQueue');
    const rows = task && ['counting', 'serial_check'].includes(task.phase)
        ? (task.items || []).filter((item) => item.state === 'serial_pending')
        : [];
    section.hidden = !rows.length;
    root.replaceChildren();
    if (!rows.length) {
        root.append(inventoryNode('div', 'inventory-empty', '没有待核对的序列号商品。'));
        return;
    }
    rows.forEach((item) => {
        const button = inventoryNode('button', 'inventory-item inventory-serial-queue-item');
        button.type = 'button';
        button.setAttribute('aria-label', `打开 ${inventoryText(item.name, item.barcode)} 序列号核对`);
        button.addEventListener('click', () => openSerialItem(item.barcode));
        const top = inventoryNode('div', 'inventory-item-top');
        const product = inventoryNode('div', 'inventory-item-product');
        product.append(
            inventoryNode('span', 'inventory-item-barcode', item.barcode),
            inventoryNode('strong', 'inventory-item-name', item.name),
        );
        top.append(product, inventoryNode('span', 'inventory-state-badge', '待序列号'));
        button.append(top, inventoryNode('p', 'inventory-item-details', inventoryDetailText(item)));
        root.append(button);
    });
}

function renderInventoryTask(task) {
    const meta = inventoryElement('inventoryTaskMeta');
    const createButton = inventoryElement('inventoryCreateTask');
    const completeButton = inventoryElement('inventoryCompleteTask');
    if (!task) {
        meta.textContent = '尚无进行中的盘点任务。';
        createButton.hidden = false;
        completeButton.hidden = true;
        renderInventorySummary(null);
        renderInventoryItems([]);
        renderSerialQueue(null);
        return;
    }
    const phaseLabels = {loading: '正在载入商品', counting: '数量盘点', serial_check: '序列号核对', sync_error: '同步需重试'};
    const phase = phaseLabels[task.phase] || inventoryText(task.phase, '进行中');
    const syncTime = inventoryText(task.last_sync_at, '尚未同步');
    meta.textContent = `${phase} · 最近 GYJ 同步 ${syncTime}`;
    createButton.hidden = true;
    completeButton.hidden = !['counting', 'serial_check'].includes(task.phase);
    renderInventorySummary(task);
    renderInventoryItems(task.items || []);
    renderSerialQueue(task);
    if (currentCountItem && inventoryElement('inventoryCountDialog').open) {
        const draft = inventoryElement('inventoryCountNewQuantity').value;
        const refreshed = (task.items || []).find(
            (item) => item.barcode === currentCountItem.barcode
        );
        if (refreshed) {
            currentCountItem = refreshed;
            renderCountEntries(refreshed);
            updateCountBook(refreshed);
            inventoryElement('inventoryCountNewQuantity').value = draft;
        }
    }
    if (serialWorkspaceOpen && !serialFinishPending) {
        const serialItem = (task.items || []).find(
            (item) => item.barcode === currentSerialBarcode
        );
        if (!serialItem || serialItem.state === 'serial_pending') refreshSerialItem();
        else closeSerialWorkspace();
    }
    if (task.gyj_status && task.gyj_status !== 'synced') setInventoryNotice(task.gyj_status, 'error');
}

function inventoryServerState(filter) {
    if (filter === 'pending' || filter === 'serial_pending') return filter;
    return '';
}

function currentInventoryQuery() {
    return {
        query: inventoryElement('inventorySearch').value.trim(),
        state: inventoryServerState(inventoryElement('inventoryFilters').value),
    };
}

function inventoryQueryKey(query) {
    return `${query.query}\n${query.state}`;
}

async function performInventoryPoll(force) {
    const generation = inventoryQueryGeneration;
    const queryState = currentInventoryQuery();
    const queryKey = inventoryQueryKey(queryState);
    const params = new URLSearchParams();
    if (!force && lastInventoryVersion !== null) params.set('version', String(lastInventoryVersion));
    if (queryState.query) params.set('query', queryState.query);
    if (queryState.state) params.set('state', queryState.state);
    const url = '/api/inventory/tasks/active' + (params.size ? `?${params.toString()}` : '');
    try {
        const data = await inventoryRequest(url);
        if (generation !== inventoryQueryGeneration || queryKey !== inventoryQueryKey(currentInventoryQuery())) return null;
        if (!data.task) {
            inventoryTask = null;
            lastInventoryVersion = null;
            renderInventoryTask(null);
            return true;
        }
        if (data.task.unchanged) return true;
        let nextTask = data.task;
        if (nextTask.phase === 'serial_check') {
            const detail = await inventoryRequest(`/api/inventory/tasks/${encodeURIComponent(nextTask.task_id)}`);
            if (generation !== inventoryQueryGeneration || queryKey !== inventoryQueryKey(currentInventoryQuery())) return null;
            if (detail.task && Array.isArray(detail.task.items)) {
                nextTask = {...nextTask, items: detail.task.items};
            }
        }
        inventoryTask = nextTask;
        lastInventoryVersion = nextTask.version;
        renderInventoryTask(inventoryTask);
        if (!nextTask.gyj_status || nextTask.gyj_status === 'synced') setInventoryNotice('');
        return true;
    } catch (error) {
        if (generation !== inventoryQueryGeneration || queryKey !== inventoryQueryKey(currentInventoryQuery())) return null;
        setInventoryNotice(error.message, 'error');
        return false;
    }
}

function pollInventoryTask(options = {}) {
    if (document.hidden) return Promise.resolve();
    if (inventoryActiveTab !== 'current' && (!options || options.force !== true)) return Promise.resolve();
    if ((!options || options.force !== true) && inventorySearchTimer) {
        return inventoryPollPromise || Promise.resolve();
    }
    if (options && options.force === true) inventoryPollQueuedForce = true;
    if (inventoryPollPromise) return inventoryPollPromise;
    inventoryPollPromise = (async () => {
        let refreshed = true;
        do {
            const force = inventoryPollQueuedForce;
            inventoryPollQueuedForce = false;
            refreshed = await performInventoryPoll(force);
        } while (inventoryPollQueuedForce);
        return refreshed !== false;
    })().finally(() => {
        inventoryPollPromise = null;
    });
    return inventoryPollPromise;
}

async function startInventoryTask() {
    const button = inventoryElement('inventoryCreateTask');
    button.disabled = true;
    button.textContent = '正在从 GYJ 读取商品…';
    setInventoryNotice('正在建立盘点任务，请保持页面打开。');
    try {
        const data = await inventoryPost('/api/inventory/tasks', {});
        acceptInventoryMutationVersion(data);
        await pollInventoryTask({force: true});
        setInventoryNotice('盘点任务已建立，可以开始扫描商品。', 'success');
    } catch (error) {
        setInventoryNotice(error.message, 'error');
    } finally {
        button.disabled = false;
        button.textContent = '开始新盘点';
    }
}

function closeCompletionConfirmation() {
    completionConfirmation = null;
    const dialog = inventoryElement('inventoryCompletionConfirmDialog');
    if (dialog.open) dialog.close();
}

function showCompletionConfirmation(taskId, version, pendingCount) {
    completionConfirmation = {taskId, version};
    inventoryElement('inventoryCompletionConfirmMessage').textContent =
        `仍有 ${pendingCount} 个已盘商品未核对序列号。确认完成后，将按“序列号未核对”写入差异报告。`;
    const dialog = inventoryElement('inventoryCompletionConfirmDialog');
    if (!dialog.open) dialog.showModal();
}

async function completeInventoryTask(allowUnverifiedSerials = false) {
    if (!inventoryTask || !['counting', 'serial_check'].includes(inventoryTask.phase)) return;
    const confirmation = allowUnverifiedSerials ? completionConfirmation : null;
    const button = inventoryElement(
        allowUnverifiedSerials ? 'inventoryCompletionConfirm' : 'inventoryCompleteTask'
    );
    const taskId = confirmation ? confirmation.taskId : inventoryTask.task_id;
    const version = confirmation ? confirmation.version : inventoryTask.version;
    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = '正在完成…';
    try {
        const data = await inventoryMutationPost(
            `/api/inventory/tasks/${encodeURIComponent(taskId)}/complete`,
            {allow_unverified_serials: allowUnverifiedSerials},
            version,
        );
        acceptInventoryMutationVersion(data);
        closeCompletionConfirmation();
        await pollInventoryTask({force: true});
        setInventoryNotice('盘点已完成，未盘商品未计入差异报告。', 'success');
    } catch (error) {
        if (!allowUnverifiedSerials && error.data && error.data.confirmation_required === true) {
            showCompletionConfirmation(
                taskId, version, Number(error.data.pending_serial_count) || 0
            );
            return;
        }
        const versionConflict = inventoryIsVersionConflict(error);
        if (versionConflict) {
            await refreshInventoryAfterVersionConflict();
            if (
                allowUnverifiedSerials && completionConfirmation
                && inventoryTask && inventoryTask.task_id === taskId
            ) {
                completionConfirmation.version = inventoryTask.version;
            }
        }
        if (allowUnverifiedSerials) {
            inventoryElement('inventoryCompletionConfirmMessage').textContent = versionConflict
                ? `${error.message}，已刷新。请再次确认完成。`
                : error.message;
        } else {
            setInventoryNotice(error.message, 'error');
        }
    } finally {
        button.disabled = false;
        button.textContent = originalText;
    }
}

function setCountMessage(message, kind = '') {
    const target = inventoryElement('inventoryCountMessage');
    target.textContent = message || '';
    target.className = 'inventory-dialog-message' + (kind ? ` is-${kind}` : '');
}

function renderCountProduct(item) {
    const root = inventoryElement('inventoryCountProduct');
    root.replaceChildren();
    root.append(
        inventoryNode('strong', 'inventory-item-name', item.name),
        inventoryNode('div', 'inventory-item-barcode', item.barcode),
        inventoryNode('div', '', inventoryDetailText(item)),
    );
}

function renderLiveDifference() {
    const panel = inventoryElement('inventoryCountDifference').parentElement;
    const actual = currentCountItem && currentCountItem.count_total !== null
        && currentCountItem.count_total !== undefined
        ? String(currentCountItem.count_total) : '';
    const book = inventoryElement('inventoryCountBook').dataset.quantity || '';
    const difference = decimalDifferenceText(actual, book);
    const value = inventoryElement('inventoryCountDifference');
    const state = inventoryElement('inventoryCountDifferenceState');
    panel.className = 'inventory-live-difference';
    if (difference === null) {
        value.textContent = '—';
        state.textContent = actual ? '数量无效' : '尚未记录';
        return;
    }
    value.textContent = decimalDirection(difference) > 0 ? `+${difference}` : difference;
    if (decimalDirection(difference) > 0) {
        panel.classList.add('is-positive');
        state.textContent = '盘盈';
    } else if (decimalDirection(difference) < 0) {
        panel.classList.add('is-negative');
        state.textContent = '盘亏';
    } else {
        panel.classList.add('is-matched');
        state.textContent = '数量一致';
    }
}

function updateCountBook(item) {
    const book = inventoryBookQuantity(item);
    const target = inventoryElement('inventoryCountBook');
    target.textContent = inventoryText(book);
    target.dataset.quantity = book === null ? '' : book;
    renderLiveDifference();
}

function closeCountDialog() {
    countDialogRequestId += 1;
    countDialogEditable = false;
    currentCountItem = null;
    countEntryDrafts.clear();
    const dialog = inventoryElement('inventoryCountDialog');
    if (dialog.open) dialog.close();
}

function renderCountEntries(item) {
    const entriesRoot = inventoryElement('inventoryCountEntries');
    const expression = inventoryElement('inventoryCountExpression');
    const entries = Array.isArray(item && item.count_entries) ? item.count_entries : [];
    const barcode = inventoryText(item && item.barcode, '');
    const currentKeys = new Set(entries.map(
        (entry) => `${barcode}:${entry.entry_id}`
    ));
    for (const key of countEntryDrafts.keys()) {
        if (key.startsWith(`${barcode}:`) && !currentKeys.has(key)) {
            countEntryDrafts.delete(key);
        }
    }
    inventoryElement('inventoryCountAuditButton').hidden = !item;
    entriesRoot.replaceChildren();
    expression.textContent = item && item.count_expression
        ? item.count_expression : '尚未记录';
    if (!entries.length) {
        entriesRoot.append(inventoryNode('div', 'inventory-count-entry-empty', '还没有分次盘点数量。'));
        return;
    }
    entries.forEach((entry, index) => {
        const row = inventoryNode('div', 'inventory-count-entry');
        const copy = inventoryNode('div', 'inventory-count-entry-copy');
        copy.append(
            inventoryNode('strong', '', `第 ${index + 1} 笔`),
            inventoryNode(
                'span', '',
                `${inventoryText(entry.updated_by || entry.created_by, '未知')} · ${inventoryText(entry.updated_at || entry.created_at, '时间未知')}`,
            ),
        );
        const input = inventoryNode('input', 'inventory-count-entry-input');
        input.type = 'number';
        input.inputMode = 'decimal';
        input.min = '0';
        input.step = 'any';
        const draftKey = `${barcode}:${entry.entry_id}`;
        const draft = countEntryDrafts.get(draftKey);
        if (draft && draft.version === entry.version) {
            input.value = draft.value;
        } else {
            input.value = inventoryText(entry.quantity, '');
            if (draft) {
                countEntryDrafts.delete(draftKey);
                setCountMessage('该笔数量已被其他设备修改，已显示最新数值，请核对后重新填写。', 'error');
            }
        }
        input.disabled = !countDialogEditable || countMutationPending;
        input.addEventListener('input', () => {
            countEntryDrafts.set(draftKey, {
                value: input.value,
                version: entry.version,
            });
        });
        const save = inventoryNode('button', 'btn btn-secondary', '保存');
        save.type = 'button';
        save.disabled = !countDialogEditable || countMutationPending;
        save.addEventListener('click', () => updateCountEntry(entry, input.value));
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') updateCountEntry(entry, input.value);
        });
        const remove = inventoryNode('button', 'btn btn-danger', '删除');
        remove.type = 'button';
        remove.disabled = !countDialogEditable || countMutationPending;
        remove.addEventListener('click', () => deleteCountEntry(entry));
        const actions = inventoryNode('div', 'inventory-count-entry-actions');
        actions.append(input, save, remove);
        row.append(copy, actions);
        entriesRoot.append(row);
    });
}

async function applyCountEntryResult(data, message) {
    acceptInventoryMutationVersion(data);
    currentCountItem = data.item;
    replaceInventoryItem(data.item);
    renderCountEntries(data.item);
    updateCountBook(data.item);
    renderInventoryItems(inventoryTask.items || []);
    lastInventoryVersion = null;
    const refreshed = await pollInventoryTask({force: true});
    setCountMessage(
        refreshed === false ? `${message} 已保存，但统计刷新失败。` : message,
        refreshed === false ? 'error' : 'success',
    );
}

function setCountMutationPending(pending) {
    countMutationPending = pending;
    if (currentCountItem) renderCountEntries(currentCountItem);
    inventoryElement('inventoryCountNewQuantity').disabled = !countDialogEditable || pending;
    inventoryElement('inventoryCountAdd').disabled = !countDialogEditable || pending;
}

async function reloadOpenCountItem(draft = '') {
    if (!currentCountItem || !inventoryTask) return;
    const data = await inventoryRequest(
        `/api/inventory/tasks/${encodeURIComponent(inventoryTask.task_id)}/items/${encodeURIComponent(currentCountItem.barcode)}/count-entries`,
    );
    acceptInventoryMutationVersion(data);
    currentCountItem = data.item;
    replaceInventoryItem(data.item);
    renderCountEntries(data.item);
    updateCountBook(data.item);
    inventoryElement('inventoryCountNewQuantity').value = draft;
}

async function handleCountEntryError(error, draft = '') {
    if (inventoryIsVersionConflict(error) || error.status === 404) {
        try {
            await reloadOpenCountItem(draft);
            setCountMessage('该笔数量已被其他设备修改，已刷新，请核对后重试。', 'error');
            return;
        } catch (refreshError) {
            setCountMessage(refreshError.message, 'error');
            return;
        }
    }
    setCountMessage(error.message, 'error');
}

async function openCountItem(barcode) {
    if (!inventoryTask) return;
    const item = (inventoryTask.items || []).find((row) => row.barcode === barcode);
    if (!item) return;
    if (item.data_error) {
        setInventoryNotice(`资料异常，不可盘：${item.data_error}`, 'error');
        return;
    }
    const requestId = ++countDialogRequestId;
    countEntryDrafts.clear();
    currentCountItem = item;
    countDialogEditable = false;
    renderCountProduct(item);
    updateCountBook(item);
    renderCountEntries(item);
    const input = inventoryElement('inventoryCountNewQuantity');
    input.value = '';
    input.disabled = true;
    inventoryElement('inventoryCountAdd').disabled = true;
    setCountMessage('正在读取 GYJ 当前账面数量。');
    const dialog = inventoryElement('inventoryCountDialog');
    if (!dialog.open) dialog.showModal();
    try {
        const data = await inventoryRequest(
            `/api/inventory/tasks/${encodeURIComponent(inventoryTask.task_id)}/items/${encodeURIComponent(barcode)}/count-entries`,
        );
        if (requestId !== countDialogRequestId || !dialog.open) return;
        acceptInventoryMutationVersion(data);
        currentCountItem = data.item;
        replaceInventoryItem(data.item);
        renderCountProduct(data.item);
        updateCountBook(data.item);
        renderCountEntries(data.item);
        input.disabled = false;
        inventoryElement('inventoryCountAdd').disabled = false;
        countDialogEditable = true;
        renderCountEntries(data.item);
        setCountMessage('已读取最新账面数量，可新增或修改任意一笔。', 'success');
        input.focus();
        renderLiveDifference();
    } catch (error) {
        if (requestId !== countDialogRequestId || !dialog.open) return;
        input.disabled = true;
        inventoryElement('inventoryCountAdd').disabled = true;
        setCountMessage(error.message, 'error');
    }
}

async function addCountEntry() {
    if (countMutationPending || !countDialogEditable || !currentCountItem || !inventoryTask) return;
    const input = inventoryElement('inventoryCountNewQuantity');
    const quantity = input.value.trim();
    if (!decimalParts(quantity)) {
        setCountMessage('请输入大于或等于 0 的有效数量。', 'error');
        input.focus();
        return;
    }
    setCountMutationPending(true);
    setCountMessage('正在保存这一笔数量并读取 GYJ 最新库存。');
    try {
        const data = await inventoryPost(
            `/api/inventory/tasks/${encodeURIComponent(inventoryTask.task_id)}/items/${encodeURIComponent(currentCountItem.barcode)}/count-entries`,
            {device_id: inventoryDeviceId, quantity},
        );
        input.value = '';
        await applyCountEntryResult(data, '已加入这一笔数量。');
    } catch (error) {
        await handleCountEntryError(error, quantity);
    } finally {
        setCountMutationPending(false);
        if (countDialogEditable) input.focus();
    }
}

async function updateCountEntry(entry, rawQuantity) {
    if (countMutationPending || !countDialogEditable || !currentCountItem || !inventoryTask) return;
    const quantity = String(rawQuantity || '').trim();
    if (!decimalParts(quantity)) {
        setCountMessage('请输入大于或等于 0 的有效数量。', 'error');
        return;
    }
    setCountMutationPending(true);
    try {
        const data = await inventoryPost(
            `/api/inventory/tasks/${encodeURIComponent(inventoryTask.task_id)}/items/${encodeURIComponent(currentCountItem.barcode)}/count-entries/${encodeURIComponent(entry.entry_id)}`,
            {device_id: inventoryDeviceId, quantity, entry_version: entry.version},
        );
        countEntryDrafts.delete(`${currentCountItem.barcode}:${entry.entry_id}`);
        await applyCountEntryResult(data, '该笔数量已更新。');
    } catch (error) {
        await handleCountEntryError(error, inventoryElement('inventoryCountNewQuantity').value);
    } finally {
        setCountMutationPending(false);
    }
}

async function deleteCountEntry(entry) {
    if (countMutationPending || !countDialogEditable || !currentCountItem || !inventoryTask) return;
    setCountMutationPending(true);
    try {
        const data = await inventoryDelete(
            `/api/inventory/tasks/${encodeURIComponent(inventoryTask.task_id)}/items/${encodeURIComponent(currentCountItem.barcode)}/count-entries/${encodeURIComponent(entry.entry_id)}`,
            {device_id: inventoryDeviceId, entry_version: entry.version},
        );
        countEntryDrafts.delete(`${currentCountItem.barcode}:${entry.entry_id}`);
        await applyCountEntryResult(data, '该笔数量已删除。');
    } catch (error) {
        await handleCountEntryError(error, inventoryElement('inventoryCountNewQuantity').value);
    } finally {
        setCountMutationPending(false);
    }
}

function runInventorySearch() {
    if (inventorySearchTimer) clearTimeout(inventorySearchTimer);
    inventorySearchTimer = null;
    inventoryQueryGeneration += 1;
    lastInventoryVersion = null;
    return pollInventoryTask({force: true});
}

function pollInventorySearch() {
    inventorySearchTimer = null;
    return pollInventoryTask({force: true});
}

function handleInventorySearchInput() {
    if (inventorySearchTimer) clearTimeout(inventorySearchTimer);
    inventorySearchTimer = null;
    const query = inventoryElement('inventorySearch').value.trim();
    inventoryQueryGeneration += 1;
    lastInventoryVersion = null;
    if (!query) {
        pollInventoryTask({force: true});
        return;
    }
    inventorySearchTimer = setTimeout(pollInventorySearch, 250);
}

async function handleInventorySearchEnter(event) {
    if (event.key === 'Enter') {
        event.preventDefault();
        if (inventorySearchTimer) clearTimeout(inventorySearchTimer);
        inventorySearchTimer = null;
        inventoryQueryGeneration += 1;
        lastInventoryVersion = null;
        await pollInventoryTask({force: true});
        const query = inventoryElement('inventorySearch').value.trim();
        const item = inventoryTask && (inventoryTask.items || []).find((item) => item.barcode === query);
        if (item) await openCountItem(item.barcode);
        else setInventoryNotice('没有找到完全匹配的商品条码。', 'error');
    }
}

function setSerialMessage(message, kind = '') {
    const target = inventoryElement('inventorySerialMessage');
    target.textContent = message || '';
    target.className = 'inventory-dialog-message' + (kind ? ` is-${kind}` : '');
}

function setInventoryCameraMessage(message, kind = '') {
    const target = inventoryElement('inventoryCameraMessage');
    if (!target) return;
    target.textContent = message || '';
    target.className = 'inventory-camera-message' + (kind ? ` is-${kind}` : '');
}

function inventoryCameraCanUseHttp() {
    const hostname = window.location && String(window.location.hostname || '').toLowerCase();
    return window.isSecureContext || hostname === 'localhost' || hostname === '127.0.0.1'
        || hostname === '::1' || hostname === '[::1]';
}

function stopInventoryCamera() {
    inventoryCameraGeneration += 1;
    const controls = inventoryCameraControls;
    inventoryCameraControls = null;
    if (controls && typeof controls.stop === 'function') {
        try {
            controls.stop();
        } catch (_error) {
            // Media tracks below are the final cleanup path.
        }
    }
    const video = inventoryElement('inventoryCameraVideo');
    if (video && video.srcObject && typeof video.srcObject.getTracks === 'function') {
        video.srcObject.getTracks().forEach((track) => track.stop());
        video.srcObject = null;
    }
    const panel = inventoryElement('inventoryCameraPanel');
    if (panel) panel.hidden = true;
    const start = inventoryElement('inventoryCameraStart');
    const stop = inventoryElement('inventoryCameraStop');
    if (start) start.disabled = false;
    if (stop) stop.disabled = true;
    inventoryCameraLastSerial = '';
    inventoryCameraLastDecodedAt = 0;
}

async function startInventoryCamera() {
    stopInventoryCamera();
    if (!inventoryCameraCanUseHttp()) {
        setInventoryCameraMessage('相机连续扫码需要 HTTPS；仍可使用扫码枪或键盘输入。', 'error');
        return;
    }
    if (!serialWorkspaceOpen || !serialWorkspaceEditable) return;
    if (!window.ZXingBrowser || !window.ZXingBrowser.BrowserMultiFormatReader) {
        setInventoryCameraMessage('相机扫码组件未能载入；仍可使用扫码枪或键盘输入。', 'error');
        return;
    }

    const generation = ++inventoryCameraGeneration;
    const start = inventoryElement('inventoryCameraStart');
    const stop = inventoryElement('inventoryCameraStop');
    const panel = inventoryElement('inventoryCameraPanel');
    const video = inventoryElement('inventoryCameraVideo');
    start.disabled = true;
    stop.disabled = false;
    panel.hidden = false;
    setInventoryCameraMessage('正在启动后置相机…');
    try {
        const reader = new window.ZXingBrowser.BrowserMultiFormatReader();
        const controls = await reader.decodeFromConstraints(
            {video: {facingMode: {ideal: 'environment'}}},
            video,
            (result) => {
                if (!result || generation !== inventoryCameraGeneration) return;
                const serial = String(result.getText ? result.getText() : result.text || '').trim();
                if (!serial) return;
                const decodedAt = Date.now();
                if (serial === inventoryCameraLastSerial && decodedAt - inventoryCameraLastDecodedAt < 1500) return;
                inventoryCameraLastSerial = serial;
                inventoryCameraLastDecodedAt = decodedAt;
                submitDecodedSerial(serial);
            },
        );
        if (generation !== inventoryCameraGeneration || !serialWorkspaceOpen) {
            if (controls && typeof controls.stop === 'function') controls.stop();
            if (video.srcObject && typeof video.srcObject.getTracks === 'function') {
                video.srcObject.getTracks().forEach((track) => track.stop());
                video.srcObject = null;
            }
            return;
        }
        inventoryCameraControls = controls;
        setInventoryCameraMessage('相机连续扫码已开启。', 'success');
    } catch (error) {
        if (generation !== inventoryCameraGeneration) return;
        stopInventoryCamera();
        if (error && error.name === 'NotAllowedError') {
            setInventoryCameraMessage('无法使用相机：请允许相机权限。仍可使用扫码枪或键盘输入。', 'error');
        } else if (error && error.name === 'NotFoundError') {
            setInventoryCameraMessage('未找到可用相机；仍可使用扫码枪或键盘输入。', 'error');
        } else {
            setInventoryCameraMessage('相机启动失败；仍可使用扫码枪或键盘输入。', 'error');
        }
    }
}

function resetSerialOperations() {
    serialOperationGeneration += 1;
    serialRenderedGeneration = serialOperationGeneration;
    serialOperationQueue = Promise.resolve();
    serialScanQueueLength = 0;
    serialScanPending = false;
    serialFinishPending = false;
    serialMutationFailures.clear();
}

function serialOperationIsOpen(requestId) {
    return requestId === serialWorkspaceRequestId && serialWorkspaceOpen;
}

function queueSerialOperation(operation) {
    const requestId = serialWorkspaceRequestId;
    const generation = ++serialOperationGeneration;
    const run = async () => {
        if (!serialOperationIsOpen(requestId)) return null;
        return operation(requestId, generation);
    };
    const pending = serialOperationQueue.then(run, run);
    serialOperationQueue = pending.catch(() => null);
    return pending;
}

function renderSerialOperation(value, requestId, generation) {
    if (!serialOperationIsOpen(requestId) || generation < serialRenderedGeneration) return false;
    serialRenderedGeneration = generation;
    renderSerialReconciliation(value);
    return true;
}

function serialMutationKey(kind, serial) {
    return `${kind}:${serial}`;
}

function restoreSerialAfterFailedFinish(message) {
    serialFinishPending = false;
    if (!serialWorkspaceOpen || !serialWorkspaceEditable) return;
    const input = inventoryElement('inventorySerialInput');
    input.disabled = false;
    inventoryElement('inventorySerialRefresh').disabled = false;
    inventoryElement('inventorySerialFinish').disabled = false;
    setSerialMessage(message, 'error');
    input.focus();
}

function serialClassificationLabel(classification) {
    const labels = {
        matched: '匹配',
        system_only: '账面独有',
        physical_only: '实物独有',
        unknown: '实物独有',
        already_shipped: '实物独有（已出库）',
        other_product: '其他商品',
        duplicate: '重复扫描',
    };
    return labels[classification] || inventoryText(classification);
}

function serialListRow(row, canDelete) {
    const item = inventoryNode('li', 'inventory-serial-row');
    const copy = inventoryNode('div', 'inventory-serial-copy');
    copy.append(
        inventoryNode('strong', '', row.serial),
        inventoryNode('span', '', [
            row.lookup_barcode ? `查询商品 ${row.lookup_barcode}` : '',
            row.lookup_name ? row.lookup_name : '',
            row.warehouse ? `仓库 ${row.warehouse}` : '',
            row.shipped ? '已出库' : '',
        ].filter(Boolean).join(' · ')),
    );
    item.append(copy);
    if (canDelete) {
        const remove = inventoryNode('button', 'btn btn-secondary inventory-serial-remove', '删除误扫');
        remove.type = 'button';
        remove.addEventListener('click', () => deleteSerialScan(row.serial));
        item.append(remove);
    }
    return item;
}

function renderSerialReconciliation(value) {
    currentSerialData = value || {};
    const item = currentSerialData.item || {};
    inventoryElement('inventorySerialSyncedAt').textContent =
        `最近获取：${inventoryText(item.serial_synced_at, '尚未获取')}`;
    const product = inventoryElement('inventorySerialProduct');
    product.replaceChildren();
    product.append(
        inventoryNode('strong', 'inventory-item-name', item.name),
        inventoryNode('div', 'inventory-item-barcode', currentSerialData.barcode || item.barcode),
        inventoryNode('div', '', inventoryDetailText(item)),
    );

    const groups = [
        ['matched', '匹配', true],
        ['system_only', '账面独有', false],
        ['physical_only', '实物独有', true],
        ['other_product', '其他商品', true],
        ['duplicates', '重复扫描', false],
    ];
    const counts = inventoryElement('inventorySerialCounts');
    counts.replaceChildren();
    groups.forEach(([key, label]) => {
        const rows = Array.isArray(currentSerialData[key]) ? currentSerialData[key] : [];
        const count = currentSerialData.counts && currentSerialData.counts[key] !== undefined
            ? currentSerialData.counts[key]
            : rows.length;
        counts.append(inventoryMetric(label, count));
    });

    const details = inventoryElement('inventorySerialDetails');
    details.replaceChildren();
    groups.forEach(([key, label, canDelete]) => {
        const rows = Array.isArray(currentSerialData[key]) ? currentSerialData[key] : [];
        const section = inventoryNode('section', 'inventory-serial-group');
        const heading = inventoryNode('h3', '', `${label} (${rows.length})`);
        const list = inventoryNode('ul', 'inventory-serial-list');
        if (rows.length) rows.forEach((row) => list.append(serialListRow(row, canDelete)));
        else list.append(inventoryNode('li', 'inventory-empty', '暂无记录'));
        section.append(heading, list);
        details.append(section);
    });
}

function closeSerialWorkspace() {
    stopInventoryCamera();
    serialWorkspaceRequestId += 1;
    resetSerialOperations();
    serialWorkspaceOpen = false;
    serialWorkspaceEditable = false;
    currentSerialBarcode = '';
    currentSerialData = null;
    const dialog = inventoryElement('inventorySerialWorkspace');
    if (dialog.open) dialog.close();
}

async function openSerialItem(barcode) {
    if (!inventoryTask || !['counting', 'serial_check'].includes(inventoryTask.phase)) return;
    const item = (inventoryTask.items || []).find((row) => row.barcode === barcode);
    if (!item || item.state !== 'serial_pending') return;
    stopInventoryCamera();
    const requestId = ++serialWorkspaceRequestId;
    resetSerialOperations();
    const dialog = inventoryElement('inventorySerialWorkspace');
    currentSerialBarcode = barcode;
    currentSerialData = null;
    serialWorkspaceOpen = true;
    serialWorkspaceEditable = false;
    inventoryElement('inventorySerialInput').value = '';
    inventoryElement('inventorySerialInput').disabled = true;
    inventoryElement('inventorySerialRefresh').disabled = true;
    inventoryElement('inventorySerialFinish').disabled = true;
    inventoryElement('inventoryCameraStart').disabled = true;
    renderSerialReconciliation({barcode, item, counts: {}});
    setSerialMessage('正在读取已缓存的 GYJ 账面序列号…');
    if (!dialog.open) dialog.showModal();
    try {
        const data = await inventoryPost(
            `/api/inventory/tasks/${encodeURIComponent(inventoryTask.task_id)}/items/${encodeURIComponent(barcode)}/serial/open`,
            {device_id: inventoryDeviceId},
        );
        if (requestId !== serialWorkspaceRequestId || !serialWorkspaceOpen || !dialog.open) return;
        acceptInventoryMutationVersion(data);
        renderSerialReconciliation(data.serial);
        serialWorkspaceEditable = true;
        const input = inventoryElement('inventorySerialInput');
        input.disabled = false;
        inventoryElement('inventorySerialRefresh').disabled = false;
        inventoryElement('inventorySerialFinish').disabled = false;
        inventoryElement('inventoryCameraStart').disabled = false;
        setSerialMessage('已读取账面序列号缓存，可以开始扫描。', 'success');
        input.focus();
    } catch (_error) {
        if (requestId !== serialWorkspaceRequestId || !serialWorkspaceOpen || !dialog.open) return;
        serialWorkspaceEditable = false;
        inventoryElement('inventorySerialInput').disabled = true;
        inventoryElement('inventorySerialRefresh').disabled = true;
        inventoryElement('inventorySerialFinish').disabled = true;
        inventoryElement('inventoryCameraStart').disabled = true;
        setSerialMessage('暂时无法读取账面序列号缓存，请关闭窗口后重试。', 'error');
    }
}

async function performSerialRefresh(force, requestId, generation) {
    if (!serialWorkspaceOpen || !serialWorkspaceEditable || !currentSerialBarcode || !inventoryTask) return null;
    try {
        const data = await inventoryPost(
            `/api/inventory/tasks/${encodeURIComponent(inventoryTask.task_id)}/items/${encodeURIComponent(currentSerialBarcode)}/serial/refresh`,
            {device_id: inventoryDeviceId, force: force === true},
        );
        acceptInventoryMutationVersion(data);
        if (!renderSerialOperation(data.serial, requestId, generation)) return null;
        return data.serial;
    } catch (error) {
        if (!serialOperationIsOpen(requestId)) return null;
        setSerialMessage('暂时无法读取账面序列号缓存，请稍后重试。', 'error');
        throw error;
    }
}

function refreshSerialItem(force = false) {
    if (!serialWorkspaceOpen || !serialWorkspaceEditable || serialFinishPending || !currentSerialBarcode || !inventoryTask) {
        return Promise.resolve(null);
    }
    return queueSerialOperation((requestId, generation) => (
        performSerialRefresh(force, requestId, generation)
    ));
}

async function manualRefreshSerialItem() {
    const button = inventoryElement('inventorySerialRefresh');
    button.disabled = true;
    setSerialMessage('正在重新获取 GYJ 账面序列号…');
    try {
        await refreshSerialItem(true);
        setSerialMessage('账面序列号已重新获取。', 'success');
    } catch (_error) {
        setSerialMessage('重新获取失败。已保留上次成功获取的数据，请确认 GYJ 已登录后重试。', 'error');
    } finally {
        button.disabled = false;
        inventoryElement('inventorySerialInput').focus();
    }
}

async function scanSerial(event) {
    if (!event || event.key !== 'Enter') return;
    event.preventDefault();
    const input = inventoryElement('inventorySerialInput');
    if (!serialWorkspaceEditable || serialFinishPending || !currentSerialBarcode || !inventoryTask) {
        input.focus();
        return;
    }
    const serial = input.value.trim();
    if (!serial) {
        input.focus();
        return;
    }
    input.value = '';
    return submitDecodedSerial(serial);
}

async function submitDecodedSerial(serial) {
    serial = String(serial || '').trim();
    const input = inventoryElement('inventorySerialInput');
    if (!serialWorkspaceEditable || serialFinishPending || !currentSerialBarcode || !inventoryTask || !serial) {
        if (input) input.focus();
        return;
    }
    const taskId = inventoryTask.task_id;
    const barcode = currentSerialBarcode;
    serialScanQueueLength += 1;
    serialScanPending = true;
    const mutationKey = serialMutationKey('scan', serial);
    return queueSerialOperation(async (requestId, generation) => {
        try {
            if (!serialWorkspaceEditable) return;
            let data;
            try {
                data = await inventoryPost(
                    `/api/inventory/tasks/${encodeURIComponent(taskId)}/items/${encodeURIComponent(barcode)}/serials`,
                    {device_id: inventoryDeviceId, serial},
                );
                acceptInventoryMutationVersion(data);
            } catch (error) {
                if (serialOperationIsOpen(requestId)) {
                    serialMutationFailures.set(mutationKey, '扫码保存失败，请重试。');
                    setSerialMessage('扫码保存失败，请重试。', 'error');
                }
                return;
            }
            if (!serialOperationIsOpen(requestId)) return;
            serialMutationFailures.delete(mutationKey);
            const scan = data.scan || {};
            if (scan.classification === 'duplicate') {
                const duplicates = Array.isArray(currentSerialData && currentSerialData.duplicates)
                    ? currentSerialData.duplicates.slice()
                    : [];
                duplicates.push(scan);
                renderSerialOperation({
                    ...currentSerialData,
                    duplicates,
                    counts: {...(currentSerialData.counts || {}), duplicates: duplicates.length},
                }, requestId, generation);
            } else {
                try {
                    await performSerialRefresh(false, requestId, generation);
                } catch (_error) {
                    return;
                }
            }
            if (serialOperationIsOpen(requestId)) {
                setSerialMessage(`${inventoryText(scan.serial, serial)}：${serialClassificationLabel(scan.classification)}`, scan.classification === 'matched' ? 'success' : '');
            }
        } finally {
            if (requestId === serialWorkspaceRequestId) {
                serialScanQueueLength = Math.max(0, serialScanQueueLength - 1);
                serialScanPending = serialScanQueueLength > 0;
            }
            if (serialOperationIsOpen(requestId) && serialWorkspaceEditable && !serialFinishPending && input) input.focus();
        }
    });
}

function deleteSerialScan(serial) {
    if (!serialWorkspaceEditable || serialFinishPending || !currentSerialBarcode || !inventoryTask) return;
    const taskId = inventoryTask.task_id;
    const barcode = currentSerialBarcode;
    const mutationKey = serialMutationKey('delete', serial);
    return queueSerialOperation(async (requestId, generation) => {
        try {
            if (!serialWorkspaceEditable) return;
            let data;
            try {
                data = await inventoryDelete(
                    `/api/inventory/tasks/${encodeURIComponent(taskId)}/items/${encodeURIComponent(barcode)}/serials/${encodeURIComponent(serial)}`,
                    {device_id: inventoryDeviceId},
                );
                acceptInventoryMutationVersion(data);
            } catch (error) {
                if (serialOperationIsOpen(requestId)) {
                    serialMutationFailures.set(mutationKey, error.message || '删除失败');
                    setSerialMessage(error.message, 'error');
                }
                return;
            }
            if (!serialOperationIsOpen(requestId)) return;
            serialMutationFailures.delete(mutationKey);
            if (!renderSerialOperation(data.serial, requestId, generation)) return;
            setSerialMessage(`${serial} 已删除，操作已记录。`, 'success');
        } finally {
            if (serialOperationIsOpen(requestId) && serialWorkspaceEditable && !serialFinishPending) {
                inventoryElement('inventorySerialInput').focus();
            }
        }
    });
}

function finishSerialItem() {
    if (!serialWorkspaceEditable || serialFinishPending || !currentSerialBarcode || !inventoryTask) return;
    serialFinishPending = true;
    const input = inventoryElement('inventorySerialInput');
    const refresh = inventoryElement('inventorySerialRefresh');
    const finish = inventoryElement('inventorySerialFinish');
    input.disabled = true;
    refresh.disabled = true;
    finish.disabled = true;
    setSerialMessage('正在使用已缓存的 GYJ 账面序列号完成核对…');
    const taskId = inventoryTask.task_id;
    const barcode = currentSerialBarcode;
    return queueSerialOperation(async (requestId, generation) => {
        try {
            if (!serialWorkspaceEditable) return;
            const failedMutation = serialMutationFailures.values().next();
            if (!failedMutation.done) {
                restoreSerialAfterFailedFinish(
                    `未能完成核对：${failedMutation.value}。请重试失败的扫码或删除操作。`
                );
                return;
            }
            const prefix = `/api/inventory/tasks/${encodeURIComponent(taskId)}/items/${encodeURIComponent(barcode)}`;
            const data = await inventoryPost(`${prefix}/serial/finish`, {device_id: inventoryDeviceId});
            acceptInventoryMutationVersion(data);
            if (!renderSerialOperation(data.serial, requestId, generation)) return;
            closeSerialWorkspace();
            lastInventoryVersion = null;
            setInventoryNotice('序列号核对已完成。', 'success');
            await pollInventoryTask({force: true});
        } catch (error) {
            if (!serialOperationIsOpen(requestId)) return;
            restoreSerialAfterFailedFinish(
                `未能完成核对：${error.message}。当前扫描界面已保留。`
            );
        }
    });
}

function setWorkspaceStatus(id, message, kind = '') {
    const target = inventoryElement(id);
    target.textContent = message || '';
    target.className = 'inventory-notice' + (kind ? ` is-${kind}` : '');
}

function inventoryHistoryCount(value) {
    const count = Number(value);
    return Number.isFinite(count) && count >= 0 ? count : 0;
}

function inventorySerialAuditValue(serial, classification) {
    if (!serial) return '无';
    const labels = {
        matched: '账实一致',
        system_only: '账面独有',
        physical_only: '实物独有',
        other_product: '其他商品',
        already_shipped: '已出库',
        unknown: '未知',
    };
    const label = labels[classification] || inventoryText(classification, '未分类');
    return `${serial}（${label}）`;
}

function renderInventoryAudit(events) {
    const root = inventoryElement('inventoryAuditEvents');
    root.replaceChildren();
    if (!events.length) {
        root.append(inventoryNode('div', 'inventory-empty', '暂无修改记录。'));
        return;
    }
    events.forEach((event) => {
        const row = inventoryNode('article', 'inventory-audit-event');
        let title = inventoryText(event.event_label, event.event_type);
        const countEntryAction = {
            count_entry_added: '新增',
            count_entry_updated: '修改',
            count_entry_deleted: '删除',
        }[event.event_type];
        if (countEntryAction && Number.isInteger(event.entry_number) && event.entry_number > 0) {
            title = `${countEntryAction}第 ${event.entry_number} 笔数量`;
        }
        row.append(
            inventoryNode('strong', '', title),
            inventoryNode('span', '', `${inventoryText(event.actor, '未知账号')} · ${inventoryText(event.created_at, '时间未知')}`),
        );
        if (event.before_quantity !== null || event.after_quantity !== null) {
            row.append(inventoryNode(
                'code', '',
                `数量 ${inventoryText(event.before_quantity, '无')} → ${inventoryText(event.after_quantity, '无')}`,
            ));
        }
        if (event.before_serial !== null || event.after_serial !== null) {
            row.append(inventoryNode(
                'code', '',
                `序列号 ${inventorySerialAuditValue(event.before_serial, event.before_classification)} → ${inventorySerialAuditValue(event.after_serial, event.after_classification)}`,
            ));
        }
        root.append(row);
    });
}

async function openInventoryAudit(taskId, barcode = '') {
    const dialog = inventoryElement('inventoryAuditDialog');
    inventoryElement('inventoryAuditTitle').textContent = barcode
        ? `修改记录 · ${barcode}` : '任务修改记录';
    inventoryElement('inventoryAuditStatus').textContent = '正在读取修改记录…';
    inventoryElement('inventoryAuditEvents').replaceChildren();
    if (!dialog.open) dialog.showModal();
    try {
        const params = new URLSearchParams();
        if (barcode) params.set('barcode', barcode);
        const suffix = params.size ? `?${params.toString()}` : '';
        const data = await inventoryRequest(
            `/api/inventory/tasks/${encodeURIComponent(taskId)}/audit${suffix}`
        );
        renderInventoryAudit(Array.isArray(data.events) ? data.events : []);
        inventoryElement('inventoryAuditStatus').textContent = '';
    } catch (error) {
        inventoryElement('inventoryAuditStatus').textContent = error.message;
    }
}

function closeInventoryAudit() {
    const dialog = inventoryElement('inventoryAuditDialog');
    if (dialog.open) dialog.close();
}

async function reopenInventoryTask(taskId) {
    setWorkspaceStatus('inventoryHistoryStatus', '正在读取 GYJ 最新库存并继续盘点…');
    try {
        await inventoryPost(
            `/api/inventory/tasks/${encodeURIComponent(taskId)}/reopen`, {}
        );
        inventoryHistoryTasks = [];
        inventoryHistoryHasMore = false;
        lastInventoryVersion = null;
        await switchInventoryTab('current');
        await pollInventoryTask({force: true});
        setInventoryNotice('已恢复历史任务，可继续盘点。', 'success');
    } catch (error) {
        setWorkspaceStatus('inventoryHistoryStatus', error.message, 'error');
    }
}

function renderInventoryHistory(tasks) {
    const root = inventoryElement('inventoryHistory');
    root.replaceChildren();
    if (!tasks.length) {
        root.append(inventoryNode('div', 'inventory-empty', '暂无已完成的盘点任务。'));
        return;
    }
    tasks.forEach((task) => {
        const row = inventoryNode('article', 'inventory-history-row');
        const title = inventoryNode('div', 'inventory-history-title');
        title.append(
            inventoryNode('strong', '', `任务号 ${inventoryText(task.task_id)}`),
            inventoryNode('span', '', `开始时间 ${inventoryText(task.started_at)}`),
            inventoryNode('span', '', `完成时间 ${inventoryText(task.completed_at)}`),
        );
        const metrics = inventoryNode('div', 'inventory-history-metrics');
        metrics.append(
            inventoryMetric('参与人数', inventoryHistoryCount(task.participant_count)),
            inventoryMetric('商品总数', inventoryHistoryCount(task.product_total)),
            inventoryMetric('数量差异', inventoryHistoryCount(task.quantity_difference_count)),
            inventoryMetric('序列号差异', inventoryHistoryCount(task.serial_difference_count)),
        );
        const download = inventoryNode('a', 'btn btn-secondary inventory-history-export', '下载 Excel');
        download.href = `/api/inventory/tasks/${encodeURIComponent(task.task_id)}/export`;
        const actions = inventoryNode('div', 'inventory-history-actions');
        const audit = inventoryNode('button', 'btn btn-secondary', '修改记录');
        audit.type = 'button';
        audit.addEventListener('click', () => openInventoryAudit(task.task_id));
        actions.append(download, audit);
        if (CURRENT_ACCOUNT && CURRENT_ACCOUNT.is_admin) {
            const reopen = inventoryNode('button', 'btn btn-primary', '继续盘点');
            reopen.type = 'button';
            reopen.addEventListener('click', () => reopenInventoryTask(task.task_id));
            actions.append(reopen);
        }
        row.append(title, metrics, actions);
        root.append(row);
    });
}

async function loadInventoryHistory(expectedGeneration = inventoryTabGeneration, reset = true) {
    const button = inventoryElement('inventoryHistoryLoadMore');
    const offset = reset ? 0 : inventoryHistoryTasks.length;
    setWorkspaceStatus('inventoryHistoryStatus', reset ? '正在读取历史任务…' : '正在加载更多历史任务…');
    button.disabled = true;
    try {
        const data = await inventoryRequest(
            `/api/inventory/tasks/history?limit=20&offset=${offset}`
        );
        if (expectedGeneration !== inventoryTabGeneration || inventoryActiveTab !== 'history') return;
        const tasks = Array.isArray(data.tasks) ? data.tasks : [];
        inventoryHistoryTasks = reset ? tasks : inventoryHistoryTasks.concat(tasks);
        const pagination = data.pagination || {};
        inventoryHistoryHasMore = pagination.has_more === true;
        renderInventoryHistory(inventoryHistoryTasks);
        button.hidden = !inventoryHistoryHasMore;
        setWorkspaceStatus('inventoryHistoryStatus', '');
    } catch (error) {
        if (expectedGeneration !== inventoryTabGeneration || inventoryActiveTab !== 'history') return;
        if (reset) {
            inventoryHistoryTasks = [];
            inventoryHistoryHasMore = false;
            inventoryElement('inventoryHistory').replaceChildren();
            button.hidden = true;
        }
        setWorkspaceStatus('inventoryHistoryStatus', error.message, 'error');
    } finally {
        if (expectedGeneration === inventoryTabGeneration && inventoryActiveTab === 'history') {
            button.disabled = false;
        }
    }
}

function discrepancyKindLabel(kind) {
    const labels = {
        product_quantity: '商品数量差异',
        system_only_serial: '账面独有',
        physical_only_serial: '实物独有',
        other_product_serial: '其他商品',
        already_shipped_serial: '已出库序列号',
        unknown_serial: '未知序列号',
        serial_unverified: '序列号未核对',
    };
    return labels[kind] || inventoryText(kind);
}

function discrepancyNoteInputId(id, serial) {
    return `inventoryDiscrepancyNote-${id}-${serial ? encodeURIComponent(serial) : 'product'}`;
}

function renderDiscrepancyRows(rows, state) {
    const root = inventoryElement(state === 'archived' ? 'inventoryDifferencesArchived' : 'inventoryDifferencesOpen');
    root.replaceChildren();
    if (!rows.length) {
        root.append(inventoryNode('div', 'inventory-empty', state === 'archived' ? '暂无已归档差异。' : '暂无待处理差异。'));
        return;
    }
    rows.forEach((row) => {
        const card = inventoryNode('article', 'inventory-discrepancy-card');
        const head = inventoryNode('div', 'inventory-discrepancy-head');
        const identity = inventoryNode('div', 'inventory-history-title');
        identity.append(
            inventoryNode('strong', '', `${inventoryText(row.barcode)} · ${inventoryText(row.name, '未命名商品')}`),
            inventoryNode('span', '', `${discrepancyKindLabel(row.kind)} · 任务 ${inventoryText(row.task_id)}`),
        );
        if (row.serial !== null && row.serial !== undefined && row.serial !== '') {
            identity.append(inventoryNode('code', 'inventory-discrepancy-serial', row.serial));
        } else {
            identity.append(inventoryNode('span', '', `账面 ${inventoryText(row.book_quantity)} · 实盘 ${inventoryText(row.counted_quantity)} · 差异 ${inventoryText(row.difference)}`));
        }
        head.append(identity);
        if (CURRENT_ACCOUNT && CURRENT_ACCOUNT.is_admin) {
            const action = inventoryNode('button', 'btn btn-secondary', state === 'archived' ? '恢复' : '归档');
            action.type = 'button';
            action.addEventListener('click', () => state === 'archived'
                ? restoreDiscrepancy(row.id, row.task_version)
                : archiveDiscrepancy(row.id, row.task_version));
            head.append(action);
        }

        const history = inventoryNode('div', 'inventory-note-history');
        const notes = Array.isArray(row.notes) ? row.notes : [];
        history.append(inventoryNode('h3', '', row.serial ? '序列号备注历史' : '商品备注历史'));
        if (!notes.length) history.append(inventoryNode('p', 'inventory-empty', '暂无备注。'));
        notes.forEach((note) => {
            const entry = inventoryNode('div', 'inventory-note-entry');
            entry.append(
                inventoryNode('p', '', note.note),
                inventoryNode('span', '', `${inventoryText(note.actor, '未知账号')} · ${inventoryText(note.created_at)}`),
            );
            history.append(entry);
        });

        const form = inventoryNode('div', 'inventory-note-form');
        const input = inventoryNode('input', '');
        input.id = discrepancyNoteInputId(row.id, row.serial);
        input.type = 'text';
        input.autocomplete = 'off';
        input.placeholder = row.serial ? '追加序列号备注' : '追加商品备注';
        input.setAttribute('aria-label', input.placeholder);
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') saveDiscrepancyNote(row.id, row.serial, row.task_version);
        });
        const save = inventoryNode('button', 'btn btn-primary', '追加备注');
        save.type = 'button';
        save.addEventListener('click', () => saveDiscrepancyNote(row.id, row.serial, row.task_version));
        form.append(input, save);
        card.append(head, history, form);
        root.append(card);
    });
}

async function loadDiscrepancies(expectedTabGeneration = inventoryTabGeneration) {
    const requestGeneration = ++inventoryDifferenceGeneration;
    const state = inventoryDifferenceState;
    const query = inventoryElement('inventoryDifferenceSearch').value.trim();
    const params = new URLSearchParams({state});
    if (query) params.set('query', query);
    setWorkspaceStatus('inventoryDifferencesStatus', '正在读取差异记录…');
    try {
        const data = await inventoryRequest(`/api/inventory/discrepancies?${params.toString()}`);
        if (
            expectedTabGeneration !== inventoryTabGeneration
            || requestGeneration !== inventoryDifferenceGeneration
            || inventoryActiveTab !== 'differences'
            || state !== inventoryDifferenceState
            || query !== inventoryElement('inventoryDifferenceSearch').value.trim()
        ) return;
        renderDiscrepancyRows(Array.isArray(data.discrepancies) ? data.discrepancies : [], state);
        setWorkspaceStatus('inventoryDifferencesStatus', '');
    } catch (error) {
        if (expectedTabGeneration !== inventoryTabGeneration || requestGeneration !== inventoryDifferenceGeneration || inventoryActiveTab !== 'differences') return;
        const root = inventoryElement(state === 'archived' ? 'inventoryDifferencesArchived' : 'inventoryDifferencesOpen');
        root.replaceChildren();
        setWorkspaceStatus('inventoryDifferencesStatus', error.message, 'error');
    }
}

async function handleDiscrepancyMutationError(error) {
    if (inventoryIsVersionConflict(error)) {
        await loadDiscrepancies();
        setWorkspaceStatus(
            'inventoryDifferencesStatus',
            '差异内容已刷新，请核对后重试。',
            'error',
        );
        return;
    }
    setWorkspaceStatus('inventoryDifferencesStatus', error.message, 'error');
}

async function saveDiscrepancyNote(id, serial = null, taskVersion = null) {
    const input = inventoryElement(discrepancyNoteInputId(id, serial));
    const note = input.value.trim();
    if (!note) {
        input.focus();
        return;
    }
    input.disabled = true;
    const body = {note};
    if (serial !== null && serial !== undefined && serial !== '') body.serial = serial;
    try {
        await inventoryMutationPost(
            `/api/inventory/discrepancies/${encodeURIComponent(id)}/notes`,
            body, taskVersion, false,
        );
        input.value = '';
        await loadDiscrepancies();
    } catch (error) {
        await handleDiscrepancyMutationError(error);
    } finally {
        input.disabled = false;
    }
}

async function archiveDiscrepancy(id, taskVersion) {
    try {
        await inventoryMutationPost(
            `/api/inventory/discrepancies/${encodeURIComponent(id)}/archive`,
            {}, taskVersion, false,
        );
        await loadDiscrepancies();
    } catch (error) {
        await handleDiscrepancyMutationError(error);
    }
}

async function restoreDiscrepancy(id, taskVersion) {
    try {
        await inventoryMutationPost(
            `/api/inventory/discrepancies/${encodeURIComponent(id)}/restore`,
            {}, taskVersion, false,
        );
        await loadDiscrepancies();
    } catch (error) {
        await handleDiscrepancyMutationError(error);
    }
}

function exportDiscrepancies() {
    const params = new URLSearchParams({state: inventoryDifferenceState});
    const query = inventoryElement('inventoryDifferenceSearch').value.trim();
    if (query) params.set('query', query);
    window.location.assign(`/api/inventory/discrepancies/export?${params.toString()}`);
}

function switchDifferenceState(state) {
    inventoryDifferenceState = state === 'archived' ? 'archived' : 'open';
    inventoryDifferenceGeneration += 1;
    const open = inventoryDifferenceState === 'open';
    inventoryElement('inventoryDifferencesOpen').hidden = !open;
    inventoryElement('inventoryDifferencesArchived').hidden = open;
    inventoryElement('inventoryDifferenceTabOpen').classList.toggle('is-active', open);
    inventoryElement('inventoryDifferenceTabOpen').setAttribute('aria-selected', String(open));
    inventoryElement('inventoryDifferenceTabOpen').tabIndex = open ? 0 : -1;
    inventoryElement('inventoryDifferenceTabArchived').classList.toggle('is-active', !open);
    inventoryElement('inventoryDifferenceTabArchived').setAttribute('aria-selected', String(!open));
    inventoryElement('inventoryDifferenceTabArchived').tabIndex = open ? -1 : 0;
    return loadDiscrepancies();
}

function handleDifferenceSearch() {
    if (inventoryDifferenceSearchTimer) clearTimeout(inventoryDifferenceSearchTimer);
    inventoryDifferenceSearchTimer = setTimeout(() => {
        inventoryDifferenceSearchTimer = null;
        loadDiscrepancies();
    }, 250);
}

function switchInventoryTab(tab) {
    const selected = ['current', 'history', 'differences'].includes(tab) ? tab : 'current';
    inventoryActiveTab = selected;
    const generation = ++inventoryTabGeneration;
    if (selected !== 'current') closeSerialWorkspace();
    const definitions = [
        ['current', 'inventoryCurrentRoot', 'inventoryTabCurrent'],
        ['history', 'inventoryHistoryRoot', 'inventoryTabHistory'],
        ['differences', 'inventoryDiscrepanciesRoot', 'inventoryTabDifferences'],
    ];
    definitions.forEach(([name, panelId, tabId]) => {
        const active = name === selected;
        inventoryElement(panelId).hidden = !active;
        inventoryElement(tabId).classList.toggle('is-active', active);
        inventoryElement(tabId).setAttribute('aria-selected', String(active));
        inventoryElement(tabId).tabIndex = active ? 0 : -1;
    });
    if (selected === 'history') return loadInventoryHistory(generation);
    if (selected === 'differences') return loadDiscrepancies(generation);
    lastInventoryVersion = null;
    return pollInventoryTask({force: true});
}

function bindRovingTablist(definitions, activate) {
    const tabs = definitions.map(([id]) => inventoryElement(id));
    definitions.forEach(([, value], index) => {
        const tab = tabs[index];
        tab.addEventListener('click', () => activate(value));
        tab.addEventListener('keydown', (event) => {
            let targetIndex = null;
            if (event.key === 'ArrowRight') targetIndex = (index + 1) % tabs.length;
            if (event.key === 'ArrowLeft') targetIndex = (index - 1 + tabs.length) % tabs.length;
            if (event.key === 'Home') targetIndex = 0;
            if (event.key === 'End') targetIndex = tabs.length - 1;
            if (targetIndex === null) return;
            event.preventDefault();
            activate(definitions[targetIndex][1]);
            tabs[targetIndex].focus();
        });
    });
}

function bindInventoryTablists() {
    bindRovingTablist([
        ['inventoryTabCurrent', 'current'],
        ['inventoryTabHistory', 'history'],
        ['inventoryTabDifferences', 'differences'],
    ], switchInventoryTab);
    bindRovingTablist([
        ['inventoryDifferenceTabOpen', 'open'],
        ['inventoryDifferenceTabArchived', 'archived'],
    ], switchDifferenceState);
}

function setGyjLoginMessage(message, kind = '') {
    const target = inventoryElement('inventoryGyjLoginMessage');
    target.textContent = message || '';
    target.className = 'inventory-dialog-message' + (kind ? ` is-${kind}` : '');
}

function stopGyjLoginPolling() {
    if (gyjLoginPollTimer) clearInterval(gyjLoginPollTimer);
    gyjLoginPollTimer = null;
}

function clearGyjCaptcha() {
    inventoryElement('inventoryGyjCaptcha').value = '';
    const image = inventoryElement('inventoryGyjCaptchaImage');
    image.removeAttribute('src');
    inventoryElement('inventoryGyjCaptchaRow').hidden = true;
}

function closeGyjLogin() {
    gyjLoginSession += 1;
    stopGyjLoginPolling();
    clearGyjCaptcha();
    inventoryElement('inventoryGyjPassword').value = '';
    const dialog = inventoryElement('inventoryGyjLoginDialog');
    if (dialog.open) dialog.close();
}

function renderGyjLoginState(data) {
    const loggedIn = Boolean(data.logged_in);
    const waitingCaptcha = Boolean(data.waiting_captcha);
    const button = inventoryElement('inventoryGyjLoginButton');
    const captchaRow = inventoryElement('inventoryGyjCaptchaRow');
    const wasWaitingCaptcha = !captchaRow.hidden;
    button.textContent = loggedIn ? 'GYJ 已登录' : '登录 GYJ';
    button.classList.toggle('btn-primary', !loggedIn);
    button.classList.toggle('btn-secondary', loggedIn);
    captchaRow.hidden = !waitingCaptcha;
    if (waitingCaptcha) {
        stopGyjLoginPolling();
        if (!wasWaitingCaptcha) refreshGyjCaptcha();
    }
    if (loggedIn) {
        setGyjLoginMessage('GYJ 登录成功。', 'success');
        stopGyjLoginPolling();
    } else if (data.message) {
        setGyjLoginMessage(data.message, waitingCaptcha ? '' : 'error');
    }
    return loggedIn;
}

async function refreshGyjCaptcha(session = gyjLoginSession, regenerate = false) {
    try {
        const data = regenerate
            ? await inventoryPost('/api/gyj/captcha/refresh', {})
            : await inventoryRequest('/api/gyj/captcha-preview');
        const dialog = inventoryElement('inventoryGyjLoginDialog');
        if (session !== gyjLoginSession || !dialog.open) return;
        const source = String(data.captcha_image || '');
        const image = inventoryElement('inventoryGyjCaptchaImage');
        if (source.startsWith('data:image/')) image.src = source;
        else image.removeAttribute('src');
    } catch (error) {
        setGyjLoginMessage(error.message, 'error');
    }
}

async function pollGyjLoginStatus() {
    const dialog = inventoryElement('inventoryGyjLoginDialog');
    if (!dialog.open) {
        stopGyjLoginPolling();
        return;
    }
    const session = gyjLoginSession;
    try {
        const data = await inventoryRequest('/api/gyj/login-status', {acceptFailureState: true});
        if (session !== gyjLoginSession || !dialog.open) return;
        if (renderGyjLoginState(data)) {
            lastInventoryVersion = null;
            pollInventoryTask({force: true});
        }
    } catch (error) {
        if (error.data && ('logged_in' in error.data || 'waiting_captcha' in error.data)) {
            renderGyjLoginState(error.data);
        }
        setGyjLoginMessage(error.message, 'error');
    }
}

async function openGyjLogin() {
    const dialog = inventoryElement('inventoryGyjLoginDialog');
    const session = ++gyjLoginSession;
    clearGyjCaptcha();
    setGyjLoginMessage('正在读取登录状态…');
    if (!dialog.open) dialog.showModal();
    try {
        const credentials = await inventoryRequest('/api/gyj/credentials');
        if (session !== gyjLoginSession || !dialog.open) return;
        inventoryElement('inventoryGyjUsername').value = credentials.username || '';
        inventoryElement('inventoryGyjPassword').value = credentials.password || '';
        inventoryElement('inventoryGyjRemember').checked = Boolean(credentials.remember);
        await pollGyjLoginStatus();
        if (session !== gyjLoginSession || !dialog.open) return;
        stopGyjLoginPolling();
        if (inventoryElement('inventoryGyjCaptchaRow').hidden) {
            gyjLoginPollTimer = setInterval(pollGyjLoginStatus, 1000);
        }
        inventoryElement('inventoryGyjUsername').focus();
    } catch (error) {
        setGyjLoginMessage(error.message, 'error');
    }
}

async function submitGyjLogin() {
    const username = inventoryElement('inventoryGyjUsername').value.trim();
    const password = inventoryElement('inventoryGyjPassword').value;
    const remember = inventoryElement('inventoryGyjRemember').checked;
    const dialog = inventoryElement('inventoryGyjLoginDialog');
    const session = gyjLoginSession;
    if (!username || !password) {
        setGyjLoginMessage('请输入 GYJ 账号和密码。', 'error');
        return;
    }
    const button = inventoryElement('inventoryGyjLoginSubmit');
    button.disabled = true;
    button.textContent = '正在登录…';
    setGyjLoginMessage('正在连接 GYJ，请稍候。');
    try {
        const data = await inventoryPost('/api/gyj/login', {username, password, remember});
        if (session !== gyjLoginSession || !dialog.open) return;
        renderGyjLoginState(data);
        if (data.waiting_captcha) inventoryElement('inventoryGyjCaptcha').focus();
        if (data.logged_in) setTimeout(() => {
            if (session === gyjLoginSession) closeGyjLogin();
        }, 500);
    } catch (error) {
        if (session !== gyjLoginSession || !dialog.open) return;
        if (error.data) renderGyjLoginState(error.data);
        setGyjLoginMessage(error.message, 'error');
    } finally {
        button.disabled = false;
        button.textContent = '登录 GYJ';
    }
}

async function submitGyjCaptcha() {
    const captcha = inventoryElement('inventoryGyjCaptcha').value.trim();
    if (!captcha) {
        setGyjLoginMessage('请输入验证码。', 'error');
        return;
    }
    const button = inventoryElement('inventoryGyjCaptchaSubmit');
    const dialog = inventoryElement('inventoryGyjLoginDialog');
    const session = gyjLoginSession;
    stopGyjLoginPolling();
    button.disabled = true;
    button.textContent = '正在提交…';
    setGyjLoginMessage('正在提交验证码，请稍候。');
    try {
        const data = await inventoryPost('/api/gyj/login/captcha', {captcha});
        if (session !== gyjLoginSession || !dialog.open) return;
        inventoryElement('inventoryGyjCaptcha').value = '';
        renderGyjLoginState(data);
        if (data.logged_in) setTimeout(() => {
            if (session === gyjLoginSession) closeGyjLogin();
        }, 500);
    } catch (error) {
        if (session !== gyjLoginSession || !dialog.open) return;
        inventoryElement('inventoryGyjCaptcha').value = '';
        if (error.data) renderGyjLoginState(error.data);
        setGyjLoginMessage(error.message, 'error');
        await refreshGyjCaptcha();
    } finally {
        button.disabled = false;
        button.textContent = '提交验证码';
    }
}

async function refreshGyjStatusButton() {
    try {
        const data = await inventoryRequest('/api/gyj/login-status');
        const button = inventoryElement('inventoryGyjLoginButton');
        button.textContent = data.logged_in ? 'GYJ 已登录' : '登录 GYJ';
        button.classList.toggle('btn-primary', !data.logged_in);
        button.classList.toggle('btn-secondary', Boolean(data.logged_in));
    } catch (_error) {
        inventoryElement('inventoryGyjLoginButton').textContent = '登录 GYJ';
    }
}

function bindInventoryDialogBackdrop(dialog, close) {
    dialog.addEventListener('click', (event) => {
        if (event.target === dialog) close();
    });
    dialog.addEventListener('close', close);
}

function initializeInventoryPage() {
    inventoryDeviceId = getInventoryDeviceId();
    bindInventoryTablists();
    inventoryElement('inventoryHistoryLoadMore').addEventListener('click', () => {
        if (inventoryHistoryHasMore) loadInventoryHistory(inventoryTabGeneration, false);
    });
    inventoryElement('inventoryCreateTask').addEventListener('click', startInventoryTask);
    inventoryElement('inventoryCompleteTask').addEventListener('click', completeInventoryTask);
    inventoryElement('inventoryCompletionConfirm').addEventListener('click', () => completeInventoryTask(true));
    inventoryElement('inventoryCompletionConfirmClose').addEventListener('click', closeCompletionConfirmation);
    inventoryElement('inventoryCompletionCancel').addEventListener('click', closeCompletionConfirmation);
    inventoryElement('inventorySearch').addEventListener('input', handleInventorySearchInput);
    inventoryElement('inventorySearch').addEventListener('keydown', handleInventorySearchEnter);
    inventoryElement('inventoryFilters').addEventListener('change', runInventorySearch);
    inventoryElement('inventoryCountNewQuantity').addEventListener('keydown', (event) => {
        if (event.key === 'Enter') addCountEntry();
    });
    inventoryElement('inventoryCountAdd').addEventListener('click', addCountEntry);
    inventoryElement('inventoryCountAuditButton').addEventListener('click', () => {
        if (inventoryTask && currentCountItem) {
            openInventoryAudit(inventoryTask.task_id, currentCountItem.barcode);
        }
    });
    inventoryElement('inventoryCountClose').addEventListener('click', closeCountDialog);
    inventoryElement('inventoryCountCancel').addEventListener('click', closeCountDialog);
    inventoryElement('inventorySerialInput').addEventListener('keydown', scanSerial);
    inventoryElement('inventoryCameraStart').addEventListener('click', startInventoryCamera);
    inventoryElement('inventoryCameraStop').addEventListener('click', stopInventoryCamera);
    inventoryElement('inventorySerialRefresh').addEventListener('click', manualRefreshSerialItem);
    inventoryElement('inventorySerialFinish').addEventListener('click', finishSerialItem);
    inventoryElement('inventorySerialClose').addEventListener('click', closeSerialWorkspace);
    inventoryElement('inventorySerialCancel').addEventListener('click', closeSerialWorkspace);
    inventoryElement('inventoryAuditClose').addEventListener('click', closeInventoryAudit);
    inventoryElement('inventoryAuditDone').addEventListener('click', closeInventoryAudit);
    inventoryElement('inventoryDifferenceSearch').addEventListener('input', handleDifferenceSearch);
    inventoryElement('inventoryDifferenceSearch').addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            if (inventoryDifferenceSearchTimer) clearTimeout(inventoryDifferenceSearchTimer);
            inventoryDifferenceSearchTimer = null;
            loadDiscrepancies();
        }
    });
    inventoryElement('inventoryDifferenceExport').addEventListener('click', exportDiscrepancies);
    inventoryElement('inventoryGyjLoginButton').addEventListener('click', openGyjLogin);
    inventoryElement('inventoryGyjLoginClose').addEventListener('click', closeGyjLogin);
    inventoryElement('inventoryGyjLoginCancel').addEventListener('click', closeGyjLogin);
    inventoryElement('inventoryGyjLoginSubmit').addEventListener('click', submitGyjLogin);
    inventoryElement('inventoryGyjCaptchaSubmit').addEventListener('click', submitGyjCaptcha);
    inventoryElement('inventoryGyjCaptchaRefresh').addEventListener('click', () => {
        refreshGyjCaptcha(gyjLoginSession, true);
    });
    inventoryElement('inventoryGyjPassword').addEventListener('keydown', (event) => {
        if (event.key === 'Enter') submitGyjLogin();
    });
    inventoryElement('inventoryGyjCaptcha').addEventListener('keydown', (event) => {
        if (event.key === 'Enter') submitGyjCaptcha();
    });
    bindInventoryDialogBackdrop(inventoryElement('inventoryCountDialog'), closeCountDialog);
    bindInventoryDialogBackdrop(inventoryElement('inventorySerialWorkspace'), closeSerialWorkspace);
    bindInventoryDialogBackdrop(inventoryElement('inventoryCompletionConfirmDialog'), closeCompletionConfirmation);
    bindInventoryDialogBackdrop(inventoryElement('inventoryAuditDialog'), closeInventoryAudit);
    bindInventoryDialogBackdrop(inventoryElement('inventoryGyjLoginDialog'), closeGyjLogin);
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            stopInventoryCamera();
            return;
        }
        if (inventoryActiveTab === 'current') pollInventoryTask({force: true});
    });
    window.addEventListener('beforeunload', () => {
        stopInventoryCamera();
        stopGyjLoginPolling();
    });
    renderInventoryTask(null);
    pollInventoryTask({force: true});
    refreshGyjStatusButton();
    inventoryPollTimer = setInterval(pollInventoryTask, 1000);
}

document.addEventListener('DOMContentLoaded', initializeInventoryPage);
