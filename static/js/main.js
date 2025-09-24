// Initialize Socket.IO connection
const socket = io();

// DOM Elements
const searchInput = document.getElementById('search-input');
const searchType = document.getElementById('search-type');
const searchResults = document.getElementById('search-results');
const updateDbBtn = document.getElementById('update-db-btn');
const loadAnimeListBtn = document.getElementById('load-anime-list-btn');
const animeListContainer = document.getElementById('anime-list');
const voeUrlInput = document.getElementById('voe-url');
const voeFilenameInput = document.getElementById('voe-filename');
const voeDownloadBtn = document.getElementById('voe-download-btn');
const resetSessionBtn = document.getElementById('reset-session-btn');

// Settings elements
const downloadDirForm = document.getElementById('download-dir-form');
const downloadDirInput = document.getElementById('download-dir');
const currentDownloadDir = document.getElementById('current-download-dir');
const scanDirBtn = document.getElementById('scan-dir-btn');
const clearDbBtn = document.getElementById('clear-db-btn');
const dbStats = document.getElementById('db-stats');

// Library elements
const libraryForm = document.getElementById('library-form');
const libraryNameInput = document.getElementById('library-name');
const libraryPathInput = document.getElementById('library-path');
const libraryDefaultInput = document.getElementById('library-default');
const librariesList = document.getElementById('libraries-list');
const librarySearch = document.getElementById('library-search');
const libraryType = document.getElementById('library-type');
const libraryContent = document.getElementById('library-content');
const refreshLibraryBtn = document.getElementById('refresh-library-btn');

// Status elements
const statusViews = [
    {
        container: document.getElementById('downloadStatus'),
        title: document.getElementById('statusTitle'),
        message: document.getElementById('statusMessage'),
        progressBar: document.getElementById('statusProgress'),
        progressWrapper: (() => {
            const bar = document.getElementById('statusProgress');
            return bar ? bar.parentElement : null;
        })(),
        progressDisplayStyle: 'block',
        episode: document.getElementById('statusEpisode'),
        totalEpisodes: document.getElementById('statusTotalEpisodes'),
        cancelButton: document.getElementById('cancelDownloadBtn'),
        hideWhenInactive: true,
    },
    {
        container: document.getElementById('download-status-card'),
        title: document.getElementById('status-card-title'),
        message: document.getElementById('status-card-message'),
        progressBar: document.getElementById('status-card-progress'),
        progressWrapper: document.getElementById('status-card-progress-wrapper'),
        progressDisplayStyle: 'block',
        episode: document.getElementById('status-card-episode'),
        totalEpisodes: document.getElementById('status-card-total-episodes'),
        cancelButton: document.getElementById('cancel-download-btn'),
        hideWhenInactive: false,
    },
].filter(view => view.container || view.title || view.message || view.progressBar || view.cancelButton);

const cancelButtons = Array.from(new Set(statusViews
    .map(view => view.cancelButton)
    .filter((btn) => Boolean(btn))));

// Variables
let searchTimeout = null;
let isDownloading = false;

// Event Listeners
document.addEventListener('DOMContentLoaded', () => {
    // Live search with debounce
    searchInput.addEventListener('input', handleSearchInput);

    // Type filter change
    searchType.addEventListener('change', () => {
        if (searchInput.value.trim().length > 0) {
            performSearch(searchInput.value.trim(), searchType.value);
        }
    });

    // Update database button
    updateDbBtn.addEventListener('click', updateDatabase);

    // Load Aniworld list
    if (loadAnimeListBtn) {
        loadAnimeListBtn.addEventListener('click', loadAniworldList);
    }

    // VOE.sx download button
    voeDownloadBtn.addEventListener('click', startVoeDownload);

    // Reset session button
    resetSessionBtn.addEventListener('click', resetSession);

    // Cancel download button
    cancelButtons.forEach((btn) => {
        btn.addEventListener('click', cancelDownload);
    });

    // Check download status on page load
    checkDownloadStatus();
    setInterval(checkDownloadStatus, 5000);

    // Load download directory
    loadDownloadDirectory();

    // Load database statistics
    loadDatabaseStats();

    // Load library content
    loadLibraryContent();
    loadLibraries();

    // Settings event listeners
    downloadDirForm.addEventListener('submit', (e) => {
        e.preventDefault();
        updateDownloadDirectory();
    });

    scanDirBtn.addEventListener('click', scanDirectory);
    clearDbBtn.addEventListener('click', clearDatabase);

    // Library event listeners
    if (libraryForm) {
        libraryForm.addEventListener('submit', handleLibraryFormSubmit);
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
        refreshLibraryBtn.addEventListener('click', () => {
            loadLibraryContent();
            loadLibraries();
        });
    }

    // Socket.IO event listeners
    socket.on('connect', () => {
        console.log('Connected to server');
    });

    socket.on('status_update', updateStatusDisplay);
    socket.on('download_progress', updateStatusDisplay);
});

// Functions

/**
 * Handle search input with debounce
 */
function handleSearchInput() {
    const query = searchInput.value.trim();

    // Clear previous timeout
    if (searchTimeout) {
        clearTimeout(searchTimeout);
    }

    // If query is empty, clear results
    if (query.length === 0) {
        searchResults.innerHTML = '';
        return;
    }

    // Set a timeout to avoid too many requests
    searchTimeout = setTimeout(() => {
        performSearch(query, searchType.value);
    }, 300); // 300ms debounce
}

/**
 * Perform search request
 */
function performSearch(query, type) {
    fetch(`/search?q=${encodeURIComponent(query)}&type=${type}`)
        .then(response => response.json())
        .then(data => {
            displaySearchResults(data);
        })
        .catch(error => {
            console.error('Search error:', error);
        });
}

/**
 * Display search results
 */
function displaySearchResults(results) {
    searchResults.innerHTML = '';

    if (results.length === 0) {
        searchResults.innerHTML = '<div class="alert alert-info">Keine Ergebnisse gefunden</div>';
        return;
    }

    results.forEach(item => {
        const resultItem = document.createElement('a');
        resultItem.href = '#';
        resultItem.className = 'list-group-item list-group-item-action search-result-item d-flex justify-content-between align-items-center';
        resultItem.innerHTML = `
            <div>
                <strong>${item.title}</strong>
                <span class="badge bg-${item.type === 'anime' ? 'primary' : 'secondary'}">${item.type === 'anime' ? 'Anime' : 'Serie'}</span>
            </div>
            <button class="btn btn-sm btn-success download-btn">Download</button>
        `;

        // Add click event for download button
        resultItem.querySelector('.download-btn').addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            startDownload(item.url);
        });

        searchResults.appendChild(resultItem);
    });
}

/**
 * Update database
 */
function updateDatabase() {
    const type = searchType.value === 'anime' ? 'anime' : 'series';

    updateDbBtn.disabled = true;
    updateDbBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Aktualisiere...';

    fetch('/api/scrape/list', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ type })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            alert(`Datenbank erfolgreich aktualisiert. ${data.count} Einträge gefunden.`);
        } else {
            alert('Fehler beim Aktualisieren der Datenbank: ' + (data.error || 'Unbekannter Fehler'));
        }
    })
    .catch(error => {
        console.error('Error updating database:', error);
        alert('Fehler beim Aktualisieren der Datenbank');
    })
    .finally(() => {
        updateDbBtn.disabled = false;
        updateDbBtn.innerHTML = 'Datenbank aktualisieren';
    });
}

/**
 * Load Aniworld list via backend scrape
 */
function loadAniworldList() {
    if (!loadAnimeListBtn || !animeListContainer) {
        return;
    }

    const originalLabel = loadAnimeListBtn.innerHTML;
    loadAnimeListBtn.disabled = true;
    loadAnimeListBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Laedt...';

    showAniworldMessage('Lade Aniworld-Liste...', 'info');

    fetch('/api/scrape/list', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ type: 'anime' })
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success' && Array.isArray(data.items)) {
                renderAniworldList(data.items);
            } else {
                const message = typeof data.error === 'string' ? data.error : 'Unbekannter Fehler';
                showAniworldMessage('Fehler beim Laden: ' + message, 'danger');
            }
        })
        .catch(error => {
            console.error('Error loading Aniworld list:', error);
            showAniworldMessage('Fehler beim Laden der Aniworld-Liste', 'danger');
        })
        .finally(() => {
            loadAnimeListBtn.disabled = false;
            loadAnimeListBtn.innerHTML = originalLabel;
        });
}

/**
 * Render Aniworld results
 */
function renderAniworldList(items) {
    if (!animeListContainer) {
        return;
    }

    animeListContainer.innerHTML = '';
    animeListContainer.classList.remove('d-none');

    if (!Array.isArray(items) || items.length === 0) {
        showAniworldMessage('Keine Animes gefunden.', 'info');
        return;
    }

    const summary = document.createElement('div');
    summary.className = 'alert alert-secondary mb-2';
    summary.textContent = items.length + ' Animes geladen';
    animeListContainer.appendChild(summary);

    const listGroup = document.createElement('div');
    listGroup.className = 'list-group';

    items.forEach((item) => {
        const entry = document.createElement('a');
        entry.href = '#';
        entry.className = 'list-group-item list-group-item-action d-flex justify-content-between align-items-center';

        const titleWrapper = document.createElement('div');
        const title = document.createElement('strong');
        title.textContent = item.title || 'Unbekannt';
        titleWrapper.appendChild(title);

        const badge = document.createElement('span');
        badge.className = 'badge bg-primary ms-2';
        badge.textContent = 'Anime';
        titleWrapper.appendChild(badge);

        const downloadBtn = document.createElement('button');
        downloadBtn.className = 'btn btn-sm btn-success download-btn';
        downloadBtn.textContent = 'Download';

        if (!item.url) {
            downloadBtn.disabled = true;
            downloadBtn.classList.remove('btn-success');
            downloadBtn.classList.add('btn-secondary');
            downloadBtn.textContent = 'Kein Link';
        } else {
            downloadBtn.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                startDownload(item.url);
            });
        }

        entry.appendChild(titleWrapper);
        entry.appendChild(downloadBtn);
        listGroup.appendChild(entry);
    });

    animeListContainer.appendChild(listGroup);
}

/**
 * Show helper message inside Aniworld list container
 */
function showAniworldMessage(message, level = 'info') {
    if (!animeListContainer) {
        return;
    }

    animeListContainer.innerHTML = '';
    const alert = document.createElement('div');
    alert.className = 'alert alert-' + level + ' mb-0';
    alert.textContent = message;
    animeListContainer.appendChild(alert);
    animeListContainer.classList.remove('d-none');
}

/**
 * Start download
 */
function startDownload(url) {
    if (isDownloading) {
        alert('Es läuft bereits ein Download!');
        return;
    }

    fetch('/download', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ url })
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.error || 'Unbekannter Fehler');
            });
        }
        return response.json();
    })
    .then(data => {
        console.log('Download started:', data);
        // Switch to status tab
        document.getElementById('status-tab').click();
    })
    .catch(error => {
        console.error('Error starting download:', error);
        alert('Fehler beim Starten des Downloads: ' + error.message);
    });
}

/**
 * Start VOE.sx download
 */
function startVoeDownload() {
    const url = voeUrlInput.value.trim();
    const filename = voeFilenameInput.value.trim();

    if (!url) {
        alert('Bitte gib eine VOE.sx URL ein');
        return;
    }

    if (isDownloading) {
        alert('Es läuft bereits ein Download!');
        return;
    }

    fetch('/download_voe', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            url: url,
            filename: filename || null
        })
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.error || 'Unbekannter Fehler');
            });
        }
        return response.json();
    })
    .then(data => {
        console.log('VOE download started:', data);
        // Switch to status tab
        document.getElementById('status-tab').click();
    })
    .catch(error => {
        console.error('Error starting VOE download:', error);
        alert('Fehler beim Starten des Downloads: ' + error.message);
    });
}

/**
 * Reset session
 */
function resetSession() {
    fetch('/api/reset', {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        console.log('Session reset:', data);
        alert('Session zurückgesetzt');
        checkDownloadStatus();
    })
    .catch(error => {
        console.error('Error resetting session:', error);
        alert('Fehler beim Zurücksetzen der Session');
    });
}

/**
 * Cancel download
 */
function cancelDownload() {
    if (!isDownloading) {
        return;
    }

    fetch('/api/cancel', {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        console.log('Download cancelled:', data);
        if (data && data.ok) {
            alert('Download abgebrochen');
        } else {
            const message = (data && data.message) ? data.message : 'Kein aktiver Download';
            alert(message);
        }
        checkDownloadStatus();
    })
    .catch(error => {
        console.error('Error cancelling download:', error);
        alert('Fehler beim Abbrechen des Downloads');
    });
}

/**
 * Check download status
 */
function checkDownloadStatus() {
    fetch('/api/downloads/status')
        .then(response => response.json())
        .then(data => {
            if (data && data.ok) {
                updateStatusDisplay(data);
            }
        })
        .catch(error => {
            console.error('Error checking download status:', error);
        });
}

/**
 * Update status display
 */
function updateStatusDisplay(payload) {
    if (statusViews.length === 0) {
        return;
    }

    console.log('Status update:', payload);

    let activeJob = null;
    let active = false;

    if (payload) {
        if (payload.ok && payload.data) {
            active = Boolean(payload.data.is_downloading);
            activeJob = payload.data.active || null;
        } else if (Object.prototype.hasOwnProperty.call(payload, 'job') || Object.prototype.hasOwnProperty.call(payload, 'is_downloading')) {
            active = Boolean(payload.is_downloading);
            activeJob = payload.job || null;
        } else if (Object.prototype.hasOwnProperty.call(payload, 'is_downloading')) {
            active = Boolean(payload.is_downloading);
        }

        if (!activeJob && (payload.current_title || payload.status_message)) {
            activeJob = {
                progress: payload.progress,
                message: payload.status_message,
                series_name: payload.current_title,
                current_episode: payload.current_episode,
                total_episodes: payload.total_episodes,
            };
        }
    }

    const firstMessageView = statusViews.find((view) => view.message);
    const previousMessage = firstMessageView && firstMessageView.message
        ? firstMessageView.message.textContent || '-'
        : '-';
    const fallbackMessage = payload && payload.status_message
        ? payload.status_message
        : previousMessage;

    isDownloading = active;

    if (active && activeJob) {
        const pct = typeof activeJob.progress === 'number' && !Number.isNaN(activeJob.progress)
            ? Math.max(0, Math.min(100, activeJob.progress))
            : null;

        statusViews.forEach((view) => {
            const {
                container,
                title,
                message,
                progressBar,
                progressWrapper,
                progressDisplayStyle,
                episode,
                totalEpisodes,
                cancelButton,
                hideWhenInactive,
            } = view;

            if (container) {
                container.classList.add('downloading');
                if (hideWhenInactive) {
                    container.classList.remove('hidden');
                }
            }

            if (title) {
                title.textContent = activeJob.series_name || activeJob.title || 'Download läuft...';
            }

            if (message) {
                message.textContent = activeJob.message || activeJob.status_message || 'Verarbeite...';
            }

            if (progressWrapper && progressDisplayStyle) {
                progressWrapper.style.display = progressDisplayStyle;
            }

            if (progressBar && pct !== null) {
                progressBar.style.width = `${pct}%`;
                progressBar.textContent = `${Math.round(pct)}%`;
            }

            if (episode) {
                episode.textContent = activeJob.current_episode || '1';
            }

            if (totalEpisodes) {
                totalEpisodes.textContent = activeJob.total_episodes || '?';
            }

            if (cancelButton) {
                cancelButton.style.display = 'inline-block';
            }
        });
    } else {
        statusViews.forEach((view) => {
            const {
                container,
                title,
                message,
                progressWrapper,
                episode,
                totalEpisodes,
                cancelButton,
                hideWhenInactive,
            } = view;

            if (container) {
                container.classList.remove('downloading');
                if (hideWhenInactive) {
                    container.classList.add('hidden');
                }
            }

            if (title) {
                title.textContent = 'Kein aktiver Download';
            }

            if (message) {
                message.textContent = (activeJob && (activeJob.message || activeJob.status_message)) || fallbackMessage || '-';
            }

            if (progressWrapper) {
                progressWrapper.style.display = 'none';
            }

            if (episode) {
                episode.textContent = '-';
            }

            if (totalEpisodes) {
                totalEpisodes.textContent = '-';
            }

            if (cancelButton) {
                cancelButton.style.display = 'none';
            }
        });
    }
}

/**
 * Load download directory
 */
function loadDownloadDirectory() {
    fetch('/api/settings/download-dir')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                currentDownloadDir.textContent = data.download_dir;
                downloadDirInput.value = data.download_dir;
            }
        })
        .catch(error => {
            console.error('Error loading download directory:', error);
            currentDownloadDir.textContent = 'Fehler beim Laden';
        });
}

/**
 * Update download directory
 */
function updateDownloadDirectory() {
    const newDir = downloadDirInput.value.trim();

    if (!newDir) {
        alert('Bitte gib ein Verzeichnis ein');
        return;
    }

    fetch('/api/settings/download-dir', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ download_dir: newDir })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            currentDownloadDir.textContent = data.download_dir;
            alert(data.message);
        } else {
            alert('Fehler: ' + (data.error || 'Unbekannter Fehler'));
        }
    })
    .catch(error => {
        console.error('Error updating download directory:', error);
        alert('Fehler beim Aktualisieren des Download-Verzeichnisses');
    });
}

/**
 * Scan directory
 */
function scanDirectory() {
    if (!confirm('Möchtest du das Download-Verzeichnis scannen? Dies kann je nach Größe des Verzeichnisses einige Zeit dauern.')) {
        return;
    }

    scanDirBtn.disabled = true;
    scanDirBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Scanne...';

    fetch('/api/settings/download-dir', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ scan_only: true })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            alert(data.message);
            loadDatabaseStats();
            loadLibraryContent();
        } else {
            alert('Fehler: ' + (data.error || 'Unbekannter Fehler'));
        }
    })
    .catch(error => {
        console.error('Error scanning directory:', error);
        alert('Fehler beim Scannen des Verzeichnisses');
    })
    .finally(() => {
        scanDirBtn.disabled = false;
        scanDirBtn.textContent = 'Verzeichnis scannen';
    });
}

/**
 * Clear database
 */
function clearDatabase() {
    if (!confirm('Möchtest du wirklich die Datenbank zurücksetzen? Alle Informationen über vorhandene Serien und Animes werden gelöscht.')) {
        return;
    }

    clearDbBtn.disabled = true;
    clearDbBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Lösche...';

    fetch('/api/media/clear', {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            alert('Datenbank erfolgreich zurückgesetzt');
            loadDatabaseStats();
            loadLibraryContent();
        } else {
            alert('Fehler: ' + (data.error || 'Unbekannter Fehler'));
        }
    })
    .catch(error => {
        console.error('Error clearing database:', error);
        alert('Fehler beim Zurücksetzen der Datenbank');
    })
    .finally(() => {
        clearDbBtn.disabled = false;
        clearDbBtn.textContent = 'Datenbank zurücksetzen';
    });
}

/**
 * Load database statistics
 */
function loadDatabaseStats() {
    fetch('/api/media/stats')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const stats = data.stats;
                dbStats.innerHTML = `
                    <h5>Statistiken</h5>
                    <ul class="list-group">
                        <li class="list-group-item d-flex justify-content-between align-items-center">
                            Serien
                            <span class="badge bg-primary rounded-pill">${stats.series_count}</span>
                        </li>
                        <li class="list-group-item d-flex justify-content-between align-items-center">
                            Animes
                            <span class="badge bg-primary rounded-pill">${stats.anime_count}</span>
                        </li>
                        <li class="list-group-item d-flex justify-content-between align-items-center">
                            Episoden
                            <span class="badge bg-primary rounded-pill">${stats.episode_count}</span>
                        </li>
                        <li class="list-group-item d-flex justify-content-between align-items-center">
                            Gesamtgröße
                            <span class="badge bg-primary rounded-pill">${stats.total_size_gb.toFixed(2)} GB</span>
                        </li>
                    </ul>
                `;
            } else {
                dbStats.innerHTML = `<div class="alert alert-danger">Fehler beim Laden der Statistiken</div>`;
            }
        })
        .catch(error => {
            console.error('Error loading database stats:', error);
            dbStats.innerHTML = `<div class="alert alert-danger">Fehler beim Laden der Statistiken</div>`;
        });
}

/**
 * Load library content
 */
function loadLibraryContent() {
    libraryContent.innerHTML = `<div class="alert alert-info">Lade Mediathek...</div>`;

    fetch('/api/media/list')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                if (data.media.length === 0) {
                    libraryContent.innerHTML = `<div class="alert alert-info">Keine Medien in der Datenbank gefunden</div>`;
                    return;
                }

                // Sortiere nach Typ und Titel
                const sortedMedia = data.media.sort((a, b) => {
                    if (a.type !== b.type) {
                        return a.type === 'series' ? -1 : 1;
                    }
                    return a.title.localeCompare(b.title);
                });

                // Gruppiere nach Typ
                const seriesMedia = sortedMedia.filter(m => m.type === 'series');
                const animeMedia = sortedMedia.filter(m => m.type === 'anime');

                let html = '';

                if (seriesMedia.length > 0) {
                    html += `<h4>Serien (${seriesMedia.length})</h4>`;
                    html += `<div class="row row-cols-1 row-cols-md-3 g-4 mb-4">`;
                    seriesMedia.forEach(media => {
                        html += createMediaCard(media);
                    });
                    html += `</div>`;
                }

                if (animeMedia.length > 0) {
                    html += `<h4>Animes (${animeMedia.length})</h4>`;
                    html += `<div class="row row-cols-1 row-cols-md-3 g-4">`;
                    animeMedia.forEach(media => {
                        html += createMediaCard(media);
                    });
                    html += `</div>`;
                }

                libraryContent.innerHTML = html;

                // Füge Event-Listener für die Karten hinzu
                document.querySelectorAll('.media-card').forEach(card => {
                    card.addEventListener('click', () => {
                        const mediaId = card.getAttribute('data-id');
                        loadMediaDetails(mediaId);
                    });
                });
            } else {
                libraryContent.innerHTML = `<div class="alert alert-danger">Fehler beim Laden der Mediathek</div>`;
            }
        })
        .catch(error => {
            console.error('Error loading library content:', error);
            libraryContent.innerHTML = `<div class="alert alert-danger">Fehler beim Laden der Mediathek</div>`;
        });
}

/**
 * Create media card
 */
function createMediaCard(media) {
    return `
        <div class="col">
            <div class="card h-100 media-card" data-id="${media.id}">
                <div class="card-body">
                    <h5 class="card-title">${media.title}</h5>
                    <p class="card-text">
                        <span class="badge bg-${media.type === 'anime' ? 'primary' : 'secondary'}">${media.type === 'anime' ? 'Anime' : 'Serie'}</span>
                    </p>
                </div>
                <div class="card-footer">
                    <small class="text-muted">Verzeichnis: ${media.directory.split('/').pop()}</small>
                </div>
            </div>
        </div>
    `;
}

/**
 * Load media details
 */
function loadMediaDetails(mediaId) {
    fetch(`/api/media/details/${mediaId}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const media = data.media;
                const seasons = data.seasons;

                let html = `
                    <div class="card mb-4">
                        <div class="card-header d-flex justify-content-between align-items-center">
                            <h5 class="mb-0">${media.title}</h5>
                            <button class="btn btn-sm btn-secondary" onclick="loadLibraryContent()">Zurück</button>
                        </div>
                        <div class="card-body">
                            <p><strong>Typ:</strong> ${media.type === 'anime' ? 'Anime' : 'Serie'}</p>
                            <p><strong>Verzeichnis:</strong> ${media.directory}</p>
                            <p><strong>Zuletzt aktualisiert:</strong> ${new Date(media.last_updated).toLocaleString()}</p>
                        </div>
                    </div>
                `;

                if (seasons.length === 0) {
                    html += `<div class="alert alert-info">Keine Staffeln gefunden</div>`;
                } else {
                    seasons.forEach(season => {
                        html += `
                            <div class="card mb-3">
                                <div class="card-header">
                                    <h5>Staffel ${season.season_number}</h5>
                                </div>
                                <div class="card-body">
                        `;

                        if (season.episodes.length === 0) {
                            html += `<p>Keine Episoden gefunden</p>`;
                        } else {
                            html += `<div class="table-responsive"><table class="table table-striped">
                                <thead>
                                    <tr>
                                        <th>Episode</th>
                                        <th>Titel</th>
                                        <th>Größe</th>
                                        <th>Sprache</th>
                                    </tr>
                                </thead>
                                <tbody>
                            `;

                            season.episodes.forEach(episode => {
                                const fileSize = episode.file_size ? (episode.file_size / (1024 * 1024)).toFixed(2) + ' MB' : 'Unbekannt';
                                const language = [];
                                if (episode.has_german_dub) language.push('GerDub');
                                if (episode.has_german_sub) language.push('GerSub');

                                html += `
                                    <tr>
                                        <td>${episode.episode_number}</td>
                                        <td>${episode.title || 'Unbekannt'}</td>
                                        <td>${fileSize}</td>
                                        <td>${language.join(', ') || 'Unbekannt'}</td>
                                    </tr>
                                `;
                            });

                            html += `
                                </tbody>
                            </table></div>
                            `;
                        }

                        html += `
                                </div>
                            </div>
                        `;
                    });
                }

                libraryContent.innerHTML = html;
            } else {
                alert('Fehler: ' + (data.error || 'Unbekannter Fehler'));
            }
        })
        .catch(error => {
            console.error('Error loading media details:', error);
            alert('Fehler beim Laden der Mediendetails');
        });
}

/**
 * Handle library search
 */
function handleLibrarySearch() {
    if (!librarySearch) {
        return;
    }

    const query = librarySearch.value.trim();

    if (query.length === 0) {
        loadLibraryContent();
        return;
    }

    const typeFilter = libraryType ? libraryType.value : 'all';
    filterLibraryContent(query, typeFilter);
}

/**
 * Filter library content
 */
function filterLibraryContent(query, type) {
    const mediaCards = document.querySelectorAll('.media-card');
    let visibleCount = 0;

    mediaCards.forEach(card => {
        const title = card.querySelector('.card-title').textContent.toLowerCase();
        const mediaType = card.querySelector('.badge').textContent.toLowerCase();

        const matchesQuery = title.includes(query.toLowerCase());
        const matchesType = type === 'all' ||
                           (type === 'series' && mediaType === 'serie') ||
                           (type === 'anime' && mediaType === 'anime');

        if (matchesQuery && matchesType) {
            card.closest('.col').style.display = '';
            visibleCount++;
        } else {
            card.closest('.col').style.display = 'none';
        }
    });

    // Zeige eine Nachricht an, wenn keine Ergebnisse gefunden wurden
    const seriesHeader = document.querySelector('h4:contains("Serien")');
    const animeHeader = document.querySelector('h4:contains("Animes")');

    if (seriesHeader) seriesHeader.style.display = type === 'anime' ? 'none' : '';
    if (animeHeader) animeHeader.style.display = type === 'series' ? 'none' : '';

    if (visibleCount === 0) {
        const noResultsMsg = document.createElement('div');
        noResultsMsg.className = 'alert alert-info';
        noResultsMsg.textContent = 'Keine Ergebnisse gefunden';

        // Entferne vorherige Nachrichten
        const existingMsg = libraryContent.querySelector('.alert');
        if (existingMsg) existingMsg.remove();

        libraryContent.appendChild(noResultsMsg);
    } else {
        // Entferne vorherige Nachrichten
        const existingMsg = libraryContent.querySelector('.alert');
        if (existingMsg) existingMsg.remove();
    }
}

/**
 * Handle submit of the library creation form
 */
function handleLibraryFormSubmit(event) {
    event.preventDefault();

    if (!libraryForm || !libraryNameInput || !libraryPathInput) {
        return;
    }

    const name = libraryNameInput.value.trim();
    const path = libraryPathInput.value.trim();
    const isDefault = libraryDefaultInput ? libraryDefaultInput.checked : false;

    if (!name || !path) {
        alert('Bitte gib sowohl einen Namen als auch einen Pfad an.');
        return;
    }

    const submitButton = libraryForm.querySelector('button[type="submit"]');
    if (submitButton) {
        submitButton.disabled = true;
        submitButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Speichere...';
    }

    fetch('/api/libraries', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            name,
            path,
            is_default: isDefault
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                libraryForm.reset();
                if (libraryDefaultInput) {
                    libraryDefaultInput.checked = false;
                }
                loadLibraries();
                loadLibraryContent();
            } else {
                alert('Fehler beim Speichern der Bibliothek: ' + (data.error || 'Unbekannter Fehler'));
            }
        })
        .catch(error => {
            console.error('Error creating library:', error);
            alert('Fehler beim Erstellen der Bibliothek');
        })
        .finally(() => {
            if (submitButton) {
                submitButton.disabled = false;
                submitButton.textContent = 'Bibliothek hinzufügen';
            }
        });
}

/**
 * Load all libraries from the backend and render them
 */
function loadLibraries() {
    if (!librariesList) {
        return;
    }

    librariesList.innerHTML = '<div class="text-muted">Lade Bibliotheken...</div>';

    fetch('/api/libraries')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                renderLibraries(Array.isArray(data.libraries) ? data.libraries : []);
            } else {
                librariesList.innerHTML = '<div class="alert alert-danger mb-0">Fehler beim Laden der Bibliotheken</div>';
            }
        })
        .catch(error => {
            console.error('Error loading libraries:', error);
            librariesList.innerHTML = '<div class="alert alert-danger mb-0">Fehler beim Laden der Bibliotheken</div>';
        });
}

/**
 * Render the list of libraries with action buttons
 */
function renderLibraries(libraries) {
    if (!librariesList) {
        return;
    }

    if (!libraries || libraries.length === 0) {
        librariesList.innerHTML = '<div class="alert alert-info mb-0">Keine Bibliotheken vorhanden</div>';
        return;
    }

    let html = '<div class="list-group">';

    libraries.forEach((library) => {
        const defaultBadge = library.is_default
            ? '<span class="badge bg-success me-2">Standard</span>'
            : `<button type="button" class="btn btn-sm btn-outline-secondary me-2" data-action="set-default" data-id="${library.id}">Als Standard</button>`;

        html += `
            <div class="list-group-item d-flex justify-content-between align-items-center">
                <div>
                    <div class="fw-bold">${library.name}</div>
                    <div class="small text-muted">${library.path}</div>
                </div>
                <div class="d-flex align-items-center">
                    ${defaultBadge}
                    <button type="button" class="btn btn-sm btn-outline-danger" data-action="delete" data-id="${library.id}">Löschen</button>
                </div>
            </div>
        `;
    });

    html += '</div>';

    librariesList.innerHTML = html;

    librariesList.querySelectorAll('[data-action="set-default"]').forEach((button) => {
        button.addEventListener('click', () => {
            setDefaultLibrary(button.getAttribute('data-id'));
        });
    });

    librariesList.querySelectorAll('[data-action="delete"]').forEach((button) => {
        button.addEventListener('click', () => {
            deleteLibrary(button.getAttribute('data-id'));
        });
    });
}

/**
 * Set a library as default
 */
function setDefaultLibrary(libraryId) {
    const id = parseInt(libraryId, 10);
    if (Number.isNaN(id)) {
        return;
    }

    fetch(`/api/libraries/${id}`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ is_default: true })
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                loadLibraries();
            } else {
                alert('Fehler beim Aktualisieren der Bibliothek: ' + (data.error || 'Unbekannter Fehler'));
            }
        })
        .catch(error => {
            console.error('Error setting default library:', error);
            alert('Fehler beim Setzen der Standard-Bibliothek');
        });
}

/**
 * Delete a library entry
 */
function deleteLibrary(libraryId) {
    const id = parseInt(libraryId, 10);
    if (Number.isNaN(id)) {
        return;
    }

    if (!confirm('Möchtest du diese Bibliothek wirklich löschen?')) {
        return;
    }

    fetch(`/api/libraries/${id}`, {
        method: 'DELETE'
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                loadLibraries();
                loadLibraryContent();
            } else {
                alert('Fehler beim Löschen der Bibliothek: ' + (data.error || 'Unbekannter Fehler'));
            }
        })
        .catch(error => {
            console.error('Error deleting library:', error);
            alert('Fehler beim Löschen der Bibliothek');
        });
}
