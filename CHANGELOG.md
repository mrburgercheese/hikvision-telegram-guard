# 📜 Changelog

All notable changes to **Hikvision Telegram Guard & Web Monitor** (`hikvision-telegram-guard`) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.0] - 2026-09-22

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

## [1.0.0] - 2026-09-22

### 🎉 Initial Release
- Standalone Python 3 microservice (`cctv_it_guard.py`) without heavy external frameworks.
- Hikvision ISAPI XML multipart event parsing for Motion Detection (VMD).
- High-Resolution snapshot capturing and Telegram Bot dispatcher (`sendPhoto`) with formatted HTML captions.
- Integrated MJPEG live streaming endpoint (`/api/live-stream`).
- Retro Light / Soft Neo-Brutalism Web Dashboard with 6-digit PIN authentication.
- Automated snapshot disk storage retention worker (days and count limit).
- Systemd daemon unit template (`cctv-tg-guard.service`).
