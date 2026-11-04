const {contextBridge, ipcRenderer} = require('electron');

// The overlay page can only receive frames. It cannot send mouse input or call any other IPC.
contextBridge.exposeInMainWorld('manuxOverlay', {
  onFrame: callback => {
    if (typeof callback !== 'function') throw new TypeError('callback must be a function');
    ipcRenderer.on('manux:overlay-frame', (_event, frame) => callback(frame));
  },
});
