#!/usr/bin/env bash
# One-time VPS OS hardening (plan.md §13). Run once, directly on the VPS
# (not in a container — ufw/sshd/unattended-upgrades are host-level).
# Idempotent: safe to re-run after a system update.
#
# Usage: sudo ./harden-vps.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root (sudo ./harden-vps.sh)" >&2
    exit 1
fi

echo "== Firewall (ufw): allow SSH/HTTP/HTTPS only, default-deny =="
apt-get install -y -qq ufw > /dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status verbose

echo "== SSH: disable password auth, key-only login =="
SSHD_CONFIG=/etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' "$SSHD_CONFIG"
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' "$SSHD_CONFIG"
if ! grep -q '^PasswordAuthentication no' "$SSHD_CONFIG"; then
    echo "PasswordAuthentication no" >> "$SSHD_CONFIG"
fi
systemctl reload sshd || systemctl reload ssh

echo "== Automatic security updates =="
apt-get install -y -qq unattended-upgrades > /dev/null
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== Done. Verify you can still SSH in via key BEFORE closing this session. =="
