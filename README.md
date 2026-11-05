# Hermes Control Center (Remote Config)

Full-spectrum local-first web control center and system manager for **Hermes Agent**.

## 🚀 Quick Start (NPX / NPM)

### 1. Instant Run via GitHub (No NPM Install Required)
Jalankan langsung tanpa perlu install apa pun:

```bash
npx github:0xhannn/hermes-config-remote
```

### 2. NPM Package (Global Install)
```bash
npm install -g hermes-remote
hermes-remote
```

### 3. Git Clone (Manual Setup)
```bash
git clone https://github.com/0xhannn/hermes-config-remote.git
cd hermes-config-remote
./bin/cli.js
```

---

### ⚙️ CLI Options & Flags

```bash
# Ganti port custom
npx github:0xhannn/hermes-config-remote -p 8090

# Jalankan di VPS / Headless (tanpa otomatis buka browser GUI)
npx github:0xhannn/hermes-config-remote --no-open

# Bind ke semua IP interface VPS
npx github:0xhannn/hermes-config-remote --host 0.0.0.0

# Bantuan lengkap
npx github:0xhannn/hermes-config-remote --help
```

- **Default Master PIN**: `123456` *(atau PIN kustom yang tersimpan di `~/.hermes/.pin_secret`)*.
- **Web UI URL**: `http://localhost:8093/hermes/`
- **Prasyarat**: Node.js 16+ & Python 3.8+ (stdlib bawaan OS).

---

- **Brain Model & Provider Switcher**: Real-time LLM inspection and dynamic model switching with 9Router integration.
- **Persistent Memory & Context Vault**: View, search, and manage long-term persona notes and facts (`USER.md` & `MEMORY.md`).
- **Dynamic Skills Hub**: Real-time procedural skill viewer and manager.
- **Backup & Restore**: One-click local backup archive (`tar.gz`) export & import.
- **Built-in Self Updater**: Git-based version checking and one-click updates directly from the settings panel.
- **Zero-Secret Security**: No hardcoded credentials, PIN-protected admin session management (Default PIN: `123456`).

## Project Structure

```text
hermes-config-remote/
├── index.html            # Main Dashboard Hub
├── auth/                 # Protected Entry Gate
├── brain/                # Model, Provider, & Parameters Monitor
├── context/              # Long-term Memory & Persona Viewer
├── skills/               # Procedural Skills Catalog & Viewer
├── settings/             # System Settings, Backup & Update Center
├── assets/               # CSS styles, Web Audio SFX, Icons
└── server/
    └── hermes_brain_api.py # Python-based Local Management API Daemon
```

## Setup & Running

### 1. Requirements
- Python 3.10+
- PyYAML (`pip install pyyaml`)

### 2. Run Backend Daemon
```bash
python3 server/hermes_brain_api.py
```
Default port: `8093` (configurable via `HERMES_PORT` environment variable).

### 3. Serve Frontend
Serve the repository root using any static web server (Nginx, Caddy, or Python HTTP):
```bash
python3 -m http.server 8080
```

## License
MIT
