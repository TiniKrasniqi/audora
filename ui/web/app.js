const asset = (name) => `assets/${name}`;

const navItems = [
  ["overview", "house", "Overview"],
  ["library", "music-notes", "Library"],
  ["downloads", "download-simple", "Downloads"],
  ["devices", "monitor", "Devices"],
  ["sync", "arrows-clockwise", "Sync"],
  ["settings", "gear-six", "Settings"],
];

const state = {
  page: "overview",
  loading: true,
  mode: "Audio",
  settings: {
    audio_quality: "192 kbps",
    video_quality: "1080p",
    parallel_downloads: "3",
    download_dir: "",
  },
  history: [],
  tracks: [],
  downloads: [],
  jobs: [],
  downloadsActive: false,
  downloadStartedAt: 0,
  logs: [],
  sync: { online: false, baseUrl: "", host: "", port: "5353", devices: [], activity: [], trackCount: 0 },
  stats: { used: "0 B", total: "0 B", percent: 0, items: 0, tracks: 0, videos: 0, playlists: 0, downloaded: 0, sessions: 0 },
  qr: "",
  searchQuery: "",
  libraryRefreshing: false,
  libraryVersion: 0,
  selectedHistoryIndex: 0,
  selectedTrackIndex: 0,
  selectedLibraryIndex: 0,
  libraryFilter: "All",
  libraryPlaylistHistoryIndex: null,
  player: { playing: false, current: null, shuffle: false, repeat: false },
  deleteStates: {},
  parallelMenuOpen: false,
  settingMenus: {},
  qrLoading: false,
  activeDownloadTitle: "Playlist Download",
};

const AUDIO_QUALITIES = ["128 kbps", "192 kbps", "256 kbps", "320 kbps"];
const VIDEO_QUALITIES = ["480p", "720p", "1080p", "1440p", "2160p"];

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function h(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function cleanTrackTitle(title) {
  return String(title ?? "").replace(/^\s*\d{1,4}\s*[-.)_]\s*/, "").trim();
}

function formatDisplayDate(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";

  let match = raw.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (match) return `${match[4]}:${match[5]} ${match[3]}.${match[2]}.${match[1]}`;

  match = raw.match(/^(\d{2}):(\d{2})\s+(\d{2})\.(\d{2})\.(\d{4})$/);
  if (match) return raw;

  const parsed = new Date(raw);
  if (!Number.isNaN(parsed.getTime())) {
    const pad = (part) => String(part).padStart(2, "0");
    return `${pad(parsed.getHours())}:${pad(parsed.getMinutes())} ${pad(parsed.getDate())}.${pad(parsed.getMonth() + 1)}.${parsed.getFullYear()}`;
  }
  return raw;
}

function joinMeta(parts) {
  return parts.filter(Boolean).map((part) => h(part)).join('<span class="meta-dot">.</span>');
}

function normalizePathValue(path) {
  return String(path || "").replaceAll("\\", "/").toLowerCase().replace(/\/+$/, "");
}

function isPathInside(path, folder) {
  const child = normalizePathValue(path);
  const parent = normalizePathValue(folder);
  return Boolean(child && parent && (child === parent || child.startsWith(`${parent}/`)));
}

function isSamePath(path, target) {
  return normalizePathValue(path) === normalizePathValue(target);
}

function deleteKey(path) {
  return normalizePathValue(path);
}

function deleteStateFor(path) {
  return state.deleteStates[deleteKey(path)]?.status || "";
}

function setDeleteState(path, status) {
  const key = deleteKey(path);
  if (!key) return;
  state.deleteStates[key] = { ...(state.deleteStates[key] || {}), status };
}

function clearDeleteState(path) {
  const key = deleteKey(path);
  if (key) delete state.deleteStates[key];
}

function hasVisibleDeleteState() {
  return Object.values(state.deleteStates).some((item) => ["deleting", "deleted"].includes(item?.status));
}

function decorateEntryDeleteState(entry) {
  const deleteState = deleteStateFor(entry.path);
  const children = Array.isArray(entry.children) ? entry.children.map(decorateEntryDeleteState) : entry.children;
  return { ...entry, deleteState, children };
}

function searchableText(item) {
  return [
    item.title,
    item.name,
    item.artist,
    item.type,
    item.mediaType,
    item.format,
    item.timestamp,
    item.path,
    ...(Array.isArray(item.children) ? item.children.flatMap((child) => [child.title, child.name, child.artist, child.format]) : []),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function matchesSearch(item) {
  const query = String(state.searchQuery || "").trim().toLowerCase();
  if (!query) return true;
  return query.split(/\s+/).every((part) => searchableText(item).includes(part));
}

function indexedHistoryEntries() {
  return state.history.map((entry, historyIndex) => ({ ...entry, historyIndex })).filter(matchesSearch);
}

function indexedTracks() {
  return state.tracks.map((track, trackIndex) => ({ ...track, trackIndex })).filter(matchesSearch);
}

function historyPlaylistEntries() {
  return state.history
    .map((entry, historyIndex) => ({ ...entry, historyIndex }))
    .filter((entry) => entry.type === "folder" && matchesSearch(entry));
}

function libraryItems() {
  const activePlaylist =
    state.libraryPlaylistHistoryIndex === null ? null : state.history[state.libraryPlaylistHistoryIndex];
  if (activePlaylist) {
    const children = Array.isArray(activePlaylist.children) ? activePlaylist.children : [];
    return children.map((child, childIndex) => ({
      ...child,
      kind: "track",
      trackIndex: childIndex,
      historyIndex: state.libraryPlaylistHistoryIndex,
      title: child.title || child.name || `Track ${childIndex + 1}`,
      artist: child.artist || activePlaylist.name || "Audora",
      type: child.mediaType || child.type || "Audio",
      art: child.thumbnailUri || child.art || activePlaylist.art || coverFor(childIndex),
      status: child.status || "Downloaded",
    })).filter((item) => libraryFilterMatch(item) && matchesSearch(item));
  }

  const playlists = historyPlaylistEntries().map((entry) => ({
    kind: "playlist",
    historyIndex: entry.historyIndex,
    title: entry.title || entry.name || "Untitled playlist",
    artist: "Playlist Folder",
    art: artworkFor(entry, entry.historyIndex, asset("album-party.png")),
    thumbnailUri: entry.thumbnailUri || firstChildArt(entry.children),
    count: entry.count || 0,
    size: entry.size || "",
    format: "Playlist",
    bitrate: `${entry.count || 0} tracks`,
    timestamp: entry.timestamp || "",
    path: entry.path || "",
    children: entry.children || [],
  }));
  const playlistFolders = playlists.map((entry) => entry.path).filter(Boolean);
  const tracks = state.tracks
    .map((track, trackIndex) => ({ ...track, kind: "track", trackIndex }))
    .filter((track) => !playlistFolders.some((folder) => isPathInside(track.path, folder)));
  return [...playlists, ...tracks].filter((item) => libraryFilterMatch(item) && matchesSearch(item));
}

function libraryFilterMatch(item) {
  const filter = state.libraryFilter || "All";
  const type = item.mediaType || item.type;
  if (filter === "All") return true;
  if (filter === "Downloaded") return item.kind === "playlist" || item.status === "Downloaded";
  if (filter === "Playlists") return item.kind === "playlist";
  return item.kind === "track" && type === filter;
}

function icon(name, fill = false) {
  return `<i class="${fill ? "ph-fill" : "ph"} ph-${name}"></i>`;
}

function api() {
  return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
}

async function callApi(method, payload = undefined, fallback = undefined) {
  const bridge = api();
  if (!bridge || typeof bridge[method] !== "function") return fallback;
  try {
    return payload === undefined ? await bridge[method]() : await bridge[method](payload);
  } catch (error) {
    toast(error.message || String(error));
    return fallback;
  }
}

function toast(message) {
  const host = $("#toastHost");
  if (!host) return;
  const item = document.createElement("div");
  item.className = "toast";
  item.textContent = message;
  host.appendChild(item);
  setTimeout(() => item.remove(), 3400);
}

function coverFor(index, fallback = "album-moment-apart.png") {
  const covers = [
    "album-moment-apart.png",
    "album-another-life.png",
    "album-weightless.png",
    "album-bloom.png",
    "album-innerbloom.png",
    "album-party.png",
  ];
  return asset(covers[index % covers.length] || fallback);
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function firstChildArt(children) {
  if (!Array.isArray(children)) return "";
  const child = children.find((item) => item?.thumbnailUri || item?.art);
  return child?.thumbnailUri || child?.art || "";
}

function artworkFor(item, index = 0, fallback = "") {
  return item?.thumbnailUri || item?.art || firstChildArt(item?.children) || fallback || coverFor(index);
}

function normalizeBackend(data) {
  if (!data || typeof data !== "object") return;
  if (data.settings) state.settings = { ...state.settings, ...data.settings };
  if (data.stats) state.stats = { ...state.stats, ...data.stats };
  const incomingRefreshing = Boolean(data.libraryRefreshing);
  const incomingVersion = Number(data.libraryVersion || state.libraryVersion || 0);
  const currentHasLibraryData = state.history.length > 0 || state.tracks.length > 0;
  const incomingHistoryEmpty = Array.isArray(data.history) && data.history.length === 0;
  const incomingLibraryEmpty = Array.isArray(data.library) && data.library.length === 0;
  const holdVisibleLibrary =
    incomingRefreshing &&
    currentHasLibraryData &&
    incomingVersion <= state.libraryVersion &&
    incomingHistoryEmpty &&
    incomingLibraryEmpty;
  const holdDeleteLifecycle = hasVisibleDeleteState() && currentHasLibraryData;
  if (Array.isArray(data.history)) {
    if (!holdVisibleLibrary && !holdDeleteLifecycle) {
      state.history = data.history.map((entry, index) => decorateEntryDeleteState({ ...entry, art: artworkFor(entry, index) }));
    }
  }
  if (Array.isArray(data.library)) {
    if (!holdVisibleLibrary && !holdDeleteLifecycle) {
      state.tracks = data.library.map((entry, index) => ({ ...entry, deleteState: deleteStateFor(entry.path), art: artworkFor(entry, index) }));
    }
  }
  state.libraryRefreshing = incomingRefreshing;
  state.libraryVersion = incomingVersion;
  const startupGrace = isDownloadStartupGrace();
  if (data.downloads && typeof data.downloads.active === "boolean") {
    state.downloadsActive = data.downloads.active || startupGrace;
  }
  if (data.downloads && Array.isArray(data.downloads.jobs)) {
    const jobs = normalizeJobs(data.downloads.jobs);
    if (jobs.length) state.jobs = jobs;
    else if (!state.downloadsActive) state.jobs = [];
  }
  if (data.sync) {
    const devices = Array.isArray(data.sync.devices) ? data.sync.devices : state.sync.devices;
    state.sync = { ...state.sync, ...data.sync, devices };
  }
  if (data.qr) state.qr = data.qr;
  state.loading = false;
  updateChrome();
}

function currentDownloadJobs() {
  return shouldKeepDownloadQueueVisible() ? state.jobs : [];
}

function makeOptimisticJob(url) {
  const playlist = url.includes("list=") || url.toLowerCase().includes("playlist");
  return {
    optimistic: true,
    job_id: "starting",
    title: playlist ? "Resolving playlist" : "Preparing download",
    source: "YouTube",
    message: "Reading link",
    status: "Preparing",
    percent: 0,
    downloadedText: "",
    mode: state.mode,
    art: coverFor(0),
  };
}

function shouldKeepDownloadQueueVisible() {
  return state.downloadsActive || isDownloadStartupGrace();
}

function isDownloadStartupGrace() {
  return Boolean(state.downloadStartedAt && Date.now() - state.downloadStartedAt < 30000 && state.jobs.length > 0);
}

function normalizeJobs(jobs) {
  return jobs.map((entry, index) => ({ ...entry, art: entry.thumbnail_url || entry.art || coverFor(index) }));
}

function isTerminalDownloadEvent(event) {
  const progress = event?.progress;
  if (event?.type !== "progress" || !progress || progress.job_id) return false;
  const status = String(progress.status || "").toLowerCase();
  const message = String(progress.message || "").toLowerCase();
  return status === "stopped" || status === "error" || (status === "finished" && message === "all_done");
}

function downloadStatusLabel(item) {
  const status = String(item?.status || "").toLowerCase();
  const message = String(item?.message || "").toLowerCase();
  const progress = Math.round(item?.percent ?? item?.progress ?? 0);
  if (status === "error" || item?.failed) return "Failed";
  if (status === "stopped") return "Stopped";
  if (status === "queued") return "Queued";
  if (item?.optimistic || message === "preparing" || status === "preparing") return "Reading link";
  if (status === "downloading") return progress > 0 ? "Downloading" : "Preparing";
  if (status === "finished" && (message === "postprocessing" || message === "processing")) return "Processing";
  if (status === "finished") return "Completed";
  return status ? status.charAt(0).toUpperCase() + status.slice(1) : "Queued";
}

function playlistTotalCount(list) {
  return Math.max(0, ...list.map((item) => Number(item.item_count || item.itemCount || 0)), list.length);
}

function playlistProgressPercent(list, total) {
  if (!list.length || !total) return 0;
  const sum = list.reduce((amount, item) => amount + Math.max(0, Math.min(100, Number(item.percent ?? item.progress ?? 0))), 0);
  return Math.max(0, Math.min(100, Math.round(sum / Math.max(total, list.length))));
}

function updateChrome() {
  $("#syncStatusText").textContent = state.sync.online ? "Local sync online" : "Local sync offline";
  const search = $("#globalSearch");
  if (search && document.activeElement !== search && search.value !== state.searchQuery) {
    search.value = state.searchQuery;
  }
  const active = state.player.current || state.tracks[state.selectedTrackIndex] || state.tracks[0];
  const isCurrentVideo = isVideoItem(active);
  $("#miniTitle").textContent = active?.title || "Nothing playing";
  $("#miniArtist").textContent = active?.artist || "No media selected";
  $("#miniArt").src = active?.art || asset("album-moment-apart.png");
  const miniArtButton = $(".mini-art-button");
  const miniPreview = $("#miniVideoPreview");
  if (miniArtButton) miniArtButton.classList.toggle("video-active", Boolean(isCurrentVideo && active?.uri));
  if (miniPreview) {
    const media = $("#mediaPlayer");
    if (isCurrentVideo && media?.src) {
      if (miniPreview.src !== media.src) miniPreview.src = media.src;
      miniPreview.hidden = false;
      miniPreview.muted = true;
      if (Number.isFinite(media.currentTime) && Math.abs((miniPreview.currentTime || 0) - media.currentTime) > 0.75) {
        try {
          miniPreview.currentTime = media.currentTime;
        } catch (_error) {
          // Some engines reject currentTime until metadata is ready; the next tick catches up.
        }
      }
      if (!media.paused && miniPreview.paused) miniPreview.play().catch(() => {});
      if (media.paused && !miniPreview.paused) miniPreview.pause();
    } else {
      miniPreview.pause();
      miniPreview.removeAttribute("src");
      miniPreview.load();
      miniPreview.hidden = true;
    }
  }
  const playIcon = $("#miniPlay i");
  if (playIcon) playIcon.className = state.player.playing ? "ph-fill ph-pause" : "ph-fill ph-play";
  $$('[data-action="toggle-shuffle"]').forEach((button) => button.classList.toggle("active", state.player.shuffle));
  $$('[data-action="toggle-repeat"]').forEach((button) => button.classList.toggle("active", state.player.repeat));
  updatePlayerProgress();
  const storageCard = $(".storage-card");
  if (storageCard) {
    const details = storageCard.querySelector(".muted");
    const meter = storageCard.querySelector(".meter span");
    const percent = storageCard.querySelector("strong");
    if (details) details.textContent = `${state.stats.used} of ${state.stats.total}`;
    if (meter) meter.style.width = `${state.stats.percent || 0}%`;
    if (percent) percent.textContent = `${state.stats.percent || 0}%`;
  }
}

function formatMediaClock(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

function updatePlayerProgress() {
  const media = $("#mediaPlayer");
  const seek = $("#playerSeek");
  const elapsed = $("#playerElapsed");
  const duration = $("#playerDuration");
  if (!media || !seek || !elapsed || !duration) return;
  const total = Number(media.duration || 0);
  const current = Number(media.currentTime || 0);
  seek.disabled = !total;
  seek.value = total ? String(Math.round((current / total) * 1000)) : "0";
  elapsed.textContent = formatMediaClock(current);
  duration.textContent = formatMediaClock(total);
}

function isVideoItem(item) {
  const type = String(item?.mediaType || item?.type || "").toLowerCase();
  const format = String(item?.format || item?.path || item?.title || item?.name || "").toLowerCase();
  return type === "video" || /\.(mp4|webm|mkv|mov|avi|m4v)$/.test(format) || ["mp4", "webm", "mkv", "mov", "avi", "m4v"].includes(format);
}

function setVideoPanelVisible(visible, item = null) {
  const panel = $("#videoPlayerPanel");
  const title = $("#videoPlayerTitle");
  if (!panel) return;
  panel.hidden = !visible;
  if (title && item) title.textContent = cleanTrackTitle(item.title || item.name || "Now playing");
}

async function playMediaItem(item) {
  if (!item) {
    toast("No media selected.");
    return;
  }
  const media = $("#mediaPlayer");
  const source = item.uri || "";
  const video = isVideoItem(item);
  state.player.current = {
    title: cleanTrackTitle(item.title || item.name || "Now playing"),
    artist: item.artist || item.timestamp || "Audora",
    art: item.art || item.thumbnailUri || coverFor(0),
    path: item.path || "",
    uri: source,
    type: item.mediaType || item.type || "",
  };
  const trackIndex = findTrackIndexByPath(item.path);
  if (trackIndex >= 0) state.selectedTrackIndex = trackIndex;
  updateChrome();
  if (!media || !source) {
    state.player.playing = false;
    setVideoPanelVisible(false);
    updateChrome();
    toast("This item has no playable file yet.");
    return;
  }
  try {
    if (media.src !== source) media.src = source;
    media.volume = Number($(".volume-cluster input")?.value || 37) / 100;
    setVideoPanelVisible(video, item);
    await media.play();
    state.player.playing = true;
  } catch (_error) {
    state.player.playing = false;
    setVideoPanelVisible(false);
    toast("Audora could not play this file inside the app.");
  }
  updateChrome();
}

function playableTracks() {
  return state.tracks.filter((track) => track.uri);
}

function findTrackIndexByPath(path) {
  if (!path) return -1;
  return state.tracks.findIndex((track) => isSamePath(track.path, path));
}

function currentPlayableIndex(list = playableTracks()) {
  if (!list.length) return -1;
  const currentPath = state.player.current?.path || state.tracks[state.selectedTrackIndex]?.path || "";
  const byPath = list.findIndex((track) => currentPath && isSamePath(track.path, currentPath));
  if (byPath >= 0) return byPath;
  const selected = state.tracks[state.selectedTrackIndex];
  return list.findIndex((track) => selected?.path && isSamePath(track.path, selected.path));
}

async function playAdjacent(direction) {
  const list = playableTracks();
  if (!list.length) {
    toast("No playable media in your library yet.");
    return;
  }
  let nextIndex = 0;
  const current = currentPlayableIndex(list);
  if (state.player.shuffle && direction > 0 && list.length > 1) {
    do {
      nextIndex = Math.floor(Math.random() * list.length);
    } while (nextIndex === current);
  } else {
    nextIndex = current < 0 ? 0 : (current + direction + list.length) % list.length;
  }
  await playMediaItem(list[nextIndex]);
}

function firstPlayableFromHistory(entry) {
  if (!entry) return null;
  if (entry.type === "folder" && Array.isArray(entry.children) && entry.children.length) {
    const child = entry.children.find((item) => item.uri || item.path) || entry.children[0];
    return { ...child, title: entry.title || entry.name || child.title || child.name, artist: "Playlist", art: entry.art || child.thumbnailUri || coverFor(0) };
  }
  return { ...entry, artist: entry.artist || "Audora" };
}

function renderNav() {
  $("#mainNav").innerHTML = navItems
    .map(([page, iconName, label]) => {
      const targetPage = page === "sync" ? "devices" : page;
      const active = (page === state.page || targetPage === state.page) && !(state.page === "devices" && page === "sync");
      return `
        <button class="nav-button ${active ? "active" : ""}" data-page="${targetPage}">
          ${icon(iconName)}
          <span>${label}</span>
        </button>
      `;
    })
    .join("");
}

function render() {
  renderNav();
  updateChrome();
  const root = $("#pageRoot");
  if (state.loading) {
    root.innerHTML = `<div class="loading-state"><div><div class="spinner"></div><strong>Loading Audora</strong><p class="muted">Preparing your local library.</p></div></div>`;
    return;
  }
  const page = state.page;
  if (page === "overview") root.innerHTML = renderOverview();
  else if (page === "library") root.innerHTML = renderLibrary();
  else if (page === "downloads") root.innerHTML = renderDownloads();
  else if (page === "new-download") root.innerHTML = renderNewDownload();
  else if (page === "playlist") root.innerHTML = renderPlaylist();
  else if (page === "devices") {
    root.innerHTML = renderDevices();
    ensurePairingQr();
  }
  else if (page === "settings") root.innerHTML = renderSettings();
  else if (page === "history") root.innerHTML = renderHistory();
  else if (page === "history-detail") root.innerHTML = renderHistoryDetail();
  else root.innerHTML = renderOverview();
}

function renderOverview() {
  const downloads = indexedHistoryEntries().slice(0, 4);
  return `
    <div class="hero-card hero-card-reference" role="img" aria-label="Your music, synced locally. Private. Fast. Yours.">
      <div class="hero-reference-actions">
        <button class="primary-button" data-page="devices">${icon("device-mobile")} Pair Phone</button>
        <button class="ghost-button" data-page="new-download">${icon("download-simple")} New Download</button>
      </div>
    </div>

    <div class="stats-grid">
      ${statCard("music-notes", "Tracks", String(state.stats.tracks || state.tracks.length), "Audio in library")}
      ${statCard("download-simple", "Downloaded", String(state.stats.downloaded || state.tracks.length), "Media files")}
      ${statCard("monitor", "Paired Devices", String(state.sync.devices.length || 0), "Active connections")}
      ${statCard("database", "Storage Used", state.stats.used, `of ${state.stats.total}`)}
    </div>

    <div class="dashboard-grid">
      <section class="glass-card">
        <div class="section-header"><h2>Recent Downloads</h2><button class="link-button" data-page="history">View all</button></div>
        <div class="list-stack">
          ${downloads.map((entry, index) => compactDownloadRow(entry, index)).join("") || emptyState("No downloads yet", state.searchQuery ? "No recent downloads match your search." : "Start a download to fill this list.", { label: "New Download", page: "new-download", iconName: "download-simple" })}
        </div>
      </section>
      <section class="glass-card">
        <div class="section-header"><h2>Sync Activity</h2><button class="link-button" data-page="devices">View all</button></div>
        <div class="list-stack">
          ${Array.isArray(state.sync.activity) && state.sync.activity.length ? state.sync.activity.slice(0, 4).map(syncReportRow).join("") : syncEmptyState("No phone sync yet", "Synced phone downloads will appear here.")}
        </div>
        <button class="plain-button" data-page="devices" style="width:100%; margin-top: 12px">${icon("device-mobile")} Manage phone sync</button>
      </section>
      <section class="glass-card">
        <div class="section-header"><h2>Quick Actions</h2></div>
        <div class="quick-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          ${quickAction("waveform", "Scan Library", "Find new music", "scan-library")}
          ${quickAction("qr-code", "Generate QR", "Pair new device", "generate-qr")}
          ${quickAction("download-simple", "Open Downloads", "View downloaded files", "open-folder")}
          ${quickAction("monitor", "Manage Devices", "Add or remove devices", "open-devices")}
        </div>
      </section>
    </div>
  `;
}

function statCard(iconName, title, value, note) {
  return `
    <article class="stats-card">
      <div class="stat-icon">${icon(iconName)}</div>
      <div><span class="muted">${h(title)}</span><strong>${h(value)}</strong><span class="muted">${h(note)}</span></div>
    </article>
  `;
}

function quickAction(iconName, title, note, action) {
  return `
    <button class="glass-card" data-action="${action}" style="min-height:82px;padding:10px;display:grid;place-items:center;text-align:center;gap:5px">
      <span class="round-icon">${icon(iconName)}</span>
      <strong>${h(title)}</strong>
      <span class="muted">${h(note)}</span>
    </button>
  `;
}

function compactDownloadRow(entry, index) {
  const openAction = entry.type === "folder" ? "open-library-playlist" : "open-history-detail";
  const meta = entry.artist || formatDisplayDate(entry.timestamp) || "Downloaded";
  const historyIndex = entry.historyIndex ?? index;
  return `
    <div class="compact-row recent-download-row" data-action="${openAction}" data-idx="${historyIndex}">
      <img class="cover" src="${h(entry.art || coverFor(index))}" data-fallback="${h(entry.type === "folder" ? asset("album-party.png") : coverFor(index))}" alt="" />
      <div class="row-title"><strong>${h(cleanTrackTitle(entry.title || entry.name))}</strong><span>${h(meta)}</span></div>
      <span class="recent-download-status">${icon("check-circle")} Downloaded</span>
      <button class="recent-play-button" data-action="play-history" data-idx="${historyIndex}" title="Play">${icon("play", true)}</button>
    </div>
  `;
}

function syncReportRow(report) {
  const status = String(report.status || "synced").toLowerCase();
  const failed = status.includes("fail") || status.includes("error");
  return `
    <div class="compact-row" style="grid-template-columns:34px minmax(0,1fr) 28px">
      ${icon("device-mobile")}
      <div class="row-title">
        <strong>${h(report.trackTitle || "Phone sync")}</strong>
        <span>${h(report.deviceName || "Paired phone")} <span class="meta-dot">.</span> ${h(formatDisplayDate(report.timestamp) || "Synced")}</span>
      </div>
      <span class="sync-done-icon ${failed ? "failed" : ""}">${icon(failed ? "x-circle" : "check-circle")}</span>
    </div>
  `;
}

function syncStatusRow() {
  return `
    <div class="compact-row" style="grid-template-columns:34px minmax(0,1fr) 28px">
      ${icon("monitor")}
      <div class="row-title"><strong>${state.sync.online ? "Local sync online" : "Local sync offline"}</strong><span>${h(state.sync.baseUrl || state.sync.error || "No recent activity")}</span></div>
      <span class="sync-done-icon">${icon(state.sync.online ? "check-circle" : "warning-circle")}</span>
    </div>
  `;
}

function syncEmptyState(title, detail) {
  return emptyState(title, detail, { label: "Pair Device", page: "devices", iconName: "device-mobile" });
}

function renderLibrary() {
  const items = libraryItems();
  const hasItems = items.length > 0;
  const selectedIndex = Math.min(state.selectedLibraryIndex || 0, Math.max(items.length - 1, 0));
  state.selectedLibraryIndex = selectedIndex;
  const selected = items[selectedIndex] || {};
  const selectedIsPlaylist = selected.kind === "playlist";
  const selectedTitle = cleanTrackTitle(selected.title || selected.name);
  const selectedDate = formatDisplayDate(selected.timestamp);
  const filters = state.libraryPlaylistHistoryIndex === null ? ["All", "Downloaded", "Audio", "Video", "Playlists"] : ["All", "Downloaded", "Audio", "Video"];
  const title = state.libraryPlaylistHistoryIndex === null
    ? "Desktop Library"
    : (state.history[state.libraryPlaylistHistoryIndex]?.title || state.history[state.libraryPlaylistHistoryIndex]?.name || "Playlist");
  const subtitle = state.libraryPlaylistHistoryIndex === null
    ? "All your music and videos, organized and ready to play."
    : "Tracks grouped inside this playlist.";
  const syncActionLabel = state.sync.devices.length ? "Manage Phone Sync" : "Pair Phone";
  const syncActionIcon = state.sync.devices.length ? "device-mobile" : "qr-code";

  return `
    <div class="library-main">
      <div class="library-content">
        <div class="library-headline">
          <div class="page-title library-title">
            <div><h1>${h(title)}</h1><p>${h(subtitle)}</p></div>
            <div class="filters">
              ${filters.map((item) => `<button class="filter-chip ${state.libraryFilter === item ? "active" : ""}" data-action="set-library-filter" data-filter="${h(item)}">${h(item)}</button>`).join("")}
            </div>
          </div>
          <article class="glass-card library-summary">
            <span class="muted">Library Size</span>
            <strong>${h(state.stats.used.split(" ")[0] || "0")} <span>${h(state.stats.used.split(" ")[1] || "B")}</span></strong>
            <span class="muted">${joinMeta([`${state.stats.items || 0} items`, `${state.stats.total} total`, state.libraryRefreshing ? "Refreshing" : "Ready"])}</span>
            <div class="library-sync-status">
              <span class="muted">Phone sync</span>
              <strong>${h(state.sync.devices.length ? `${state.sync.devices.length} paired` : "Not paired")}</strong>
            </div>
            <button class="ghost-button" data-page="devices">${icon(syncActionIcon)} ${syncActionLabel}</button>
          </article>
        </div>
        ${state.libraryPlaylistHistoryIndex === null ? "" : `<button class="back-link library-back" data-action="close-library-playlist">${icon("arrow-left")} Library</button>`}
        <section class="table-card">
          <div class="track-row header">
            <span></span><span>Track</span><span>Type</span><span>Duration</span><span>Status</span><span>Size</span><span>Actions</span>
          </div>
          ${items.map((item, index) => renderLibraryRow(item, index)).join("") || emptyState("No library items", state.searchQuery || state.libraryFilter !== "All" ? "No items match the current view." : "Start a download to add music here.", { label: "New Download", page: "new-download", iconName: "download-simple" })}
        </section>
      </div>
      <aside class="side-panel album-side">
        ${hasItems ? `
        <img src="${h(selected.art)}" data-fallback="${h(selectedIsPlaylist ? asset("album-party.png") : coverFor(selectedIndex))}" alt="" />
        <h2>${h(selectedTitle)}</h2>
        <p class="muted">${selectedIsPlaylist ? "Playlist folder<br />Grouped download" : `${h(selected.artist || "Audora")}<br />${h(selected.type || "Audio")}${selectedDate ? ` <span class="meta-dot">.</span> ${h(selectedDate)}` : ""}`}</p>
        <div class="metadata-list">
          <div><span>${icon(selectedIsPlaylist ? "playlist" : "file-audio")} ${selectedIsPlaylist ? "Total Tracks" : "File Type"}</span><strong>${h(selectedIsPlaylist ? selected.count || 0 : selected.format || "-")}</strong></div>
          <div><span>${icon(selectedIsPlaylist ? "folder" : "clock")} ${selectedIsPlaylist ? "Type" : "Duration"}</span><strong>${h(selectedIsPlaylist ? "Playlist" : selected.duration || "-")}</strong></div>
          <div><span>${icon("calendar")} ${selectedIsPlaylist ? "Created" : "Date Added"}</span><strong>${h(selectedDate || "-")}</strong></div>
          <div><span>${icon("hard-drives")} Size</span><strong>${h(selected.size || "-")}</strong></div>
          ${selectedIsPlaylist ? "" : `<div><span>${icon("folder")} Location</span><strong>${h(shortPath(selected.path || ""))}</strong></div>`}
        </div>
        <button class="primary-button" data-action="${selectedIsPlaylist ? "open-library-playlist" : "play-library"}" data-idx="${selectedIsPlaylist ? selected.historyIndex : selectedIndex}" style="width:100%">${selectedIsPlaylist ? icon("playlist") : icon("play", true)} ${selectedIsPlaylist ? "Open Playlist" : "Play"}</button>
        <button class="plain-button" data-action="${selectedIsPlaylist ? "open-path" : "open-library-path"}" data-idx="${selectedIsPlaylist ? selected.historyIndex : selectedIndex}" style="width:100%;margin-top:10px">${icon("folder-open")} ${selectedIsPlaylist ? "Reveal Folder" : "Reveal File"}</button>
        ` : emptyState("No item selected", "Music details will appear here when your library has items.", { label: "New Download", page: "new-download", iconName: "download-simple" })}
      </aside>
    </div>
  `;
}

function renderLibraryRow(item, index) {
  if (item.kind !== "playlist") return renderTrackRow(item, index);
  const active = index === state.selectedLibraryIndex;
  const title = cleanTrackTitle(item.title);
  const deleting = item.deleteState === "deleting";
  const deleted = item.deleteState === "deleted";
  const playlistThumb = item.thumbnailUri || "";
  return `
    <div class="track-row library-row playlist-library-row ${active ? "active" : ""} ${deleting ? "deleting" : ""} ${deleted ? "deleted" : ""}" data-action="open-library-playlist" data-idx="${item.historyIndex}">
      <span class="library-row-icon">${icon("folder")}</span>
      <div class="row-title" style="grid-template-columns:42px minmax(0,1fr);display:grid;align-items:center">
        <span class="library-folder-thumb">${playlistThumb ? `<img src="${h(playlistThumb)}" data-fallback="${h(asset("album-party.png"))}" alt="" />` : icon("playlist")}</span>
        <div><strong>${h(title)}</strong><span>${joinMeta([`${item.count || 0} tracks`, "Playlist folder"])}</span></div>
      </div>
      <span>${icon("playlist")}</span>
      <span>${h(item.count || 0)} tracks</span>
      <span class="status-tag">${icon("folder")} ${deleting ? "Deleting..." : deleted ? "Deleted" : "Grouped"}</span>
      <span>${h(item.size || "-")}</span>
      <span class="row-actions">
        <button class="icon-button danger" data-action="delete-library" data-idx="${index}" title="Delete" ${deleting || deleted ? "disabled" : ""}>${icon("trash-simple")}</button>
      </span>
    </div>
  `;
}

function renderTrackRow(track, index) {
  const trackIndex = track.trackIndex ?? index;
  const active = index === state.selectedLibraryIndex;
  const title = cleanTrackTitle(track.title);
  const deleting = track.deleteState === "deleting";
  const deleted = track.deleteState === "deleted";
  const statusText = deleting ? "Deleting..." : deleted ? "Deleted" : track.status || "Downloaded";
  return `
    <div class="track-row library-row ${active ? "active" : ""} ${deleting ? "deleting" : ""} ${deleted ? "deleted" : ""}" data-action="select-library" data-idx="${index}">
      <button class="icon-button" data-action="play-library" data-idx="${index}" title="Play" ${deleting || deleted ? "disabled" : ""}>${state.selectedTrackIndex === trackIndex ? icon("pause", true) : icon("play", true)}</button>
      <div class="row-title" style="grid-template-columns:42px minmax(0,1fr);display:grid;align-items:center">
        <img class="cover" style="width:42px;height:42px" src="${h(track.art)}" data-fallback="${h(coverFor(index))}" alt="" />
        <div><strong>${h(title)}</strong><span>${h(track.artist || track.type || "Audora")}</span></div>
      </div>
      <span>${track.type === "Video" ? icon("film-strip") : icon("music-note")}</span>
      <span>${h(track.duration || "-")}</span>
      <span class="${track.status === "Syncing" || deleting || deleted ? "status-tag" : "success-tag"}">${track.status === "Syncing" ? icon("arrows-clockwise") : icon("check-circle")} ${h(statusText)}</span>
      <span>${h(track.size || "-")}</span>
      <span class="row-actions">
        <button class="icon-button" data-action="open-library-path" data-idx="${index}" title="Reveal file" ${deleting || deleted ? "disabled" : ""}>${icon("folder-open")}</button>
        <button class="icon-button danger" data-action="delete-library" data-idx="${index}" title="Delete" ${deleting || deleted ? "disabled" : ""}>${icon("trash-simple")}</button>
      </span>
    </div>
  `;
}

function renderDownloads() {
  const activeJobs = currentDownloadJobs();
  const recentTracks = indexedTracks().slice(0, 5);
  const failedJobs = activeJobs.filter((item) => item.failed || item.status === "error").length;
  return `
    <div class="page-title">
      <div><h1>Downloads</h1><p>Manage your downloads and transfers.</p></div>
      <div class="filters">
        <button class="primary-button" data-page="new-download">${icon("plus")} New Download</button>
        <button class="ghost-button" data-page="history">${icon("clock-counter-clockwise")} History</button>
      </div>
    </div>
    <div class="stats-grid">
      ${statCard("download-simple", "Active Downloads", String(activeJobs.length), "In progress")}
      ${statCard("check-circle", "Completed", String(state.stats.downloaded || state.tracks.length), "Media files")}
      ${statCard("x", "Failed", String(failedJobs), "Errors")}
      ${statCard("database", "Storage Used", state.stats.used, `of ${state.stats.total}`)}
    </div>
    <div class="two-column">
      <section class="download-card">
        <div class="section-header"><h2>Current Downloads <span class="status-tag">${activeJobs.length}</span></h2><span class="muted">Max speed: Unlimited</span></div>
        ${activeJobs.map((item, index) => renderDownloadRow(item, index)).join("") || emptyState("No active downloads", "Start a new download to see progress here.", { label: "New Download", page: "new-download", iconName: "download-simple" })}
        ${activeJobs.length ? `<button class="plain-button" data-page="playlist" style="width:100%;margin-top:12px">View all downloads ${icon("arrow-right")}</button>` : ""}
      </section>
      <div class="list-stack">
        <section class="download-card">
          <div class="section-header"><h2>Recently Downloaded</h2><button class="link-button" data-page="history">View all</button></div>
          ${recentTracks.map((track, index) => recentDownloaded(track, index)).join("") || emptyState("No music yet", state.searchQuery ? "No downloaded tracks match your search." : "Downloaded tracks will appear here.", { label: "New Download", page: "new-download", iconName: "download-simple" })}
        </section>
        <section class="download-card">
          <div class="section-header"><h2>Transfer Queue to Mobile <span class="status-tag">0</span></h2></div>
          ${emptyState("No mobile transfers", "Pair a device to sync downloaded music.", { label: "Pair Device", page: "devices", iconName: "device-mobile" })}
        </section>
      </div>
    </div>
  `;
}

function renderDownloadRow(item, index) {
  const progress = Math.max(0, Math.min(100, Math.round(item.percent ?? item.progress ?? 0)));
  const failed = item.failed || item.status === "error";
  const label = downloadStatusLabel(item);
  const active = !failed && progress === 0 && ["Reading link", "Preparing", "Downloading", "Processing"].includes(label);
  const detail = [item.downloadedText, item.speed, item.eta ? `ETA: ${item.eta}` : ""].filter(Boolean).join(" . ") || label;
  const format = item.format || (item.mode === "Video" ? "MP4" : item.mode === "Audio" ? "MP3" : "");
  return `
    <div class="download-row ${failed ? "failed" : ""}">
      <img class="cover large" style="width:64px;height:64px" src="${h(item.thumbnail_url || item.art || coverFor(index))}" data-fallback="${h(coverFor(index))}" alt="" />
      <div class="row-title">
        <strong>${h(cleanTrackTitle(item.title || `Download ${index + 1}`))}</strong>
        <span>${h(item.artist || item.source || item.message || "YouTube")}</span>
        ${failed ? `<span style="color:#ff5b88">Download failed - ${h(item.message || "Network error")}</span>` : `<div class="progress-track ${active ? "indeterminate" : ""}"><span style="width:${progress || (active ? 34 : 0)}%"></span></div><span>${h(detail || label)}</span>`}
      </div>
      <span class="format-tag">${h(format || item.mode || "")}</span>
      <span>${failed ? "" : `${progress}%`}</span>
      ${failed ? `<button class="ghost-button" data-page="new-download">Retry</button>` : `<button class="icon-button" data-action="stop-download" title="Pause">${icon("pause")}</button>`}
    </div>
  `;
}

function recentDownloaded(track, index) {
  const trackIndex = track.trackIndex ?? index;
  const format = track.format || (track.type === "Video" ? "MP4" : track.type === "Audio" ? "MP3" : "");
  return `
    <div class="compact-row" style="grid-template-columns:42px minmax(0,1fr) 86px 30px 30px">
      <img class="cover" style="width:42px;height:42px" src="${h(track.art)}" data-fallback="${h(coverFor(index))}" alt="" />
      <div class="row-title"><strong>${h(cleanTrackTitle(track.title))}</strong><span>${h(track.artist || track.type || "Audora")}</span></div>
      <span class="muted">${h(format)}</span>
      <span style="color:#22c55e">${icon("check-circle")}</span>
      <button class="icon-button" data-action="play-track" data-idx="${trackIndex}" title="Play">${icon("play")}</button>
    </div>
  `;
}

function activeQualityOptions() {
  return state.mode === "Audio" ? AUDIO_QUALITIES : VIDEO_QUALITIES;
}

function activeQualityValue() {
  return state.mode === "Audio" ? state.settings.audio_quality : state.settings.video_quality;
}

function renderNewDownload() {
  const isAudio = state.mode === "Audio";
  const activeJobs = currentDownloadJobs();
  const failedJobs = activeJobs.filter((item) => item.failed || item.status === "error").length;
  const qualities = activeQualityOptions();
  const currentQuality = activeQualityValue();
  const parallelOptions = ["1", "2", "3", "4", "5", "6", "7", "8"];
  return `
    <div class="page-title">
      <div><h1>New Download</h1><p>Download audio, video, and playlists from a YouTube link.</p></div>
    </div>
    <div class="queue-grid">
      <section class="new-download-panel glass-card">
        <div class="download-form">
          <label class="url-input">${icon("link")}<input id="downloadUrl" type="url" placeholder="Paste YouTube URL here..." /></label>
          <div class="segmented">
            <button class="tab-button ${isAudio ? "active" : ""}" data-action="set-mode" data-mode="Audio">${icon("music-note")} Audio</button>
            <button class="tab-button ${!isAudio ? "active" : ""}" data-action="set-mode" data-mode="Video">${icon("video-camera")} Video</button>
          </div>
          <button class="primary-button" data-action="start-download">${icon("play", true)} Start</button>
        </div>
        <p>Downloading ${state.mode.toLowerCase()} in <span style="color:#fb5aa6">${isAudio ? state.settings.audio_quality : state.settings.video_quality}</span></p>
        <div class="download-options">
          ${qualities.map((value) => `<button class="chip ${currentQuality === value ? "active" : ""}" data-action="set-quality" data-quality="${h(value)}">${h(value)}</button>`).join("")}
          <div class="option-picker">
            <button class="chip option-chip ${state.parallelMenuOpen ? "active-soft" : ""}" data-action="toggle-parallel-menu" title="Change parallel downloads">${icon("arrows-left-right")} <span>Parallel downloads: ${h(state.settings.parallel_downloads)}</span></button>
            ${state.parallelMenuOpen ? `<div class="option-menu parallel-menu">${parallelOptions.map((value) => `<button class="${String(state.settings.parallel_downloads) === value ? "active" : ""}" data-action="set-parallel" data-value="${h(value)}">${h(value)}</button>`).join("")}</div>` : ""}
          </div>
          <button class="chip option-chip" data-action="browse-dir" title="${h(state.settings.download_dir)}">${icon("folder")} <span>Save to: ${h(shortPath(state.settings.download_dir))}</span></button>
        </div>
      </section>
      <aside class="summary-panel glass-card">
        <h2>Download Summary</h2>
        ${summaryRow("download-simple", "Queue", String(activeJobs.length), "In progress")}
        ${summaryRow("check", "Completed", String(state.stats.downloaded || state.tracks.length), "Media files")}
        ${summaryRow("x", "Failed", String(failedJobs), "Errors")}
        ${summaryRow("folder", "Output Folder", state.settings.download_dir || "Audora", "", "path-row")}
      </aside>
    </div>
    <section class="download-card" style="margin-top:18px">
      <div class="section-header"><h2>Recent Sessions</h2><button class="link-button" data-page="history">View all</button></div>
      ${indexedHistoryEntries().slice(0, 3).map((entry) => renderHistoryRow(entry, entry.historyIndex, true)).join("") || emptyState("No recent sessions", state.searchQuery ? "No sessions match your search." : "Completed downloads will appear here.", { label: "New Download", page: "new-download", iconName: "download-simple" })}
    </section>
  `;
}

function summaryRow(iconName, title, value, note, className = "") {
  return `
    <div class="summary-row ${h(className)}" title="${h(value)}">
      <span class="round-icon" style="width:52px;height:52px;font-size:28px">${icon(iconName)}</span>
      <div class="summary-row-content">
        <span class="muted">${h(title)}</span>
        <strong>${h(value)}</strong>
        ${note ? `<span class="muted">${h(note)}</span>` : ""}
      </div>
    </div>
  `;
}

function renderPlaylist() {
  const list = currentDownloadJobs();
  const total = playlistTotalCount(list);
  const discovered = list.length;
  const completed = list.filter((item) => downloadStatusLabel(item) === "Completed").length;
  const failed = list.filter((item) => item.failed || item.status === "error").length;
  const currentSpeed = list.find((item) => item.speed)?.speed || "0 B/s";
  const progress = playlistProgressPercent(list, total);
  return `
    <div class="playlist-shell">
      <section>
        <div class="page-title">
          <div><h1>Playlist Download</h1><p>Discovering and downloading tracks from a playlist.</p></div>
          <button class="danger-button" data-action="stop-download">Stop</button>
        </div>
        <section class="download-card playlist-card">
          <div class="section-header"><h2>Playlist <span class="meta-dot">.</span> discovering tracks</h2><span class="muted">${completed} of ${total} tracks</span></div>
          <div class="playlist-list">
            ${list.map((item, index) => playlistRow(item, index)).join("") || emptyState("No active playlist download", "Start a playlist download to see the queue here.", { label: "New Download", page: "new-download", iconName: "download-simple" })}
          </div>
        </section>
      </section>
      ${renderPlaylistSidebar(list, { total, discovered, completed, failed, progress, currentSpeed })}
    </div>
  `;
}

function renderPlaylistSidebar(list, metrics) {
  const firstVisual = list.find((item) => item.thumbnail_url || item.art);
  const titleItem = list.find((item) => item.title && !item.optimistic);
  const eta = list.find((item) => item.eta)?.eta || "";
  const cover = firstVisual?.thumbnail_url || firstVisual?.art || asset("album-party.png");
  const title = titleItem?.title || state.activeDownloadTitle || "Playlist Download";
  const activeText = list.length ? "Active transfer" : "No active transfer";
  return `
    <aside class="summary-panel playlist-summary-panel glass-card">
      <div class="playlist-summary-head">
        <img class="playlist-side-cover" src="${h(cover)}" data-fallback="${h(asset("album-party.png"))}" alt="" />
        <div><h2>${h(cleanTrackTitle(title))}</h2><p class="muted">${h(activeText)}</p></div>
      </div>
      <div class="playlist-side-stats">
        ${playlistStat("music-note", "Total Tracks", String(metrics.total), "Tracks")}
        ${playlistStat("magnifying-glass", "Discovered", String(metrics.discovered), "Tracks")}
        ${playlistStat("check-circle", "Completed", String(metrics.completed), "Tracks")}
        ${playlistStat("x", "Failed", String(metrics.failed), "Tracks")}
      </div>
      <div class="playlist-progress-block">
        <div class="section-header"><strong>Overall Progress</strong><span class="muted">${metrics.completed} of ${metrics.total} tracks</span></div>
        <div class="progress-track"><span style="width:${metrics.progress}%"></span></div>
        <span>${metrics.progress}%</span>
      </div>
      <section class="playlist-transfer-card">
        <div class="section-header"><h2>${icon("waveform")} Transfer</h2>${icon("caret-up")}</div>
        <div class="playlist-transfer-grid">
          <div><strong>${h(metrics.currentSpeed)}</strong><span class="muted">Current speed</span></div>
          <div><strong>${h(eta || "--:--")}</strong><span class="muted">ETA</span></div>
        </div>
      </section>
    </aside>
  `;
}

function playlistStat(iconName, title, value, note) {
  return `
    <article class="playlist-stat-card">
      <span class="round-icon">${icon(iconName)}</span>
      <div><span class="muted">${h(title)}</span><strong>${h(value)}</strong><span class="muted">${h(note)}</span></div>
    </article>
  `;
}

function playlistRow(item, index) {
  const progress = Math.round(item.percent ?? item.progress ?? 0);
  const label = downloadStatusLabel(item);
  const active = ["Reading link", "Preparing", "Downloading", "Processing"].includes(label);
  return `
    <div class="playlist-row">
      <img class="cover" style="width:64px;height:64px" src="${h(item.thumbnail_url || item.art || coverFor(index))}" data-fallback="${h(coverFor(index))}" alt="" />
      <span class="index-badge">#${index + 1}</span>
      <div class="row-title">
        <strong>${h(cleanTrackTitle(item.title || (item.optimistic ? "Reading playlist link" : `Track ${index + 1}`)))}</strong>
        <span style="color:${active ? "#fb5aa6" : "var(--muted)"}">${h(label)}</span>
        <div class="progress-track ${active && !progress ? "indeterminate" : ""}"><span style="width:${progress || (active ? 34 : 0)}%"></span></div>
      </div>
      <span>${progress ? `${progress}%` : icon("dots-three-vertical")}</span>
    </div>
  `;
}

function renderDevices() {
  const activity = Array.isArray(state.sync.activity) ? state.sync.activity : [];
  return `
    <div class="page-title">
      <div><h1>Devices & Sync</h1><p>Connect your devices. Keep your music in perfect sync.</p></div>
    </div>
    <section class="qr-card glass-card">
      <div class="pairing-copy">
        <h1>Pair your phone</h1>
        <p class="muted">Scan the QR code with Audora on your phone to connect.</p>
        <div class="pairing-tags">
          <span>${icon("check-circle")} Local Wi-Fi sync</span>
          <span>${icon("check-circle")} Fast</span>
          <span>${icon("check-circle")} Private</span>
        </div>
        <div class="pairing-actions">
          <button class="primary-button" data-action="generate-qr">${icon("arrows-clockwise")} Generate New QR</button>
          <button class="ghost-button" data-action="test-sync">${icon("pulse")} Test Connection</button>
        </div>
      </div>
      <div class="qr-box">
        ${state.qr ? `<img src="${h(state.qr)}" alt="Pairing QR code" />` : `<button class="qr-placeholder-button" data-action="generate-qr">${icon("qr-code")} <span>${state.qrLoading ? "Generating QR" : "Generate QR"}</span></button>`}
      </div>
      <div class="sync-metric-list">
        <div class="sync-metric-row">
          <span class="sync-metric-icon">${icon("crosshair")}</span>
          <div><span>Local IP</span><strong>${h(state.sync.host || "Unavailable")}</strong></div>
        </div>
        <div class="sync-metric-row">
          <span class="sync-metric-icon">${icon("monitor")}</span>
          <div><span>Port</span><strong>${h(state.sync.port || "5353")}</strong></div>
        </div>
        <div class="sync-metric-row server">
          <span class="sync-status-dot"></span>
          <div><span>Local Server</span><strong class="${state.sync.online ? "online-text" : "offline-text"}">${state.sync.online ? "Online" : "Offline"}</strong><em>Your devices can discover and connect.</em></div>
        </div>
      </div>
    </section>
    <div class="device-grid">
      <section class="download-card device-section-card">
        <div class="section-header"><h2><span class="section-icon">${icon("device-mobile")}</span> Paired Devices</h2><button class="ghost-button" disabled title="Device management is not available yet">${icon("sliders-horizontal")} Manage Devices</button></div>
        ${state.sync.devices.map((device, index) => deviceRow(device, index)).join("") || emptyState("No paired phones", "Generate a QR code and pair your phone to start local sync.", { label: "Generate QR", action: "generate-qr", iconName: "qr-code" })}
      </section>
      <section class="download-card device-section-card">
        <div class="section-header"><h2><span class="section-icon">${icon("clock-counter-clockwise")}</span> Sync History</h2></div>
        ${activity.map(syncHistory).join("") || syncEmptyState("No phone sync yet", "Phone sync activity will appear after a paired device syncs files.")}
      </section>
      <section class="download-card device-section-card manual-pairing-card">
        <div class="section-header"><h2><span class="section-icon">${icon("link")}</span> Manual Pairing</h2><button class="icon-button" data-action="toggle-manual" title="Show pairing details">${icon("caret-down")}</button></div>
        <p class="muted">If you can't scan the QR code, you can pair manually.</p>
        <div id="manualPairingBody" class="manual-grid accordion-body">
          <div class="copy-field"><div><span>Server Address</span><strong>${h(state.sync.host || "Unavailable")}</strong></div><button class="icon-button" data-copy="${h(state.sync.host || "")}" ${state.sync.host ? "" : "disabled"}>${icon("copy")}</button></div>
          <div class="copy-field"><div><span>Port</span><strong>${h(state.sync.port || "5353")}</strong></div><button class="icon-button" data-copy="${h(state.sync.port || "")}">${icon("copy")}</button></div>
          <div class="copy-field"><div><span>Server URL</span><strong>${h(state.sync.baseUrl || "")}</strong></div><button class="icon-button" data-copy="${h(state.sync.baseUrl || "")}">${icon("copy")}</button></div>
        </div>
        <section class="privacy-callout">
          <strong>${icon("shield-check")} No cloud. Local sync only.</strong>
          <p class="muted">Your music never leaves your network.</p>
        </section>
      </section>
    </div>
  `;
}

function deviceRow(device, index) {
  return `
    <div class="device-row">
      ${icon(device.platform && device.platform.toLowerCase().includes("android") ? "android-logo" : "device-mobile")}
      <div class="row-title"><strong>${h(device.name)}</strong><span>${h(device.platform || "Phone")}</span></div>
      <span>${h(formatDisplayDate(device.lastSeen) || "Not synced")}</span>
      <span>${h(device.storage || "0 GB")}</span>
      <button class="ghost-button" disabled title="Desktop-initiated sync is not available yet">Sync Now</button>
      <span>${icon("dots-three")}</span>
    </div>
  `;
}

function syncHistory(device, index) {
  const status = String(device.status || "synced").toLowerCase();
  const failed = status.includes("fail") || status.includes("error");
  return `
    <div class="compact-row" style="grid-template-columns:30px minmax(0,1fr) 112px 26px">
      ${icon("device-mobile")}
      <div class="row-title"><strong>${h(device.trackTitle || "Phone sync")}</strong><span>${h(device.deviceName || "Paired phone")}</span></div>
      <span class="muted">${h(formatDisplayDate(device.timestamp) || "Synced")}</span>
      <span class="sync-done-icon ${failed ? "failed" : ""}">${icon(failed ? "x-circle" : "check-circle")}</span>
    </div>
  `;
}

function renderSettings() {
  const syncState = state.sync.online ? "Online" : "Offline";
  return `
    <div class="page-title">
      <div><h1>Settings</h1><p>Control how Audora downloads, syncs, stores, and plays your media.</p></div>
      <button class="primary-button" data-action="save-settings">${icon("floppy-disk")} Save Settings</button>
    </div>
    <div class="settings-grid">
      ${settingsPanel("folder", "Library Paths", "Manage locations of your media folders.", [
    settingPath("Download folder", state.settings.download_dir || "Music/Audora"),
    settingText("Videos folder", `<span class="muted">Uses download folder</span>${disabledButton("Edit")}`),
    settingText("Artwork", `<span class="muted">Saved beside media</span>${disabledButton("Edit")}`),
    `<button class="plain-button" data-action="browse-dir" style="width:100%;margin-top:12px">${icon("folder-open")} Change Folder</button>`,
  ])}
      ${settingsPanel("arrows-clockwise", "Sync Server", "Configure your local sync server.", [
    settingText("Local Sync", `<span style="color:${state.sync.online ? "#22c55e" : "#fb5b88"}">${h(syncState)}</span>${toggle(true)}`),
    settingText("Server Port", `<span class="path-input">${h(state.sync.port || "5353")}</span>`),
    settingText("LAN Visibility", `<span class="success-tag">Visible on LAN</span>`),
    settingText("QR Pairing", toggle(true)),
    settingText("Device Discovery", toggle(true)),
    settingText("Server Address", `<span class="path-input">${h(state.sync.baseUrl || "")}</span>`),
  ])}
      ${settingsPanel("download-simple", "Downloads", "Control how and where downloads are saved.", [
    settingSelect("Default audio bitrate", "audioQualitySetting", AUDIO_QUALITIES, state.settings.audio_quality || "192 kbps", "audio_quality"),
    settingSelect("Default video resolution", "videoQualitySetting", VIDEO_QUALITIES, state.settings.video_quality || "1080p", "video_quality"),
    settingSelect("Parallel downloads", "parallelDownloadsSetting", ["1", "2", "3", "4", "5", "6", "7", "8"], String(state.settings.parallel_downloads || "3"), "parallel_downloads"),
    settingText("File naming", `<span class="muted">Track title</span>${disabledButton("Locked")}`),
    settingText("Format", `<span class="muted">MP3 audio / MP4 video</span>${disabledButton("Auto")}`),
  ])}
      ${settingsPanel("waveform", "Playback", "Customize your playback experience.", [
    settingText("Shuffle", toggle(true, state.player.shuffle)),
    settingText("Repeat", toggle(true, state.player.repeat)),
    settingText("Crossfade", `<span class="muted">Not available yet</span>${disabledButton("Soon")}`),
    settingText("Default Output", `<span class="muted">System default</span>${disabledButton("Locked")}`),
  ])}
      ${settingsPanel("database", "Storage", "Monitor usage and manage cached data.", [
    settingText("Used Space", `<span>${h(state.stats.percent || 0)}% &middot; ${h(state.stats.used)} of ${h(state.stats.total)}</span>`),
    settingText("Library items", `<span>${h(state.stats.items || 0)} items</span>${disabledButton("Manage")}`),
    settingText("Cache Size", `<span class="muted">No separate cache</span>${disabledButton("Clear")}`),
    settingText("Analyze Library", `<button class="danger-button" data-action="scan-library">${icon("arrows-clockwise")} Analyze</button>`),
  ])}
      ${settingsPanel("shield-check", "Security", "Manage access and keep your data private.", [
    settingText("Paired Devices", `<span>${state.sync.devices.length} devices</span><button class="ghost-button" data-page="devices">Manage</button>`),
    settingText("Active Sessions", `<span class="muted">Local desktop session</span>${disabledButton("View")}`),
    settingText("API Tokens", `<span class="muted">Managed by pairing</span>${disabledButton("Manage")}`),
    settingText("Privacy Mode", `<span class="success-tag">Local Only</span>`),
  ])}
    </div>
  `;
}

function settingsPanel(iconName, title, note, rows) {
  return `
    <section class="settings-panel">
      <div class="settings-head"><span class="storage-icon">${icon(iconName)}</span><div><h2>${h(title)}</h2><p class="muted">${h(note)}</p></div></div>
      <div>${rows.join("")}</div>
    </section>
  `;
}

function settingPath(title, path) {
  return `<div class="settings-row"><div><strong>${h(title)}</strong><div class="muted">${h(path)}</div></div><button class="ghost-button" data-action="browse-dir">Edit</button></div>`;
}

function settingText(title, control) {
  return `<div class="settings-row"><strong>${h(title)}</strong><div class="filters" style="justify-content:end">${control}</div></div>`;
}

function settingSelect(title, id, options, selected, settingKey) {
  const open = Boolean(state.settingMenus[id]);
  return `
    <div class="settings-row">
      <strong>${h(title)}</strong>
      <div class="setting-select ${open ? "open" : ""}">
        <button class="setting-select-button" data-action="toggle-setting-menu" data-menu="${h(id)}" type="button">
          <span>${h(selected)}</span>${icon(open ? "caret-up" : "caret-down")}
        </button>
        ${open ? `<div class="setting-select-menu">${options.map((option) => `<button class="${option === selected ? "active" : ""}" data-action="set-setting-select" data-menu="${h(id)}" data-setting="${h(settingKey)}" data-value="${h(option)}" type="button">${h(option)}</button>`).join("")}</div>` : ""}
      </div>
    </div>
  `;
}

function toggle(disabled = false, enabled = true) {
  return `<button class="toggle ${enabled ? "" : "off"}" aria-label="${enabled ? "Enabled" : "Disabled"}" ${disabled ? 'disabled title="Not configurable yet"' : ""}></button>`;
}

function disabledButton(label) {
  return `<button class="ghost-button" disabled title="Not available yet">${h(label)}</button>`;
}

function renderHistory() {
  const entries = indexedHistoryEntries();
  return `
    <div class="history-layout">
      <div class="page-title">
        <div><h1>Download History</h1><p>Your past downloads, playlists, and saved sessions.</p></div>
        <div class="filters">
          <span class="muted">Sort by:</span>
          <button class="select-pill" disabled title="Newest first">${icon("sort-descending")} Newest First</button>
          <button class="ghost-button" data-action="open-folder">${icon("folder-open")} Open in File Explorer</button>
        </div>
      </div>
      <div class="stats-grid">
        ${statCard("download-simple", "Total Sessions", String(state.stats.sessions || state.history.length), "All time")}
        ${statCard("music-note", "Tracks Downloaded", String(state.stats.tracks || state.tracks.length), "All time")}
        ${statCard("video-camera", "Videos", String(state.stats.videos || 0), "All time")}
        ${statCard("list-bullets", "Playlists", String(state.stats.playlists || historyPlaylistEntries().length), "All time")}
      </div>
      <section class="download-card">
        <div class="section-header"><h2>All Download Sessions <span class="muted">(${entries.length})</span></h2></div>
        ${entries.map((entry) => renderHistoryRow(entry, entry.historyIndex)).join("") || emptyState("No downloads yet", state.searchQuery ? "No downloads match your search." : "Start a download to build your history.", { label: "New Download", page: "new-download", iconName: "download-simple" })}
      </section>
    </div>
  `;
}

function renderHistoryRow(entry, index, compact = false) {
  const deleting = entry.deleteState === "deleting";
  const deleted = entry.deleteState === "deleted";
  const className = `history-row ${deleting ? "deleting" : ""} ${deleted ? "deleted" : ""}`;
  const status = deleting ? "Deleting..." : deleted ? "Deleted" : "";
  const meta = joinMeta([
    formatDisplayDate(entry.timestamp),
    entry.count ? `${entry.count} track${entry.count === 1 ? "" : "s"}` : "",
    entry.format,
    entry.quality,
    entry.size,
  ]);
  return `
    <div class="${className}" data-action="open-history-detail" data-idx="${index}">
      <img class="cover large" style="width:${compact ? 72 : 74}px;height:${compact ? 72 : 74}px" src="${h(entry.art || coverFor(index))}" data-fallback="${h(entry.type === "folder" ? asset("album-party.png") : coverFor(index))}" alt="" />
      <div class="row-title">
        <strong>${h(cleanTrackTitle(entry.title || entry.name))}</strong>
        <span>${meta}</span>
        <span class="delete-note">${status}</span>
      </div>
      <button class="icon-button" data-action="play-history" data-idx="${index}" title="Play" ${deleting || deleted ? "disabled" : ""}>${icon("play", true)}</button>
      <button class="icon-button" data-action="open-path" data-idx="${index}" title="Open folder" ${deleting || deleted ? "disabled" : ""}>${icon("folder-open")}</button>
      <button class="icon-button danger" data-action="delete-history" data-idx="${index}" title="Delete" ${deleting || deleted ? "disabled" : ""}>${icon("trash-simple")}</button>
    </div>
  `;
}

function renderHistoryDetail() {
  const entry = state.history[state.selectedHistoryIndex] || state.history[0];
  if (!entry) {
    return `
      <button class="back-link" data-page="history">${icon("arrow-left")} Download History</button>
      <section class="download-card" style="margin-top:24px">
        ${emptyState("No download selected", "Completed downloads will appear here.", { label: "Download History", page: "history", iconName: "clock-counter-clockwise" })}
      </section>
    `;
  }
  const children = Array.isArray(entry.children) ? entry.children : [];
  const entryDeleting = entry.deleteState === "deleting";
  const entryDeleted = entry.deleteState === "deleted";
  const detailThumb = entry.thumbnailUri || firstChildArt(children);
  return `
    <button class="back-link" data-page="history">${icon("arrow-left")} Download History</button>
    <div class="detail-layout" style="margin-top:34px">
      <section>
        <div class="detail-heading">
          <p>${entry.type === "folder" ? "Downloaded Playlist" : "Downloaded File"}</p>
          <h1>${h(cleanTrackTitle(entry.title || entry.name))}</h1>
          <p>${h(formatDisplayDate(entry.timestamp) || "")}</p>
        </div>
        <section class="detail-list glass-card" style="margin-top:18px">
          <div class="section-header"><div></div><div class="segmented"><button class="tab-button active" disabled title="List view">${icon("list-bullets")}</button><button class="tab-button" disabled title="Grid view is not available yet">${icon("squares-four")}</button></div></div>
          ${entry.type === "folder" ? (children.map((child, index) => detailTrackRow(child, index)).join("") || emptyState("No tracks in this download", "Audora did not find child files for this item.", { label: "Back to History", page: "history", iconName: "arrow-left" })) : detailSingleRow(entry)}
          <p class="muted" style="text-align:center">${entry.type === "folder" ? children.length : 1} track${entry.type === "folder" && children.length !== 1 ? "s" : ""}</p>
        </section>
      </section>
      <aside class="side-panel detail-side">
        ${detailThumb ? `<img class="folder-hero-image" src="${h(detailThumb)}" data-fallback="${h(asset("album-party.png"))}" alt="" />` : `<div class="folder-hero">${icon(entry.type === "folder" ? "folder" : "file-audio")}</div>`}
        <h2>${h(cleanTrackTitle(entry.title || entry.name))}</h2>
        <div class="metadata-list">
          <div><span>${icon("list-bullets")} Total Tracks</span><strong>${h(entry.count || children.length || 1)}</strong></div>
          <div><span>${icon("archive")} Type</span><strong>${entry.type === "folder" ? "Playlist Folder" : "Media File"}</strong></div>
          <div><span>${icon("calendar")} Created</span><strong>${h(formatDisplayDate(entry.timestamp) || "-")}</strong></div>
          <div><span>${icon("folder")} Location</span><strong>${h(shortPath(entry.path || ""))}</strong></div>
          <div><span>${icon("hard-drives")} Size</span><strong>${h(entry.size || "-")}</strong></div>
        </div>
        <button class="primary-button" data-action="open-path" data-idx="${state.selectedHistoryIndex}" style="width:100%" ${entryDeleting || entryDeleted ? "disabled" : ""}>${icon("folder-open")} Open Folder</button>
        <button class="plain-button" data-action="play-history" data-idx="${state.selectedHistoryIndex}" style="width:100%;margin-top:12px" ${entryDeleting || entryDeleted ? "disabled" : ""}>${icon("play")} Play All</button>
      </aside>
    </div>
  `;
}

function detailTrackRow(child, index) {
  const deleting = child.deleteState === "deleting";
  const deleted = child.deleteState === "deleted";
  return `
    <div class="detail-row ${deleting ? "deleting" : ""} ${deleted ? "deleted" : ""}">
      <img class="cover large" style="width:92px;height:72px" src="${h(child.thumbnailUri || child.art || coverFor(index))}" data-fallback="${h(coverFor(index))}" alt="" />
      <div class="row-title"><strong>${h(cleanTrackTitle(child.title || child.name))}</strong><span>${deleting ? "Deleting..." : deleted ? "Deleted" : `${icon("calendar")} ${h(formatDisplayDate(child.timestamp) || "")}`}</span></div>
      <div class="filters" style="justify-content:end">
        <button class="icon-button" data-action="play-detail-child" data-idx="${index}" ${deleting || deleted ? "disabled" : ""}>${icon("play", true)}</button>
        <button class="icon-button" data-action="open-detail-child-path" data-idx="${index}" ${deleting || deleted ? "disabled" : ""}>${icon("folder-open")}</button>
        <button class="icon-button danger" data-action="delete-detail-child" data-idx="${index}" ${deleting || deleted ? "disabled" : ""}>${icon("trash-simple")}</button>
      </div>
    </div>
  `;
}

function detailSingleRow(entry) {
  const deleting = entry.deleteState === "deleting";
  const deleted = entry.deleteState === "deleted";
  return `
    <div class="detail-row ${deleting ? "deleting" : ""} ${deleted ? "deleted" : ""}">
      <img class="cover large" style="width:92px;height:72px" src="${h(entry.thumbnailUri || entry.art || coverFor(0))}" data-fallback="${h(entry.type === "folder" ? asset("album-party.png") : coverFor(0))}" alt="" />
      <div class="row-title"><strong>${h(cleanTrackTitle(entry.title || entry.name))}</strong><span>${deleting ? "Deleting..." : deleted ? "Deleted" : `${icon("calendar")} ${h(formatDisplayDate(entry.timestamp) || "")}`}</span></div>
      <div class="filters" style="justify-content:end">
        <button class="icon-button" data-action="play-history" data-idx="${state.selectedHistoryIndex}" ${deleting || deleted ? "disabled" : ""}>${icon("play", true)}</button>
        <button class="icon-button" data-action="open-path" data-idx="${state.selectedHistoryIndex}" ${deleting || deleted ? "disabled" : ""}>${icon("folder-open")}</button>
        <button class="icon-button danger" data-action="delete-history" data-idx="${state.selectedHistoryIndex}" ${deleting || deleted ? "disabled" : ""}>${icon("trash-simple")}</button>
      </div>
    </div>
  `;
}

function emptyState(title, detail, action = null) {
  const actionMarkup = action
    ? `<button class="ghost-button" ${action.page ? `data-page="${h(action.page)}"` : ""} ${action.action ? `data-action="${h(action.action)}"` : ""}>${icon(action.iconName || "arrow-right")} ${h(action.label || "Continue")}</button>`
    : "";
  return `<div class="empty-state"><div><strong>${h(title)}</strong><p>${h(detail)}</p>${actionMarkup ? `<div class="empty-state-actions">${actionMarkup}</div>` : ""}</div></div>`;
}

function shortPath(path) {
  if (!path) return "Audora";
  const value = String(path);
  if (value.length <= 42) return value;
  return `${value.slice(0, 24)}...${value.slice(-15)}`;
}

async function refreshState() {
  const data = await callApi("get_state", undefined, null);
  if (!data) {
    state.loading = false;
    updateChrome();
    render();
    return;
  }
  normalizeBackend(data);
  render();
}

async function ensurePairingQr() {
  if (state.qr || state.qrLoading || state.page !== "devices") return;
  state.qrLoading = true;
  const result = await callApi("generate_pairing", undefined, null);
  state.qrLoading = false;
  if (result && result.qr) {
    state.qr = result.qr;
    if (result.sync) state.sync = { ...state.sync, ...result.sync };
  }
  if (state.page === "devices") render();
}

function refreshAfterDownloadSettles() {
  [160, 520, 1100, 2200].forEach((delay) => {
    setTimeout(refreshState, delay);
  });
}

async function pollEvents() {
  const data = await callApi("poll_events", undefined, null);
  if (!data) return;
  const wasActive = state.downloadsActive;
  const terminalDownload = Array.isArray(data.events) && data.events.some(isTerminalDownloadEvent);
  const startupGrace = isDownloadStartupGrace();
  if (typeof data.active === "boolean") state.downloadsActive = data.active || startupGrace;
  if (Array.isArray(data.jobs)) {
    const jobs = normalizeJobs(data.jobs);
    if (jobs.length) state.jobs = jobs;
    else if (!state.downloadsActive) state.jobs = [];
  }
  if (Array.isArray(data.logs)) state.logs = data.logs;
  if (data.sync) state.sync = { ...state.sync, ...data.sync };
  if (typeof data.libraryRefreshing === "boolean") state.libraryRefreshing = data.libraryRefreshing;
  if (terminalDownload) {
    state.downloadsActive = false;
    state.downloadStartedAt = 0;
    state.jobs = [];
    render();
    refreshAfterDownloadSettles();
    return;
  }
  if (data.libraryChanged) {
    await refreshState();
    return;
  }
  if (data.changed || wasActive !== state.downloadsActive) render();
}

async function handleStartDownload() {
  const url = $("#downloadUrl")?.value.trim();
  if (!url) {
    toast("Paste a YouTube link first.");
    return;
  }
  const payload = {
    url,
    mode: state.mode,
    audioQuality: state.settings.audio_quality,
    videoQuality: state.settings.video_quality,
    outDir: state.settings.download_dir,
  };
  state.downloadsActive = true;
  state.downloadStartedAt = Date.now();
  state.jobs = [makeOptimisticJob(url)];
  state.activeDownloadTitle = url.includes("list=") || url.toLowerCase().includes("playlist") ? "Playlist Download" : "Current Download";
  state.page = state.activeDownloadTitle === "Playlist Download" ? "playlist" : "downloads";
  render();
  const result = await callApi("start_download", payload, { ok: true });
  if (result && result.ok !== false) {
    toast("Download started.");
  } else {
    state.downloadsActive = false;
    state.downloadStartedAt = 0;
    state.jobs = [];
    state.page = "new-download";
    toast((result && result.error) || "Could not start download.");
    render();
  }
}

function removeDeletedEntry(index, entry) {
  const deletedPath = entry?.path || "";
  const activePlaylist =
    state.libraryPlaylistHistoryIndex === null ? null : state.history[state.libraryPlaylistHistoryIndex];
  const activePlaylistDeleted = deletedPath && activePlaylist?.path && isPathInside(activePlaylist.path, deletedPath);

  state.history = state.history.filter((_, entryIndex) => entryIndex !== index);
  removeDeletedMediaPath(deletedPath);
  if (state.selectedHistoryIndex >= state.history.length) {
    state.selectedHistoryIndex = Math.max(0, state.history.length - 1);
  }
  if (state.libraryPlaylistHistoryIndex !== null) {
    if (activePlaylistDeleted || state.libraryPlaylistHistoryIndex === index) {
      state.libraryPlaylistHistoryIndex = null;
      state.selectedLibraryIndex = 0;
    } else if (state.libraryPlaylistHistoryIndex > index) {
      state.libraryPlaylistHistoryIndex -= 1;
    }
  }

}

function removeDeletedMediaPath(path) {
  if (!path) return;
  state.tracks = state.tracks.filter((track) => !isPathInside(track.path, path));
  state.history = state.history
    .map((entry) => {
      if (!Array.isArray(entry.children)) return entry;
      const children = entry.children.filter((child) => !isSamePath(child.path, path) && !isPathInside(child.path, path));
      return { ...entry, children, count: children.length };
    })
    .filter((entry) => {
      if (entry.path && (isSamePath(entry.path, path) || isPathInside(entry.path, path))) return false;
      return entry.type !== "folder" || !Array.isArray(entry.children) || entry.children.length > 0;
    });
  const currentPath = state.player.current?.path || "";
  if (currentPath && isPathInside(currentPath, path)) {
    const media = $("#mediaPlayer");
    media?.pause();
    state.player = { ...state.player, playing: false, current: null };
    setVideoPanelVisible(false);
  }
}

function isDeleteGone(result) {
  return String(result?.error || "").toLowerCase().includes("no longer exists");
}

async function deleteWithLifecycle(path, removeFromState) {
  if (!path || deleteStateFor(path)) return;
  setDeleteState(path, "deleting");
  render();
  const startedAt = Date.now();
  const result = await callApi("delete_path", { path }, { ok: true });
  const remainingDeleteTime = Math.max(0, 750 - (Date.now() - startedAt));
  if (remainingDeleteTime) await delay(remainingDeleteTime);
  if (!result || result.ok === false) {
    if (!isDeleteGone(result)) {
      clearDeleteState(path);
      toast((result && result.error) || "Delete failed.");
      render();
      return;
    }
  }
  setDeleteState(path, "deleted");
  render();
  setTimeout(() => {
    removeFromState();
    clearDeleteState(path);
    render();
    refreshState();
  }, 3000);
}

async function deleteLibraryItem(index) {
  const item = libraryItems()[index];
  if (!item) return;
  if (item.kind === "playlist") {
    await deleteHistory(item.historyIndex);
    return;
  }
  await deleteWithLifecycle(item.path, () => {
    removeDeletedMediaPath(item.path);
    if (state.libraryPlaylistHistoryIndex !== null) {
      const activePlaylist = state.history[state.libraryPlaylistHistoryIndex];
      if (!activePlaylist || activePlaylist.type !== "folder") {
        state.libraryPlaylistHistoryIndex = null;
        state.selectedLibraryIndex = 0;
      }
    }
    if (state.selectedLibraryIndex >= libraryItems().length) {
      state.selectedLibraryIndex = Math.max(0, libraryItems().length - 1);
    }
  });
}

async function deleteHistory(index) {
  const entry = state.history[index];
  if (!entry) return;
  await deleteWithLifecycle(entry.path, () => removeDeletedEntry(index, entry));
}

async function deleteDetailChild(index) {
  const entryIndex = state.selectedHistoryIndex;
  const entry = state.history[entryIndex];
  const child = Array.isArray(entry?.children) ? entry.children[index] : null;
  if (!entry || !child) return;
  const entryPath = entry.path;
  await deleteWithLifecycle(child.path, () => {
    removeDeletedMediaPath(child.path);
    const nextEntryIndex = state.history.findIndex((item) => isSamePath(item.path, entryPath));
    if (nextEntryIndex < 0) {
      state.page = "history";
      state.selectedHistoryIndex = Math.max(0, Math.min(state.selectedHistoryIndex, state.history.length - 1));
    } else {
      state.selectedHistoryIndex = nextEntryIndex;
    }
  });
}

document.addEventListener("click", async (event) => {
  if (event.target.closest("#miniPlay")) {
    const media = $("#mediaPlayer");
    if (media && media.src) {
      if (media.paused) {
        try {
          await media.play();
          state.player.playing = true;
        } catch (_error) {
          toast("Audora could not resume this file inside the app.");
        }
      } else {
        media.pause();
        state.player.playing = false;
      }
      updateChrome();
    } else {
      await playMediaItem(state.tracks[state.selectedTrackIndex] || state.tracks[0]);
    }
    return;
  }

  const copyButton = event.target.closest("[data-copy]");
  if (copyButton) {
    navigator.clipboard?.writeText(copyButton.dataset.copy || "");
    toast("Copied.");
    return;
  }

  const pageButton = event.target.closest("[data-page]");
  if (pageButton) {
    state.page = pageButton.dataset.page;
    state.parallelMenuOpen = false;
    state.settingMenus = {};
    render();
    return;
  }

  const actionButton = event.target.closest("[data-action]");
  if (!actionButton) return;
  event.stopPropagation();
  const action = actionButton.dataset.action;
  const idx = Number(actionButton.dataset.idx || 0);

  if (action === "set-mode") {
    state.mode = actionButton.dataset.mode || "Audio";
    state.parallelMenuOpen = false;
    render();
  } else if (action === "set-quality") {
    const value = actionButton.dataset.quality || "";
    if (state.mode === "Audio" && AUDIO_QUALITIES.includes(value)) state.settings.audio_quality = value;
    if (state.mode === "Video" && VIDEO_QUALITIES.includes(value)) state.settings.video_quality = value;
    state.parallelMenuOpen = false;
    const result = await callApi("save_settings", state.settings, { ok: true, settings: state.settings });
    if (result?.settings) state.settings = { ...state.settings, ...result.settings };
    render();
  } else if (action === "toggle-parallel-menu") {
    state.parallelMenuOpen = !state.parallelMenuOpen;
    render();
  } else if (action === "set-parallel") {
    const value = actionButton.dataset.value || state.settings.parallel_downloads;
    state.settings.parallel_downloads = value;
    state.parallelMenuOpen = false;
    const result = await callApi("save_settings", state.settings, { ok: true, settings: state.settings });
    if (result?.settings) state.settings = { ...state.settings, ...result.settings };
    toast(`Parallel downloads set to ${state.settings.parallel_downloads}.`);
    render();
  } else if (action === "toggle-setting-menu") {
    const menu = actionButton.dataset.menu || "";
    state.settingMenus = { [menu]: !state.settingMenus[menu] };
    render();
  } else if (action === "set-setting-select") {
    const key = actionButton.dataset.setting || "";
    const value = actionButton.dataset.value || "";
    if (key) state.settings[key] = value;
    state.settingMenus = {};
    render();
  } else if (action === "set-library-filter") {
    state.libraryFilter = actionButton.dataset.filter || "All";
    state.selectedLibraryIndex = 0;
    render();
  } else if (action === "start-download") {
    await handleStartDownload();
  } else if (action === "stop-download") {
    await callApi("stop_download", undefined, { ok: true });
    state.downloadsActive = false;
    state.downloadStartedAt = 0;
    state.jobs = [];
    toast("Download stopped.");
    render();
    refreshAfterDownloadSettles();
  } else if (action === "open-devices") {
    state.page = "devices";
    render();
  } else if (action === "player-next") {
    await playAdjacent(1);
  } else if (action === "player-prev") {
    await playAdjacent(-1);
  } else if (action === "toggle-shuffle") {
    state.player.shuffle = !state.player.shuffle;
    updateChrome();
  } else if (action === "toggle-repeat") {
    state.player.repeat = !state.player.repeat;
    updateChrome();
  } else if (action === "open-folder" || action === "open-path") {
    const entry = actionButton.dataset.idx === undefined ? null : state.history[idx];
    await callApi("open_path", { path: entry?.path || state.settings.download_dir }, { ok: true });
  } else if (action === "open-detail-child-path") {
    const entry = state.history[state.selectedHistoryIndex] || state.history[0];
    const child = Array.isArray(entry?.children) ? entry.children[idx] : null;
    await callApi("open_path", { path: child?.path || entry?.path || state.settings.download_dir }, { ok: true });
  } else if (action === "open-library-path") {
    const item = libraryItems()[idx];
    await callApi("open_path", { path: item?.path || state.settings.download_dir }, { ok: true });
  } else if (action === "delete-history") {
    await deleteHistory(idx);
  } else if (action === "delete-detail-child") {
    await deleteDetailChild(idx);
  } else if (action === "delete-library") {
    await deleteLibraryItem(idx);
  } else if (action === "open-history-detail") {
    state.selectedHistoryIndex = idx;
    state.page = "history-detail";
    render();
  } else if (action === "play-history") {
    const entry = state.history[idx] || state.history[0];
    await playMediaItem(firstPlayableFromHistory(entry));
  } else if (action === "close-video-player") {
    setVideoPanelVisible(false);
  } else if (action === "show-video-player") {
    if (isVideoItem(state.player.current) && state.player.current?.uri) {
      setVideoPanelVisible(true, state.player.current);
    }
  } else if (action === "open-library-playlist") {
    state.libraryPlaylistHistoryIndex = idx;
    state.libraryFilter = "All";
    state.selectedLibraryIndex = 0;
    state.page = "library";
    render();
  } else if (action === "close-library-playlist") {
    state.libraryPlaylistHistoryIndex = null;
    state.selectedLibraryIndex = 0;
    render();
  } else if (action === "select-library") {
    const item = libraryItems()[idx];
    state.selectedLibraryIndex = idx;
    if (item && item.kind === "track") state.selectedTrackIndex = item.trackIndex;
    render();
  } else if (action === "select-track") {
    state.selectedTrackIndex = idx;
    render();
  } else if (action === "play-library") {
    const item = libraryItems()[idx];
    if (item && item.kind === "track") {
      state.selectedLibraryIndex = idx;
      if (typeof item.trackIndex === "number") state.selectedTrackIndex = item.trackIndex;
      await playMediaItem(item);
      render();
    }
  } else if (action === "play-detail-child") {
    const entry = state.history[state.selectedHistoryIndex] || state.history[0];
    const child = Array.isArray(entry?.children) ? entry.children[idx] : null;
    await playMediaItem(child);
  } else if (action === "play-track") {
    state.selectedTrackIndex = idx;
    await playMediaItem(state.tracks[idx] || state.tracks[0]);
  } else if (action === "generate-qr") {
    state.qrLoading = true;
    render();
    const result = await callApi("generate_pairing", undefined, null);
    state.qrLoading = false;
    if (result && result.qr) {
      state.qr = result.qr;
      if (result.sync) state.sync = { ...state.sync, ...result.sync };
      toast("Pairing QR generated.");
      state.page = "devices";
      render();
    } else {
      toast("QR generation is not available yet.");
      render();
    }
  } else if (action === "scan-library") {
    const result = await callApi("scan_library", undefined, { ok: true, message: "Scanning library." });
    toast((result && result.message) || "Library scan started.");
    setTimeout(refreshState, 1200);
  } else if (action === "test-sync") {
    const result = await callApi("test_sync", undefined, { ok: true, message: "Connection looks good." });
    toast((result && result.message) || "Connection looks good.");
  } else if (action === "toggle-manual") {
    $("#manualPairingBody")?.classList.toggle("open");
  } else if (action === "browse-dir") {
    state.parallelMenuOpen = false;
    const result = await callApi("choose_download_dir", undefined, null);
    if (result && result.path) {
      state.settings.download_dir = result.path;
      render();
    }
  } else if (action === "save-settings") {
    const nextSettings = {
      ...state.settings,
      audio_quality: state.settings.audio_quality,
      video_quality: state.settings.video_quality,
      parallel_downloads: state.settings.parallel_downloads,
    };
    const result = await callApi("save_settings", nextSettings, { ok: true, settings: nextSettings });
    if (result?.settings) state.settings = { ...state.settings, ...result.settings };
    toast((result && result.message) || "Settings saved.");
    render();
  }
});

window.addEventListener("DOMContentLoaded", () => {
  document.addEventListener(
    "error",
    (event) => {
      const target = event.target;
      if (target && target.tagName === "IMG" && !target.dataset.fallbackApplied) {
        target.dataset.fallbackApplied = "true";
        target.src = target.dataset.fallback || asset("album-moment-apart.png");
      }
    },
    true,
  );
  $("#globalSearch")?.addEventListener("input", (event) => {
    state.searchQuery = event.target.value || "";
    state.selectedLibraryIndex = 0;
    render();
  });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      const search = $("#globalSearch");
      search?.focus();
      search?.select();
    }
  });
  renderNav();
  render();
  const media = $("#mediaPlayer");
  if (media) {
    media.addEventListener("play", () => {
      state.player.playing = true;
      updateChrome();
    });
    media.addEventListener("pause", () => {
      state.player.playing = false;
      updateChrome();
    });
    media.addEventListener("ended", async () => {
      state.player.playing = false;
      updateChrome();
      if (state.player.repeat && state.player.current) {
        await playMediaItem(state.player.current);
      } else {
        await playAdjacent(1);
      }
    });
    media.addEventListener("loadedmetadata", updatePlayerProgress);
    media.addEventListener("durationchange", updatePlayerProgress);
    media.addEventListener("timeupdate", updatePlayerProgress);
  }
  $("#playerSeek")?.addEventListener("input", (event) => {
    const mediaElement = $("#mediaPlayer");
    const duration = Number(mediaElement?.duration || 0);
    if (mediaElement && duration) {
      mediaElement.currentTime = (Number(event.target.value || 0) / 1000) * duration;
      updatePlayerProgress();
    }
  });
  $(".volume-cluster input")?.addEventListener("input", (event) => {
    const mediaElement = $("#mediaPlayer");
    if (mediaElement) mediaElement.volume = Number(event.target.value || 0) / 100;
  });
  setTimeout(refreshState, 180);
  setInterval(pollEvents, 900);
});

window.addEventListener("pywebviewready", refreshState);
