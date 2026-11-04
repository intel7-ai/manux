const {contextBridge,ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('manux', {
  command: value => ipcRenderer.send('command',value),
  onEngine: callback => { const listener=(_,value)=>callback(value);ipcRenderer.on('engine',listener);return()=>ipcRenderer.removeListener('engine',listener); },
  showRecords:()=>ipcRenderer.send('show-records')
});
