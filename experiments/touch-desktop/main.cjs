const { app, BrowserWindow, ipcMain, globalShortcut, powerMonitor, shell } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

let win, worker, quitting = false, lastHeartbeat = 0;
if(!app.requestSingleInstanceLock()){app.quit();}
app.on('second-instance',()=>{if(win){win.restore();win.show();win.focus();}});
const root = __dirname;
const runtime = path.join(root, '.runtime');
fs.mkdirSync(runtime, {recursive:true});
function command(value) {
  if (worker && !worker.killed && worker.stdin.writable) worker.stdin.write(JSON.stringify(value)+'\n');
}
function send(value) { if (win && !win.isDestroyed()) win.webContents.send('engine', value); }
function safeStop() { command({type:'native', enabled:false}); }

app.commandLine.appendSwitch('disable-renderer-backgrounding');
app.whenReady().then(() => {
  win = new BrowserWindow({width:1440,height:940,minWidth:1080,minHeight:740,
    title:'MANUX Touch Desktop',backgroundColor:'#101716',autoHideMenuBar:true,
    webPreferences:{preload:path.join(root,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true,backgroundThrottling:false}});
  win.loadFile(path.join(root,'ui','index.html'));
  win.webContents.setWindowOpenHandler(() => ({action:'deny'}));
  win.webContents.on('will-navigate', (event) => event.preventDefault());
  win.webContents.on('render-process-gone', safeStop);
  win.on('close', () => { quitting = true; command({type:'quit'}); });
  const candidates = [process.env.MANUX_PYTHON,
    path.join(root,'.venv','Scripts','python.exe'),
    path.join(root,'..','manux-dan-personal','.venv','Scripts','python.exe')].filter(Boolean);
  const python = candidates.find(p => fs.existsSync(p));
  if (!python) { win.webContents.once('did-finish-load',() => send({type:'error',message:'Python 환경을 찾지 못했습니다. MANUX_PYTHON을 설정해주세요.'})); return; }
  const log = fs.createWriteStream(path.join(runtime,'engine.log'),{flags:'a'});
  worker = spawn(python,['-u','-m','engine.worker'],{cwd:root,windowsHide:true,
    env:{...process.env,PYTHONIOENCODING:'utf-8'},stdio:['pipe','pipe','pipe']});
  let pending = '';
  worker.stdout.setEncoding('utf8');
  worker.stdout.on('data', data => {
    pending += data;
    let end;
    while ((end = pending.indexOf('\n')) >= 0) {
      const line = pending.slice(0,end); pending = pending.slice(end+1);
      try { send(JSON.parse(line)); } catch (error) { log.write(`Invalid service packet: ${error.message}\n`); }
    }
    if(pending.length>4000000) pending='';
  });
  worker.stderr.pipe(log);
  worker.stdin.on('error',() => {});
  worker.on('error',error => send({type:'error',message:error.message}));
  worker.on('exit',code => { worker=null; if(!quitting)send({type:'error',message:`추적 엔진이 종료되었습니다 (${code}). 앱을 다시 열어주세요.`}); });
  globalShortcut.register('Escape',safeStop);
  globalShortcut.register('CommandOrControl+Alt+M',() => {safeStop();win.show();win.focus();});
  powerMonitor.on('suspend',safeStop);
  powerMonitor.on('lock-screen',safeStop);
});

ipcMain.on('command',(event,value) => {
  if(!win || event.sender!==win.webContents || event.senderFrame!==win.webContents.mainFrame) return;
  if(!value || typeof value!=='object')return;
  const allowed = ['heartbeat','feedback','native','pause','scope','settings','calibrate','camera','save'];
  if(!allowed.includes(value.type))return;
  if(value.type==='heartbeat') lastHeartbeat=Date.now();
  command(value);
});
ipcMain.on('show-records',event => {
  if(win && event.sender===win.webContents) shell.openPath(runtime);
});
app.on('window-all-closed',() => {command({type:'quit'});app.quit();});
app.on('will-quit',() => {globalShortcut.unregisterAll();if(worker)worker.stdin.end();});
