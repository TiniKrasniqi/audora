# Audora Local Sync Roadmap

This is the implementation plan for Audora local sync. The desktop app comes first, using the existing Python codebase. The mobile app comes after the desktop MVP is working end to end.

## Stage 1 - Desktop MVP

Goal: Audora Desktop can index local downloads, pair a phone, and serve selected media files over the local network without cloud access.

### Phase 1.1 - Sync Foundation

Status: Completed

- Add a desktop sync package under `core/sync`.
- Add a SQLite database for tracks, paired devices, pairing sessions, and sync history.
- Add a library scanner that indexes Audora downloads without exposing absolute paths through the API.
- Add secure token helpers that store only token hashes.
- Add a local HTTP API foundation on the default port `37124`.

### Phase 1.2 - Protected Desktop API

Status: Completed

- Add `GET /api/v1/health`.
- Add `POST /api/v1/pair/start`.
- Add `POST /api/v1/pair/complete`.
- Add `GET /api/v1/library`.
- Add `GET /api/v1/tracks/:id/file` with range request support.
- Add `POST /api/v1/sync/report`.
- Reject arbitrary file paths and unknown track IDs.

### Phase 1.3 - Desktop UI

Status: Completed

- Add Sync Settings to the desktop app.
- Add server status: running state, port, local IP, and paired device count.
- Add Pair Phone view with QR payload and manual IP/code fallback.
- Add paired devices list with revoke action.
- Add Scan Library Now action.

### Phase 1.4 - Desktop MVP Validation

Status: Completed

- Add unit tests for database, scanner, pairing, token validation, and protected media access.
- Add a simple local API smoke test.
- Verify the app still launches and existing downloader tests pass.

## Stage 2 - Mobile MVP

Goal: Audora Mobile pairs with desktop, fetches the desktop library, downloads audio over Wi-Fi, and plays it offline.

### Phase 2.1 - Expo App Foundation

Status: Planned

- Create an Expo Router TypeScript app under `mobile/`.
- Add Home, Pair Desktop, Desktop Library, Downloads, Player, and Settings screens.
- Add local SQLite schema for paired desktops, mobile tracks, and download queue.
- Add secure token storage.

### Phase 2.2 - Pairing And Library Fetch

Status: Planned

- Add QR scanner and manual pairing fallback.
- Save desktop pairing details securely.
- Add typed desktop API client.
- Fetch and persist remote library metadata.

### Phase 2.3 - Download Queue

Status: Planned

- Download one file at a time into app-private storage.
- Store local file URIs in SQLite.
- Show progress, failure, and retry states.
- Report successful sync back to desktop.

### Phase 2.4 - Offline Audio Player

Status: Planned

- Play only downloaded local files.
- Add play, pause, seek, next, and previous.
- Add mini player on main screens.
- Add full player screen.

## Stage 3 - Later Polish

Goal: improve convenience after the local sync MVP works.

- Add playlists.
- Add automatic LAN discovery through mDNS/Bonjour.
- Add video download/playback with large-file warnings.
- Add better HTTPS or signed-request protection for hostile LANs.
- Add background sync if native platform constraints allow it.
