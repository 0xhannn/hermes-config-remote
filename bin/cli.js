#!/usr/bin/env node

/**
 * hermes-remote CLI runner
 * Pure Node.js stdlib (Zero extra npm dependencies)
 */

const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const readline = require('readline');

const args = process.argv.slice(2);

function printHelp() {
  console.log(`
⚡ hermes-remote — Web Control Center for Hermes Agent

Usage:
  npx hermes-remote [options]
  hermes-remote [options]

Options:
  -p, --port <number>    Port to listen on (default: 8093)
  --host <string>        Host interface to bind (default: 0.0.0.0)
  --no-open              Do not automatically open browser
  -h, --help             Show this help menu
  -v, --version          Show version

Default Credentials:
  Master PIN: 123456 (or existing ~/.hermes/.pin_secret)
`);
}

let port = 8093;
let host = '0.0.0.0';
let autoOpen = true;

for (let i = 0; i < args.length; i++) {
  const arg = args[i];
  if (arg === '-h' || arg === '--help') {
    printHelp();
    process.exit(0);
  }
  if (arg === '-v' || arg === '--version') {
    const pkg = require('../package.json');
    console.log(`v${pkg.version}`);
    process.exit(0);
  }
  if (arg === '-p' || arg === '--port') {
    port = parseInt(args[++i], 10) || 8093;
  }
  if (arg === '--host') {
    host = args[++i] || '0.0.0.0';
  }
  if (arg === '--no-open') {
    autoOpen = false;
  }
}

const ROOT_DIR = path.resolve(__dirname, '..');
const API_SCRIPT = path.join(ROOT_DIR, 'server', 'hermes_brain_api.py');
const displayUrl = `http://${host === '0.0.0.0' ? 'localhost' : host}:${port}/hermes/`;

const pkg = require('../package.json');
const CURRENT_VERSION = pkg.version;

// 1. Start Python Backend API
console.log(`⚡ Starting Hermes Remote Control Center...`);

const pyBin = process.platform === 'win32' ? 'python' : 'python3';
const pyProcess = spawn(pyBin, [API_SCRIPT], {
  env: {
    ...process.env,
    HERMES_HOST: host,
    HERMES_PORT: String(port),
    PORT: String(port)
  },
  stdio: 'inherit'
});

pyProcess.on('error', (err) => {
  console.error(`❌ Failed to start backend (${pyBin}): ${err.message}`);
  console.error(`Ensure python is installed and available in your PATH.`);
  process.exit(1);
});

pyProcess.on('exit', (code) => {
  if (code !== 0 && code !== null) {
    console.error(`Backend process exited with code ${code}`);
  }
  process.exit(0);
});

// Clean exit handlers
process.on('SIGINT', () => {
  pyProcess.kill('SIGINT');
  process.exit(0);
});
process.on('SIGTERM', () => {
  pyProcess.kill('SIGTERM');
  process.exit(0);
});

function openBrowser() {
  try {
    let openCmd, openArgs;
    if (process.platform === 'win32') {
      openCmd = 'cmd';
      openArgs = ['/c', 'start', '', displayUrl];
    } else if (process.platform === 'darwin') {
      openCmd = 'open';
      openArgs = [displayUrl];
    } else {
      openCmd = 'xdg-open';
      openArgs = [displayUrl];
    }
    spawn(openCmd, openArgs, { detached: true, stdio: 'ignore' }).unref();
    console.log(`🌐 Browser opened → ${displayUrl}`);
  } catch (_) {
    console.log(`📋 Open manually: ${displayUrl}`);
  }
}

function fetchLatestVersion(cb) {
  https.get('https://registry.npmjs.org/hermes-remote/latest', (res) => {
    let body = '';
    res.on('data', (c) => (body += c));
    res.on('end', () => {
      try { cb(JSON.parse(body).version); } catch (_) { cb(null); }
    });
  }).on('error', () => cb(null));
}

function showMenu() {
  fetchLatestVersion((latest) => {
    const hasUpdate = latest && latest !== CURRENT_VERSION;
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout
    });

    const items = ['Open Web UI', 'Hide to Tray'];
    if (hasUpdate) items.push(`Update (v${latest})`);
    items.push('Exit');

    console.log(`
======================================================
🚀 Hermes Remote UI: \x1b[36m${displayUrl}\x1b[0m
🔑 Default Access PIN: \x1b[33m123456\x1b[0m
======================================================

Pilih menu:`);
    items.forEach((label, i) => console.log(`  [${i + 1}] ${label}`));
    console.log('');

    rl.question('Pilihan: ', (ans) => {
      const idx = parseInt(ans.trim(), 10);
      rl.close();
      if (idx === 1) {
        openBrowser();
        showMenu();
      } else if (idx === 2) {
        console.log(`💤 CLI ditutup. Backend tetap jalan di ${displayUrl}`);
        console.log(`   Untuk berhenti total, tutup proses hermes-remote/python (Ctrl+C / task manager).`);
        // keep backend alive, CLI/menu closed
      } else if (hasUpdate && idx === 3) {
        console.log(`📦 Update tersedia: v${CURRENT_VERSION} → v${latest}`);
        console.log(`   Jalankan: npm install -g hermes-remote@latest`);
        showMenu();
      } else if (idx === items.length) {
        console.log(`👋 Keluar.`);
        pyProcess.kill('SIGINT');
        process.exit(0);
      } else {
        console.log(`❌ Pilihan tidak valid.`);
        showMenu();
      }
    });
  });
}

// Banner + menu
showMenu();
