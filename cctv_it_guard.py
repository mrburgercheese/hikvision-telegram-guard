#!/usr/bin/env python3
"""
================================================================================
CCTV GUARD & TELEGRAM ALERT SYSTEM - MULTI-CAMERA NVR HUB
================================================================================
Architecture: Centralized NVR ISAPI Stream Listener + Multi-Channel Dispatcher
Features:
- Monitors Central Hikvision NVR (192.168.99.10) alertStream for all channels
- Smart Independent Cooldown per Channel (5-180s dynamic)
- High-Resolution Snapshot Capture (1080p Main-Stream) per Camera Target
- Live Video MJPEG Streamer (~8 FPS) with Multi-Channel Switcher in Web UI
- Gallery Storage Management with Date & Channel Filtering + Auto Retention
- Interactive Web Dashboard (Retro Light / Soft Neo-Brutalism Design)
- Per-Channel Alert Toggle (Enable/Disable notification per camera)
- Pure Python 3 standard library + requests
================================================================================
"""

import os
import sys
import time
import json
import io
import re
import glob
import shutil
import logging
import datetime
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import requests
from requests.auth import HTTPDigestAuth

# ------------------------------------------------------------------------------
# DIRECTORY & DEFAULT MULTI-CHANNEL CONFIGURATION
# ------------------------------------------------------------------------------
BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_DIR = BASE_DIR / "snapshots"
CONFIG_FILE = BASE_DIR / "config.json"

SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "nvr_ip": "192.168.1.10",
    "nvr_user": "admin",
    "nvr_pass": "Password123#",
    "telegram_token": "123456789:AAFxSampleTokenFromBotFather",
    "telegram_chat_id": "-100123456789",
    "telegram_enabled": True,
    "cooldown_seconds": 15,
    "web_port": 8088,
    "web_pin": "060708",
    "retention_days": 3,
    "max_gallery_items": 150,
    "channels": {
        "1": {
            "name": "CAM 1 - ENTRANCE",
            "location": "MAIN GATE / LOBBY",
            "ip": "192.168.1.91",
            "enabled": True,
            "main_stream": "101",
            "sub_stream": "102"
        },
        "2": {
            "name": "CAM 2 - PARKING LOT",
            "location": "PARKING AREA",
            "ip": "192.168.1.92",
            "enabled": True,
            "main_stream": "101",
            "sub_stream": "102"
        },
        "3": {
            "name": "CAM 3 - SERVER ROOM",
            "location": "IT SERVER RACK",
            "ip": "192.168.1.93",
            "enabled": True,
            "main_stream": "101",
            "sub_stream": "102"
        }
    }
}

# ------------------------------------------------------------------------------
# IN-MEMORY LOG BUFFER & STATS TRACKER
# ------------------------------------------------------------------------------
MAX_LOG_ENTRIES = 250
log_buffer = []
log_lock = threading.Lock()

class BufferLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            level = record.levelname
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            tag = "INFO"
            if "TRIGGER" in msg:
                tag = "TRIGGER"
            elif "DELIVERED" in msg or ("Telegram" in msg and "sukses" in msg.lower()):
                tag = "DELIVERED"
            elif "COOLDOWN" in msg:
                tag = "COOLDOWN"
            elif level in ["ERROR", "CRITICAL"]:
                tag = "ERROR"
            elif level == "WARNING":
                tag = "WARN"

            with log_lock:
                log_buffer.append({
                    "time": now_str,
                    "level": level,
                    "tag": tag,
                    "message": msg
                })
                if len(log_buffer) > MAX_LOG_ENTRIES:
                    log_buffer.pop(0)
        except Exception:
            pass

# Root logger
logger = logging.getLogger("cctv_guard")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

stream_hdlr = logging.StreamHandler(sys.stdout)
stream_hdlr.setFormatter(formatter)
logger.addHandler(stream_hdlr)

buf_hdlr = BufferLogHandler()
buf_hdlr.setFormatter(formatter)
logger.addHandler(buf_hdlr)

# Statistics tracker
stats = {
    "triggers_today": 0,
    "delivered_today": 0,
    "cooldown_suppressed": 0,
    "last_trigger_time": None,
    "last_delivered_time": None,
    "current_date": datetime.date.today().isoformat(),
    "stream_connected": False,
    "last_stream_error": None,
    "channel_stats": {}
}
stats_lock = threading.Lock()

def check_date_rollover():
    today = datetime.date.today().isoformat()
    with stats_lock:
        if stats["current_date"] != today:
            stats["triggers_today"] = 0
            stats["delivered_today"] = 0
            stats["cooldown_suppressed"] = 0
            stats["channel_stats"] = {}
            stats["current_date"] = today

# ------------------------------------------------------------------------------
# CONFIG MANAGER
# ------------------------------------------------------------------------------
config_lock = threading.Lock()
current_config = {}

def load_config():
    global current_config
    with config_lock:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    # Migrate single-cam config to multi-channel if needed
                    if "channels" not in saved:
                        saved["channels"] = DEFAULT_CONFIG["channels"]
                    if "nvr_ip" not in saved and "camera_ip" in saved:
                        saved["nvr_ip"] = "192.168.99.10"
                        saved["nvr_user"] = saved.get("camera_user", "admin")
                        saved["nvr_pass"] = saved.get("camera_pass", "Bestari008")
                    current_config = {**DEFAULT_CONFIG, **saved}
            except Exception as e:
                logger.error(f"Gagal memuat config.json: {e}")
                current_config = dict(DEFAULT_CONFIG)
        else:
            current_config = dict(DEFAULT_CONFIG)
            save_config_file()

def save_config_file():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(current_config, f, indent=2, ensure_ascii=False)

def get_config(key=None):
    with config_lock:
        if key:
            return current_config.get(key)
        return dict(current_config)

def update_config(new_values):
    global current_config
    with config_lock:
        for k, v in new_values.items():
            if k == "channels" and isinstance(v, dict):
                current_config["channels"] = v
            elif k in DEFAULT_CONFIG:
                if isinstance(DEFAULT_CONFIG[k], int):
                    try:
                        current_config[k] = int(v)
                    except ValueError:
                        pass
                elif isinstance(DEFAULT_CONFIG[k], bool):
                    current_config[k] = (str(v).lower() == 'true')
                else:
                    current_config[k] = str(v)
        save_config_file()
    logger.info("Konfigurasi Multi-Kamera berhasil disimpan & diperbarui")

def sync_cameras_from_nvr():
    cfg = get_config()
    nvr_ip = cfg.get("nvr_ip", "192.168.99.10")
    nvr_user = cfg.get("nvr_user", "admin")
    nvr_pass = cfg.get("nvr_pass", "Bestari008")
    
    url = f"http://{nvr_ip}/ISAPI/ContentMgmt/InputProxy/channels"
    auth = HTTPDigestAuth(nvr_user, nvr_pass)
    try:
        r = requests.get(url, auth=auth, timeout=5)
        if r.status_code == 200:
            root = ET.fromstring(r.text)
            new_channels = dict(cfg.get("channels", {}))
            for ch_elem in root.iter():
                if ch_elem.tag.endswith("InputProxyChannel"):
                    ch_id = None
                    ch_name = None
                    ch_ip = None
                    for child in ch_elem.iter():
                        if child.tag.endswith("id") and not ch_id:
                            ch_id = child.text
                        elif child.tag.endswith("name") and not ch_name:
                            ch_name = child.text
                        elif child.tag.endswith("ipAddress") and not ch_ip:
                            ch_ip = child.text
                    
                    if ch_id:
                        ch_str = str(ch_id)
                        if ch_str in new_channels:
                            if ch_name: new_channels[ch_str]["name"] = ch_name
                            if ch_ip: new_channels[ch_str]["ip"] = ch_ip
                        else:
                            new_channels[ch_str] = {
                                "name": ch_name or f"CAM {ch_str}",
                                "location": f"Area NVR Ch {ch_str}",
                                "ip": ch_ip or "",
                                "enabled": True,
                                "main_stream": "101",
                                "sub_stream": "102"
                            }
            update_config({"channels": new_channels})
            logger.info(f"Auto-Sync NVR: Berhasil menyinkronkan {len(new_channels)} kamera dari NVR.")
            return len(new_channels)
    except Exception as e:
        logger.error(f"Gagal auto-sync kamera dari NVR: {e}")
    return 0

load_config()

# ------------------------------------------------------------------------------
# RETENTION & STORAGE CLEANUP
# ------------------------------------------------------------------------------
def run_gallery_cleanup():
    cfg = get_config()
    retention_days = cfg.get("retention_days", 3)
    max_items = cfg.get("max_gallery_items", 150)

    try:
        files = sorted(SNAPSHOT_DIR.glob("snap_*.jpg"), key=os.path.getmtime, reverse=True)
        deleted_count = 0
        now = time.time()

        if retention_days > 0:
            max_age_seconds = retention_days * 86400
            for f in list(files):
                if (now - os.path.getmtime(f)) > max_age_seconds:
                    try:
                        f.unlink()
                        files.remove(f)
                        deleted_count += 1
                    except Exception:
                        pass

        if max_items > 0 and len(files) > max_items:
            for f in files[max_items:]:
                try:
                    f.unlink()
                    deleted_count += 1
                except Exception:
                    pass

        if deleted_count > 0:
            logger.info(f"Auto-Retention: Dihapus {deleted_count} file snapshot kadaluarsa/melebihi kuota.")
    except Exception as e:
        logger.error(f"Error pada cleanup galeri: {e}")

def retention_worker():
    while True:
        try:
            run_gallery_cleanup()
        except Exception:
            pass
        time.sleep(1800)

# ------------------------------------------------------------------------------
# SNAPSHOT CAPTURE & TELEGRAM DISPATCHER (MULTI-CHANNEL)
# ------------------------------------------------------------------------------
channel_last_alert = {}
cooldown_lock = threading.Lock()

def get_channel_snapshot(channel_id):
    cfg = get_config()
    ch_str = str(channel_id)
    ch_info = cfg.get("channels", {}).get(ch_str)
    
    # Try Direct Camera IP first if configured
    if ch_info and ch_info.get("ip"):
        cam_ip = ch_info["ip"]
        cam_user = cfg.get("nvr_user", "admin")
        cam_pass = cfg.get("nvr_pass", "Bestari008")
        url = f"http://{cam_ip}/ISAPI/Streaming/channels/{ch_info.get('main_stream', '101')}/picture"
        try:
            r = requests.get(url, auth=HTTPDigestAuth(cam_user, cam_pass), timeout=4)
            if r.status_code == 200 and len(r.content) > 1000:
                return r.content
        except Exception:
            pass

    # Fallback to NVR channel streaming endpoint
    nvr_ip = cfg.get("nvr_ip", "192.168.99.10")
    nvr_user = cfg.get("nvr_user", "admin")
    nvr_pass = cfg.get("nvr_pass", "Bestari008")
    nvr_url = f"http://{nvr_ip}/ISAPI/Streaming/channels/{ch_str}01/picture"
    try:
        r = requests.get(nvr_url, auth=HTTPDigestAuth(nvr_user, nvr_pass), timeout=5)
        if r.status_code == 200 and len(r.content) > 1000:
            return r.content
        logger.warning(f"Gagal mengambil snapshot untuk Ch {ch_str} (NVR HTTP {r.status_code})")
    except Exception as e:
        logger.error(f"Exception snapshot Ch {ch_str}: {e}")
    return None

def trigger_channel_alert(channel_id, event_type="VMD", details="Motion Detected"):
    check_date_rollover()
    cfg = get_config()
    ch_str = str(channel_id)
    ch_info = cfg.get("channels", {}).get(ch_str)

    if not ch_info:
        logger.info(f"[AUTO-DISCOVERY] Channel {ch_str} baru terdeteksi dari NVR! Mendaftarkan profil otomatis...")
        ch_info = {
            "name": f"CAM {ch_str}",
            "location": f"Area NVR Ch {ch_str}",
            "ip": "",
            "enabled": True,
            "main_stream": "101",
            "sub_stream": "102"
        }
        all_ch = cfg.get("channels", {})
        all_ch[ch_str] = ch_info
        update_config({"channels": all_ch})

    # Check if this camera channel is enabled
    if not ch_info.get("enabled", True):
        logger.info(f"Event pada Ch {ch_str} ({ch_info['name']}) diabaikan karena status channel DINONAKTIFKAN di pengaturan.")
        return

    now = time.time()
    cooldown = cfg.get("cooldown_seconds", 15)

    with stats_lock:
        stats["triggers_today"] += 1
        stats["last_trigger_time"] = datetime.datetime.now().strftime("%H:%M:%S")
        if ch_str not in stats["channel_stats"]:
            stats["channel_stats"][ch_str] = {"triggers": 0, "delivered": 0}
        stats["channel_stats"][ch_str]["triggers"] += 1

    with cooldown_lock:
        last_time = channel_last_alert.get(ch_str, 0)
        if (now - last_time) < cooldown:
            remaining = int(cooldown - (now - last_time))
            with stats_lock:
                stats["cooldown_suppressed"] += 1
            logger.info(f"[COOLDOWN] Ch {ch_str} ({ch_info['name']}) event {event_type} diabaikan ({remaining}s tersisa dari {cooldown}s)")
            return
        channel_last_alert[ch_str] = now

    logger.info(f"[TRIGGER] 🔥 Event {event_type} Valid pada Ch {ch_str} ({ch_info['name']})! Memproses capture & dispatch...")

    # Capture High-Res Snapshot
    snap_bytes = get_channel_snapshot(ch_str)
    
    saved_filename = None
    if snap_bytes:
        timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_filename = f"snap_ch{ch_str}_{timestamp_str}.jpg"
        filepath = SNAPSHOT_DIR / saved_filename
        try:
            with open(filepath, "wb") as f:
                f.write(snap_bytes)
            threading.Thread(target=run_gallery_cleanup, daemon=True).start()
        except Exception as e:
            logger.error(f"Gagal menyimpan file snapshot Ch {ch_str}: {e}")

    # Check global Telegram status
    if not cfg.get("telegram_enabled", True):
        logger.info("[INFO] Telegram notification dalam status Global Mute (Tidak dikirim)")
        return

    bot_token = cfg.get("telegram_token", "")
    chat_id = cfg.get("telegram_chat_id", "")
    if not bot_token or not chat_id:
        return

    wib_time = datetime.datetime.now().strftime("%d-%m-%Y %H:%M:%S WIB")
    caption = (
        f"🚨 <b>DETEKSI GERAKAN CCTV KOS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📹 <b>Kamera</b>: <b>{ch_info['name']}</b> (Ch {ch_str})\n"
        f"📍 <b>Lokasi</b>: <code>{ch_info['location']}</code>\n"
        f"⚡ <b>Event</b>: <code>{event_type.upper()} ({details})</code>\n"
        f"⏰ <b>Waktu</b>: <code>{wib_time}</code>\n"
        f"🛡️ <b>Anti-Spam Jeda</b>: {cooldown} Detik\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <i>CCTV Guard Multi-Cam Engine</i>"
    )

    sent_success = False
    if snap_bytes:
        try:
            files = {'photo': (saved_filename or 'snapshot.jpg', io.BytesIO(snap_bytes), 'image/jpeg')}
            data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}
            res = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendPhoto",
                data=data,
                files=files,
                timeout=12
            )
            if res.status_code == 200:
                sent_success = True
                logger.info(f"[DELIVERED] 🚀 Foto snapshot Ch {ch_str} ({ch_info['name']}) berhasil terkirim ke Telegram!")
            else:
                logger.error(f"Gagal kirim foto Telegram Ch {ch_str} (HTTP {res.status_code}): {res.text}")
        except Exception as e:
            logger.error(f"Exception kirim Telegram Ch {ch_str}: {e}")

    if not sent_success:
        try:
            data = {
                'chat_id': chat_id,
                'text': caption + "\n⚠️ <i>(Snapshot gambar gagal diambil dari kamera)</i>",
                'parse_mode': 'HTML'
            }
            requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", data=data, timeout=8)
            sent_success = True
            logger.info(f"[DELIVERED] ✉️ Teks fallback Ch {ch_str} terkirim ke Telegram")
        except Exception:
            pass

    if sent_success:
        with stats_lock:
            stats["delivered_today"] += 1
            stats["last_delivered_time"] = datetime.datetime.now().strftime("%H:%M:%S")
            if ch_str in stats["channel_stats"]:
                stats["channel_stats"][ch_str]["delivered"] += 1

# ------------------------------------------------------------------------------
# CENTRAL NVR ISAPI EVENT STREAM LISTENER (LONG-POLLING DAEMON)
# ------------------------------------------------------------------------------
def process_nvr_event_xml(xml_bytes):
    try:
        idx = xml_bytes.find(b"<EventNotificationAlert")
        if idx == -1:
            return
        clean_xml = xml_bytes[idx:]
        end_idx = clean_xml.find(b"</EventNotificationAlert>")
        if end_idx == -1:
            return
        clean_xml = clean_xml[:end_idx + len(b"</EventNotificationAlert>")]

        root = ET.fromstring(clean_xml.decode('utf-8', errors='ignore'))

        def get_tag(tag_name):
            for elem in root.iter():
                if elem.tag.endswith(tag_name):
                    return elem.text
            return None

        event_type = (get_tag("eventType") or "").lower()
        event_state = (get_tag("eventState") or "").lower()
        channel_id = get_tag("channelID") or get_tag("dynVideoInputChannelID") or get_tag("videoInputChannelID")
        event_desc = get_tag("eventDescription") or event_type

        if not channel_id:
            return

        valid_events = ["vmd", "motion", "linedetection", "fielddetection", "human", "regionentrance"]
        if any(v in event_type for v in valid_events):
            if event_state in ["active", "", None]:
                threading.Thread(
                    target=trigger_channel_alert,
                    args=(str(channel_id), event_type.upper(), event_desc),
                    daemon=True
                ).start()
    except Exception as e:
        logger.debug(f"XML parse error: {e}")

def nvr_listener_thread():
    while True:
        cfg = get_config()
        nvr_ip = cfg.get("nvr_ip", "192.168.99.10")
        nvr_user = cfg.get("nvr_user", "admin")
        nvr_pass = cfg.get("nvr_pass", "Bestari008")

        if not nvr_ip or "." not in nvr_ip:
            with stats_lock:
                stats["stream_connected"] = False
                stats["last_stream_error"] = "IP NVR belum disetel"
            time.sleep(5)
            continue

        stream_url = f"http://{nvr_ip}/ISAPI/Event/notification/alertStream"
        auth = HTTPDigestAuth(nvr_user, nvr_pass)

        logger.info(f"Menghubungkan ke Hikvision NVR Central alertStream: {nvr_ip}...")
        try:
            with requests.get(stream_url, auth=auth, stream=True, timeout=(10, 60)) as r:
                if r.status_code == 200:
                    with stats_lock:
                        stats["stream_connected"] = True
                        stats["last_stream_error"] = None
                    logger.info(f"🟢 Terhubung sukses ke NVR Central Event Stream ({nvr_ip})! Memonitor seluruh channel kamera...")

                    buffer = b""
                    for chunk in r.iter_content(chunk_size=1024):
                        if chunk:
                            buffer += chunk
                            while b"</EventNotificationAlert>" in buffer:
                                end_pos = buffer.find(b"</EventNotificationAlert>") + len(b"</EventNotificationAlert>")
                                event_xml = buffer[:end_pos]
                                buffer = buffer[end_pos:]
                                process_nvr_event_xml(event_xml)
                else:
                    with stats_lock:
                        stats["stream_connected"] = False
                        stats["last_stream_error"] = f"HTTP {r.status_code}"
                    logger.warning(f"NVR menolak alertStream (HTTP {r.status_code}). Coba lagi dalam 8 detik...")
                    time.sleep(8)
        except requests.exceptions.Timeout:
            logger.debug("Stream timeout idle (Normal), menyambung ulang stream NVR...")
            continue
        except requests.exceptions.RequestException as e:
            with stats_lock:
                stats["stream_connected"] = False
                stats["last_stream_error"] = str(e)
            logger.warning(f"Koneksi stream NVR terputus ({e}). Menyambung ulang dalam 5 detik...")
            time.sleep(5)
        except Exception as e:
            with stats_lock:
                stats["stream_connected"] = False
                stats["last_stream_error"] = str(e)
            logger.error(f"Error tak terduga pada stream listener: {e}")
            time.sleep(5)

# ------------------------------------------------------------------------------
# WEB INTERFACE (RETRO LIGHT / SOFT NEO-BRUTALISM HTML/CSS/JS)
# ------------------------------------------------------------------------------
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CCTV Guard & Telegram Alert Monitor - Multi-Camera Hub</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #F8FAFC;
      --card-bg: #FFFFFF;
      --text: #0F172A;
      --text-muted: #64748B;
      --border: #0F172A;
      --primary: #FDE047;
      --primary-hover: #FACC15;
      --accent-blue: #38BDF8;
      --accent-green: #4ADE80;
      --accent-red: #FB7185;
      --accent-purple: #C084FC;
      --radius: 12px;
      --shadow: 3px 3px 0px #0F172A;
      --shadow-sm: 2px 2px 0px #0F172A;
      --shadow-lg: 5px 5px 0px #0F172A;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: 'Plus Jakarta Sans', sans-serif;
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.5;
      display: flex;
      min-height: 100vh;
      overflow-x: hidden;
    }

    h1, h2, h3, h4 { font-weight: 800; color: var(--text); letter-spacing: -0.02em; }
    .mono { font-family: 'JetBrains Mono', monospace; }

    .neo-card {
      background: var(--card-bg);
      border: 2px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 20px;
      transition: all 0.15s ease;
    }
    .neo-card:hover {
      box-shadow: var(--shadow-lg);
      transform: translate(-1px, -1px);
    }

    .neo-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      font-family: 'Plus Jakarta Sans', sans-serif;
      font-weight: 700;
      font-size: 0.9rem;
      padding: 10px 18px;
      background: var(--primary);
      color: var(--text);
      border: 2px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow-sm);
      cursor: pointer;
      text-decoration: none;
      transition: all 0.1s ease;
    }
    .neo-btn:hover {
      background: var(--primary-hover);
      box-shadow: var(--shadow);
      transform: translate(-1px, -1px);
    }
    .neo-btn:active {
      box-shadow: none;
      transform: translate(2px, 2px);
    }

    .neo-btn-blue { background: var(--accent-blue); }
    .neo-btn-green { background: var(--accent-green); }
    .neo-btn-red { background: var(--accent-red); color: #FFF; }
    .neo-btn-purple { background: var(--accent-purple); }
    .neo-btn-outline { background: #FFF; }

    .neo-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.75rem;
      font-weight: 700;
      padding: 4px 10px;
      border: 1.5px solid var(--border);
      border-radius: 6px;
      box-shadow: 1.5px 1.5px 0px var(--border);
      text-transform: uppercase;
    }
    .badge-live { background: #DCFCE7; color: #166534; }
    .badge-dead { background: #FFE4E6; color: #9F1239; }
    .badge-yellow { background: #FEF08A; color: #854D0E; }
    .badge-blue { background: #E0F2FE; color: #0369A1; }
    .badge-purple { background: #F3E8FF; color: #6B21A8; }

    .sidebar {
      width: 280px;
      background: #FFFFFF;
      border-right: 2.5px solid var(--border);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
      position: sticky;
      top: 0;
      height: 100vh;
      z-index: 50;
      padding: 24px 16px;
    }

    .brand-box {
      display: flex;
      align-items: center;
      gap: 12px;
      padding-bottom: 20px;
      border-bottom: 2px dashed #CBD5E1;
      margin-bottom: 20px;
    }

    .brand-icon {
      width: 44px;
      height: 44px;
      background: var(--primary);
      border: 2px solid var(--border);
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1.4rem;
      box-shadow: var(--shadow-sm);
    }

    .brand-title { font-size: 1.1rem; font-weight: 800; line-height: 1.2; }
    .brand-sub { font-size: 0.75rem; font-family: 'JetBrains Mono', monospace; color: var(--text-muted); }

    .cam-status-box {
      background: #F1F5F9;
      border: 2px solid var(--border);
      border-radius: 10px;
      padding: 12px;
      margin-bottom: 20px;
      box-shadow: var(--shadow-sm);
    }

    .nav-list { display: flex; flex-direction: column; gap: 8px; list-style: none; flex: 1; }

    .nav-item {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 12px 16px;
      font-weight: 700;
      font-size: 0.9rem;
      color: var(--text);
      border: 2px solid transparent;
      border-radius: 10px;
      cursor: pointer;
      transition: all 0.1s ease;
    }
    .nav-item:hover {
      background: #F8FAFC;
      border-color: var(--border);
      box-shadow: var(--shadow-sm);
      transform: translate(-1px, -1px);
    }
    .nav-item.active {
      background: var(--primary);
      border-color: var(--border);
      box-shadow: var(--shadow-sm);
    }

    .sidebar-footer {
      padding-top: 16px;
      border-top: 2px dashed #CBD5E1;
      font-size: 0.75rem;
      color: var(--text-muted);
      text-align: center;
    }

    .main-container {
      flex: 1;
      padding: 32px 40px;
      overflow-y: auto;
      max-width: 1400px;
    }

    .tab-content { display: none; }
    .tab-content.active { display: block; animation: fadeIn 0.2s ease; }

    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .bento-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }

    .stat-card {
      background: #FFF;
      border: 2px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 18px;
    }

    .stat-label {
      font-size: 0.8rem;
      font-weight: 700;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 6px;
    }

    .stat-value { font-family: 'JetBrains Mono', monospace; font-size: 2.2rem; font-weight: 800; line-height: 1; }
    .stat-sub { font-size: 0.75rem; margin-top: 6px; color: var(--text-muted); }

    /* MULTI-CAM SELECTOR BAR */
    .cam-selector-bar {
      display: flex;
      gap: 10px;
      margin-bottom: 16px;
      flex-wrap: wrap;
    }

    .cam-tab-btn {
      padding: 10px 16px;
      font-family: 'Plus Jakarta Sans', sans-serif;
      font-weight: 700;
      font-size: 0.85rem;
      background: #FFF;
      border: 2px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow-sm);
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      transition: all 0.1s ease;
    }

    .cam-tab-btn:hover {
      background: #FEF9C3;
      box-shadow: var(--shadow);
      transform: translate(-1px, -1px);
    }

    .cam-tab-btn.active {
      background: var(--primary);
      box-shadow: var(--shadow);
      border-color: #000;
    }

    .monitor-card {
      background: #FFF;
      border: 2px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      overflow: hidden;
      margin-bottom: 24px;
    }

    .monitor-header {
      padding: 14px 20px;
      background: #F1F5F9;
      border-bottom: 2px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
    }

    .video-viewport {
      background: #0F172A;
      width: 100%;
      height: 520px;
      display: flex;
      align-items: center;
      justify-content: center;
      position: relative;
      overflow: hidden;
    }

    .video-viewport img {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
    }

    .stream-overlay-badge { position: absolute; top: 14px; left: 14px; z-index: 10; }

    .pulse-dot {
      display: inline-block;
      width: 8px;
      height: 8px;
      background: #EF4444;
      border-radius: 50%;
      animation: pulse 1s infinite;
    }

    @keyframes pulse {
      0% { transform: scale(0.95); opacity: 0.8; }
      50% { transform: scale(1.3); opacity: 1; }
      100% { transform: scale(0.95); opacity: 0.8; }
    }

    .terminal-box {
      background: #0F172A;
      color: #F8FAFC;
      border: 2px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.82rem;
      padding: 18px;
      height: 600px;
      overflow-y: auto;
      line-height: 1.6;
    }

    .log-line { margin-bottom: 4px; white-space: pre-wrap; word-break: break-all; }
    .log-time { color: #94A3B8; }
    .log-tag-trigger { color: #F87171; font-weight: 700; }
    .log-tag-delivered { color: #4ADE80; font-weight: 700; }
    .log-tag-cooldown { color: #FBBF24; }
    .log-tag-error { color: #F43F5E; background: #881337; padding: 1px 4px; border-radius: 4px; }

    .gallery-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 16px;
      margin-top: 16px;
    }

    .gallery-card {
      background: #FFF;
      border: 2px solid var(--border);
      border-radius: 10px;
      box-shadow: var(--shadow-sm);
      overflow: hidden;
      transition: all 0.15s ease;
      cursor: pointer;
    }
    .gallery-card:hover {
      box-shadow: var(--shadow);
      transform: translate(-2px, -2px);
    }

    .gallery-thumb { width: 100%; height: 160px; background: #1E293B; object-fit: cover; display: block; }

    .gallery-info {
      padding: 10px 14px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: #F8FAFC;
      border-top: 1.5px solid var(--border);
    }

    .gallery-date { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; font-weight: 600; }

    .form-group { margin-bottom: 18px; }
    .form-label { display: block; font-weight: 700; font-size: 0.85rem; margin-bottom: 6px; }

    .form-input, .form-select {
      width: 100%;
      padding: 10px 14px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.9rem;
      border: 2px solid var(--border);
      border-radius: 8px;
      background: #FFF;
      box-shadow: var(--shadow-sm);
      outline: none;
    }
    .form-input:focus, .form-select:focus {
      background: #FEF9C3;
      border-color: #000;
    }

    .modal-backdrop {
      position: fixed;
      top: 0; left: 0; width: 100vw; height: 100vh;
      background: rgba(15, 23, 42, 0.75);
      backdrop-filter: blur(4px);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
    }

    .pin-card {
      background: #FFF;
      border: 3px solid var(--border);
      border-radius: 16px;
      box-shadow: var(--shadow-lg);
      width: 340px;
      padding: 28px 24px;
      text-align: center;
    }

    .numpad-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
      margin-top: 18px;
    }

    .numpad-btn {
      padding: 14px 0;
      font-family: 'JetBrains Mono', monospace;
      font-size: 1.3rem;
      font-weight: 800;
      background: #F1F5F9;
      border: 2px solid var(--border);
      border-radius: 10px;
      box-shadow: var(--shadow-sm);
      cursor: pointer;
    }
    .numpad-btn:hover {
      background: var(--primary);
      transform: translate(-1px, -1px);
      box-shadow: var(--shadow);
    }
    .numpad-btn:active {
      transform: translate(1px, 1px);
      box-shadow: none;
    }

    .toast {
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: #FFF;
      border: 2px solid var(--border);
      border-radius: 10px;
      box-shadow: var(--shadow-lg);
      padding: 14px 20px;
      font-weight: 700;
      font-size: 0.9rem;
      display: flex;
      align-items: center;
      gap: 10px;
      z-index: 2000;
      transform: translateY(100px);
      opacity: 0;
      transition: all 0.2s cubic-bezier(0.175, 0.885, 0.32, 1.275);
    }
    .toast.show { transform: translateY(0); opacity: 1; }

    /* CHANNEL CARD IN SETTINGS */
    .channel-box {
      border: 2px solid var(--border);
      border-radius: 10px;
      padding: 14px 16px;
      background: #FFFFFF;
      box-shadow: 2px 2px 0px var(--border);
      transition: all 0.2s ease;
      margin-bottom: 12px;
    }
    .channel-box.ch-active {
      border-left: 6px solid #22C55E;
      background: #FAFCF8;
    }
    .channel-box.ch-muted {
      border-left: 6px solid #94A3B8;
      background: #F8FAFC;
      opacity: 0.85;
    }

    .settings-grid {
      display: grid;
      grid-template-columns: minmax(320px, 1fr) minmax(380px, 1.4fr);
      gap: 24px;
      align-items: start;
    }

    body.app-locked .main-container,
    body.app-locked .sidebar {
      filter: blur(10px);
      pointer-events: none;
      user-select: none;
      transition: filter 0.3s ease;
    }

    @media (max-width: 1024px) {
      .settings-grid {
        grid-template-columns: 1fr;
      }
    }

    @media (max-width: 900px) {
      body { flex-direction: column; }
      .sidebar { width: 100%; height: auto; position: static; border-right: none; border-bottom: 2.5px solid var(--border); }
      .main-container { padding: 20px 16px; }
      .video-viewport { height: 320px; }
    }
  </style>
</head>
<body class="app-locked">

  <!-- PIN LOCK MODAL -->
  <div id="pinModal" class="modal-backdrop" style="display: flex;">
    <div class="pin-card">
      <div style="font-size: 2.2rem; margin-bottom: 8px;">🔐</div>
      <h2>Akses CCTV Guard</h2>
      <p style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 16px;">Masukkan 6-Digit PIN Keamanan</p>
      
      <input type="password" id="pinInput" class="form-input" style="text-align: center; font-size: 1.6rem; letter-spacing: 0.3em;" maxlength="6" readonly>

      <div class="numpad-grid">
        <button class="numpad-btn" onclick="addPin('1')">1</button>
        <button class="numpad-btn" onclick="addPin('2')">2</button>
        <button class="numpad-btn" onclick="addPin('3')">3</button>
        <button class="numpad-btn" onclick="addPin('4')">4</button>
        <button class="numpad-btn" onclick="addPin('5')">5</button>
        <button class="numpad-btn" onclick="addPin('6')">6</button>
        <button class="numpad-btn" onclick="addPin('7')">7</button>
        <button class="numpad-btn" onclick="addPin('8')">8</button>
        <button class="numpad-btn" onclick="addPin('9')">9</button>
        <button class="numpad-btn" style="background: #FFE4E6;" onclick="clearPin()">C</button>
        <button class="numpad-btn" onclick="addPin('0')">0</button>
        <button class="numpad-btn" style="background: var(--accent-green);" onclick="submitPin()">↵</button>
      </div>
    </div>
  </div>

  <!-- SIDEBAR -->
  <aside class="sidebar">
    <div class="brand-box">
      <div class="brand-icon">🛡️</div>
      <div>
        <div class="brand-title">CCTV GUARD</div>
        <div class="brand-sub">Multi-Camera Hub Engine</div>
      </div>
    </div>

    <div class="cam-status-box">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
        <span id="badgeStream" class="neo-badge badge-dead">● OFFLINE</span>
        <span id="sideCooldown" class="neo-badge badge-yellow">15s Jeda</span>
      </div>
      <div style="font-size: 0.85rem; font-weight: 800;" id="sideNvrIp">NVR: 192.168.99.10</div>
      <div style="font-size: 0.75rem; color: var(--text-muted);" id="sideChannelsSummary">Memuat Kamera...</div>
    </div>

    <ul class="nav-list">
      <li class="nav-item active" onclick="switchTab('tab-dashboard')">
        <span>📊</span> <span>Dashboard</span>
      </li>
      <li class="nav-item" onclick="switchTab('tab-logs')">
        <span>📜</span> <span>Live Logs</span>
      </li>
      <li class="nav-item" onclick="switchTab('tab-gallery')">
        <span>🖼️</span> <span>Galeri Event</span>
      </li>
      <li class="nav-item" onclick="switchTab('tab-settings')">
        <span>⚙️</span> <span>Pengaturan</span>
      </li>
      <li class="nav-item" onclick="switchTab('tab-guide')">
        <span>📘</span> <span>Panduan & Diagram</span>
      </li>
      <li class="nav-item" style="background: #FFE4E6; border-color: #F43F5E; color: #BE123C; margin-top: 14px;" onclick="lockPanel()">
        <span>🔒</span> <span>Kunci Panel</span>
      </li>
    </ul>

    <div class="sidebar-footer">
      <div>Hosterbyte Surveillance Hub</div>
      <div class="mono" style="margin-top: 4px; font-weight: 800; color: #0F172A;">v1.1.0 Multi-Cam</div>
    </div>
  </aside>

  <!-- MAIN VIEW CONTAINER -->
  <main class="main-container">
    
    <!-- TAB 1: DASHBOARD -->
    <section id="tab-dashboard" class="tab-content active">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 12px;">
        <div>
          <h2>Monitor Realtime Multi-Kamera</h2>
          <p style="color: var(--text-muted); font-size: 0.85rem;">Pemantauan live stream dan deteksi pergerakan lintas channel</p>
        </div>
        <div style="display: flex; gap: 10px;">
          <button class="neo-btn neo-btn-purple" onclick="testTelegramAlert()">
            <span>🚀</span> <span>Test Kirim Telegram</span>
          </button>
        </div>
      </div>

      <!-- BENTO GRID -->
      <div class="bento-grid">
        <div class="stat-card">
          <div class="stat-label">Total Trigger Hari Ini</div>
          <div class="stat-value" id="statTriggers">0</div>
          <div class="stat-sub">Deteksi seluruh channel</div>
        </div>
        <div class="stat-card" style="background: #F0FDF4;">
          <div class="stat-label">Terkirim ke Telegram</div>
          <div class="stat-value" style="color: #15803D;" id="statDelivered">0</div>
          <div class="stat-sub">Snapshot & Notif Lolos</div>
        </div>
        <div class="stat-card" style="background: #FFFBEB;">
          <div class="stat-label">Suppressed by Cooldown</div>
          <div class="stat-value" style="color: #B45309;" id="statSuppressed">0</div>
          <div class="stat-sub">Spam tersaring otomatis</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Notif Telegram Status</div>
          <div class="stat-value" style="font-size: 1.5rem;" id="statTgStatus">AKTIF</div>
          <div class="stat-sub" id="statLastDelivery">Terakhir: -</div>
        </div>
      </div>

      <!-- MULTI-CAM SELECTOR BAR -->
      <div class="cam-selector-bar" id="camSelectorContainer">
        <!-- Dynamic Camera Tabs injected here -->
      </div>

      <!-- MONITOR CARD -->
      <div class="monitor-card">
        <div class="monitor-header">
          <div style="display: flex; align-items: center; gap: 10px;">
            <span class="neo-badge badge-live" id="streamBadgeHeader">🔴 LIVE STREAM (~8 FPS)</span>
            <span class="mono" style="font-size: 0.85rem; font-weight: 800;" id="streamActiveCamLabel">CAM 2 - JALAN UTARA</span>
          </div>
          <div style="display: flex; gap: 8px;">
            <button class="neo-btn neo-btn-outline" style="padding: 6px 12px; font-size: 0.8rem;" onclick="setStreamMode('live')">🔴 Live Stream</button>
            <button class="neo-btn neo-btn-outline" style="padding: 6px 12px; font-size: 0.8rem;" onclick="setStreamMode('snapshot')">📷 Snapshot</button>
            <button class="neo-btn neo-btn-outline" style="padding: 6px 12px; font-size: 0.8rem;" onclick="refreshStream()">🔄 Refresh</button>
          </div>
        </div>

        <div class="video-viewport">
          <div class="stream-overlay-badge">
            <span class="neo-badge" style="background: rgba(0,0,0,0.7); color: #FFF; border-color: #FFF;">
              <span class="pulse-dot"></span> <span id="viewModeLabel">MJPEG STREAM</span>
            </span>
          </div>
          <img id="cameraStreamImg" src="/api/live-stream?channel=2" alt="Live Camera Stream">
        </div>
      </div>
    </section>

    <!-- TAB 2: LIVE LOGS -->
    <section id="tab-logs" class="tab-content">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <div>
          <h2>Live Terminal Logs</h2>
          <p style="color: var(--text-muted); font-size: 0.85rem;">Aktivitas ISAPI event stream & riwayat pengiriman Telegram</p>
        </div>
        <div style="display: flex; gap: 10px;">
          <button class="neo-btn neo-btn-outline" onclick="fetchLogs()">🔄 Refresh Logs</button>
        </div>
      </div>
      <div class="terminal-box" id="terminalLogBox"></div>
    </section>

    <!-- TAB 3: GALLERY -->
    <section id="tab-gallery" class="tab-content">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 12px;">
        <div>
          <h2>Galeri Event Snapshot</h2>
          <p style="color: var(--text-muted); font-size: 0.85rem;" id="gallerySummaryText">Memuat riwayat snapshot...</p>
        </div>
        <div style="display: flex; gap: 8px; flex-wrap: wrap;">
          <select id="galleryCamFilter" class="form-select" style="width: auto; padding: 6px 12px;" onchange="loadGallery(1)">
            <option value="">Semua Kamera</option>
          </select>
          <select id="galleryDateFilter" class="form-select" style="width: auto; padding: 6px 12px;" onchange="loadGallery(1)">
            <option value="">Semua Tanggal</option>
          </select>
          <button class="neo-btn neo-btn-blue" style="padding: 8px 14px;" onclick="runCleanup()">🧹 Bersihkan Kadaluarsa</button>
          <button class="neo-btn neo-btn-red" style="padding: 8px 14px;" onclick="clearAllGallery()">🗑️ Kosongkan Galeri</button>
        </div>
      </div>

      <div class="gallery-grid" id="galleryContainer"></div>
      <div style="display: flex; justify-content: center; gap: 10px; margin-top: 24px;" id="paginationBox"></div>
    </section>

    <!-- TAB 4: SETTINGS -->
    <section id="tab-settings" class="tab-content">
      <div style="margin-bottom: 20px;">
        <h2>Pengaturan Multi-Kamera & NVR</h2>
        <p style="color: var(--text-muted); font-size: 0.85rem;">Konfigurasi NVR Hikvision, pemetaan channel kamera, webhook Telegram, dan retensi disk</p>
      </div>

      <form id="settingsForm" onsubmit="saveSettings(event)">
        <div class="settings-grid">
          
          <!-- LEFT COLUMN: SYSTEM & TELEGRAM CONFIG -->
          <div style="display: flex; flex-direction: column; gap: 20px;">
            
            <!-- CARD 1: NVR -->
            <div class="neo-card">
              <h3 style="margin-bottom: 14px;">📹 Parameter NVR Hikvision</h3>
              <div class="form-group">
                <label class="form-label">IP Address NVR</label>
                <input type="text" name="nvr_ip" class="form-input" placeholder="192.168.99.10" required>
              </div>
              <div class="form-group">
                <label class="form-label">Username NVR</label>
                <input type="text" name="nvr_user" class="form-input" placeholder="admin" required>
              </div>
              <div class="form-group">
                <label class="form-label">Password NVR</label>
                <input type="password" name="nvr_pass" class="form-input" required>
              </div>
            </div>

            <!-- CARD 2: TELEGRAM & SECURITY -->
            <div class="neo-card">
              <h3 style="margin-bottom: 14px;">🤖 Telegram Bot & Anti-Spam</h3>
              <div class="form-group">
                <label class="form-label">Bot Token Telegram</label>
                <input type="text" name="telegram_token" class="form-input" placeholder="123456789:AA..." required>
              </div>
              <div class="form-group">
                <label class="form-label">Target Chat / Group ID</label>
                <input type="text" name="telegram_chat_id" class="form-input" placeholder="-100..." required>
              </div>
              <div class="form-group">
                <label class="form-label">Status Notifikasi Global</label>
                <select name="telegram_enabled" class="form-select">
                  <option value="true">🔔 Aktif (Kirim Foto & Pesan)</option>
                  <option value="false">🔕 Senyap / Muted (Simpan Lokal Saja)</option>
                </select>
              </div>
              <div class="form-group">
                <label class="form-label">Jeda Cooldown Anti-Spam: <span id="cooldownValBadge" class="neo-badge badge-yellow">15 Detik</span></label>
                <input type="range" name="cooldown_seconds" min="5" max="180" value="15" style="width: 100%;" oninput="document.getElementById('cooldownValBadge').innerText = this.value + ' Detik'">
              </div>
              <div class="form-group">
                <label class="form-label">PIN Akses Web Panel (6 Digit)</label>
                <input type="text" name="web_pin" class="form-input" maxlength="6" required>
              </div>
            </div>

            <!-- CARD 3: RETENTION -->
            <div class="neo-card">
              <h3 style="margin-bottom: 14px;">🗄️ Manajemen Retensi Disk</h3>
              <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 14px;">
                <div class="form-group">
                  <label class="form-label">Batas Masa Simpan</label>
                  <select name="retention_days" class="form-select">
                    <option value="1">1 Hari</option>
                    <option value="3">3 Hari (Rekomendasi)</option>
                    <option value="7">7 Hari (1 Minggu)</option>
                    <option value="14">14 Hari (2 Minggu)</option>
                    <option value="30">30 Hari (1 Bulan)</option>
                    <option value="0">Unlimited</option>
                  </select>
                </div>
                <div class="form-group">
                  <label class="form-label">Maks. File Simpan</label>
                  <select name="max_gallery_items" class="form-select">
                    <option value="50">50 Foto</option>
                    <option value="100">100 Foto</option>
                    <option value="150">150 Foto (Rekomendasi)</option>
                    <option value="300">300 Foto</option>
                    <option value="500">500 Foto</option>
                    <option value="0">Unlimited</option>
                  </select>
                </div>
              </div>
            </div>

            <!-- BUTTON SIMPAN KIRI -->
            <div>
              <button type="submit" class="neo-btn neo-btn-green" style="width: 100%; padding: 14px; font-size: 1rem;">
                <span>💾</span> <span>Simpan Seluruh Pengaturan</span>
              </button>
            </div>

          </div>

          <!-- RIGHT COLUMN: MULTI-CAMERA CHANNELS -->
          <div class="neo-card" style="display: flex; flex-direction: column; gap: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; padding-bottom: 14px; border-bottom: 2px solid var(--border);">
              <div>
                <h3 style="margin: 0;">🎛️ Pemetaan Channel Kamera</h3>
                <p style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;">Daftar kamera terdaftar di NVR & pengaturan alert per channel</p>
              </div>
              <div style="display: flex; gap: 8px;">
                <button type="button" class="neo-btn neo-btn-purple" style="padding: 6px 12px; font-size: 0.8rem;" onclick="syncCamerasFromNvr()">🔄 Auto-Sync dari NVR</button>
                <button type="button" class="neo-btn neo-btn-blue" style="padding: 6px 12px; font-size: 0.8rem;" onclick="addNewChannelPrompt()">➕ Tambah Manual</button>
              </div>
            </div>

            <div id="channelConfigContainer" style="display: flex; flex-direction: column; gap: 12px;">
              <!-- Channels injected dynamically -->
            </div>

            <div style="padding-top: 14px; border-top: 2px dashed var(--border);">
              <button type="submit" class="neo-btn neo-btn-green" style="width: 100%; padding: 12px; font-size: 0.95rem;">
                <span>💾</span> <span>Simpan Seluruh Pengaturan & Channel</span>
              </button>
            </div>
          </div>

        </div>
      </form>
    </section>

    <!-- TAB 5: GUIDE -->
    <section id="tab-guide" class="tab-content">
      <div style="margin-bottom: 20px;">
        <h2>Panduan Teknis & Diagram Arsitektur Multi-Kamera</h2>
        <p style="color: var(--text-muted); font-size: 0.85rem;">Arsitektur pemantauan terpusat via NVR Hikvision</p>
      </div>

      <div class="neo-card" style="margin-bottom: 20px;">
        <h3 style="margin-bottom: 12px;">📊 Diagram Alir NVR Central Hub</h3>
        <div style="background: #F8FAFC; border: 2px dashed #0F172A; border-radius: 8px; padding: 20px; text-align: center;">
          <div class="mono" style="display: inline-block; text-align: left; font-size: 0.85rem; line-height: 1.8;">
            [ NVR Hikvision (192.168.99.10) ] ──(ISAPI alertStream)──> [ CCTV Guard Microservice ]<br>
            &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;│<br>
            &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;▼ (Detect Channel & Event)<br>
            &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[ Per-Channel Cooldown & Enable Check ]<br>
            &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;│ (Pass Cooldown)<br>
            &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;▼<br>
            &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[ Pull 1080p Frame Target Camera IP ]<br>
            &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;├──> [ Simpan Galeri Lokal ]<br>
            &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;└──> [ Dispatch ke Telegram Bot API ]
          </div>
        </div>
      </div>
    </section>

  </main>

  <!-- MODAL PREVIEW SNAPSHOT HD -->
  <div id="previewModal" class="modal-backdrop" style="display: none;" onclick="closePreview()">
    <div class="neo-card" style="max-width: 900px; width: 95%; max-height: 90vh; display: flex; flex-direction: column; padding: 14px;" onclick="event.stopPropagation()">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
        <span class="mono" style="font-weight: 700; font-size: 0.9rem;" id="previewTitle">Snapshot Preview</span>
        <button class="neo-btn neo-btn-outline" style="padding: 4px 10px;" onclick="closePreview()">✕ Tutup</button>
      </div>
      <div style="flex: 1; background: #000; border: 2px solid var(--border); border-radius: 8px; overflow: hidden; display: flex; align-items: center; justify-content: center;">
        <img id="previewImg" src="" style="max-width: 100%; max-height: 70vh; object-fit: contain;">
      </div>
      <div style="display: flex; justify-content: flex-end; gap: 10px; margin-top: 12px;">
        <a id="btnDownloadPreview" href="#" download class="neo-btn neo-btn-blue" style="padding: 8px 14px;">⬇️ Unduh HD</a>
        <button id="btnDeletePreview" class="neo-btn neo-btn-red" style="padding: 8px 14px;" onclick="deleteCurrentPreview()">🗑️ Hapus Foto</button>
      </div>
    </div>
  </div>

  <!-- TOAST NOTIFICATION -->
  <div id="toastBox" class="toast">
    <span id="toastIcon">🔔</span>
    <span id="toastMsg">Pesan Notifikasi</span>
  </div>

  <script>
    let currentAuthPin = sessionStorage.getItem("cctv_pin") || "";
    let activeChannel = "2";
    let activeStreamMode = "live";
    let isTabVisible = true;
    let currentPreviewFile = "";
    let globalChannels = {};

    async function authFetch(url, options = {}) {
      if (!options.headers) options.headers = {};
      if (options.headers instanceof Headers) {
        options.headers.append("X-Web-PIN", currentAuthPin);
      } else {
        options.headers["X-Web-PIN"] = currentAuthPin;
      }
      try {
        const res = await fetch(url, options);
        if (res.status === 401) {
          lockPanel();
          showToast("🔒 Sesi terkunci. Masukkan PIN keamanan.", "warn");
          throw new Error("Unauthorized");
        }
        return res;
      } catch (e) {
        throw e;
      }
    }

    function lockPanel() {
      sessionStorage.removeItem("cctv_pin");
      currentAuthPin = "";
      clearPin();
      document.body.classList.add("app-locked");
      document.getElementById("pinModal").style.display = "flex";
      const streamImg = document.getElementById("cameraStreamImg");
      if (streamImg) streamImg.src = "";
    }

    if (currentAuthPin) {
      document.body.classList.remove("app-locked");
      document.getElementById("pinModal").style.display = "none";
      initApp();
    } else {
      document.body.classList.add("app-locked");
      document.getElementById("pinModal").style.display = "flex";
    }

    function addPin(num) {
      const inp = document.getElementById("pinInput");
      if (inp.value.length < 6) inp.value += num;
      if (inp.value.length === 6) submitPin();
    }

    function clearPin() {
      document.getElementById("pinInput").value = "";
    }

    async function submitPin() {
      const pin = document.getElementById("pinInput").value;
      if (pin.length !== 6) return showToast("⚠️ Masukkan 6 digit PIN", "warn");

      try {
        const res = await fetch("/api/auth/verify", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({pin: pin})
        });
        const data = await res.json();
        if (data.ok) {
          sessionStorage.setItem("cctv_pin", pin);
          currentAuthPin = pin;
          document.body.classList.remove("app-locked");
          document.getElementById("pinModal").style.display = "none";
          showToast("🔓 Akses Diterima! Panel terbuka.", "success");
          initApp();
        } else {
          clearPin();
          showToast("❌ PIN Salah! Silakan coba lagi.", "error");
        }
      } catch (e) {
        showToast("Gagal memverifikasi PIN", "error");
      }
    }

    function showToast(msg, type="info") {
      const t = document.getElementById("toastBox");
      const m = document.getElementById("toastMsg");
      const i = document.getElementById("toastIcon");
      m.innerText = msg;
      i.innerText = type === "success" ? "✅" : type === "error" ? "❌" : type === "warn" ? "⚠️" : "🔔";
      t.classList.add("show");
      setTimeout(() => t.classList.remove("show"), 3500);
    }

    function switchTab(tabId) {
      document.querySelectorAll(".tab-content").forEach(el => el.classList.remove("active"));
      document.querySelectorAll(".nav-item").forEach(el => el.classList.remove("active"));
      
      document.getElementById(tabId).classList.add("active");
      event.currentTarget.classList.add("active");

      const streamImg = document.getElementById("cameraStreamImg");
      if (tabId !== "tab-dashboard") {
        streamImg.src = "";
      } else {
        refreshStream();
      }

      if (tabId === "tab-logs") fetchLogs();
      if (tabId === "tab-gallery") loadGallery(1);
      if (tabId === "tab-settings") loadSettingsForm();
    }

    document.addEventListener("visibilitychange", () => {
      isTabVisible = !document.hidden;
      const streamImg = document.getElementById("cameraStreamImg");
      const activeTab = document.querySelector(".tab-content.active") ? document.querySelector(".tab-content.active").id : "";
      if (activeTab === "tab-dashboard") {
        if (isTabVisible && activeStreamMode === "live" && currentAuthPin) {
          streamImg.src = `/api/live-stream?channel=${activeChannel}&pin=${encodeURIComponent(currentAuthPin)}&t=` + Date.now();
        } else {
          streamImg.src = "";
        }
      }
    });

    function selectCamera(ch) {
      activeChannel = ch;
      document.querySelectorAll(".cam-tab-btn").forEach(b => b.classList.remove("active"));
      const btn = document.getElementById("camTabBtn_" + ch);
      if (btn) btn.classList.add("active");
      
      const chInfo = globalChannels[ch] || {name: `Kamera Ch ${ch}`};
      document.getElementById("streamActiveCamLabel").innerText = chInfo.name;
      refreshStream();
    }

    function setStreamMode(mode) {
      activeStreamMode = mode;
      const label = document.getElementById("viewModeLabel");
      const badge = document.getElementById("streamBadgeHeader");
      const streamImg = document.getElementById("cameraStreamImg");

      if (mode === "live") {
        label.innerText = "MJPEG STREAM";
        badge.innerText = "🔴 LIVE STREAM (~8 FPS)";
        badge.className = "neo-badge badge-live";
        streamImg.src = `/api/live-stream?channel=${activeChannel}&pin=${encodeURIComponent(currentAuthPin)}&t=` + Date.now();
      } else {
        label.innerText = "STATIC SNAPSHOT";
        badge.innerText = "📷 SNAPSHOT FRAME";
        badge.className = "neo-badge badge-yellow";
        streamImg.src = `/api/camera-snapshot?channel=${activeChannel}&pin=${encodeURIComponent(currentAuthPin)}&t=` + Date.now();
      }
    }

    function refreshStream() {
      if (!currentAuthPin) return;
      const streamImg = document.getElementById("cameraStreamImg");
      if (activeStreamMode === "live") {
        streamImg.src = `/api/live-stream?channel=${activeChannel}&pin=${encodeURIComponent(currentAuthPin)}&t=` + Date.now();
      } else {
        streamImg.src = `/api/camera-snapshot?channel=${activeChannel}&pin=${encodeURIComponent(currentAuthPin)}&t=` + Date.now();
      }
    }

    async function fetchStats() {
      if (!currentAuthPin) return;
      try {
        const res = await authFetch("/api/stats");
        const data = await res.json();
        
        document.getElementById("statTriggers").innerText = data.triggers_today;
        document.getElementById("statDelivered").innerText = data.delivered_today;
        document.getElementById("statSuppressed").innerText = data.cooldown_suppressed;
        document.getElementById("statLastDelivery").innerText = "Terakhir: " + (data.last_delivered_time || "-");
        
        const b = document.getElementById("badgeStream");
        if (data.stream_connected) {
          b.innerText = "● LIVE CONNECTED";
          b.className = "neo-badge badge-live";
        } else {
          b.innerText = "● RECONNECTING";
          b.className = "neo-badge badge-dead";
        }

        document.getElementById("sideNvrIp").innerText = "NVR: " + data.nvr_ip;
        document.getElementById("sideCooldown").innerText = data.cooldown_seconds + "s Jeda";
        document.getElementById("statTgStatus").innerText = data.telegram_enabled ? "🔔 AKTIF" : "🔕 MUTED";

        globalChannels = data.channels || {};
        renderCamSelector();
      } catch (e) {}
    }

    function renderCamSelector() {
      const container = document.getElementById("camSelectorContainer");
      const galleryCamSelect = document.getElementById("galleryCamFilter");
      
      let html = "";
      let galleryOptions = '<option value="">Semua Kamera</option>';

      for (let ch in globalChannels) {
        const c = globalChannels[ch];
        const isActive = ch === activeChannel ? "active" : "";
        const statusBadge = c.enabled ? "🟢" : "⏸️";
        html += `
          <button id="camTabBtn_${ch}" class="cam-tab-btn ${isActive}" onclick="selectCamera('${ch}')">
            <span>${statusBadge}</span>
            <span>${c.name}</span>
            <span class="mono" style="font-size: 0.7rem; color: var(--text-muted);">(Ch ${ch})</span>
          </button>
        `;
        galleryOptions += `<option value="${ch}">Ch ${ch}: ${c.name}</option>`;
      }
      container.innerHTML = html;
      
      const currentGalleryCam = galleryCamSelect.value;
      galleryCamSelect.innerHTML = galleryOptions;
      galleryCamSelect.value = currentGalleryCam;

      document.getElementById("sideChannelsSummary").innerText = `${Object.keys(globalChannels).length} Kamera Terdaftar`;
    }

    async function fetchLogs() {
      if (!currentAuthPin) return;
      try {
        const res = await authFetch("/api/logs");
        const logs = await res.json();
        const box = document.getElementById("terminalLogBox");
        box.innerHTML = logs.map(l => {
          let tagClass = "log-tag-cooldown";
          if (l.tag === "TRIGGER") tagClass = "log-tag-trigger";
          if (l.tag === "DELIVERED") tagClass = "log-tag-delivered";
          if (l.tag === "ERROR") tagClass = "log-tag-error";
          return `<div class="log-line"><span class="log-time">[${l.time}]</span> <span class="${tagClass}">[${l.tag}]</span> ${escapeHtml(l.message)}</div>`;
        }).join("");
        box.scrollTop = box.scrollHeight;
      } catch (e) {}
    }

    function escapeHtml(text) {
      return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    async function testTelegramAlert() {
      showToast(`Mengirim test alert Ch ${activeChannel} ke Telegram...`, "info");
      try {
        const res = await authFetch("/api/test-telegram", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({channel: activeChannel})
        });
        const data = await res.json();
        if (data.ok) {
          showToast(`🚀 Test snapshot Ch ${activeChannel} berhasil dikirim!`, "success");
          fetchStats();
        } else {
          showToast("Gagal mengirim test: " + data.msg, "error");
        }
      } catch (e) {
        showToast("Error koneksi test Telegram", "error");
      }
    }

    async function loadGallery(page=1) {
      if (!currentAuthPin) return;
      const date = document.getElementById("galleryDateFilter").value;
      const channel = document.getElementById("galleryCamFilter").value;
      try {
        const res = await authFetch(`/api/gallery?page=${page}&date=${encodeURIComponent(date)}&channel=${encodeURIComponent(channel)}`);
        const data = await res.json();
        
        document.getElementById("gallerySummaryText").innerText = `Total ${data.total_files} file snapshot (${data.total_mb} MB) terpakai`;
        
        const select = document.getElementById("galleryDateFilter");
        const currentVal = select.value;
        select.innerHTML = '<option value="">Semua Tanggal</option>' + data.available_dates.map(d => `<option value="${d}" ${d===currentVal?'selected':''}>${d}</option>`).join("");

        const container = document.getElementById("galleryContainer");
        if (data.items.length === 0) {
          container.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-muted);">Belum ada snapshot event pada filter ini</div>';
        } else {
          container.innerHTML = data.items.map(item => `
            <div class="gallery-card" onclick="openPreview('${item.filename}', '${item.url}?pin=${encodeURIComponent(currentAuthPin)}', '${item.formatted_date}')">
              <img class="gallery-thumb" src="${item.url}?pin=${encodeURIComponent(currentAuthPin)}" loading="lazy" alt="Snapshot">
              <div class="gallery-info">
                <div>
                  <span class="neo-badge badge-purple" style="font-size: 0.65rem; margin-bottom: 2px;">Ch ${item.channel}</span>
                  <div class="gallery-date">⏰ ${item.formatted_date}</div>
                </div>
                <span class="neo-badge badge-blue" style="font-size: 0.65rem;">${item.size_kb} KB</span>
              </div>
            </div>
          `).join("");
        }

        const pag = document.getElementById("paginationBox");
        let pagHtml = "";
        for (let i = 1; i <= data.total_pages; i++) {
          pagHtml += `<button class="neo-btn ${i===data.page?'neo-btn-purple':'neo-btn-outline'}" style="padding: 6px 12px;" onclick="loadGallery(${i})">${i}</button>`;
        }
        pag.innerHTML = pagHtml;
      } catch (e) {}
    }

    function openPreview(filename, url, dateStr) {
      currentPreviewFile = filename;
      document.getElementById("previewTitle").innerText = `Snapshot: ${filename} (${dateStr})`;
      document.getElementById("previewImg").src = url;
      document.getElementById("btnDownloadPreview").href = url;
      document.getElementById("previewModal").style.display = "flex";
    }

    function closePreview() {
      document.getElementById("previewModal").style.display = "none";
    }

    async function deleteCurrentPreview() {
      if (!confirm("Hapus file snapshot ini secara permanen?")) return;
      try {
        const res = await authFetch("/api/gallery/delete", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({filename: currentPreviewFile})
        });
        const data = await res.json();
        if (data.ok) {
          showToast("🗑️ Foto berhasil dihapus", "success");
          closePreview();
          loadGallery(1);
        }
      } catch (e) {}
    }

    async function runCleanup() {
      showToast("Membersihkan foto kadaluarsa...", "info");
      try {
        const res = await authFetch("/api/gallery/cleanup", {method: "POST"});
        const data = await res.json();
        showToast(data.msg, "success");
        loadGallery(1);
      } catch (e) {}
    }

    async function clearAllGallery() {
      if (!confirm("PERINGATAN: Apakah Anda yakin ingin MENGHAPUS SEMUA snapshot di galeri?")) return;
      try {
        const res = await authFetch("/api/gallery/clear-all", {method: "POST"});
        const data = await res.json();
        showToast(data.msg, "success");
        loadGallery(1);
      } catch (e) {}
    }

    function toggleChannelActiveStyle(ch) {
      const box = document.getElementById(`channelBox_${ch}`);
      const chk = document.getElementById(`ch_enabled_${ch}`);
      const badge = document.getElementById(`ch_badge_${ch}`);
      if (!box || !chk) return;
      if (chk.checked) {
        box.className = "channel-box ch-active";
        if (badge) { badge.className = "neo-badge badge-live"; badge.innerText = `● Ch ${ch} (Aktif)`; }
      } else {
        box.className = "channel-box ch-muted";
        if (badge) { badge.className = "neo-badge badge-dead"; badge.innerText = `⏸️ Ch ${ch} (Muted)`; }
      }
    }

    function renderSingleChannelBox(ch, c) {
      const isEnabled = !!c.enabled;
      return `
        <div class="channel-box ${isEnabled ? 'ch-active' : 'ch-muted'}" id="channelBox_${ch}" data-ch="${ch}">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-wrap: wrap; gap: 8px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <span id="ch_badge_${ch}" class="neo-badge ${isEnabled ? 'badge-live' : 'badge-dead'}" style="font-size: 0.8rem;">${isEnabled ? '●' : '⏸️'} Ch ${ch} (${isEnabled ? 'Aktif' : 'Muted'})</span>
              <span class="mono" style="font-weight: 800; font-size: 0.9rem;" id="ch_label_${ch}">${escapeHtml(c.name || 'Channel ' + ch)}</span>
            </div>
            <div style="display: flex; align-items: center; gap: 12px;">
              <label style="display: flex; align-items: center; gap: 6px; font-size: 0.82rem; font-weight: 700; cursor: pointer;">
                <input type="checkbox" id="ch_enabled_${ch}" ${isEnabled ? 'checked' : ''} onchange="toggleChannelActiveStyle('${ch}')">
                <span>Aktifkan Alert Telegram</span>
              </label>
              <button type="button" class="neo-btn neo-btn-red" style="padding: 4px 8px; font-size: 0.75rem;" onclick="removeChannelBox('${ch}')">🗑️ Hapus</button>
            </div>
          </div>
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px;">
            <div>
              <label class="form-label" style="font-size: 0.75rem;">Nama Kamera</label>
              <input type="text" id="ch_name_${ch}" class="form-input" style="padding: 8px 10px; font-size: 0.85rem;" value="${escapeHtml(c.name || '')}" oninput="document.getElementById('ch_label_${ch}').innerText = this.value || 'Channel ${ch}'">
            </div>
            <div>
              <label class="form-label" style="font-size: 0.75rem;">Lokasi / Area</label>
              <input type="text" id="ch_loc_${ch}" class="form-input" style="padding: 8px 10px; font-size: 0.85rem;" value="${escapeHtml(c.location || '')}">
            </div>
            <div>
              <label class="form-label" style="font-size: 0.75rem;">IP Kamera (Direct)</label>
              <input type="text" id="ch_ip_${ch}" class="form-input" style="padding: 8px 10px; font-size: 0.85rem;" value="${escapeHtml(c.ip || '')}">
            </div>
          </div>
        </div>
      `;
    }

    function addNewChannelPrompt() {
      const ch = prompt("Masukkan Nomor Channel Kamera Baru di NVR (Contoh: 4):");
      if (!ch || isNaN(ch)) return;
      const cleanCh = String(parseInt(ch));
      if (document.getElementById(`channelBox_${cleanCh}`)) {
        return alert(`Channel ${cleanCh} sudah ada dalam daftar!`);
      }
      const container = document.getElementById("channelConfigContainer");
      const defaultInfo = {
        name: `CAM ${cleanCh} - AREA BARU`,
        location: `LOKASI KAMERA ${cleanCh}`,
        ip: `192.168.99.${90 + parseInt(cleanCh)}`,
        enabled: true
      };
      container.insertAdjacentHTML('beforeend', renderSingleChannelBox(cleanCh, defaultInfo));
      showToast(`Channel ${cleanCh} ditambahkan! Klik 'Simpan' untuk menerapkan.`, "info");
    }

    function removeChannelBox(ch) {
      if (!confirm(`Hapus konfigurasi Channel ${ch}?`)) return;
      const el = document.getElementById(`channelBox_${ch}`);
      if (el) el.remove();
      showToast(`Channel ${ch} dihapus dari form. Klik 'Simpan' untuk menerapkan.`, "warn");
    }

    async function loadSettingsForm() {
      if (!currentAuthPin) return;
      try {
        const res = await authFetch("/api/config");
        const cfg = await res.json();
        const form = document.getElementById("settingsForm");
        for (let k in cfg) {
          if (form.elements[k] && k !== "channels") {
            form.elements[k].value = cfg[k];
          }
        }
        document.getElementById("cooldownValBadge").innerText = cfg.cooldown_seconds + " Detik";

        const chContainer = document.getElementById("channelConfigContainer");
        let chHtml = "";
        const channels = cfg.channels || {};
        for (let ch in channels) {
          chHtml += renderSingleChannelBox(ch, channels[ch]);
        }
        chContainer.innerHTML = chHtml;
      } catch (e) {}
    }

    async function saveSettings(e) {
      e.preventDefault();
      const form = document.getElementById("settingsForm");
      const formData = new FormData(form);
      const payload = {};
      formData.forEach((v, k) => payload[k] = v);

      // Collect all dynamic channels from DOM
      payload.channels = {};
      const channelBoxes = document.querySelectorAll(".channel-box");
      channelBoxes.forEach(box => {
        const ch = box.getAttribute("data-ch");
        if (ch) {
          payload.channels[ch] = {
            name: document.getElementById(`ch_name_${ch}`).value,
            location: document.getElementById(`ch_loc_${ch}`).value,
            ip: document.getElementById(`ch_ip_${ch}`).value,
            enabled: document.getElementById(`ch_enabled_${ch}`).checked,
            main_stream: "101",
            sub_stream: "102"
          };
        }
      });

      try {
        const res = await authFetch("/api/config", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.ok) {
          showToast("💾 Pengaturan Multi-Kamera berhasil disimpan & diterapkan!", "success");
          fetchStats();
        }
      } catch (e) {
        showToast("Gagal menyimpan konfigurasi", "error");
      }
    }

    async function syncCamerasFromNvr() {
      showToast("Menghubungi NVR & menyinkronkan kamera...", "info");
      try {
        const res = await authFetch("/api/sync-nvr", {method: "POST"});
        const data = await res.json();
        if (data.ok) {
          showToast(`✅ Berhasil menyinkronkan ${data.count} kamera dari NVR!`, "success");
          loadSettingsForm();
          fetchStats();
        } else {
          showToast("Gagal sync NVR: " + data.msg, "error");
        }
      } catch (e) {
        showToast("Error koneksi sync NVR", "error");
      }
    }

    function initApp() {
      fetchStats();
      setInterval(fetchStats, 3000);
      setInterval(() => {
        const activeTab = document.querySelector(".tab-content.active") ? document.querySelector(".tab-content.active").id : "";
        if (activeTab === "tab-logs") fetchLogs();
      }, 5000);
    }
  </script>
</body>
</html>
"""

# ------------------------------------------------------------------------------
# HTTP SERVER & MULTI-CHANNEL API DISPATCHER
# ------------------------------------------------------------------------------
class CCTVGuardHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Web-PIN")
        self.end_headers()
        if not getattr(self, 'is_head', False):
            self.wfile.write(body)

    def do_HEAD(self):
        self.is_head = True
        self.do_GET()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Web-PIN")
        self.end_headers()

    def check_auth(self):
        cfg = get_config()
        expected_pin = str(cfg.get("web_pin", "060708"))
        # 1. Header
        provided_pin = self.headers.get("X-Web-PIN", "")
        # 2. Query parameter (?pin=)
        if not provided_pin:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            provided_pin = qs.get("pin", [""])[0]
        return str(provided_pin) == expected_pin

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. Main Web Panel UI (HTML & Modal)
        if path in ["/", "/index.html"]:
            body = INDEX_HTML.encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not getattr(self, 'is_head', False):
                self.wfile.write(body)
            return

        # --- AUTH GATE: Protect All API & Snapshot Requests ---
        if not self.check_auth():
            self.send_json_response({"ok": False, "msg": "Unauthorized. PIN required."}, status=401)
            return

        # 2. Stats API
        if path == "/api/stats":
            cfg = get_config()
            with stats_lock:
                data = {
                    **stats,
                    "nvr_ip": cfg.get("nvr_ip", "192.168.99.10"),
                    "cooldown_seconds": cfg.get("cooldown_seconds", 15),
                    "telegram_enabled": cfg.get("telegram_enabled", True),
                    "channels": cfg.get("channels", {})
                }
            self.send_json_response(data)
            return

        # 3. Logs API
        elif path == "/api/logs":
            with log_lock:
                logs = list(log_buffer)
            self.send_json_response(logs)
            return

        # 4. Config API
        elif path == "/api/config":
            cfg = get_config()
            self.send_json_response(cfg)
            return

        # 5. Live Camera Snapshot on Demand (Specific Channel)
        elif path == "/api/camera-snapshot":
            channel = query.get("channel", ["2"])[0]
            snap = get_channel_snapshot(channel)
            if snap:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(snap)))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                if not getattr(self, 'is_head', False):
                    self.wfile.write(snap)
            else:
                self.send_response(503)
                self.end_headers()
            return

        # 6. Live Video MJPEG Stream (Specific Channel, Sub-Stream ~8 FPS)
        elif path == "/api/live-stream":
            channel = query.get("channel", ["2"])[0]
            cfg = get_config()
            ch_info = cfg.get("channels", {}).get(channel)
            
            # Determine target stream URL
            if ch_info and ch_info.get("ip"):
                cam_url = f"http://{ch_info['ip']}/ISAPI/Streaming/channels/{ch_info.get('sub_stream', '102')}/picture"
                auth = HTTPDigestAuth(cfg.get("nvr_user", "admin"), cfg.get("nvr_pass", "Bestari008"))
            else:
                cam_url = f"http://{cfg.get('nvr_ip', '192.168.99.10')}/ISAPI/Streaming/channels/{channel}02/picture"
                auth = HTTPDigestAuth(cfg.get("nvr_user", "admin"), cfg.get("nvr_pass", "Bestari008"))

            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.end_headers()

            try:
                while True:
                    try:
                        r = requests.get(cam_url, auth=auth, timeout=2.5)
                        if r.status_code == 200 and len(r.content) > 500:
                            frame = r.content
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                            self.wfile.write(frame)
                            self.wfile.write(b"\r\n")
                    except Exception:
                        pass
                    time.sleep(0.12)  # ~8 FPS frame delay
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        # 7. Gallery List & Filter API (Multi-Camera Filter)
        elif path == "/api/gallery":
            page = int(query.get("page", ["1"])[0])
            date_filter = query.get("date", [""])[0]
            channel_filter = query.get("channel", [""])[0]
            per_page = 18

            all_files = sorted(SNAPSHOT_DIR.glob("snap_*.jpg"), key=os.path.getmtime, reverse=True)
            available_dates = sorted(list({datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d") for f in all_files}), reverse=True)

            filtered_files = []
            for f in all_files:
                dt_str = datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d")
                
                # Extract channel ID from filename snap_chX_YYYYMMDD_HHMMSS.jpg
                m = re.search(r"snap_ch(\d+)_", f.name)
                ch_found = m.group(1) if m else "1"

                if date_filter and dt_str != date_filter:
                    continue
                if channel_filter and ch_found != channel_filter:
                    continue
                filtered_files.append((f, ch_found))

            total_files = len(filtered_files)
            total_mb = round(sum(f.stat().st_size for f in all_files) / (1024 * 1024), 2)
            total_pages = max(1, (total_files + per_page - 1) // per_page)
            page = max(1, min(page, total_pages))

            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            page_files = filtered_files[start_idx:end_idx]

            items = []
            for f, ch_id in page_files:
                dt = datetime.datetime.fromtimestamp(os.path.getmtime(f))
                items.append({
                    "filename": f.name,
                    "channel": ch_id,
                    "url": f"/snapshots/{f.name}",
                    "size_kb": round(f.stat().st_size / 1024, 1),
                    "formatted_date": dt.strftime("%d/%m/%Y %H:%M:%S WIB"),
                    "date_str": dt.strftime("%Y-%m-%d")
                })

            self.send_json_response({
                "page": page,
                "total_pages": total_pages,
                "total_files": total_files,
                "total_mb": total_mb,
                "available_dates": available_dates,
                "items": items
            })
            return

        # 8. Static Snapshot Image Server
        elif path.startswith("/snapshots/"):
            fname = os.path.basename(path)
            fpath = SNAPSHOT_DIR / fname
            if fpath.exists() and fpath.is_file():
                with open(fpath, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                if not getattr(self, 'is_head', False):
                    self.wfile.write(content)
            else:
                self.send_response(404)
                self.end_headers()
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        # 1. Verify PIN Access (Public Auth Endpoint)
        if path == "/api/auth/verify":
            cfg = get_config()
            pin = payload.get("pin", "")
            if str(pin) == str(cfg.get("web_pin", "060708")):
                self.send_json_response({"ok": True})
            else:
                self.send_json_response({"ok": False, "msg": "PIN Salah"}, status=401)
            return

        # --- AUTH GATE: Protect All Other POST APIs ---
        if not self.check_auth():
            self.send_json_response({"ok": False, "msg": "Unauthorized. PIN required."}, status=401)
            return

        # 2. Update Configuration
        if path == "/api/config":
            update_config(payload)
            self.send_json_response({"ok": True, "msg": "Konfigurasi berhasil disimpan"})
            return

        # 3. Test Telegram Alert (Specific Channel)
        elif path == "/api/test-telegram":
            channel = str(payload.get("channel", "2"))
            threading.Thread(
                target=trigger_channel_alert,
                args=(channel, "TEST_MANUAL", "Manual Trigger dari Web Panel"),
                daemon=True
            ).start()
            self.send_json_response({"ok": True, "msg": f"Test alert Ch {channel} dipicu"})
            return

        # 4. Delete Single Snapshot
        elif path == "/api/gallery/delete":
            fname = payload.get("filename", "")
            fpath = SNAPSHOT_DIR / os.path.basename(fname)
            if fpath.exists():
                try:
                    fpath.unlink()
                    self.send_json_response({"ok": True, "msg": "File berhasil dihapus"})
                except Exception as e:
                    self.send_json_response({"ok": False, "msg": str(e)}, status=500)
            else:
                self.send_json_response({"ok": False, "msg": "File tidak ditemukan"}, status=404)
            return

        # 5. Run Cleanup
        elif path == "/api/gallery/cleanup":
            run_gallery_cleanup()
            self.send_json_response({"ok": True, "msg": "Pembersihan retensi selesai dijalankan"})
            return

        # 6. Clear All Gallery
        elif path == "/api/gallery/clear-all":
            try:
                for f in SNAPSHOT_DIR.glob("snap_*.jpg"):
                    f.unlink()
                self.send_json_response({"ok": True, "msg": "Seluruh galeri snapshot berhasil dikosongkan"})
            except Exception as e:
                self.send_json_response({"ok": False, "msg": str(e)}, status=500)
            return

        # 7. Auto-Sync Cameras from NVR
        elif path == "/api/sync-nvr":
            count = sync_cameras_from_nvr()
            if count > 0:
                self.send_json_response({"ok": True, "count": count, "msg": f"Berhasil menyinkronkan {count} kamera dari NVR"})
            else:
                self.send_json_response({"ok": False, "count": 0, "msg": "Gagal membaca kamera dari NVR"}, status=500)
            return

        self.send_response(404)
        self.end_headers()

# ------------------------------------------------------------------------------
# MULTITHREADED HTTP SERVER RUNNER
# ------------------------------------------------------------------------------
def run_web_server():
    cfg = get_config()
    port = cfg.get("web_port", 8088)
    server_address = ('0.0.0.0', port)
    
    httpd = ThreadingHTTPServer(server_address, CCTVGuardHTTPHandler)
    logger.info(f"🚀 Multi-Cam Web Panel Monitor aktif di: http://0.0.0.0:{port} (PIN: {cfg.get('web_pin')})")
    try:
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"HTTP Server exited: {e}")

# ------------------------------------------------------------------------------
# MAIN ENTRYPOINT
# ------------------------------------------------------------------------------
def main():
    logger.info("==================================================================")
    logger.info("  CCTV GUARD & TELEGRAM ALERT - MULTI-CAMERA NVR HUB v3.0         ")
    logger.info("==================================================================")

    # 1. Start Hikvision NVR Central ISAPI Stream Listener
    t_isapi = threading.Thread(target=nvr_listener_thread, daemon=True, name="NVRISAPIListener")
    t_isapi.start()

    # 2. Start Storage Retention Worker Thread
    t_retention = threading.Thread(target=retention_worker, daemon=True, name="RetentionWorker")
    t_retention.start()

    # 3. Start Multi-Threaded Web Server (Main Thread)
    run_web_server()

if __name__ == "__main__":
    main()
