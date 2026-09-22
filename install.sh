#!/usr/bin/env bash
# ==============================================================================
# 🛡️ CCTV Guard & Telegram Alert System - Universal Installer Script
# Architecture: Multi-Camera NVR Hub & Interactive Web Dashboard
# GitHub: https://github.com/mrburgercheese/hikvision-telegram-guard
# ==============================================================================

set -e

# --- Color Scheme & Branding ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

INSTALL_DIR="/opt/cctv-tg-guard"
SERVICE_NAME="cctv-tg-guard"
REPO_RAW="https://raw.githubusercontent.com/mrburgercheese/hikvision-telegram-guard/main"

print_banner() {
    clear
    echo -e "${YELLOW}${BOLD}"
    cat << "EOF"
  ██████╗ ██████╗████████╗██╗   ██╗     ██████╗ ██╗   ██╗ █████╗ ██████╗ ██████╗ 
 ██╔════╝██╔════╝╚══██╔══╝██║   ██║    ██╔════╝ ██║   ██║██╔══██╗██╔══██╗██╔══██╗
 ██║     ██║        ██║   ██║   ██║    ██║  ███╗██║   ██║███████║██████╔╝██║  ██║
 ██║     ██║        ██║   ╚██╗ ██╔╝    ██║   ██║██║   ██║██╔══██║██╔══██╗██║  ██║
 ╚██████╗╚██████╗   ██║    ╚████╔╝     ╚██████╔╝╚██████╔╝██║  ██║██║  ██║██████╔╝
  ╚═════╝ ╚═════╝   ╚═╝     ╚═══╝       ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ 
EOF
    echo -e "${CYAN}    🛡️  Hikvision NVR & CCTV Telegram Alert System with Web Monitor${NC}"
    echo -e "${BLUE}    🔗  GitHub: https://github.com/mrburgercheese/hikvision-telegram-guard${NC}"
    echo -e "${YELLOW}================================================================================${NC}\n"
}

# --- Step 1: Privilege & OS Check ---
check_environment() {
    echo -e "${CYAN}[STEP 1/6] 🔍 Memeriksa Izin Akses & Deteksi Sistem Operasi...${NC}"

    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}[ERROR] Installer ini wajib dijalankan dengan hak akses root / sudo!${NC}"
        echo -e "Silakan jalankan ulang menggunakan: ${BOLD}sudo bash install.sh${NC}\n"
        exit 1
    fi

    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS_NAME=$NAME
        OS_ID=$ID
        OS_VER=$VERSION_ID
    else
        OS_NAME="Linux Generik"
        OS_ID="unknown"
    fi
    echo -e "  ${GREEN}✓${NC} Sistem Operasi : ${BOLD}${OS_NAME}${NC} (${OS_ID})"

    # Cek systemd
    if ! pidof systemd > /dev/null 2>&1 && [ ! -d /run/systemd/system ]; then
        echo -e "${RED}[ERROR] Systemd tidak terdeteksi pada sistem ini! Microservice membutuhkan systemd.${NC}"
        exit 1
    fi
    echo -e "  ${GREEN}✓${NC} Init System    : ${BOLD}Systemd (Active)${NC}\n"
}

# --- Step 2: Dependency Verification & Auto-Install ---
install_dependencies() {
    echo -e "${CYAN}[STEP 2/6] 📦 Memeriksa & Menginstalasi Dependensi Sistem...${NC}"

    PACKAGES_NEEDED=()

    command -v curl >/dev/null 2>&1 || PACKAGES_NEEDED+=("curl")
    command -v python3 >/dev/null 2>&1 || PACKAGES_NEEDED+=("python3")

    if [ ${#PACKAGES_NEEDED[@]} -ne 0 ]; then
        echo -e "  ${YELLOW}→ Menginstalasi paket yang belum tersedia: ${PACKAGES_NEEDED[*]}...${NC}"
        if [[ "$OS_ID" == "ubuntu" || "$OS_ID" == "debian" ]]; then
            apt-get update -qq && apt-get install -y -qq "${PACKAGES_NEEDED[@]}" python3-requests
        elif [[ "$OS_ID" == "almalinux" || "$OS_ID" == "rocky" || "$OS_ID" == "centos" || "$OS_ID" == "fedora" || "$OS_ID" == "rhel" ]]; then
            dnf install -y -q "${PACKAGES_NEEDED[@]}" python3-requests || yum install -y -q "${PACKAGES_NEEDED[@]}" python3-requests
        elif [[ "$OS_ID" == "arch" ]]; then
            pacman -Sy --noconfirm "${PACKAGES_NEEDED[@]}" python-requests
        else
            echo -e "${YELLOW}  [WARN] Manajer paket tidak dikenali, pastikan Python 3 dan curl terinstal.${NC}"
        fi
    fi

    # Verifikasi modul Python requests
    if ! python3 -c "import requests" >/dev/null 2>&1; then
        echo -e "  ${YELLOW}→ Menginstalasi pustaka Python requests via pip...${NC}"
        if command -v pip3 >/dev/null 2>&1; then
            pip3 install --quiet requests
        else
            if [[ "$OS_ID" == "ubuntu" || "$OS_ID" == "debian" ]]; then
                apt-get install -y -qq python3-pip && pip3 install --quiet requests
            else
                echo -e "${RED}[ERROR] Pustaka Python 'requests' gagal diinstal. Silakan instal manual: pip install requests${NC}"
                exit 1
            fi
        fi
    fi

    PY_VER=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')
    echo -e "  ${GREEN}✓${NC} Python Runtime : ${BOLD}Python ${PY_VER}${NC}"
    echo -e "  ${GREEN}✓${NC} Python Library : ${BOLD}requests (Ready)${NC}\n"
}

# --- Step 3: Setup Working Directories ---
setup_directories() {
    echo -e "${CYAN}[STEP 3/6] 📁 Menyiapkan Struktur Direktori Kerja...${NC}"
    mkdir -p "${INSTALL_DIR}/snapshots"
    chmod 755 "${INSTALL_DIR}"
    chmod 777 "${INSTALL_DIR}/snapshots"
    echo -e "  ${GREEN}✓${NC} Direktori Kerja : ${BOLD}${INSTALL_DIR}${NC}"
    echo -e "  ${GREEN}✓${NC} Folder Snapshot : ${BOLD}${INSTALL_DIR}/snapshots${NC}\n"
}

# --- Step 4: Download & Deploy Application Files ---
deploy_application_files() {
    echo -e "${CYAN}[STEP 4/6] 🚀 Mengunduh Berkas Microservice dari GitHub...${NC}"

    SCRIPT_PATH="${INSTALL_DIR}/cctv_it_guard.py"
    SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

    # Jika file ada di folder lokal saat menjalankan install.sh lokal
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
    if [ -f "${SCRIPT_DIR}/cctv_it_guard.py" ]; then
        echo -e "  ${BLUE}→ Menggunakan berkas lokal dari ${SCRIPT_DIR}...${NC}"
        cp "${SCRIPT_DIR}/cctv_it_guard.py" "${SCRIPT_PATH}"
        cp "${SCRIPT_DIR}/cctv-tg-guard.service" "${SERVICE_PATH}"
    else
        echo -e "  ${BLUE}→ Mengunduh berkas dari repositori GitHub resmi...${NC}"
        curl -sSL "${REPO_RAW}/cctv_it_guard.py" -o "${SCRIPT_PATH}"
        curl -sSL "${REPO_RAW}/cctv-tg-guard.service" -o "${SERVICE_PATH}"
    fi

    chmod +x "${SCRIPT_PATH}"
    echo -e "  ${GREEN}✓${NC} Microservice Core : ${BOLD}${SCRIPT_PATH}${NC}"
    echo -e "  ${GREEN}✓${NC} Systemd Unit File : ${BOLD}${SERVICE_PATH}${NC}\n"
}

# --- Step 5: Configuration Setup ---
configure_app() {
    echo -e "${CYAN}[STEP 5/6] ⚙️  Menyiapkan Berkas Konfigurasi...${NC}"

    CONFIG_FILE="${INSTALL_DIR}/config.json"

    if [ -f "$CONFIG_FILE" ]; then
        echo -e "  ${YELLOW}→ Berkas config.json yang sudah ada ditemukan.${NC}"
        read -p "  Apakah Anda ingin mempertahankan konfigurasi lama? (Y/n): " KEEP_CONF
        KEEP_CONF=${KEEP_CONF:-Y}
        if [[ "$KEEP_CONF" =~ ^[Yy]$ ]]; then
            echo -e "  ${GREEN}✓${NC} Konfigurasi lama dipertahankan.\n"
            return
        fi
    fi

    echo -e "  ${YELLOW}Silakan masukkan parameter instalasi (tekan [Enter] untuk default):${NC}"

    read -p "  [?] Port Web Dashboard [8088]: " INPUT_PORT
    INPUT_PORT=${INPUT_PORT:-8088}

    read -p "  [?] 6-Digit PIN Akses Web [060708]: " INPUT_PIN
    INPUT_PIN=${INPUT_PIN:-060708}

    read -p "  [?] IP Address NVR Hikvision [192.168.1.10]: " INPUT_NVR_IP
    INPUT_NVR_IP=${INPUT_NVR_IP:-192.168.1.10}

    read -p "  [?] Username NVR [admin]: " INPUT_NVR_USER
    INPUT_NVR_USER=${INPUT_NVR_USER:-admin}

    read -s -p "  [?] Password NVR: " INPUT_NVR_PASS
    echo ""
    INPUT_NVR_PASS=${INPUT_NVR_PASS:-Password123#}

    read -p "  [?] Bot Token Telegram: " INPUT_TG_TOKEN
    INPUT_TG_TOKEN=${INPUT_TG_TOKEN:-123456789:AAFxSampleTokenFromBotFather}

    read -p "  [?] Target Chat / Group ID Telegram: " INPUT_TG_CHAT
    INPUT_TG_CHAT=${INPUT_TG_CHAT:--100123456789}

    cat << EOF > "$CONFIG_FILE"
{
  "nvr_ip": "${INPUT_NVR_IP}",
  "nvr_user": "${INPUT_NVR_USER}",
  "nvr_pass": "${INPUT_NVR_PASS}",
  "telegram_token": "${INPUT_TG_TOKEN}",
  "telegram_chat_id": "${INPUT_TG_CHAT}",
  "telegram_enabled": true,
  "cooldown_seconds": 15,
  "web_port": ${INPUT_PORT},
  "web_pin": "${INPUT_PIN}",
  "retention_days": 3,
  "max_gallery_items": 150,
  "channels": {
    "1": {
      "name": "CAM 1 - LORONG",
      "location": "LORONG UTAMA",
      "ip": "192.168.1.91",
      "enabled": true,
      "main_stream": "101",
      "sub_stream": "102"
    },
    "2": {
      "name": "CAM 2 - JALAN UTARA",
      "location": "AREA JALAN UTARA",
      "ip": "192.168.1.92",
      "enabled": true,
      "main_stream": "101",
      "sub_stream": "102"
    },
    "3": {
      "name": "CAM 3 - JALAN SELATAN",
      "location": "AREA JALAN SELATAN",
      "ip": "192.168.1.93",
      "enabled": true,
      "main_stream": "101",
      "sub_stream": "102"
    }
  }
}
EOF

    chmod 600 "$CONFIG_FILE"
    echo -e "  ${GREEN}✓${NC} Konfigurasi berhasil dibuat: ${BOLD}${CONFIG_FILE}${NC}\n"
}

# --- Step 6: Systemd Service Activation & Verification ---
start_systemd_service() {
    echo -e "${CYAN}[STEP 6/6] ⚙️  Mengaktifkan & Menjalankan Systemd Service...${NC}"

    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}.service" > /dev/null 2>&1
    systemctl restart "${SERVICE_NAME}.service"

    sleep 2

    if systemctl is-active --quiet "${SERVICE_NAME}.service"; then
        echo -e "  ${GREEN}✓${NC} Service Status : ${GREEN}${BOLD}ACTIVE (RUNNING)${NC}\n"
    else
        echo -e "  ${RED}✗${NC} Service Status : ${RED}${BOLD}FAILED TO START${NC}"
        echo -e "  Lihat detail error dengan perintah: ${BOLD}journalctl -u ${SERVICE_NAME}.service -n 20 --no-pager${NC}\n"
        exit 1
    fi
}

# --- Print Completion Summary ---
print_success_summary() {
    SERVER_IP=$(hostname -I | awk '{print $1}')
    SERVER_IP=${SERVER_IP:-"127.0.0.1"}
    
    # Baca port dan pin aktual dari config
    CFG_PORT=$(python3 -c "import json; print(json.load(open('${INSTALL_DIR}/config.json'))['web_port'])" 2>/dev/null || echo "8088")
    CFG_PIN=$(python3 -c "import json; print(json.load(open('${INSTALL_DIR}/config.json'))['web_pin'])" 2>/dev/null || echo "060708")

    echo -e "${GREEN}${BOLD}================================================================================${NC}"
    echo -e "${GREEN}${BOLD} 🎉  INSTALASI CCTV GUARD & TELEGRAM ALERT BERHASIL DISELESAIKAN!${NC}"
    echo -e "${GREEN}${BOLD}================================================================================${NC}"
    echo -e "  🌐 ${BOLD}Web Panel URL${NC}   : ${YELLOW}http://${SERVER_IP}:${CFG_PORT}/${NC}"
    echo -e "  🔐 ${BOLD}Default PIN${NC}     : ${CYAN}${BOLD}${CFG_PIN}${NC}"
    echo -e "  📁 ${BOLD}Direktori Kerja${NC} : ${BOLD}${INSTALL_DIR}${NC}"
    echo -e "  ⚙️  ${BOLD}Systemd Service${NC} : ${BOLD}${SERVICE_NAME}.service${NC}"
    echo -e "--------------------------------------------------------------------------------"
    echo -e "  💡 ${BOLD}Perintah Pengelolaan Service:${NC}"
    echo -e "     • Cek Status  : ${BOLD}sudo systemctl status ${SERVICE_NAME}.service${NC}"
    echo -e "     • Pantau Log  : ${BOLD}sudo journalctl -u ${SERVICE_NAME}.service -f${NC}"
    echo -e "     • Restart     : ${BOLD}sudo systemctl restart ${SERVICE_NAME}.service${NC}"
    echo -e "     • Stop        : ${BOLD}sudo systemctl stop ${SERVICE_NAME}.service${NC}"
    echo -e "${GREEN}================================================================================${NC}\n"
}

# --- Main Execution Flow ---
main() {
    print_banner
    check_environment
    install_dependencies
    setup_directories
    deploy_application_files
    configure_app
    start_systemd_service
    print_success_summary
}

main "$@"
