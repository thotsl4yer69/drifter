#!/bin/bash
# ============================================
# MZ1312 DRIFTER — Vision Stack Installer (Pi5 + Hailo)
# UNCAGED TECHNOLOGY — EST 1991
# ============================================
# Usage: sudo ./scripts/install-vision.sh [--with-ocr]
# ============================================

set -e

CYAN='\033[0;36m'
RED='\033[0;31m'
GREEN='\033[0;32m'
AMBER='\033[0;33m'
NC='\033[0m'

DRIFTER_DIR="/opt/drifter"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
warn() { echo -e "${AMBER}  ! $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; exit 1; }
step() { echo -e "\n${AMBER}[$1] $2${NC}"; }

echo -e "${CYAN}  DRIFTER — Vision Installer (Hailo / ONNX)${NC}\n"
if [ "$EUID" -ne 0 ]; then fail "Run as root: sudo ./scripts/install-vision.sh"; fi

step 1 "Installing video + python deps"
apt-get install -y -qq v4l-utils ffmpeg libgl1 libglib2.0-0 2>/dev/null
source "${DRIFTER_DIR}/venv/bin/activate"
pip install --quiet opencv-python numpy pyyaml onnxruntime
if [ "$1" = "--with-ocr" ]; then
    pip install --quiet easyocr
    ok "EasyOCR installed (optional)"
fi
ok "Vision deps installed"

step 2 "Hailo runtime detection"
if dpkg -l | grep -q hailo-rt 2>/dev/null; then
    ok "Hailo runtime detected"
else
    warn "Hailo runtime not detected — vision_engine will use ONNX fallback"
    warn "Install via Hailo official .deb when ready"
fi

step 3 "Deploying vision modules + working dirs"
mkdir -p "${DRIFTER_DIR}/dashcam" "${DRIFTER_DIR}/vision-models"
for f in vision_engine.py alpr_engine.py dashcam.py forward_collision.py; do
    cp "${REPO_DIR}/src/${f}" "${DRIFTER_DIR}/"
    chmod +x "${DRIFTER_DIR}/${f}"
done
ok "Vision modules deployed"

step 4 "Deploying vision.yaml"
if [ ! -f "${DRIFTER_DIR}/vision.yaml" ]; then
    cp "${REPO_DIR}/config/vision.yaml" "${DRIFTER_DIR}/"
    ok "vision.yaml deployed"
else
    warn "vision.yaml already present — not overwriting"
fi

step 5 "Installing systemd services"
for svc in vision dashcam alpr fcw; do
    cp "${REPO_DIR}/services/drifter-${svc}.service" /etc/systemd/system/
    systemctl enable "drifter-${svc}"
    ok "drifter-${svc} enabled"
done
systemctl daemon-reload

echo ""
echo -e "${GREEN}  Vision stack installed.${NC}"
echo -e "  Drop a yolov8s.hef (Hailo) or yolov8s.onnx into ${CYAN}${DRIFTER_DIR}/vision-models/${NC}"
echo -e "  Start:  ${CYAN}sudo systemctl start drifter-vision drifter-dashcam drifter-fcw${NC}"
