const { app, BrowserWindow, dialog, shell } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const net = require('node:net');
const path = require('node:path');

let backend = null;
let mainWindow = null;
let shuttingDown = false;

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function backendCommand() {
  if (app.isPackaged) {
    const filename = process.platform === 'win32' ? 'brandscope-backend.exe' : 'brandscope-backend';
    return { command: path.join(process.resourcesPath, 'backend', filename), args: [], cwd: process.resourcesPath };
  }
  const python = process.platform === 'win32'
    ? path.join(__dirname, '..', '.venv', 'Scripts', 'python.exe')
    : path.join(__dirname, '..', '.venv', 'bin', 'python');
  return { command: python, args: [path.join(__dirname, '..', 'app.py')], cwd: path.join(__dirname, '..') };
}

function waitForBackend(port, timeoutMs = 45000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const retry = () => {
      if (Date.now() >= deadline) return reject(new Error('后端服务启动超时'));
      setTimeout(check, 300);
    };
    const check = () => {
      const request = http.get(`http://127.0.0.1:${port}/health`, response => {
        response.resume();
        response.statusCode === 200 ? resolve() : retry();
      });
      request.setTimeout(1200, () => request.destroy());
      request.on('error', retry);
    };
    check();
  });
}

function startBackend(port) {
  const dataDir = path.join(app.getPath('userData'), 'data');
  fs.mkdirSync(dataDir, { recursive: true });
  const logPath = path.join(app.getPath('userData'), 'backend.log');
  const log = fs.createWriteStream(logPath, { flags: 'a' });
  const spec = backendCommand();
  backend = spawn(spec.command, spec.args, {
    cwd: spec.cwd,
    windowsHide: true,
    env: {
      ...process.env,
      HOST: '127.0.0.1',
      PORT: String(port),
      BRAND_MONITOR_DATA_DIR: dataDir,
      PYTHONUNBUFFERED: '1'
    },
    stdio: ['ignore', 'pipe', 'pipe']
  });
  if (backend.stdout) backend.stdout.pipe(log);
  if (backend.stderr) backend.stderr.pipe(log);
  backend.on('error', error => log.write(`${error.stack || error}\n`));
  backend.on('exit', code => {
    log.end();
    backend = null;
    if (!shuttingDown && code !== 0) {
      dialog.showErrorBox('BrandScope 后端已停止', `退出代码：${code}\n日志：${logPath}`);
    }
  });
}

async function createWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 940,
    minWidth: 1040,
    minHeight: 720,
    title: 'BrandScope',
    show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true }
  });
  const localOrigin = `http://127.0.0.1:${port}`;
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(localOrigin)) shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(localOrigin)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });
  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.on('closed', () => { mainWindow = null; });
  await mainWindow.loadURL(localOrigin);
}

async function launch() {
  const port = await freePort();
  startBackend(port);
  await waitForBackend(port);
  await createWindow(port);
}

app.whenReady().then(launch).catch(error => {
  dialog.showErrorBox('BrandScope 启动失败', error.message);
  app.quit();
});
app.on('window-all-closed', () => app.quit());
app.on('before-quit', () => {
  shuttingDown = true;
  if (backend) backend.kill('SIGTERM');
});

