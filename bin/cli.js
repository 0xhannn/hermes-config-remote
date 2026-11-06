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

function checkUpdate(cb) {
  https.get('https://registry.npmjs.org/hermes-remote/latest', (res) => {
    let body = '';
    res.on('data', (c) => (body += c));
    res.on('end', () => {
      try {
        const latest = JSON.parse(body).version;
        if (latest && latest !== CURRENT_VERSION) {
          console.log(`\n📦 Update tersedia: v${CURRENT_VERSION} → v${latest}`);
          console.log(`   Jalankan: npm install -g hermes-remote@latest`);
        } else {
          console.log(`\n✅ Sudah versi terbaru (v${CURRENT_VERSION})`);
        }
      } catch (_) {
        console.log(`\n⚠️ Gagal cek versi terbaru.`);
      }
      cb();
    });
  }).on('error', () => {
    console.log(`\n⚠️ Gagal cek versi terbaru (no network).`);
    cb();
  });
}

function showMenu() {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout
  });

  console.log(`
======================================================
🚀 Hermes Remote UI: \x1b[36m${displayUrl}\x1b[0m
🔑 Default Access PIN: \x1b[33m123456\x1b[0m
======================================================

Pilih menu:
  [1] Open Web UI
  [2] Hide to Tray
  [3] Update (Jika belum versi terbaru)
  [4] Exit
`);

  rl.question('Pilihan: ', (ans) => {
    const choice = ans.trim();
    if (choice === '1') {
      openBrowser();
      rl.close();
      showMenu();
    } else if (choice === '2') {
      console.log(`💤 Berjalan di background. Tekan Ctrl+C untuk berhenti.`);
      console.log(`   Web UI: ${displayUrl}`);
      rl.close();
      // keep backend alive
    } else if (choice === '3') {
      rl.close();
      checkUpdate(() => showMenu());
    } else if (choice === '4') {
      rl.close();
      console.log(`👋 Keluar.`);
      pyProcess.kill('SIGINT');
      process.exit(0);
    } else {
      rl.close();
      console.log(`❌ Pilihan tidak valid.`);
      showMenu();
    }
  });
}

// Banner + menu
showMenu();
