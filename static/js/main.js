// Initialize Socket.IO connection
const socket = io();

// DOM Elements
const searchInput = document.getElementById('search-input');
const searchType = document.getElementById('search-type');
const searchResults = document.getElementById('search-results');
const updateDbBtn = document.getElementById('update-db-btn');
const loadAnimeListBtn = document.getElementById('load-anime-list-btn');
const animeListContainer = document.getElementById('anime-list');
const seriesDetailCard = document.getElementById('series-detail');
const seriesDetailTitle = document.getElementById('series-detail-title');
const seriesDetailBody = document.getElementById('series-detail-body');
const closeSeriesDetailBtn = document.getElementById('close-series-detail');
const enqueueSelectedBtn = document.getElementById('enqueue-selected-btn');
const selectAllEpisodesCheckbox = document.getElementById('select-all-episodes');
const voeUrlInput = document.getElementById('voe-url');
const voeFilenameInput = document.getElementById('voe-filename');
const voeDownloadBtn = document.getElementById('voe-download-btn');
const resetSessionBtn = document.getElementById('reset-session-btn');

const downloadDirForm = document.getElementById('download-dir-form');
const downloadDirInput = document.getElementById('download-dir');
const currentDownloadDir = document.getElementById('current-download-dir');
const scanDirBtn = document.getElementById('scan-dir-btn');
const clearDbBtn = document.getElementById('clear-db-btn');
const dbStats = document.getElementById('db-stats');
const languageForm = document.getElementById('language-settings-form');
const languagePreferInput = document.getElementById('language-prefer');
const languageRequireDubInput = document.getElementById('language-require-dub');
const languageVerifyWhisperInput = document.getElementById('language-verify-whisper');
const languageRemuxInput = document.getElementById('language-remux');
const languageAcceptErrorInput = document.getElementById('language-accept-error');
const languageSampleSecondsInput = document.getElementById('language-sample-seconds');

const librarySearch = document.getElementById('library-search');
const libraryType = document.getElementById('library-type');
const libraryContent = document.getElementById('library-content');
const refreshLibraryBtn = document.getElementById('refresh-library-btn');

const activeDownloadBody = document.getElementById('active-download-body');
const pauseActiveBtn = document.getElementById('pause-active-btn');
const resumeActiveBtn = document.getElementById('resume-active-btn');
const cancelActiveBtn = document.getElementById('cancel-active-btn');
const queueTable = document.getElementById('queue-table');
const queueTableBody = queueTable ? queueTable.querySelector('tbody') : null;
const queueEmptyAlert = document.getElementById('queue-empty');
const toggleQueueBtn = document.getElementById('toggle-queue-btn');
const historyContainer = document.getElementById('history-container');
const historyFilterGroup = document.getElementById('history-filter');
const statusLog = document.getElementById('status-log');

let latestSnapshot = null;
let historyFilter = 'all';
let queuePaused = false;
let searchTimeout = null;
let currentSeriesDetail = null;
let selectedEpisodes = {};

document.addEventListener('DOMContentLoaded', () => {
    if (searchInput) {
        searchInput.addEventListener('input', handleSearchInput);
    }
    if (searchType) {
        searchType.addEventListener('change', () => {
            if (searchInput && searchInput.value.trim().length > 0) {
                performSearch(searchInput.value.trim(), searchType.value);
            }
        });
    }
    if (updateDbBtn) {
        updateDbBtn.addEventListener('click', updateDatabase);
    }
    if (loadAnimeListBtn) {
        loadAnimeListBtn.addEventListener('click', loadAniworldList);
    }
    if (voeDownloadBtn) {
        voeDownloadBtn.addEventListener('click', startVoeDownload);
    }
    if (resetSessionBtn) {
        resetSessionBtn.addEventListener('click', resetSession);
    }
    if (pauseActiveBtn) {
        pauseActiveBtn.addEventListener('click', () => handleActiveJobAction('pause'));
    }
    if (resumeActiveBtn) {
        resumeActiveBtn.addEventListener('click', () => handleActiveJobAction('resume'));
    }
    if (cancelActiveBtn) {
        cancelActiveBtn.addEventListener('click', () => handleActiveJobAction('cancel'));
    }
    if (toggleQueueBtn) {
        toggleQueueBtn.addEventListener('click', toggleQueueState);
    }
    if (queueTableBody) {
        queueTableBody.addEventListener('click', handleQueueTableClick);
    }
    if (historyContainer) {
        historyContainer.addEventListener('click', handleHistoryActionClick);
    }
    if (historyFilterGroup) {
        historyFilterGroup.addEventListener('click', handleHistoryFilterClick);
    }
    if (closeSeriesDetailBtn) {
        closeSeriesDetailBtn.addEventListener('click', hideSeriesDetail);
    }
    if (enqueueSelectedBtn) {
        enqueueSelectedBtn.addEventListener('click', enqueueSelectedEpisodes);
    }
    if (selectAllEpisodesCheckbox) {
        selectAllEpisodesCheckbox.addEventListener('change', handleSelectAllEpisodes);
    }
    if (downloadDirForm) {
        downloadDirForm.addEventListener('submit', saveDownloadDirectory);
    }
    if (scanDirBtn) {
        scanDirBtn.addEventListener('click', scanDirectory);
    }
    if (clearDbBtn) {
        clearDbBtn.addEventListener('click', clearDatabase);
    }
    if (languageForm) {
        languageForm.addEventListener('submit', saveLanguageSettings);
    }
    if (librarySearch) {
        librarySearch.addEventListener('input', handleLibrarySearch);
    }
    if (libraryType) {
        libraryType.addEventListener('change', () => {
            if (librarySearch && librarySearch.value.trim().length > 0) {
                filterLibraryContent(librarySearch.value.trim(), libraryType.value);
            } else {
                loadLibraryContent();
            }
        });
    }
    if (refreshLibraryBtn) {
        refreshLibraryBtn.addEventListener('click', loadLibraryContent);
    }

    socket.on('status_update', handleStatusUpdate);
    socket.on('queue_update', handleQueueUpdate);
    socket.on('job_event', handleJobEvent);

    checkDownloadStatus();
    loadDownloadDirectory();
    loadDatabaseStats();
    loadLibraryContent();
    loadLanguageSettings();
});

function handleStatusUpdate(snapshot) {
    latestSnapshot = snapshot;
    if (snapshot && Object.prototype.hasOwnProperty.call(snapshot, 'queue_paused')) {
        queuePaused = !!snapshot.queue_paused;
    }
    updateStatusDisplay(snapshot);
}

function handleQueueUpdate(data) {
    if (!data) return;
    if (Object.prototype.hasOwnProperty.call(data, 'queue_paused')) {
        queuePaused = !!data.queue_paused;
        updateQueueToggleButton();
    }
    if (data.queue) {
        renderQueue(data.queue);
    }
    if (data.history) {
        renderHistory(data.history);
    }
    if (data.active && latestSnapshot) {
        latestSnapshot.active = data.active;
        renderActiveJob(data.active, latestSnapshot.status || {});
    }
}

function handleJobEvent(event) {
    if (!statusLog || !event) return;
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    const time = new Date().toLocaleTimeString();
    const title = event.title || event.current_episode_title || event.series_title || '';
    const message = event.error || event.message || '';
    entry.textContent = `[${time}] ${event.event || 'info'} ${title} ${message}`.trim();
    statusLog.prepend(entry);
    while (statusLog.children.length > 100) {
        statusLog.removeChild(statusLog.lastChild);
    }
}

function updateStatusDisplay(snapshot) {
    if (!snapshot) return;
    const status = snapshot.status || {};
    if (Object.prototype.hasOwnProperty.call(snapshot, 'queue_paused')) {
        queuePaused = !!snapshot.queue_paused;
    }
    updateQueueToggleButton();
    renderActiveJob(snapshot.active || null, status);
    renderQueue(snapshot.queue || []);
    renderHistory(snapshot.history || []);
}

function renderActiveJob(activeJob, status) {
    if (!activeDownloadBody) return;
    if (!activeJob) {
        activeDownloadBody.innerHTML = '<p class="text-muted mb-1">Kein aktiver Download</p>';
        if (pauseActiveBtn) pauseActiveBtn.disabled = true;
        if (resumeActiveBtn) resumeActiveBtn.disabled = true;
        if (cancelActiveBtn) cancelActiveBtn.disabled = true;
        return;
    }
    const progress = Math.round(status.progress ?? activeJob.progress ?? 0);
    const bytesDownloaded = formatBytes(status.bytes_downloaded ?? activeJob.bytes_downloaded ?? 0);
    const bytesTotal = formatBytes(status.bytes_total ?? activeJob.bytes_total ?? 0);
    const speed = formatSpeed(status.speed ?? activeJob.speed ?? 0);
    const eta = formatEta(status.eta);
    const currentEpisode = status.current_episode ?? activeJob.current_episode ?? '-';
    const totalEpisodes = status.total_episodes ?? activeJob.total_episodes ?? '-';
    const message = status.status_message || activeJob.error_message || 'Wartet...';
    const languageTag = status.language_tag || activeJob.language_tag || '';
    activeDownloadBody.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-2">
            <div>
                <h5 class="mb-1">${escapeHtml(activeJob.series_title || activeJob.title || activeJob.url)}</h5>
                <div class="text-muted job-meta">${escapeHtml(message)}</div>
            </div>
            <span class="badge bg-secondary text-uppercase">${escapeHtml(status.state || activeJob.status || 'running')}</span>
        </div>
        <div class="progress mb-2">
            <div class="progress-bar" role="progressbar" style="width: ${progress}%" aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100">${progress}%</div>
        </div>
        <div class="job-progress d-flex justify-content-between"><span>Episoden: ${currentEpisode} / ${totalEpisodes}</span><span>${bytesDownloaded} / ${bytesTotal}</span></div>
        <div class="job-progress d-flex justify-content-between text-muted"><span>Sprache: ${languageTag || '-'}</span><span>Geschwindigkeit: ${speed} • ETA: ${eta}</span></div>
    `;
    const isPaused = (status.state || activeJob.status) === 'paused';
    if (pauseActiveBtn) pauseActiveBtn.disabled = isPaused;
    if (resumeActiveBtn) resumeActiveBtn.disabled = !isPaused;
    if (cancelActiveBtn) cancelActiveBtn.disabled = false;
}

function renderQueue(queue) {
    if (!queueTableBody || !queueEmptyAlert) return;
    queueTableBody.innerHTML = '';
    if (!queue || queue.length === 0) {
        queueEmptyAlert.classList.remove('d-none');
        return;
    }
    queueEmptyAlert.classList.add('d-none');
    queue.forEach((job, index) => {
        const row = document.createElement('tr');
        const progress = Math.round(job.progress ?? 0);
        row.innerHTML = `
            <td>${index + 1}</td>
            <td><div class="fw-semibold">${escapeHtml(job.series_title || job.title || job.url)}</div><div class="text-muted small">${escapeHtml(job.url)}</div></td>
            <td>${escapeHtml(job.job_type || 'series')}</td>
            <td>${escapeHtml(job.status)}</td>
            <td>
                <div class="progress" style="height: 6px;">
                    <div class="progress-bar" role="progressbar" style="width: ${progress}%" aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100"></div>
                </div>
            </td>
            <td class="text-end queue-actions">${renderQueueActions(job)}</td>
        `;
        queueTableBody.appendChild(row);
    });
}

function renderQueueActions(job) {
    const actions = [];
    if (job.status === 'paused') {
        actions.push(`<button class="btn btn-sm btn-outline-success" data-action="resume" data-job-id="${job.id}">Fortsetzen</button>`);
    } else {
        actions.push(`<button class="btn btn-sm btn-outline-secondary" data-action="pause" data-job-id="${job.id}">Pause</button>`);
    }
    actions.push(`<button class="btn btn-sm btn-outline-danger" data-action="cancel" data-job-id="${job.id}">Abbrechen</button>`);
    return actions.join(' ');
}

function renderHistory(history) {
    if (!historyContainer) return;
    const filtered = history.filter(job => {
        if (historyFilter === 'completed') {
            return job.status === 'completed';
        }
        if (historyFilter === 'failed') {
            return job.status !== 'completed';
        }
        return true;
    });
    historyContainer.innerHTML = '';
    if (filtered.length === 0) {
        historyContainer.innerHTML = '<div class="alert alert-info">Keine Einträge vorhanden.</div>';
        return;
    }
    filtered.forEach(job => {
        historyContainer.appendChild(renderHistoryCard(job));
    });
}

function renderHistoryCard(job) {
    const card = document.createElement('div');
    card.className = `history-job-card ${job.status === 'completed' ? 'success' : 'failed'}`;
    const finishedAt = job.finished_at ? formatDateTime(job.finished_at) : '-';
    card.innerHTML = `
        <div class="d-flex justify-content-between align-items-center mb-2">
            <div>
                <h6 class="mb-1">${escapeHtml(job.series_title || job.title || job.url)}</h6>
                <div class="job-meta">${escapeHtml(job.url)} • Abschluss: ${finishedAt}</div>
            </div>
            <span class="badge ${job.status === 'completed' ? 'bg-success' : 'bg-danger'} text-uppercase">${escapeHtml(job.status)}</span>
        </div>
        ${job.error_message ? `<div class="alert alert-danger py-1 mb-2 small">${escapeHtml(job.error_message)}</div>` : ''}
        ${renderHistoryResults(job)}
    `;
    return card;
}

function renderHistoryResults(job) {
    if (!job.results || job.results.length === 0) {
        return '<div class="text-muted small">Keine Episoden protokolliert.</div>';
    }
    const rows = job.results.map(result => {
        const statusBadge = result.success ? '<span class="badge bg-success">Fertig</span>' : (result.skipped ? '<span class="badge bg-warning text-dark">Übersprungen</span>' : '<span class="badge bg-danger">Fehler</span>');
        const actions = result.file_path ? `
            <button class="btn btn-sm btn-outline-secondary" data-action="open-result" data-result-id="${result.id}">Öffnen</button>
            <button class="btn btn-sm btn-outline-danger" data-action="delete-result" data-result-id="${result.id}">Löschen</button>` : '';
        const episodeLabel = [];
        if (typeof result.season_num === 'number') {
            episodeLabel.push(`S${String(result.season_num).padStart(2, '0')}`);
        }
        if (typeof result.episode_num === 'number') {
            episodeLabel.push(`E${String(result.episode_num).padStart(2, '0')}`);
        }
        return `
            <tr>
                <td>${episodeLabel.join(' ')}</td>
                <td>${escapeHtml(result.title || '')}</td>
                <td>${escapeHtml(result.language_tag || '')}</td>
                <td>${statusBadge}</td>
                <td class="text-end">${actions}</td>
            </tr>`;
    }).join('');
    return `
        <div class="table-responsive">
            <table class="table table-sm history-results-table align-middle">
                <thead>
                    <tr>
                        <th scope="col">Episode</th>
                        <th scope="col">Titel</th>
                        <th scope="col">Sprache</th>
                        <th scope="col">Status</th>
                        <th scope="col" class="text-end">Aktionen</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows}
                </tbody>
            </table>
        </div>`;
}

function updateQueueToggleButton() {
    if (!toggleQueueBtn) return;
    toggleQueueBtn.textContent = queuePaused ? 'Queue fortsetzen' : 'Queue pausieren';
    toggleQueueBtn.dataset.paused = queuePaused ? 'true' : 'false';
}

// Queue and history interactions
function handleQueueTableClick(event) {
    const target = event.target.closest('[data-action]' );
    if (!target) return;
    const jobId = target.dataset.jobId;
    if (!jobId) return;
    const action = target.dataset.action;
    if (action === 'pause') {
        pauseJob(jobId);
    } else if (action === 'resume') {
        resumeJob(jobId);
    } else if (action === 'cancel') {
        cancelJob(jobId);
    }
}

function handleHistoryActionClick(event) {
    const target = event.target.closest('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    const resultId = target.dataset.resultId;
    if (!resultId) return;
    if (action === 'open-result') {
        openDownloadResult(resultId);
    } else if (action === 'delete-result') {
        deleteDownloadResult(resultId);
    }
}

function handleHistoryFilterClick(event) {
    const button = event.target.closest('[data-history-filter]');
    if (!button) return;
    historyFilterGroup.querySelectorAll('[data-history-filter]').forEach(btn => btn.classList.remove('active'));
    button.classList.add('active');
    historyFilter = button.dataset.historyFilter || 'all';
    if (latestSnapshot && latestSnapshot.history) {
        renderHistory(latestSnapshot.history);
    }
}

function toggleQueueState() {
    const paused = !queuePaused;
    fetch('/api/download/queue/pause', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paused })
    })
        .then(handleResponse)
        .then(data => {
            queuePaused = data.paused;
            updateQueueToggleButton();
        })
        .catch(err => console.error('Queue toggle error:', err));
}

function handleActiveJobAction(action) {
    if (!latestSnapshot || !latestSnapshot.active) return;
    const jobId = latestSnapshot.active.id;
    if (!jobId) return;
    if (action === 'pause') {
        pauseJob(jobId);
    } else if (action === 'resume') {
        resumeJob(jobId);
    } else if (action === 'cancel') {
        cancelJob(jobId);
    }
}

function pauseJob(jobId) {
    fetch(`/api/download/jobs/${jobId}/pause`, { method: 'POST' })
        .then(handleResponse)
        .then(() => checkDownloadStatus())
        .catch(err => console.error('Pause job error:', err));
}

function resumeJob(jobId) {
    fetch(`/api/download/jobs/${jobId}/resume`, { method: 'POST' })
        .then(handleResponse)
        .then(() => checkDownloadStatus())
        .catch(err => console.error('Resume job error:', err));
}

function cancelJob(jobId) {
    fetch(`/api/download/jobs/${jobId}/cancel`, { method: 'POST' })
        .then(handleResponse)
        .then(() => checkDownloadStatus())
        .catch(err => console.error('Cancel job error:', err));
}

function openDownloadResult(resultId) {
    fetch(`/api/download/results/${resultId}/open`, { method: 'POST' })
        .then(handleResponse)
        .catch(err => console.error('Open result error:', err));
}

function deleteDownloadResult(resultId) {
    fetch(`/api/download/results/${resultId}`, { method: 'DELETE' })
        .then(handleResponse)
        .then(() => checkDownloadStatus())
        .catch(err => console.error('Delete result error:', err));
}

// Search and series detail
function handleSearchInput() {
    const query = searchInput.value.trim();
    if (searchTimeout) {
        clearTimeout(searchTimeout);
    }
    if (query.length === 0) {
        searchResults.innerHTML = '';
        return;
    }
    searchTimeout = setTimeout(() => {
        performSearch(query, searchType ? searchType.value : 'all');
    }, 300);
}

function performSearch(query, type) {
    fetch(`/search?q=${encodeURIComponent(query)}&type=${encodeURIComponent(type)}`)
        .then(handleResponse)
        .then(results => renderSearchResults(results || []))
        .catch(err => console.error('Search error:', err));
}

function renderSearchResults(results) {
    if (!searchResults) return;
    searchResults.innerHTML = '';
    if (!Array.isArray(results) || results.length === 0) {
        searchResults.innerHTML = '<div class="alert alert-info">Keine Ergebnisse.</div>';
        return;
    }
    results.forEach(item => {
        const entry = document.createElement('a');
        entry.href = '#';
        entry.className = 'list-group-item list-group-item-action d-flex justify-content-between align-items-center';
        entry.innerHTML = `<span>${escapeHtml(item.title || item.url)}</span><span class="badge bg-secondary">${escapeHtml(item.type || 'serie')}</span>`;
        entry.addEventListener('click', (event) => {
            event.preventDefault();
            showSeriesDetail(item);
        });
        searchResults.appendChild(entry);
    });
}

function loadAniworldList() {
    if (!animeListContainer) return;
    animeListContainer.classList.remove('d-none');
    animeListContainer.innerHTML = '<div class="alert alert-info">Lade Liste...</div>';
    fetch('/api/scrape/list', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'anime' })
    })
        .then(handleResponse)
        .then(response => {
            if (response.status === 'success' && Array.isArray(response.items)) {
                renderAniworldList(response.items);
            } else {
                animeListContainer.innerHTML = '<div class="alert alert-danger">Fehler beim Laden der Liste.</div>';
            }
        })
        .catch(err => {
            console.error('Aniworld list error:', err);
            animeListContainer.innerHTML = '<div class="alert alert-danger">Fehler beim Laden der Liste.</div>';
        });
}

function renderAniworldList(items) {
    if (!animeListContainer) return;
    if (!Array.isArray(items) || items.length === 0) {
        animeListContainer.innerHTML = '<div class="alert alert-info">Keine Einträge.</div>';
        return;
    }
    const listGroup = document.createElement('div');
    listGroup.className = 'list-group';
    items.forEach(item => {
        const entry = document.createElement('a');
        entry.href = '#';
        entry.className = 'list-group-item list-group-item-action d-flex justify-content-between align-items-center';
        entry.innerHTML = `<span>${escapeHtml(item.title || item.url)}</span><span class="badge bg-secondary">${escapeHtml(item.type || 'anime')}</span>`;
        entry.addEventListener('click', (event) => {
            event.preventDefault();
            showSeriesDetail(item);
        });
        listGroup.appendChild(entry);
    });
    animeListContainer.innerHTML = '';
    animeListContainer.appendChild(listGroup);
}

function showSeriesDetail(item) {
    if (!seriesDetailCard) return;
    seriesDetailCard.classList.remove('d-none');
    seriesDetailTitle.textContent = item.title || item.url;
    seriesDetailBody.innerHTML = '<div class="text-muted">Lade Details...</div>';
    selectedEpisodes = {};
    fetch('/api/anime/details', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: item.url })
    })
        .then(handleResponse)
        .then(response => {
            if (response.status === 'success' && response.data) {
                currentSeriesDetail = response.data;
                renderSeriesDetail(response.data);
            } else {
                seriesDetailBody.innerHTML = '<div class="alert alert-danger">Details konnten nicht geladen werden.</div>';
            }
        })
        .catch(err => {
            console.error('Detail error:', err);
            seriesDetailBody.innerHTML = '<div class="alert alert-danger">Details konnten nicht geladen werden.</div>';
        });
}

function hideSeriesDetail() {
    if (!seriesDetailCard) return;
    seriesDetailCard.classList.add('d-none');
    seriesDetailBody.innerHTML = '';
    selectedEpisodes = {};
    currentSeriesDetail = null;
    if (selectAllEpisodesCheckbox) {
        selectAllEpisodesCheckbox.checked = false;
    }
}

function renderSeriesDetail(detail) {
    if (!seriesDetailBody) return;
    if (!detail || !Array.isArray(detail.seasons)) {
        seriesDetailBody.innerHTML = '<div class="alert alert-info">Keine Episoden gefunden.</div>';
        return;
    }
    const fragment = document.createDocumentFragment();
    detail.seasons.forEach(season => {
        const container = document.createElement('div');
        container.className = 'series-detail-season';
        container.innerHTML = `<h6>Staffel ${season.season}</h6>`;
        const episodesList = document.createElement('div');
        episodesList.className = 'series-detail-episodes';
        (season.episodes || []).forEach(ep => {
            const check = document.createElement('div');
            check.className = 'form-check';
            check.innerHTML = `
                <input class="form-check-input" type="checkbox" value="${ep.number}" data-season="${season.season}" data-episode-title="${escapeHtml(ep.title || '')}">
                <label class="form-check-label">${String(ep.number).padStart(2, '0')} - ${escapeHtml(ep.title || 'Episode')} (${ep.has_german_dub ? 'GerDub' : ep.has_german_sub ? 'GerSub' : '—'})</label>`;
            check.querySelector('input').addEventListener('change', handleEpisodeCheckboxChange);
            episodesList.appendChild(check);
        });
        container.appendChild(episodesList);
        fragment.appendChild(container);
    });
    seriesDetailBody.innerHTML = '';
    seriesDetailBody.appendChild(fragment);
}

function handleSelectAllEpisodes(event) {
    const checked = event.target.checked;
    if (!seriesDetailBody) return;
    seriesDetailBody.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.checked = checked;
        cb.dispatchEvent(new Event('change'));
    });
}

function enqueueSelectedEpisodes() {
    if (!currentSeriesDetail) return;
    const payload = {
        url: currentSeriesDetail.url,
        title: currentSeriesDetail.title,
        options: {
            selected_episodes: serializeSelection(),
            series_title: currentSeriesDetail.title
        }
    };
    startDownload(payload);
}

function serializeSelection() {
    const result = {};
    Object.keys(selectedEpisodes).forEach(season => {
        const values = Array.from(selectedEpisodes[season]);
        if (values.length > 0) {
            result[season] = values;
        }
    });
    if (Object.keys(result).length === 0 && currentSeriesDetail) {
        // No explicit selection: download all episodes
        currentSeriesDetail.seasons.forEach(season => {
            result[season.season] = 'all';
        });
    }
    return result;
}

// Download helpers
function startDownload(payload) {
    let body;
    if (typeof payload === 'string') {
        body = { url: payload };
    } else {
        body = payload;
    }
    fetch('/api/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    })
        .then(handleResponse)
        .then(response => {
            if (response && response.job) {
                hideSeriesDetail();
                checkDownloadStatus();
            } else if (response && response.status === 'queued') {
                checkDownloadStatus();
            }
        })
        .catch(err => {
            console.error('Download start error:', err);
            alert('Fehler beim Starten des Downloads');
        });
}

function startVoeDownload() {
    const url = voeUrlInput ? voeUrlInput.value.trim() : '';
    const filename = voeFilenameInput ? voeFilenameInput.value.trim() : '';
    if (!url) {
        alert('Bitte gib eine VOE.sx URL ein');
        return;
    }
    fetch('/download_voe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, filename: filename || null })
    })
        .then(handleResponse)
        .then(() => checkDownloadStatus())
        .catch(err => {
            console.error('VOE download error:', err);
            alert('Fehler beim Starten des VOE Downloads');
        });
}

function resetSession() {
    fetch('/api/reset', { method: 'POST' })
        .then(handleResponse)
        .then(() => {
            alert('Session zurückgesetzt');
            checkDownloadStatus();
        })
        .catch(err => {
            console.error('Reset error:', err);
            alert('Fehler beim Zurücksetzen der Session');
        });
}

// Settings & status fetchers
function checkDownloadStatus() {
    fetch('/api/download/status')
        .then(handleResponse)
        .then(snapshot => {
            latestSnapshot = snapshot;
            updateStatusDisplay(snapshot);
        })
        .catch(err => console.error('Status error:', err));
}

function loadDownloadDirectory() {
    fetch('/api/settings/download-dir')
        .then(handleResponse)
        .then(data => {
            if (!data) return;
            if (data.download_dir && currentDownloadDir) {
                currentDownloadDir.textContent = data.download_dir;
            }
            if (downloadDirInput && data.download_dir) {
                downloadDirInput.value = data.download_dir;
            }
        })
        .catch(err => console.error('Download directory error:', err));
}

function saveDownloadDirectory(event) {
    event.preventDefault();
    if (!downloadDirInput) return;
    fetch('/api/settings/download-dir', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ directory: downloadDirInput.value.trim() })
    })
        .then(handleResponse)
        .then(() => loadDownloadDirectory())
        .catch(err => console.error('Download directory save error:', err));
}

function scanDirectory() {
    alert('Verzeichnis-Scan ist derzeit nicht verfügbar.');
}

function clearDatabase() {
    alert('Zurücksetzen der Datenbank ist derzeit nicht verfügbar.');
}

function loadDatabaseStats() {
    fetch('/api/media/stats')
        .then(handleResponse)
        .then(data => {
            if (!dbStats) return;
            if (!data || data.status !== 'success') {
                dbStats.innerHTML = '<p>Fehler beim Laden der Statistiken.</p>';
                return;
            }
            dbStats.innerHTML = `
                <p>Serien/Animes: ${data.media_count}</p>
                <p>Staffeln: ${data.season_count}</p>
                <p>Episoden: ${data.episode_count}</p>
            `;
        })
        .catch(err => {
            console.error('DB stats error:', err);
            if (dbStats) {
                dbStats.innerHTML = '<p>Fehler beim Laden der Statistiken.</p>';
            }
        });
}

function loadLanguageSettings() {
    if (!languageForm) return;
    fetch('/api/settings/language')
        .then(handleResponse)
        .then(data => {
            if (!data || data.status !== 'success') return;
            if (languagePreferInput) languagePreferInput.value = (data.prefer || []).join(',');
            if (languageRequireDubInput) languageRequireDubInput.checked = !!data.require_dub;
            if (languageVerifyWhisperInput) languageVerifyWhisperInput.checked = !!data.verify_with_whisper;
            if (languageRemuxInput) languageRemuxInput.checked = !!data.remux_to_de_if_present;
            if (languageAcceptErrorInput) languageAcceptErrorInput.checked = !!data.accept_on_error;
            if (languageSampleSecondsInput) languageSampleSecondsInput.value = data.sample_seconds || 45;
        })
        .catch(err => console.error('Language settings error:', err));
}

function saveLanguageSettings(event) {
    event.preventDefault();
    fetch('/api/settings/language', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            prefer: languagePreferInput ? languagePreferInput.value.split(',').map(v => v.trim()).filter(Boolean) : ['de','deu','ger'],
            require_dub: languageRequireDubInput ? languageRequireDubInput.checked : true,
            verify_with_whisper: languageVerifyWhisperInput ? languageVerifyWhisperInput.checked : true,
            remux_to_de_if_present: languageRemuxInput ? languageRemuxInput.checked : true,
            accept_on_error: languageAcceptErrorInput ? languageAcceptErrorInput.checked : false,
            sample_seconds: languageSampleSecondsInput ? Number(languageSampleSecondsInput.value) || 45 : 45
        })
    })
        .then(handleResponse)
        .catch(err => console.error('Language settings save error:', err));
}

// Library helpers
function loadLibraryContent() {
    fetch('/api/media/list')
        .then(handleResponse)
        .then(data => {
            if (!libraryContent) return;
            if (!data || data.status !== 'success') {
                libraryContent.innerHTML = '<div class="alert alert-danger">Fehler beim Laden der Mediathek.</div>';
                return;
            }
            renderLibrary(data.media || []);
        })
        .catch(err => {
            console.error('Library load error:', err);
            if (libraryContent) {
                libraryContent.innerHTML = '<div class="alert alert-danger">Fehler beim Laden der Mediathek.</div>';
            }
        });
}

function renderLibrary(items) {
    if (!libraryContent) return;
    if (!Array.isArray(items) || items.length === 0) {
        libraryContent.innerHTML = '<div class="alert alert-info">Keine Einträge in der Mediathek.</div>';
        return;
    }
    const fragment = document.createDocumentFragment();
    items.forEach(item => {
        const card = document.createElement('div');
        card.className = 'card mb-3';
        card.innerHTML = `
            <div class="card-body">
                <div class="d-flex justify-content-between">
                    <div>
                        <h5 class="card-title">${escapeHtml(item.title || item.url)}</h5>
                        <div class="text-muted small">${escapeHtml(item.type || 'serie')}</div>
                    </div>
                    <span class="badge bg-secondary">${item.episode_count || 0} Episoden</span>
                </div>
            </div>`;
        card.addEventListener('click', () => {
            if (item.id) {
                fetch(`/api/media/details/${item.id}`)
                    .then(handleResponse)
                    .then(response => {
                        // reuse series detail rendering
                        if (response && response.status === 'success' && response.media) {
                            showLibraryDetail(response.media);
                        }
                    });
            }
        });
        fragment.appendChild(card);
    });
    libraryContent.innerHTML = '';
    libraryContent.appendChild(fragment);
}

function showLibraryDetail(media) {
    if (!media) return;
    seriesDetailCard.classList.remove('d-none');
    seriesDetailTitle.textContent = media.title || media.directory;
    selectedEpisodes = {};
    const seasons = media.seasons || [];
    const detail = {
        url: media.url,
        title: media.title,
        seasons: seasons.map(season => ({
            season: season.season_number,
            episodes: season.episodes || []
        }))
    };
    currentSeriesDetail = detail;
    renderSeriesDetail(detail);
}

function handleLibrarySearch() {
    if (!librarySearch) return;
    const query = librarySearch.value.trim();
    if (query.length === 0) {
        loadLibraryContent();
        return;
    }
    filterLibraryContent(query, libraryType ? libraryType.value : 'all');
}

function filterLibraryContent(query, type) {
    if (!libraryContent) return;
    const cards = libraryContent.querySelectorAll('.card');
    cards.forEach(card => {
        const title = card.querySelector('.card-title')?.textContent?.toLowerCase() || '';
        const badge = card.querySelector('.text-muted')?.textContent?.toLowerCase() || '';
        const matchesQuery = title.includes(query.toLowerCase());
        const matchesType = type === 'all' || badge.includes(type.toLowerCase());
        card.style.display = matchesQuery && matchesType ? '' : 'none';
    });
}

// Utilities
function handleResponse(response) {
    if (!response.ok) {
        return response.json().then(data => {
            throw data;
        }).catch(() => {
            throw new Error('Unbekannter Fehler');
        });
    }
    return response.json();
}

function formatBytes(bytes) {
    if (!bytes && bytes !== 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }
    return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function formatSpeed(bytesPerSecond) {
    if (!bytesPerSecond) return '-';
    return `${formatBytes(bytesPerSecond)}/s`;
}

function formatEta(seconds) {
    if (!seconds && seconds !== 0) return '-';
    if (seconds < 0) return '-';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    if (mins === 0) {
        return `${secs}s`;
    }
    return `${mins}m ${secs}s`;
}

function formatDateTime(value) {
    if (!value) return '-';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '-';
    return `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`;
}

function escapeHtml(text) {
    if (text === undefined || text === null) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function handleEpisodeCheckboxChange(event) {
    const checkbox = event.target;
    const season = checkbox.dataset.season;
    const episode = Number(checkbox.value);
    if (!season || Number.isNaN(episode)) return;
    if (!selectedEpisodes[season]) {
        selectedEpisodes[season] = new Set();
    }
    if (checkbox.checked) {
        selectedEpisodes[season].add(episode);
    } else {
        selectedEpisodes[season].delete(episode);
        if (selectedEpisodes[season].size === 0) {
            delete selectedEpisodes[season];
        }
    }
}

function updateDatabase() {
    loadAniworldList();
}
