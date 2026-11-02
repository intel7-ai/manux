// Minimal DOM and desktop-bridge doubles for MouseWorkspace. They record calls; no OS input is possible.
function element(selector, values) {
  const classes = new Set();
  return {
    selector, textContent:'', hidden:false, disabled:false, checked:false, dataset:{}, style:{},
    value:values[selector] ?? '', clientWidth:400, clientHeight:240, offsetWidth:40, offsetHeight:40,
    offsetLeft:0, offsetTop:0, scrollTop:0,
    classList:{add:name => classes.add(name), remove:name => classes.delete(name),
      toggle:(name, on) => { (on ?? !classes.has(name)) ? classes.add(name) : classes.delete(name); }},
    addEventListener() {},
    getBoundingClientRect:() => ({left:0, top:0, right:400, bottom:240}),
    click() {},
  };
}

export function workspaceRoot({target = 'practice'} = {}) {
  const values = {'#mouse-target':target, '#mouse-sensitivity':'1800'};
  const cache = new Map();
  return {
    querySelector(selector) {
      if (!cache.has(selector)) cache.set(selector, element(selector, values));
      return cache.get(selector);
    },
    querySelectorAll:() => [],
  };
}

export function fakeBridge() {
  const calls = {send:[], stop:0, setEnabled:[]};
  return {
    calls,
    onStatus() { return () => {}; },
    getStatus:async () => ({available:true, enabled:false}),
    setEnabled:async value => { calls.setEnabled.push(value); return {available:true, enabled:value}; },
    send:events => { calls.send.push(events); return true; },
    stop:() => { calls.stop++; },
  };
}
