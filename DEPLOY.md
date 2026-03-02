# DenseWealth — Hetzner Deployment Guide

## Server Info

- **IP:** (see .env or your Hetzner dashboard)
- **OS:** Ubuntu 24.04
- **User:** root
- **App directory:** /opt/densewealth
- **Service user:** densewealth

## Step 1: Upload Project Files

From the Windows machine where the code lives:

```powershell
scp -r C:\shaneclary\DenseWealth\* root@YOUR_SERVER_IP:/root/densewealth/
```

## Step 2: SSH Into the Server

```bash
ssh root@YOUR_SERVER_IP
```

## Step 3: Run the Deploy Script

```bash
cd /root/densewealth
bash deploy.sh
```

This will:
- Install Python 3, pip, venv, sqlite3
- Create a `densewealth` service user
- Copy files to `/opt/densewealth`
- Create a Python venv and install dependencies
- Set up a systemd service (auto-restart, boot-start)
- Create the `densewealth` CLI command
- Configure log rotation and firewall

## Step 4: Configure Environment

```bash
densewealth env
```

Edit the `.env` file with your keys (MetaMask private key, Polymarket API keys, etc.)

## Step 5: Start the Bot

```bash
densewealth start
```

## Management Commands

| Command | Description |
|---|---|
| `densewealth start` | Start the bot |
| `densewealth stop` | Stop the bot |
| `densewealth restart` | Restart the bot |
| `densewealth status` | Show service status |
| `densewealth logs` | Tail live logs |
| `densewealth logs-today` | Show today's logs |
| `densewealth stats` | Show account summary |
| `densewealth positions` | Show open positions |
| `densewealth trades` | Show recent trades |
| `densewealth health` | Run health check |
| `densewealth env` | Edit .env config |
| `densewealth config` | Edit config.py |
| `densewealth reset` | Delete DB and restart fresh |
| `densewealth update` | Update deps and restart |

## Updating Code

To push new code changes from your local machine:

```powershell
scp -r C:\shaneclary\DenseWealth\*.py root@YOUR_SERVER_IP:/opt/densewealth/
ssh root@YOUR_SERVER_IP "chown -R densewealth:densewealth /opt/densewealth && systemctl restart densewealth"
```

## Troubleshooting

```bash
# Check if service is running
systemctl status densewealth

# View recent logs
journalctl -u densewealth --since "10 min ago" --no-pager

# Check Python environment
/opt/densewealth/venv/bin/python --version
/opt/densewealth/venv/bin/pip list

# Test manually (as densewealth user)
cd /opt/densewealth
sudo -u densewealth venv/bin/python main.py

# Restart from scratch
densewealth reset
```

## SSH Key Location

Private key: your default SSH key (e.g. `~/.ssh/id_ed25519`)
