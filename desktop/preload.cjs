const {contextBridge, ipcRenderer} = require('electron');

let generation = -1;
let enabled = false;
let revision = 0;
const apply = status => {
  if (status.generation >= generation) {
    generation = status.generation;
    enabled = Boolean(status.enabled);
  }
  return status;
};
ipcRenderer.on('manux:mouse-status', (_event, status) => apply(status));

contextBridge.exposeInMainWorld('manuxDesktop', {
  getStatus: async () => apply(await ipcRenderer.invoke('manux:mouse-status')),
  setEnabled: async value => {
    if (typeof value !== 'boolean') throw new TypeError('enabled must be boolean');
    const request = ++revision;
    if (!value) enabled = false;
    const status = await ipcRenderer.invoke('manux:mouse-enable', value);
    if (request === revision) apply(status);
    return status;
  },
  send: events => {
    if (enabled) ipcRenderer.send('manux:mouse-input', {events, generation, sentAt:Date.now()});
  },
  stop: () => { ++revision; enabled = false; ipcRenderer.send('manux:mouse-stop'); },
  onStatus: callback => {
    if (typeof callback !== 'function') throw new TypeError('callback must be a function');
    const handler = (_event, status) => callback(apply(status));
    ipcRenderer.on('manux:mouse-status', handler);
    return () => ipcRenderer.removeListener('manux:mouse-status', handler);
  },
});
