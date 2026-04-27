// UI Elements - Views & Navigation
const navDrawer = document.getElementById('navDrawer');
const drawerOverlay = document.getElementById('drawerOverlay');
const menuBtn = document.getElementById('menuBtn');
const closeDrawerBtn = document.getElementById('closeDrawerBtn');
const viewTitle = document.getElementById('viewTitle');
const navItems = document.querySelectorAll('.nav-item');
const viewContents = document.querySelectorAll('.view-content');

// UI Elements - Chat
const chatMessages = document.getElementById('chatMessages');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');

// UI Elements - Settings
const settingsForm = document.getElementById('settingsForm');
const settingsFields = document.getElementById('settingsFields');
const reloadSettingsBtn = document.getElementById('reloadSettingsBtn');
const saveSettingsBtn = document.getElementById('saveSettingsBtn');
const settingsTabs = document.getElementById('settingsTabs');
let currentSettingsTab = 'line';
let allSettingsItems = [];

// UI Elements - Monitoring
const logSummary = document.getElementById('logSummary');
const logList = document.getElementById('logList');
const logStatusFilter = document.getElementById('logStatusFilter');
const logLimit = document.getElementById('logLimit');
const logActiveOnly = document.getElementById('logActiveOnly');
const logAutoRefresh = document.getElementById('logAutoRefresh');
const refreshLogsBtn = document.getElementById('refreshLogsBtn');
const logSelectAll = document.getElementById('logSelectAll');
const deleteSelectedLogsBtn = document.getElementById('deleteSelectedLogsBtn');

// UI Elements - Others
const toastContainer = document.getElementById('toastContainer');

// UI Elements - Skills
const skillsList = document.getElementById('skillsList');
const saveSkillSettingsBtn = document.getElementById('saveSkillSettingsBtn');

const API_FALLBACK_ORIGINS = ['http://localhost:8080', 'http://127.0.0.1:8080'];
const SETTINGS_API_URL = '/api/v1/settings/env';
const LOGS_API_URL = '/api/v1/logs/line/requests';
const SKILLS_API_URL = '/api/v1/skills';
const LOG_AUTO_REFRESH_MS = 3000;
const API_REQUEST_TIMEOUT_MS = 30000;

const messages = [];
let isLoadingLogs = false;
let logRefreshTimer = null;
let lastLogErrorMessage = '';
let selectedLogIds = new Set();
let currentLogItems = [];

function getApiRequestUrls(url) {
    if (typeof url !== 'string' || !url.startsWith('/api/')) {
        return [url];
    }

    const fallbackUrls = API_FALLBACK_ORIGINS.map(origin => `${origin}${url}`);
    if (window.location.protocol === 'file:') {
        return [...new Set(fallbackUrls)];
    }

    if (API_FALLBACK_ORIGINS.includes(window.location.origin)) {
        return [url];
    }

    return [...new Set([url, ...fallbackUrls])];
}

async function fetchJsonOnce(url, options = {}, timeoutMs = API_REQUEST_TIMEOUT_MS) {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);

    try {
        const response = await fetch(url, {
            ...options,
            signal: controller.signal,
        });
        const text = await response.text();
        const contentType = response.headers.get('content-type') || '';
        let data = null;

        if (text) {
            try {
                data = JSON.parse(text);
            } catch {
                data = text;
            }
        }

        return { response, data, contentType, url };
    } catch (error) {
        if (error.name === 'AbortError') {
            throw new Error('請求逾時');
        }
        throw error;
    } finally {
        window.clearTimeout(timeoutId);
    }
}

async function fetchJsonWithTimeout(url, options = {}, timeoutMs = API_REQUEST_TIMEOUT_MS) {
    const requestUrls = getApiRequestUrls(url);
    const isApiRequest = typeof url === 'string' && url.startsWith('/api/');
    const currentOriginIsKnownBackend = API_FALLBACK_ORIGINS.includes(window.location.origin);
    let lastError = null;

    for (const requestUrl of requestUrls) {
        try {
            const result = await fetchJsonOnce(requestUrl, options, timeoutMs);
            const isJsonResponse = result.contentType.includes('application/json');
            const shouldTryNextOrigin = isApiRequest
                && !currentOriginIsKnownBackend
                && requestUrls.length > 1
                && (!result.response.ok || !isJsonResponse);

            if (shouldTryNextOrigin) {
                lastError = new Error('目前頁面來源未提供後端 API，改用後端服務埠重試');
                continue;
            }

            return result;
        } catch (error) {
            lastError = error;
        }
    }

    throw lastError || new Error('請求失敗');
}

// --- View Switching Logic ---

function switchView(viewId) {
    // Update Nav Items
    navItems.forEach(item => {
        if (item.dataset.view === viewId) {
            item.classList.add('active');
            viewTitle.textContent = item.querySelector('span').textContent;
        } else {
            item.classList.remove('active');
        }
    });

    // Update View Contents
    viewContents.forEach(content => {
        if (content.id === `${viewId}View`) {
            content.classList.add('active');
        } else {
            content.classList.remove('active');
        }
    });

    // Close drawer on mobile/tablet after selection
    closeDrawer();

    // Trigger data loading if needed
    if (viewId === 'settings') {
        loadSettings();
        setupSettingsTabs();
    } else if (viewId === 'monitoring') {
        loadRequestLogs();
    } else if (viewId === 'skills') {
        loadSkills();
        loadSkillSettings();
    }
}

function openDrawer() {
    navDrawer.classList.add('active');
    drawerOverlay.classList.add('active');
}

function closeDrawer() {
    navDrawer.classList.remove('active');
    drawerOverlay.classList.remove('active');
}

// --- Toast & Notifications ---

function showToast(text, type = 'info', duration = 3200) {
    if (!toastContainer) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = text;

    toastContainer.appendChild(toast);

    window.setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(20px)';
        toast.style.transition = 'all 0.3s ease';
        window.setTimeout(() => {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
        }, 300);
    }, duration);
}

function notifySettings(text, type = 'info') {
    showToast(text, type);
}

// --- Chat Logic ---

function addMessage(role, text) {
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.textContent = text;

    msgDiv.appendChild(contentDiv);
    chatMessages.appendChild(msgDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    if (role === 'user' || role === 'bot') {
        messages.push({
            role: role === 'user' ? 'user' : 'assistant',
            content: text,
        });
    }
}

async function sendMessage() {
    const text = userInput.value.trim();
    if (!text) return;

    userInput.value = '';
    addMessage('user', text);

    // 建立機器人回覆的容器與狀態顯示區
    const botMsgDiv = document.createElement('div');
    botMsgDiv.className = 'message bot';

    const statusDiv = document.createElement('div');
    statusDiv.className = 'message-status active'; // 預設開啟顯示

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content loading';
    contentDiv.textContent = '...';

    botMsgDiv.appendChild(contentDiv);
    botMsgDiv.appendChild(statusDiv);
    chatMessages.appendChild(botMsgDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    // 加入初始狀態
    const addStatusItem = (text) => {
        const item = document.createElement('div');
        item.className = 'status-item';
        item.innerHTML = `<span>⚙️</span> <span>${text}</span>`;
        statusDiv.appendChild(item);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    };

    addStatusItem('分析問題中...');

    try {
        const response = await fetch('/api/v1/chat/completions/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ messages }),
        });

        if (!response.ok) throw new Error('連線失敗');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const data = JSON.parse(line.slice(6));
                    if (data.type === 'status') {
                        addStatusItem(data.content);
                    } else if (data.type === 'answer') {
                        contentDiv.textContent = data.content;
                        contentDiv.classList.remove('loading');
                        
                        // 不再自動隱藏 statusDiv，保留流程紀錄方便 debug
                        const completeItem = document.createElement('div');
                        completeItem.className = 'status-item';
                        completeItem.style.color = 'var(--accent-color)';
                        completeItem.innerHTML = `<span>✅</span> <span>回答生成完畢</span>`;
                        statusDiv.appendChild(completeItem);

                        // 同步到本地訊息紀錄
                        messages.push({ role: 'assistant', content: data.content });
                    } else if (data.type === 'error') {
                        addMessage('system', `錯誤: ${data.content}`);
                    }
                }
            }
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }
    } catch (error) {
        if (botMsgDiv.parentNode) chatMessages.removeChild(botMsgDiv);
        addMessage('system', `連線失敗: ${error.message}`);
    }
}

// --- Settings Logic ---

function extractErrorMessage(data, fallbackText) {
    if (!data) return fallbackText;
    return data.detail || data.detail?.error || data.error || fallbackText;
}

function toBoolean(value) {
    const normalized = String(value ?? '').trim().toLowerCase();
    return ['true', '1', 'yes', 'on'].includes(normalized);
}

function createSettingField(item) {
    const wrapper = document.createElement('div');
    wrapper.className = 'setting-item';
    wrapper.dataset.category = getSettingCategory(item.key);

    const fieldId = `env_${item.key.toLowerCase()}`;
    let control;

    if (item.input_type === 'boolean') {
        // Render Toggle Switch for boolean
        wrapper.innerHTML = `
            <div class="setting-item-row">
                <div class="setting-info">
                    <label class="setting-label" for="${fieldId}">${item.label}</label>
                    <div class="setting-description">${item.description}</div>
                </div>
                <label class="switch">
                    <input type="checkbox" id="${fieldId}" data-env-key="${item.key}" data-input-type="boolean" ${toBoolean(item.value) ? 'checked' : ''}>
                    <span class="slider"></span>
                </label>
            </div>
        `;
        return wrapper;
    }

    // Standard layout for other types
    const label = document.createElement('label');
    label.className = 'setting-label';
    label.textContent = item.label;
    label.htmlFor = fieldId;

    const description = document.createElement('div');
    description.className = 'setting-description';
    description.textContent = item.description;

    if (item.input_type === 'textarea') {
        control = document.createElement('textarea');
        control.rows = 4;
        control.className = 'setting-control';
        control.value = item.value ?? '';
    } else {
        control = document.createElement('input');
        control.className = 'setting-control';
        control.value = item.value ?? '';
        control.type = item.input_type === 'password' ? 'password' : (item.input_type === 'number' ? 'number' : 'text');
        if (item.input_type === 'password') control.autocomplete = 'new-password';
        if (item.input_type === 'number') control.step = 'any';
    }

    control.id = fieldId;
    control.dataset.envKey = item.key;
    control.dataset.inputType = item.input_type;

    wrapper.appendChild(label);
    wrapper.appendChild(control);
    wrapper.appendChild(description);

    return wrapper;
}

function getSettingCategory(key) {
    if (key.startsWith('LINE_')) return 'line';
    if (key.startsWith('LLM_')) return 'llm';
    if (key.startsWith('RAG_') || key.startsWith('AGENT_') || key.startsWith('RERANK_')) return 'rag';
    if (key.startsWith('QDRANT_') || key.startsWith('EMBEDDING_')) return 'services';
    return 'llm'; // Default
}

function setupSettingsTabs() {
    if (!settingsTabs) return;
    const tabBtns = settingsTabs.querySelectorAll('.tab-btn');
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            tabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentSettingsTab = btn.dataset.tab;
            filterSettingsFields();
        });
    });
}

function filterSettingsFields() {
    const items = settingsFields.querySelectorAll('.setting-item');
    items.forEach(item => {
        if (item.dataset.category === currentSettingsTab) {
            item.style.display = 'block';
        } else {
            item.style.display = 'none';
        }
    });
}

async function loadSettings() {
    if (!settingsForm) return;
    setSettingsBusy(true);

    try {
        const response = await fetch(SETTINGS_API_URL);
        const data = await response.json();
        if (!response.ok) throw new Error(extractErrorMessage(data, '載入設定失敗'));

        allSettingsItems = data.items || [];
        settingsFields.innerHTML = '';

        if (allSettingsItems.length > 0) {
            allSettingsItems.forEach(item => {
                settingsFields.appendChild(createSettingField(item));
            });
            filterSettingsFields();
        } else {
            settingsFields.innerHTML = '<div class="setting-description">目前沒有可編輯的設定項目。</div>';
        }
    } catch (error) {
        notifySettings(`載入失敗：${error.message}`, 'error');
    } finally {
        setSettingsBusy(false);
    }
}

async function saveSettings(event) {
    event.preventDefault();
    const values = {};
    settingsFields.querySelectorAll('[data-env-key]').forEach(control => {
        const key = control.dataset.envKey;
        values[key] = control.dataset.inputType === 'boolean' ? control.checked : control.value;
    });

    if (Object.keys(values).length === 0) return;
    setSettingsBusy(true);
    showToast('儲存中...', 'info', 1000);

    try {
        const response = await fetch(SETTINGS_API_URL, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ values }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(extractErrorMessage(data, '儲存設定失敗'));

        showToast('設定已成功儲存', 'success');
    } catch (error) {
        showToast(`儲存失敗：${error.message}`, 'error');
    } finally {
        setSettingsBusy(false);
    }
}

function setSettingsBusy(isBusy) {
    [reloadSettingsBtn, saveSettingsBtn].forEach(btn => { if (btn) btn.disabled = isBusy; });
    settingsFields.querySelectorAll('[data-env-key]').forEach(c => c.disabled = isBusy);
}

// --- Monitoring Logic ---

function formatDateTime(value) {
    if (!value) return '-';
    const date = new Date(value);
    return isNaN(date.getTime()) ? value : date.toLocaleString('zh-TW', { hour12: false });
}

function renderLogSummary(summary = {}) {
    if (!logSummary) return;
    const items = [
        { key: 'active', label: '進行中', value: summary.active || 0 },
        { key: 'completed', label: '完成', value: summary.completed || 0 },
        { key: 'failed', label: '失敗', value: summary.failed || 0 },
        { key: 'ignored', label: '忽略', value: summary.ignored || 0 },
        { key: 'total', label: '累計', value: summary.total || 0 },
    ];

    logSummary.innerHTML = items.map(item => `
        <div class="summary-card ${item.key}">
            <span class="summary-label">${item.label}</span>
            <strong class="summary-value">${item.value}</strong>
        </div>
    `).join('');
}

function renderLogList(items = []) {
    if (!logList) return;
    if (items.length === 0) {
        logList.innerHTML = '<div class="log-empty">目前沒有符合條件的請求紀錄。</div>';
        return;
    }

    logList.innerHTML = items.map(item => `
        <article class="log-item" data-request-id="${item.request_id}">
            <div class="log-item-header">
                <div class="log-item-checkbox">
                    <input type="checkbox" data-request-id="${item.request_id}" ${selectedLogIds.has(item.request_id) ? 'checked' : ''} />
                </div>
                <div class="log-status-container">
                    <div class="log-status-badge">
                        <span class="log-status status-${item.status || 'received'}">${item.status || 'received'}</span>
                        <span class="log-stage">${item.stage || '-'}</span>
                    </div>
                    <button class="btn-delete-log" data-request-id="${item.request_id}" title="刪除此日誌">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"/></svg>
                    </button>
                </div>
            </div>
            <div class="log-lines">
                <div class="log-line"><span>使用者</span><strong>${item.line_user_id || '-'}</strong></div>
                <div class="log-line"><span>建立時間</span><strong>${formatDateTime(item.created_at)}</strong></div>
            </div>
            ${item.user_text_preview ? `<div class="log-preview">👤 ${item.user_text_preview}</div>` : ''}
            ${item.reply_text_preview ? `<div class="log-preview">🤖 ${item.reply_text_preview}</div>` : ''}
            ${item.error ? `<div class="log-error">⚠ ${item.error}</div>` : ''}
        </article>
    `).join('');

    // Attach event listeners to checkboxes
    logList.querySelectorAll('.log-item-checkbox input').forEach(cb => {
        cb.addEventListener('change', (e) => {
            const id = e.target.dataset.requestId;
            if (e.target.checked) {
                selectedLogIds.add(id);
            } else {
                selectedLogIds.delete(id);
            }
            updateSelectionUI();
        });
    });

    // Attach event listeners to delete buttons
    logList.querySelectorAll('.btn-delete-log').forEach(btn => {
        btn.addEventListener('click', (e) => {
            console.log('Delete individual button clicked', e.currentTarget.dataset.requestId);
            const id = e.currentTarget.dataset.requestId;
            deleteLog(id);
        });
    });
}

function updateSelectionUI() {
    const totalVisible = currentLogItems.length;
    const totalSelected = selectedLogIds.size;
    console.log(`Updating selection UI: visible=${totalVisible}, selected=${totalSelected}`);

    if (logSelectAll) {
        logSelectAll.checked = totalVisible > 0 && totalSelected === totalVisible;
        logSelectAll.indeterminate = totalSelected > 0 && totalSelected < totalVisible;
    }

    if (deleteSelectedLogsBtn) {
        deleteSelectedLogsBtn.disabled = totalSelected === 0;
        deleteSelectedLogsBtn.textContent = totalSelected > 0 ? `刪除選取項目 (${totalSelected})` : '刪除選取項目';
    }
}

function toggleSelectAll() {
    if (!logSelectAll) return;

    if (logSelectAll.checked) {
        currentLogItems.forEach(item => selectedLogIds.add(item.request_id));
    } else {
        selectedLogIds.clear();
    }

    renderLogList(currentLogItems);
    updateSelectionUI();
}

async function deleteLog(requestId) {
    console.log('deleteLog called for:', requestId);
    if (!confirm('確定要刪除此日誌嗎？此操作無法復原。')) {
        console.log('Deletion cancelled by user');
        return;
    }

    try {
        const response = await fetch(`${LOGS_API_URL}/${requestId}`, { method: 'DELETE' });
        const data = await response.json();

        if (!response.ok) throw new Error(data.detail || '刪除失敗');

        showToast('日誌已刪除', 'success');
        selectedLogIds.delete(requestId);
        loadRequestLogs();
    } catch (error) {
        showToast(`刪除失敗: ${error.message}`, 'error');
    }
}

async function deleteSelectedLogs() {
    console.log('deleteSelectedLogs called');
    const ids = Array.from(selectedLogIds);
    if (ids.length === 0) {
        console.log('No logs selected for deletion');
        return;
    }

    if (!confirm(`確定要刪除選取的 ${ids.length} 筆日誌嗎？此操作無法復原。`)) {
        console.log('Bulk deletion cancelled by user');
        return;
    }

    try {
        const response = await fetch(LOGS_API_URL, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ request_ids: ids }),
        });
        const data = await response.json();

        if (!response.ok) throw new Error(data.detail || '批次刪除失敗');

        showToast(`成功刪除 ${data.deleted_count} 筆日誌`, 'success');
        selectedLogIds.clear();
        loadRequestLogs();
    } catch (error) {
        showToast(`批次刪除失敗: ${error.message}`, 'error');
    }
}

async function loadRequestLogs({ manual = false } = {}) {
    if (isLoadingLogs) return;
    isLoadingLogs = true;
    if (refreshLogsBtn) refreshLogsBtn.disabled = true;

    try {
        const params = new URLSearchParams();
        if (logLimit) params.set('limit', logLimit.value);
        if (logStatusFilter && logStatusFilter.value) params.set('status', logStatusFilter.value);
        if (logActiveOnly && logActiveOnly.checked) params.set('active_only', 'true');

        const response = await fetch(`${LOGS_API_URL}?${params.toString()}`);
        const data = await response.json();
        if (!response.ok) throw new Error(extractErrorMessage(data, '讀取 Log 失敗'));

        currentLogItems = data.items || [];
        // Clean up selectedLogIds that are no longer in the visible list (optional)
        // or keep them if they might reappear. Let's keep them for now.

        renderLogSummary(data.summary);
        renderLogList(currentLogItems);
        updateSelectionUI();
        if (manual) showToast('日誌已更新', 'success', 1000);
    } catch (error) {
        if (manual) showToast(`讀取失敗: ${error.message}`, 'error');
    } finally {
        isLoadingLogs = false;
        if (refreshLogsBtn) refreshLogsBtn.disabled = false;
    }
}

function setupLogAutoRefresh() {
    if (logRefreshTimer) clearInterval(logRefreshTimer);
    if (logAutoRefresh && logAutoRefresh.checked) {
        logRefreshTimer = setInterval(() => loadRequestLogs(), LOG_AUTO_REFRESH_MS);
    }
}

// --- Knowledge Base Logic ---

let selectedFiles = [];

const fileInput = document.getElementById('fileInput');
const dropZone = document.getElementById('dropZone');
const fileList = document.getElementById('fileList');
const uploadAllBtn = document.getElementById('uploadAllBtn');
const ragSearchInput = document.getElementById('ragSearchInput');
const ragSearchBtn = document.getElementById('ragSearchBtn');
const ragSearchResults = document.getElementById('ragSearchResults');

function setupKnowledgeBase() {
    if (!dropZone) return;

    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        handleFiles(e.dataTransfer.files);
    });

    fileInput.addEventListener('change', (e) => handleFiles(e.target.files));

    uploadAllBtn.addEventListener('click', uploadFiles);
    ragSearchBtn.addEventListener('click', searchKnowledge);

    ragSearchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') searchKnowledge();
    });

    const initQdrantBtn = document.getElementById('initQdrantBtn');
    if (initQdrantBtn) {
        initQdrantBtn.addEventListener('click', async () => {
            if (!confirm('確定要初始化 Qdrant 集合嗎？這將會嘗試建立 Collection。')) return;

            initQdrantBtn.disabled = true;
            initQdrantBtn.textContent = '初始化中...';

            try {
                const response = await fetch('/api/v1/rag/init', { method: 'POST' });
                const data = await response.json();
                if (response.ok) {
                    showToast(`初始化成功: ${data.message}`, 'success');
                } else {
                    showToast(`初始化失敗: ${data.detail}`, 'error');
                }
            } catch (error) {
                showToast(`連線失敗: ${error.message}`, 'error');
            } finally {
                initQdrantBtn.disabled = false;
                initQdrantBtn.textContent = '初始化 Qdrant 集合';
            }
        });
    }

    const clearQdrantBtn = document.getElementById('clearQdrantBtn');
    if (clearQdrantBtn) {
        clearQdrantBtn.addEventListener('click', async () => {
            if (!confirm('【警告】確定要清空所有資料嗎？這將會刪除並重新建立集合，所有已上傳的文件將會消失且無法復原。')) return;

            clearQdrantBtn.disabled = true;
            clearQdrantBtn.textContent = '清空中...';

            try {
                const response = await fetch('/api/v1/rag/clear', { method: 'POST' });
                const data = await response.json();
                if (response.ok) {
                    showToast(`清空成功: ${data.message}`, 'success');
                } else {
                    showToast(`清空失敗: ${data.detail}`, 'error');
                }
            } catch (error) {
                showToast(`連線失敗: ${error.message}`, 'error');
            } finally {
                clearQdrantBtn.disabled = false;
                clearQdrantBtn.textContent = '清空所有資料庫內容';
            }
        });
    }
}

function handleFiles(files) {
    for (const file of files) {
        if (!selectedFiles.find(f => f.name === file.name)) {
            selectedFiles.push(file);
        }
    }
    renderFileList();
}

function renderFileList() {
    if (!fileList) return;
    fileList.innerHTML = selectedFiles.map((file, index) => `
        <div class="file-item">
            <span>${file.name} (${(file.size / 1024).toFixed(1)} KB)</span>
            <span class="remove-file" onclick="removeFile(${index})">✕</span>
        </div>
    `).join('');
}

window.removeFile = (index) => {
    selectedFiles.splice(index, 1);
    renderFileList();
};

async function uploadFiles() {
    if (selectedFiles.length === 0) {
        showToast('請先選擇檔案', 'warning');
        return;
    }

    const chunkSize = document.getElementById('chunkSize').value;
    const overlapSize = document.getElementById('overlapSize').value;
    const docSection = document.getElementById('docSection').value;

    uploadAllBtn.disabled = true;
    uploadAllBtn.textContent = '處理中...';

    let successCount = 0;
    for (const file of selectedFiles) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('chunk_size', chunkSize);
        formData.append('overlap', overlapSize);
        formData.append('section', docSection);

        try {
            const response = await fetch('/api/v1/rag/upload-file', {
                method: 'POST',
                body: formData
            });
            const data = await response.json();
            if (response.ok) {
                successCount++;
            } else {
                showToast(`檔案 ${file.name} 上傳失敗: ${data.detail}`, 'error');
            }
        } catch (error) {
            showToast(`檔案 ${file.name} 連線錯誤`, 'error');
        }
    }

    showToast(`成功處理 ${successCount} 個檔案`, 'success');
    selectedFiles = [];
    renderFileList();
    uploadAllBtn.disabled = false;
    uploadAllBtn.textContent = '開始向量化並上傳';
}

async function searchKnowledge() {
    const query = ragSearchInput.value.trim();
    if (!query) return;

    ragSearchBtn.disabled = true;
    ragSearchResults.innerHTML = '<div class="system">搜尋中...</div>';

    try {
        const response = await fetch(`/api/v1/rag/search?q=${encodeURIComponent(query)}`);
        const data = await response.json();

        if (!response.ok) throw new Error(data.detail || '搜尋失敗');

        if (data.data.length === 0) {
            ragSearchResults.innerHTML = '<div class="system">找不到相關資料</div>';
        } else {
            ragSearchResults.innerHTML = data.data.map(item => `
                <div class="search-result-item">
                    <div class="result-header">
                        <span class="result-title">${item.payload.title || '無標題'}</span>
                        <span class="result-score">相似度: ${(item.score * 100).toFixed(1)}%</span>
                    </div>
                    <div class="result-text">${item.payload.text}</div>
                    <div class="result-footer" style="margin-top: 8px; font-size: 0.7rem; color: var(--text-secondary);">
                        來源: ${item.payload.source} | 分類: ${item.payload.section}
                    </div>
                </div>
            `).join('');
        }
    } catch (error) {
        showToast(`搜尋出錯: ${error.message}`, 'error');
        ragSearchResults.innerHTML = '';
    } finally {
        ragSearchBtn.disabled = false;
    }
}

// --- Event Listeners ---

menuBtn.addEventListener('click', openDrawer);
closeDrawerBtn.addEventListener('click', closeDrawer);
drawerOverlay.addEventListener('click', closeDrawer);

navItems.forEach(item => {
    item.addEventListener('click', () => switchView(item.dataset.view));
});

if (settingsForm) settingsForm.addEventListener('submit', saveSettings);
if (reloadSettingsBtn) reloadSettingsBtn.addEventListener('click', loadSettings);

if (refreshLogsBtn) refreshLogsBtn.addEventListener('click', () => loadRequestLogs({ manual: true }));
if (logStatusFilter) logStatusFilter.addEventListener('change', () => loadRequestLogs());
if (logLimit) logLimit.addEventListener('change', () => loadRequestLogs());
if (logActiveOnly) logActiveOnly.addEventListener('change', () => loadRequestLogs());
if (logAutoRefresh) logAutoRefresh.addEventListener('change', setupLogAutoRefresh);
if (logSelectAll) logSelectAll.addEventListener('change', toggleSelectAll);
if (deleteSelectedLogsBtn) deleteSelectedLogsBtn.addEventListener('click', deleteSelectedLogs);

if (sendBtn) sendBtn.addEventListener('click', sendMessage);
if (userInput) {
    userInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
}

// --- Initialization ---

window.addEventListener('beforeunload', () => {
    if (logRefreshTimer) clearInterval(logRefreshTimer);
});

// --- Health Checks ---

const connectionStatusEl = document.getElementById('connectionStatus');
const qdrantStatusEl = document.getElementById('qdrantStatus');

async function updateSystemStatus() {
    if (!connectionStatusEl) return;

    try {
        const response = await fetch('/');
        const text = connectionStatusEl.querySelector('span:not(.status-dot)');

        if (response.ok) {
            connectionStatusEl.className = 'status-badge online';
            text.textContent = '系統已連線';
        } else {
            connectionStatusEl.className = 'status-badge offline';
            text.textContent = '系統錯誤';
        }
    } catch (error) {
        if (connectionStatusEl) {
            connectionStatusEl.className = 'status-badge offline';
            const text = connectionStatusEl.querySelector('span:not(.status-dot)');
            text.textContent = '系統離線';
        }
    }
}

async function updateQdrantStatus() {
    if (!qdrantStatusEl) return;

    try {
        const response = await fetch('/api/v1/rag/health');
        const data = await response.json();
        const text = qdrantStatusEl.querySelector('span:not(.status-dot)');

        if (data.status === 'online') {
            qdrantStatusEl.className = 'status-badge online';
            text.textContent = 'Qdrant 已連線';
            qdrantStatusEl.title = '連線正常';
        } else {
            const errMsg = data.message || data.detail || '未知錯誤';
            qdrantStatusEl.className = 'status-badge offline';
            text.textContent = 'Qdrant 離線';
            qdrantStatusEl.title = errMsg;
        }
    } catch (error) {
        if (qdrantStatusEl) {
            qdrantStatusEl.className = 'status-badge offline';
            const text = qdrantStatusEl.querySelector('span:not(.status-dot)');
            text.textContent = 'Qdrant 斷線';
        }
    }
}

// --- Skill Management Logic ---

function escapeHtml(value) {
    const htmlEntities = {
        '&': String.fromCharCode(38, 97, 109, 112, 59),
        '<': String.fromCharCode(38, 108, 116, 59),
        '>': String.fromCharCode(38, 103, 116, 59),
        '"': String.fromCharCode(38, 113, 117, 111, 116, 59),
        "'": String.fromCharCode(38, 35, 51, 57, 59),
    };

    return String(value ?? '').replace(/[&<>"']/g, char => htmlEntities[char]);
}

function getSkillIcon(skillId) {
    // 統一使用具備科技感的圖示
    return '✨';
}

function renderSkills(skills, enabledSkills = [], forcedSkills = []) {
    if (!skillsList) return;

    if (!Array.isArray(skills) || skills.length === 0) {
        skillsList.innerHTML = '<div class="loading-placeholder">目前沒有可用技能。</div>';
        return;
    }

    skillsList.innerHTML = skills.map((skill, index) => {
        const skillId = String(skill.skill_id || '');
        const isEnabled = enabledSkills.includes(skillId);
        const isForced = isEnabled && forcedSkills.includes(skillId);
        const icon = getSkillIcon(skillId);

        return `
            <div class="skill-card" style="animation-delay: ${index * 0.1}s" data-skill-id="${escapeHtml(skillId)}">
                <div class="skill-card-header">
                    <div class="skill-icon-box">${icon}</div>
                    <div class="skill-title-area">
                        <h3>${escapeHtml(skill.name || skillId)}</h3>
                        <div class="skill-meta">${escapeHtml(skillId)}</div>
                    </div>
                </div>
                <p>${escapeHtml(skill.description || '無描述')}</p>
                <div class="skill-footer">
                    <div class="skill-toggle-group">
                        <label class="skill-toggle-label">
                            <span>開啟</span>
                            <div class="switch">
                                <input type="checkbox" class="enabled-toggle" data-skill-id="${escapeHtml(skillId)}" ${isEnabled ? 'checked' : ''}>
                                <span class="slider"></span>
                            </div>
                        </label>
                        <label class="skill-toggle-label">
                            <span>強制先執行</span>
                            <div class="switch">
                                <input type="checkbox" class="forced-toggle" data-skill-id="${escapeHtml(skillId)}" ${isForced ? 'checked' : ''} ${isEnabled ? '' : 'disabled'}>
                                <span class="slider"></span>
                            </div>
                        </label>
                    </div>
                </div>
                <div class="skill-hint">啟用後可設定是否在 LLM 路由前強制執行。</div>
            </div>
        `;
    }).join('');

    skillsList.querySelectorAll('.enabled-toggle').forEach(toggle => {
        toggle.addEventListener('change', () => {
            const card = toggle.closest('.skill-card');
            const forcedToggle = card ? card.querySelector('.forced-toggle') : null;
            if (forcedToggle) {
                if (toggle.checked) {
                    forcedToggle.disabled = false;
                } else {
                    forcedToggle.checked = false;
                    forcedToggle.disabled = true;
                }
            }
            updateSkillSettings();
        });
    });

    skillsList.querySelectorAll('.forced-toggle').forEach(toggle => {
        toggle.addEventListener('change', () => updateSkillSettings());
    });
}

function applySkillSettingsState(enabledSkills = [], forcedSkills = []) {
    document.querySelectorAll('.skill-card').forEach(card => {
        const enabledToggle = card.querySelector('.enabled-toggle');
        const forcedToggle = card.querySelector('.forced-toggle');
        if (!enabledToggle || !forcedToggle) return;

        const skillId = enabledToggle.dataset.skillId;
        const isEnabled = enabledSkills.includes(skillId);
        const isForced = isEnabled && forcedSkills.includes(skillId);

        enabledToggle.checked = isEnabled;
        forcedToggle.checked = isForced;
        forcedToggle.disabled = !isEnabled;
    });
}

async function fetchSkillSettings({ silent = false } = {}) {
    try {
        const { response, data } = await fetchJsonWithTimeout(
            `${SKILLS_API_URL}/settings`,
            { cache: 'no-store' },
            15000
        );

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, '讀取技能設定失敗'));
        }

        return {
            enabledSkills: Array.isArray(data?.enabled_skills) ? data.enabled_skills.map(id => String(id)) : [],
            forcedSkills: Array.isArray(data?.forced_skills) ? data.forced_skills.map(id => String(id)) : []
        };
    } catch (error) {
        if (!silent) {
            showToast(`讀取技能設定失敗，已改用預設值: ${error.message}`, 'error');
        }
        return null;
    }
}

async function loadSkills() {
    if (!skillsList) return;
    skillsList.innerHTML = '<div class="loading-placeholder">載入技能中...</div>';
    if (saveSkillSettingsBtn) saveSkillSettingsBtn.disabled = true;

    try {
        const { response, data } = await fetchJsonWithTimeout(
            SKILLS_API_URL,
            { cache: 'no-store' },
            15000
        );

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, '讀取技能資料失敗'));
        }
        if (!Array.isArray(data)) {
            throw new Error('技能資料格式錯誤');
        }

        const skillSettings = await fetchSkillSettings({ silent: true });
        const defaultEnabledSkills = data.map(skill => String(skill.skill_id || ''));
        const enabledSkills = skillSettings ? skillSettings.enabledSkills : defaultEnabledSkills;
        const forcedSkills = skillSettings ? skillSettings.forcedSkills.filter(skillId => enabledSkills.includes(skillId)) : [];

        renderSkills(data, enabledSkills, forcedSkills);
        if (saveSkillSettingsBtn) saveSkillSettingsBtn.disabled = false;
    } catch (error) {
        skillsList.innerHTML = `<div class="loading-placeholder error">載入失敗：${escapeHtml(error.message)}</div>`;
        showToast(`載入技能失敗: ${error.message}`, 'error');
    } finally {
        if (saveSkillSettingsBtn) saveSkillSettingsBtn.disabled = false;
    }
}

async function loadSkillSettings() {
    // 目前僅需確保技能開關狀態正確即可，此功能已合併在 loadSkills 中處理
}

async function updateSkillSettings() {
    const enabledSkills = Array.from(document.querySelectorAll('.enabled-toggle:checked'))
        .map(el => el.dataset.skillId);
    const forcedSkills = Array.from(document.querySelectorAll('.forced-toggle:checked'))
        .map(el => el.dataset.skillId)
        .filter(skillId => enabledSkills.includes(skillId));

    if (saveSkillSettingsBtn) {
        saveSkillSettingsBtn.disabled = true;
        saveSkillSettingsBtn.textContent = '儲存中...';
    } else {
        showToast('正在自動儲存技能設定...', 'info', 1000);
    }

    try {
        const response = await fetch(`${SKILLS_API_URL}/settings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                enabled_skills: enabledSkills,
                forced_skills: forcedSkills
            })
        });

        const data = await response.json();

        if (response.ok) {
            const normalizedEnabled = Array.isArray(data?.settings?.enabled_skills)
                ? data.settings.enabled_skills.map(id => String(id))
                : enabledSkills;
            const normalizedForced = Array.isArray(data?.settings?.forced_skills)
                ? data.settings.forced_skills.map(id => String(id))
                : forcedSkills;

            applySkillSettingsState(normalizedEnabled, normalizedForced);
            showToast('技能設定已自動儲存', 'success', 1500);
        } else {
            throw new Error(data.detail || '儲存失敗');
        }
    } catch (error) {
        showToast(`儲存失敗: ${error.message}`, 'error');
    } finally {
        if (saveSkillSettingsBtn) {
            saveSkillSettingsBtn.disabled = false;
            saveSkillSettingsBtn.textContent = '儲存技能開關設定';
        }
    }
}

// Event listener for manual save button removed as the button is gone.
// if (saveSkillSettingsBtn) {
//     saveSkillSettingsBtn.addEventListener('click', updateSkillSettings);
// }

// Initial data load
loadSettings();
loadRequestLogs();
setupLogAutoRefresh();
setupKnowledgeBase();
loadSkills();

// Status Check Initialization
updateSystemStatus();
updateQdrantStatus();
setInterval(updateSystemStatus, 10000); // Check every 10 seconds
setInterval(updateQdrantStatus, 10000); // Check every 10 seconds
