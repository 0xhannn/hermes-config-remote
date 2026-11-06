#!/usr/bin/env node

/**
 * hermes-remote CLI runner — flow mengikuti 9router:
 * spawn backend detached → waitServerReady → menu loop → hide spawn ulang --tray
 * Pure Node.js stdlib (zero extra npm dependencies)
 */

const http = require('http');
const https = require('https');
const path = require('path');
const net = require('net');
const { spawn } = require('child_process');
const readline = require('readline');

const args = process.argv.slice(2);

let port = 8093;
let host = '0.0.0.0';
let trayMode = false;
let skipUpdate = false;

for (let i = 0; i < args.length; i++) {
  const arg = args[i];
  if (arg === '-h' || arg === '--help') {
    console.log(`
⚡ hermes-remote — Web Control Center for Hermes Agent

Usage:
  hermes-remote [options]

Options:
  -p, --port <number>    Port to listen on (default: 8093)
  --host <string>        Host interface to bind (default: 0.0.0.0)
  -t, --tray             Run in background (tray) mode
  --skip-update          Skip update check
  -h, --help             Show this help menu
  -v, --version          Show version

Default Credentials:
  Master PIN: 123456 (or existing ~/.hermes/.pin_secret)
`);
    process.exit(0);
  }
  if (arg === '-v' || arg === '--version') {
    console.log(`v${require('../package.json').version}`);
    process.exit(0);
  }
  if (arg === '-p' || arg === '--port') port = parseInt(args[++i], 10) || 8093;
  if (arg === '--host') host = args[++i] || '0.0.0.0';
  if (arg === '-t' || arg === '--tray') { trayMode = true; process.env.TRAY_MODE = '1'; }
  if (arg === '--skip-update') skipUpdate = true;
}

const pkg = require('../package.json');
const CURRENT_VERSION = pkg.version;
const ROOT_DIR = path.resolve(__dirname, '..');
const API_SCRIPT = path.join(ROOT_DIR, 'server', 'hermes_brain_api.py');
const displayUrl = `http://${host === '0.0.0.0' ? 'localhost' : host}:${port}/hermes/`;

// Poll until server accepts TCP, or timeout
function waitServerReady(timeoutMs, intervalMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve) => {
    const tryConnect = () => {
      const s = net.connect({ host: '127.0.0.1', port }, () => { s.destroy(); resolve(true); });
      s.on('error', () => {
        s.destroy();
        if (Date.now() >= deadline) return resolve(false);
        setTimeout(tryConnect, intervalMs);
      });
    };
    tryConnect();
  });
}

// CREATE_NO_WINDOW (0x08000000) — mencegah console window baru di Windows
const NO_WINDOW = process.platform === 'win32' ? 0x08000000 : 0;

function spawnBackend(detached) {
  const pyBin = process.platform === 'win32' ? 'python' : 'python3';
  return spawn(pyBin, [API_SCRIPT], {
    env: { ...process.env, HERMES_HOST: host, HERMES_PORT: String(port), PORT: String(port) },
    detached: !!detached,
    stdio: detached ? 'ignore' : 'inherit',
    windowsHide: true,
    creationFlags: NO_WINDOW
  });
}

function openBrowser() {
  const url = displayUrl;
  try {
    let cmd, cmdArgs;
    if (process.platform === 'win32') { cmd = 'cmd'; cmdArgs = ['/c', 'start', '', url]; }
    else if (process.platform === 'darwin') { cmd = 'open'; cmdArgs = [url]; }
    else { cmd = 'xdg-open'; cmdArgs = [url]; }
    spawn(cmd, cmdArgs, { detached: true, stdio: 'ignore' }).unref();
    console.log(`🌐 Browser opened → ${url}`);
  } catch (_) {
    console.log(`📋 Open manually: ${url}`);
  }
}

function pause(prompt) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    rl.question(prompt, () => { rl.close(); resolve(); });
  });
}

function checkForUpdate() {
  return new Promise((resolve) => {
    if (skipUpdate) return resolve(null);
    let resolved = false;
    const safety = setTimeout(() => { if (!resolved) { resolved = true; resolve(null); } }, 8000);
    const done = (v) => { if (!resolved) { resolved = true; clearTimeout(safety); resolve(v); } };
    const req = https.get('https://registry.npmjs.org/hermes-remote/latest', { timeout: 3000 }, (res) => {
      let data = '';
      res.on('data', (c) => (data += c));
      res.on('end', () => {
        try {
          const latest = JSON.parse(data);
          if (latest.version && latest.version !== CURRENT_VERSION) done(latest.version);
          else done(null);
        } catch (_) { done(null); }
      });
    });
    req.on('error', () => done(null));
    req.on('timeout', () => { req.destroy(); done(null); });
  });
}

function cleanupServer(server) {
  try { if (server && server.pid) server.kill('SIGKILL'); } catch (_) {}
  try { if (server && server.pid) process.kill(-server.pid, 'SIGKILL'); } catch (_) {}
}

async function main() {
  if (trayMode) {
    // Background mode: spawn backend, keep alive, no menu.
    const backend = spawnBackend(true);
    backend.unref();
    console.log(`🚀 hermes-remote v${CURRENT_VERSION} (background)`);
    console.log(`Server: ${displayUrl}`);
    console.log(`💡 Backend running. Close this terminal window safely; it stays alive.`);
    // Keep event loop alive
    setInterval(() => {}, 1000);
    return;
  }

  // Foreground: spawn backend as child of THIS process (non-detached) so we control it.
  const server = spawnBackend(false);

  server.on('error', (err) => {
    console.error(`❌ Failed to start backend: ${err.message}`);
    process.exit(1);
  });

  const updatePromise = checkForUpdate();

  await waitServerReady(15000, 150);

  const latestVersion = await updatePromise;

  while (true) {
    const items = ['Open Web UI', 'Hide to Tray'];
    if (latestVersion) items.push(`Update (v${latestVersion})`);
    items.push('Exit');

    console.log(`
======================================================
🚀 Hermes Remote UI: \x1b[36m${displayUrl}\x1b[0m
🔑 Default Access PIN: \x1b[33m123456\x1b[0m
======================================================

Pilih menu:`);
    items.forEach((label, i) => console.log(`  [${i + 1}] ${label}`));

    const choice = await new Promise((resolve) => {
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
      rl.question('\nPilihan: ', (ans) => { rl.close(); resolve(parseInt(ans.trim(), 10)); });
    });

    if (choice === 1) {
      openBrowser();
      await pause('\nPress Enter to go back to menu...');
    } else if (choice === 2) {
      // Hide to Tray: spawn ulang --tray detached, kill backend lama, exit → terminal balik.
      console.log('\n⏳ Starting background process...');
      const bg = spawn(process.execPath, [__filename, '--tray', '--skip-update', '-p', String(port)], {
        detached: true,
        stdio: 'ignore',
        windowsHide: true,
        creationFlags: NO_WINDOW,
        env: { ...process.env }
      });
      bg.unref();
      console.log(`🔔 hermes-remote running in background (PID: ${bg.pid})`);
      console.log(`   Server: ${displayUrl}`);
      console.log(`\n💡 You can close this terminal. Backend stays alive.\n`);
      cleanupServer(server);
      process.exit(0);
    } else if (latestVersion && choice === 3) {
      console.log(`\n⬆ Update v${CURRENT_VERSION} → v${latestVersion}`);
      console.log(`Run after exit:\n`);
      console.log(`   npm install -g hermes-remote@latest\n`);
      cleanupServer(server);
      process.exit(0);
    } else if (choice === items.length) {
      console.log(`\n👋 Keluar.`);
      cleanupServer(server);
      process.exit(0);
    } else {
      console.log('❌ Pilihan tidak valid.');
    }
  }
}

main();
