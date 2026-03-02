#!/bin/bash
# deploy.sh — Automated Hetzner deployment for DenseWealth
#
# Usage:
#   1. Spin up a Hetzner CX11/CX22 with Ubuntu 24.04
#   2. SSH into the server: ssh root@YOUR_SERVER_IP
#   3. Upload this script: scp deploy.sh root@YOUR_SERVER_IP:~/
#   4. Run it: bash deploy.sh
#
# Or one-liner from your local machine:
#   ssh root@YOUR_SERVER_IP 'bash -s' < deploy.sh

set -euo pipefail

APP_DIR="/opt/densewealth"
APP_USER="densewealth"
PYTHON_MIN="3.10"

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  DENSEWEALTH — Hetzner Deployment"
echo "══════════════════════════════════════════════════════════"
echo ""

# ── 1. System packages ──────────────────────────────────────

echo "📦 Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv sqlite3 > /dev/null 2>&1

PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "   Python $PYTHON_VER detected"

# Version check
if python3 -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)"; then
    echo "   ✅ Python version OK"
else
    echo "   ❌ Python 3.10+ required (found $PYTHON_VER)"
    exit 1
fi

# ── 2. Create service user ──────────────────────────────────

echo ""
echo "👤 Setting up service user..."
if id "$APP_USER" &>/dev/null; then
    echo "   User '$APP_USER' already exists"
else
    useradd --system --shell /bin/false --home-dir "$APP_DIR" "$APP_USER"
    echo "   Created user '$APP_USER'"
fi

# ── 3. Create app directory ─────────────────────────────────

echo ""
echo "📁 Setting up application directory..."
mkdir -p "$APP_DIR"

# ── 4. Copy application files ───────────────────────────────

echo ""
echo "📋 Copying application files..."

# If running on the server and files are in current dir or home
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR=""

if [ -f "$SCRIPT_DIR/main.py" ]; then
    SOURCE_DIR="$SCRIPT_DIR"
elif [ -f "$HOME/densewealth/main.py" ]; then
    SOURCE_DIR="$HOME/densewealth"
elif [ -f "$HOME/DenseWealth/main.py" ]; then
    SOURCE_DIR="$HOME/DenseWealth"
elif [ -f "/tmp/densewealth/main.py" ]; then
    SOURCE_DIR="/tmp/densewealth"
fi

if [ -z "$SOURCE_DIR" ]; then
    echo "   ⚠️  No source files found."
    echo "   Upload your files to $APP_DIR first, then re-run this script."
    echo ""
    echo "   From your local machine:"
    echo "     scp -r /path/to/DenseWealth/* root@SERVER_IP:$APP_DIR/"
    echo ""
    echo "   Then re-run: bash deploy.sh"
    exit 1
fi

# Copy Python files and config
for f in main.py config.py db.py watcher.py position_manager.py paper_trader.py \
         portfolio.py dashboard.py web.py resolver.py ws_watcher.py \
         executor.py executor_us.py reconciler.py stats.py healthcheck.py \
         auth.py approval.py generate_password.py mode.py \
         requirements.txt .env.example; do
    if [ -f "$SOURCE_DIR/$f" ]; then
        cp "$SOURCE_DIR/$f" "$APP_DIR/$f"
    fi
done

# Copy .env only if it doesn't already exist (don't overwrite user config)
if [ ! -f "$APP_DIR/.env" ]; then
    if [ -f "$SOURCE_DIR/.env" ]; then
        cp "$SOURCE_DIR/.env" "$APP_DIR/.env"
    elif [ -f "$SOURCE_DIR/.env.example" ]; then
        cp "$SOURCE_DIR/.env.example" "$APP_DIR/.env"
    fi
    echo "   Created .env from template"
else
    echo "   .env already exists, keeping existing config"
fi

# Copy templates directory
if [ -d "$SOURCE_DIR/templates" ]; then
    mkdir -p "$APP_DIR/templates"
    cp -r "$SOURCE_DIR/templates/"* "$APP_DIR/templates/" 2>/dev/null || true
fi

echo "   ✅ Files copied to $APP_DIR"

# ── 5. Virtual environment ──────────────────────────────────

echo ""
echo "🐍 Setting up Python virtual environment..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
echo "   ✅ Dependencies installed"

# ── 6. Set permissions ──────────────────────────────────────

echo ""
echo "🔒 Setting permissions..."
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"
echo "   ✅ Permissions set (.env is 600)"

# ── 7. Create systemd service ───────────────────────────────

echo ""
echo "⚙️  Creating systemd service..."

cat > /etc/systemd/system/densewealth.service << EOF
[Unit]
Description=DenseWealth — Polymarket Paper Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python main.py
Restart=always
RestartSec=15
StartLimitIntervalSec=300
StartLimitBurst=5

# Environment
EnvironmentFile=$APP_DIR/.env

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=densewealth

# Security hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$APP_DIR
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable densewealth
echo "   ✅ Service created and enabled"

# ── 8. Create management script ─────────────────────────────

echo ""
echo "🛠️  Creating management commands..."

cat > /usr/local/bin/densewealth << 'MGMT_EOF'
#!/bin/bash
# densewealth — management commands
APP_DIR="/opt/densewealth"
PY="$APP_DIR/venv/bin/python"

case "${1:-help}" in
    start)
        sudo systemctl start densewealth
        echo "✅ Started"
        ;;
    stop)
        sudo systemctl stop densewealth
        echo "⏹  Stopped"
        ;;
    restart)
        sudo systemctl restart densewealth
        echo "🔄 Restarted"
        ;;
    status)
        systemctl status densewealth --no-pager
        ;;
    logs)
        journalctl -u densewealth -f --no-hostname
        ;;
    logs-today)
        journalctl -u densewealth --since today --no-hostname --no-pager
        ;;
    dashboard)
        cd "$APP_DIR" && $PY dashboard.py "${@:2}"
        ;;
    stats)
        cd "$APP_DIR" && $PY stats.py "${@:2}"
        ;;
    positions)
        cd "$APP_DIR" && $PY stats.py --positions
        ;;
    trades)
        cd "$APP_DIR" && $PY stats.py --trades "${@:2}"
        ;;
    health)
        cd "$APP_DIR" && $PY healthcheck.py
        ;;
    config)
        ${EDITOR:-nano} "$APP_DIR/config.py"
        echo ""
        echo "Restart to apply: densewealth restart"
        ;;
    env)
        ${EDITOR:-nano} "$APP_DIR/.env"
        echo ""
        echo "Restart to apply: densewealth restart"
        ;;
    reset)
        echo "⚠️  This will delete all positions, trades, and balance data."
        read -p "Are you sure? (yes/no): " confirm
        if [ "$confirm" = "yes" ]; then
            sudo systemctl stop densewealth
            rm -f "$APP_DIR/densewealth.db"
            sudo systemctl start densewealth
            echo "✅ Database reset. Bot restarted with fresh state."
        else
            echo "Cancelled."
        fi
        ;;
    update)
        echo "📦 Updating dependencies..."
        cd "$APP_DIR" && $APP_DIR/venv/bin/pip install --quiet -r requirements.txt
        sudo systemctl restart densewealth
        echo "✅ Updated and restarted"
        ;;
    help|*)
        echo ""
        echo "densewealth — Polymarket Paper Trading Bot"
        echo ""
        echo "  densewealth start       Start the bot"
        echo "  densewealth stop        Stop the bot"
        echo "  densewealth restart     Restart the bot"
        echo "  densewealth status      Show service status"
        echo "  densewealth logs        Tail live logs"
        echo "  densewealth logs-today  Show today's logs"
        echo "  densewealth dashboard   Live terminal dashboard"
        echo "  densewealth stats       Show account summary"
        echo "  densewealth positions   Show open positions"
        echo "  densewealth trades      Show recent trades"
        echo "  densewealth health      Run health check"
        echo "  densewealth config      Edit config.py"
        echo "  densewealth env         Edit .env"
        echo "  densewealth reset       Delete DB and restart fresh"
        echo "  densewealth update      Update deps and restart"
        echo "  densewealth help        Show this help"
        echo ""
        ;;
esac
MGMT_EOF

chmod +x /usr/local/bin/densewealth
echo "   ✅ 'densewealth' command available system-wide"

# ── 9. Setup log rotation ───────────────────────────────────

echo ""
echo "📝 Configuring log rotation..."

cat > /etc/logrotate.d/densewealth << EOF
/var/log/journal/densewealth*.journal {
    weekly
    rotate 4
    compress
    missingok
    notifempty
}
EOF

# Also limit journald retention for this service
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/densewealth.conf << EOF
[Journal]
SystemMaxUse=200M
EOF

echo "   ✅ Logs capped at 200MB"

# ── 10. UFW firewall (optional but recommended) ─────────────

echo ""
echo "🔥 Configuring firewall..."
if command -v ufw &> /dev/null; then
    ufw --force enable > /dev/null 2>&1
    ufw allow ssh > /dev/null 2>&1
    ufw allow 8000/tcp > /dev/null 2>&1
    echo "   ✅ Firewall enabled (SSH + port 8000 allowed)"
else
    echo "   ⚠️  ufw not installed, skipping firewall setup"
    echo "   Install with: apt install ufw"
fi

# ── 11. Validate ────────────────────────────────────────────

echo ""
echo "🧪 Running health check..."
cd "$APP_DIR"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/python" healthcheck.py 2>/dev/null || true

# ── Done ────────────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  ✅ DEPLOYMENT COMPLETE"
echo "══════════════════════════════════════════════════════════"
echo ""
echo "  App directory:  $APP_DIR"
echo "  Service user:   $APP_USER"
echo "  Python venv:    $APP_DIR/venv"
echo "  Database:       $APP_DIR/densewealth.db"
echo ""
echo "  ┌─────────────────────────────────────────────────────┐"
echo "  │  NEXT STEPS:                                        │"
echo "  │                                                     │"
echo "  │  1. Add wallets:    densewealth config                │"
echo "  │  2. Tweak settings: densewealth env                   │"
echo "  │  3. Start the bot:  densewealth start                 │"
echo "  │  4. Watch logs:     densewealth logs                  │"
echo "  │  5. Check stats:    densewealth stats                 │"
echo "  └─────────────────────────────────────────────────────┘"
echo ""
echo "  Full command list: densewealth help"
echo ""
