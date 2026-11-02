// Native protocol check: compiles the real Windows helper, but every SendInput is replaced by a dry-run record.
import assert from 'node:assert/strict';
import {once} from 'node:events';
import {MouseBridge} from '../desktop/mouse-bridge.mjs';

if (process.platform !== 'win32') { console.log('Skipped: Windows helper smoke test.'); process.exit(0); }
const bridge = new MouseBridge({dryRun:true});
const applied = [];
bridge.on('applied', event => applied.push(event));
const waitFor = async predicate => {
  const deadline = Date.now() + 5000;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error('Timed out waiting for dry-run mouse input.');
    await new Promise(resolve => setTimeout(resolve, 25));
  }
};
try {
  await bridge.start();
  assert.equal(bridge.status().dryRun, true);
  assert.equal(bridge.status().enabled, false);
  await bridge.setEnabled(true);
  bridge.send([{type:'move',dx:12,dy:-8}, {type:'button',button:'left',down:true}, {type:'scroll',dy:100}]);
  await waitFor(() => applied.length >= 3);
  assert.deepEqual(applied.slice(0,3).map(e => [e.flags,e.dx,e.dy,e.data]), [[1,12,-8,0],[2,0,0,0],[2048,0,0,-120]]);
  // Keep parent timer out of the way: verify the helper's own watchdog releases the drag.
  clearInterval(bridge.timer);
  await waitFor(() => !bridge.status().enabled);
  assert.equal(applied.at(-1).flags, 4);
  assert.equal(bridge.send([{type:'move',dx:10,dy:0}]), false);
  await bridge.setEnabled(true);
  bridge.send([{type:'button',button:'right',down:true}]);
  await waitFor(() => applied.at(-1)?.flags === 8);
  const exited = once(bridge.child, 'exit');
  bridge.child.stdin.end(); // Simulate parent disconnect without a graceful release command.
  await exited;
  assert.equal(applied.at(-1).flags, 16);
  console.log(JSON.stringify({passed:true,method:'real PowerShell/C# helper in dry-run; NO desktop input injected',checks:['native move/button/scroll translation','native watchdog release','cannot resume after lease expiry','EOF releases held button'],nativeEvents:applied.length}));
} finally { bridge.close(); }
