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

// 1. Start Python Backend API (Port + 1000 or internal)
// If Python is available, spawn hermes_brain_api.py
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

const displayUrl = `http://${host === '0.0.0.0' ? 'localhost' : host}:${port}/hermes/`;
console.log(`\n======================================================`);
console.log(`🚀 Hermes Remote UI: \x1b[36m${displayUrl}\x1b[0m`);
console.log(`🔑 Default Access PIN: \x1b[33m123456\x1b[0m`);
console.log(`======================================================\n`);

if (autoOpen) {
  setTimeout(() => {
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
    } catch (_) {}
  }, 1000);
}
