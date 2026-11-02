# Windows mouse bridge

`npm run desktop` starts the existing MANUX web app inside Electron. The web server binds only to `127.0.0.1:4175`. This stable origin preserves desktop localStorage profiles across launches. Browser-only use remains a practice surface: it does not receive the native bridge, and its profiles are separate from the desktop origin.

`MANUX_DESKTOP_PORT` may select another fixed port from 1 through 65535. If the port is already occupied, startup reports the conflict; it never stops another process or silently changes the origin. Each port has separate localStorage. Tests may explicitly set `MANUX_DESKTOP_PORT=0` for an ephemeral port; that setting does not preserve an origin between launches.

The desktop preload exposes only `getStatus`, `setEnabled`, `send`, `stop`, and `onStatus` as `window.manuxDesktop`. Renderer code has no Node.js access. The main process checks the sender, main frame, origin, event names, types, and numeric limits. Remote navigation, new windows, webviews, and remote resource requests are denied.

Real mouse control starts disabled. The app must explicitly enable it. **Ctrl+Alt+M** stops control even when another app is focused. If the shortcut cannot be registered, native control remains unavailable. Reloading, minimizing, closing, renderer failure, screen locking, and sleep also release control. Switching focus to another app keeps control active so that app can be operated.

## Frame contract

For every fresh camera inference while native control is enabled, call `send(events)`. An empty array is valid: it reports a fresh frame with no movement. Do not send artificial heartbeats from an independent interval when inference is stalled.

```js
[{type:'move', dx:12, dy:-8}]
[{type:'button', button:'left', down:true}]
[{type:'button', button:'left', down:false}]
[{type:'scroll', dy:100}]
[{type:'release'}]
```

Movement is relative in pixels, at most ±100 per axis/event. Scroll uses positive-down deltas, at most ±240/event; the Windows helper reverses its sign and scales 100 units to one 120-unit wheel detent. Only left/right mouse buttons exist in this protocol. Each batch contains at most eight events. It is validated before any member executes.

Each enabled session has a generation. Stop invalidates the generation, and queued events from older sessions are discarded. Events older than 300 ms are rejected. The main process expires input after 1 second without a fresh batch. The native helper independently expires input after 900 ms, releases its held buttons, and rejects new input until explicitly enabled again. End-of-input also releases held buttons if Electron exits unexpectedly.

## Native dependency and limitations

The fixed `windows-mouse.ps1` helper runs hidden with the Windows-provided PowerShell. It compiles the bundled C# source in memory, then uses Windows `SendInput`. It accepts structured mouse data; it never evaluates renderer strings as PowerShell or C#.

No administrator privileges are requested. Windows may reject input aimed at elevated applications or secure desktops. This implementation does not bypass that restriction. When `SendInput` fails, control stops and the UI receives the error. Camera landmark quality, latency, and real user comfort require separate webcam evaluation.

## Verification

```powershell
node --test tests/desktop-mouse.test.js
node scripts/test-desktop-mouse.mjs
```

The second command compiles and runs the real helper with **dry-run enabled**. Instead of calling `SendInput`, it reports native input records. It verifies translation, native watchdog release, explicit re-enable requirements, and button release on EOF without moving or clicking the user's desktop. It does not prove delivery to another desktop application.

For an Electron integration test without OS input, set `MANUX_MOUSE_DRY_RUN=1` before launching Electron; the returned status contains `dryRun:true`.

References: [Electron context isolation](https://www.electronjs.org/docs/latest/tutorial/context-isolation/), [global shortcuts](https://www.electronjs.org/docs/latest/api/global-shortcut), [Windows SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput).
