# 📜 Changelog & Version History

All notable changes to **Hikvision Telegram Guard & Web Monitor** (`hikvision-telegram-guard`) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.2] — 2026-09-22 21:18:00 WIB

### 🐛 Fixed & Optimized
- **Resilient Multi-Stream Snapshot Fallback**:
  - Implemented automatic fallback cascade in `get_channel_snapshot()`: `[main_stream -> sub_stream -> 102 -> 101 -> 1 -> 2 -> NVR Channels]`.
  - Resolved `HTTP 503 Service Unavailable` on IP Cameras where Main-Stream snapshot capture (`/ISAPI/Streaming/channels/101/picture`) is restricted by firmware, automatically switching to Sub-Stream `102` (171 KB JPEG) in milliseconds.
  - Verified 100% snapshot delivery across all channels: Ch 1 (Lorong - 83 KB), Ch 2 (Jalan Utara - 168 KB), and Ch 3 (Jalan Selatan - 171 KB).
- **Dynamic Application Branding & Versioning in Telegram Alerts**:
  - Replaced static footer text with dynamic caption footer `🤖 {app_name} v{APP_VERSION}` (default: `🤖 Hikvision Telegram Guard v1.1.2`).
  - Added configurable `app_name` parameter in `config.json` and synchronized Web UI sidebar footer version to `v1.1.2 Multi-Cam`.

---

## [1.1.1] — 2026-09-22 21:09:00 WIB

### 🔐 Security & Access Control
- **Strict Backend PIN Gate**:
  - Protected all backend API endpoints (`/api/stats`, `/api/logs`, `/api/config`, `/api/gallery`, `/api/sync-nvr`, `/api/test-telegram`, `/api/live-stream`, `/api/camera-snapshot`) and static snapshot files with PIN authentication (`X-Web-PIN` header or `?pin=` parameter).
  - Unauthorized requests are rejected with `401 Unauthorized`.
- **Frontend Interaction Locking**:
  - Added `app-locked` state with 10px backdrop blur and disabled pointer interactions behind the PIN modal.
  - Added central `authFetch()` client wrapper that automatically locks the panel if a 401 response is received.
  - Added **"🔒 Kunci Panel"** manual lock / logout button in the sidebar navigation.

### 🎨 UI & Layout Enhancements
- **Balanced 2-Column Settings Layout**: Restructured the Settings tab into a clean two-column grid separating core system/Telegram parameters from the Multi-Camera channel mapping cards.
- **Visual Channel Indicators**: Channel cards now feature active green / muted slate border indicators (`ch-active` / `ch-muted`) with live label updates.
- **Sidebar Footer Versioning**: Updated sidebar footer brand to `v1.1.0 Multi-Cam`.
- **Real Production Screenshots**: Replaced mockups with actual high-resolution screenshots of the live system in `README.md`.

---

## [1.1.0] — 2026-09-22 20:44:00 WIB

### 🚀 Added
- **Universal Installer (`install.sh`)**:
  - Interactive CLI wizard for guided setup and configuration.
  - One-line auto-installer support via `curl -sSL ... | sudo bash`.
  - Distro detection supporting Debian, Ubuntu, AlmaLinux, Rocky Linux, CentOS, and Arch Linux.
  - Automated dependency verification and auto-installation (`python3`, `pip`, `requests`, `curl`).
  - Systemd service unit auto-registration, enable-on-boot, and live runtime health checks.
- **Auto-Discovery & NVR Sync Hub**:
  - Centralized NVR ISAPI alert listener (`alertStream`) to monitor all connected IP cameras simultaneously.
  - 1-Click Auto-Sync endpoint (`POST /api/sync-nvr`) fetching camera names and IP addresses from NVR channels.
  - On-the-fly dynamic channel registration when new cameras trigger events.
- **Multi-Camera Web Dashboard**:
  - Camera Selector switcher to switch live stream / snapshot view across any active channel.
  - Independent cooldown tracking and Telegram notification toggle per camera.
  - Camera-filtered Event Gallery with date picker and responsive HD modal previews.
- **Changelog Documentation (`CHANGELOG.md`)**: Tracking all releases, architecture changes, and installer updates.

### 🛡️ Security & Reliability
- Added HTTP `HEAD` method handling in `BaseHTTPRequestHandler` to eliminate connection reset warnings.
- Sanitized sample configuration templates and isolated production credentials.
- Graceful stream reconnect worker with exponential backoff on network/ISAPI disconnects.

---

## [1.0.0] — 2026-09-22 17:59:00 WIB

### 🎉 Initial Release
- Standalone Python 3 microservice (`cctv_it_guard.py`) without heavy external frameworks.
- Hikvision ISAPI XML multipart event parsing for Motion Detection (VMD).
- High-Resolution snapshot capturing and Telegram Bot dispatcher (`sendPhoto`) with formatted HTML captions.
- Integrated MJPEG live streaming endpoint (`/api/live-stream`).
- Retro Light / Soft Neo-Brutalism Web Dashboard with 6-digit PIN authentication.
- Automated snapshot disk storage retention worker (days and count limit).
- Systemd daemon unit template (`cctv-tg-guard.service`).
