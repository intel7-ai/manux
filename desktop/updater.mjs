/**
 * Auto-update policy for the packaged Windows app.
 *
 * Pure of Electron: the real `autoUpdater`, timers and dialog are injected so the
 * policy is testable. Invariants:
 *  - Only a packaged win32 run on an opted-in environment checks for updates.
 *  - A failed check never throws into the app; it becomes a status string.
 *  - A downloaded update is installed only after the user approves, and never
 *    while mouse control is on. A relaunch always starts with control OFF
 *    because that is how the desktop shell starts every run.
 */
export const UPDATE_STATES = Object.freeze(['disabled', 'idle', 'checking', 'available', 'downloading', 'downloaded', 'not-available', 'error']);

const DAY_MS = 24 * 60 * 60 * 1000;
const shortMessage = error => String(error?.message ?? error ?? '알 수 없는 오류').split('\n')[0].slice(0, 200);

export function updateDisabledReason({isPackaged, platform, env, buildChannel = 'stable'}) {
  if (!isPackaged) return '개발 실행에서는 자동 업데이트를 사용하지 않습니다.';
  if (platform !== 'win32') return '자동 업데이트는 Windows 설치본에서만 지원합니다.';
  if (buildChannel !== 'stable') return '시험용 앱은 정식 배포 업데이트를 받지 않습니다.';
  if (env?.MANUX_AUTO_UPDATE === '0') return 'MANUX_AUTO_UPDATE=0 설정으로 자동 업데이트가 꺼져 있습니다.';
  return null;
}

export function createUpdateManager({
  loadUpdater, isPackaged, platform = process.platform, env = process.env, buildChannel = 'stable',
  isMouseActive = () => false, promptInstall = async () => false, onStatus = () => {},
  setTimer = setTimeout, clearTimer = clearTimeout, now = Date.now,
  firstCheckDelayMs = 20_000, intervalMs = DAY_MS / 4,
} = {}) {
  const reason = updateDisabledReason({isPackaged, platform, env, buildChannel});
  const enabled = reason === null;
  let updater = null, timer = null, started = false, starting = null, stopped = false;
  let promptedVersion = null, promptPending = false, prompting = false;
  const state = {enabled, state: enabled ? 'idle' : 'disabled', version: null, percent: null,
    error: null, checkedAt: null, reason: reason ?? null};

  const snapshot = () => ({...state});
  const publish = () => { try { onStatus(snapshot()); } catch { /* a status listener must not break updating */ } };
  const set = patch => { Object.assign(state, patch); publish(); };

  async function maybePrompt() {
    if (state.state !== 'downloaded' || prompting || promptedVersion === state.version) return;
    if (isMouseActive()) { promptPending = true; return; }
    promptPending = false; prompting = true; promptedVersion = state.version;
    try {
      if (await promptInstall(state.version) === true) await install();
    } catch (error) {
      set({error: shortMessage(error)});
    } finally { prompting = false; }
  }

  async function install() {
    if (!enabled || stopped || !updater || state.state !== 'downloaded') return {ok: false, ...snapshot(), error: '설치할 업데이트가 준비되지 않았습니다.'};
    if (isMouseActive()) {
      promptPending = true;
      set({error: '마우스 제어를 끈 뒤 설치할 수 있습니다.'});
      return {ok: false, ...snapshot()};
    }
    try {
      // isSilent=false shows the installer; isForceRunAfter=true reopens MANUX with control OFF.
      updater.quitAndInstall(false, true);
      return {ok: true, ...snapshot()};
    } catch (error) {
      set({error: shortMessage(error)});
      return {ok: false, ...snapshot()};
    }
  }

  async function check() {
    if (!enabled || stopped) return snapshot();
    if (!updater) await start();
    // Keep a verified download available even if the network later goes offline.
    if (!updater || ['checking', 'downloading', 'downloaded'].includes(state.state)) return snapshot();
    set({state: 'checking', error: null});
    try {
      await updater.checkForUpdates();
    } catch (error) {
      set({state: 'error', error: shortMessage(error), checkedAt: now()});
    }
    return snapshot();
  }

  function schedule(delay) {
    if (stopped) return;
    if (timer) clearTimer(timer);
    timer = setTimer(async () => { timer = null; await check(); schedule(intervalMs); }, delay);
    timer?.unref?.();
  }

  async function start() {
    if (!enabled || started || stopped) return snapshot();
    if (starting) return starting;
    starting = (async () => {
      try {
      const loaded = await loadUpdater();
      if (stopped) return snapshot();
      updater = loaded;
      updater.autoDownload = true;           // Downloading is not installing.
      updater.autoInstallOnAppQuit = false;  // Never install without an explicit approval.
      updater.allowDowngrade = false;
      updater.allowPrerelease = false;
      updater.on('checking-for-update', () => { if (state.state !== 'downloaded') set({state: 'checking', error: null}); });
      updater.on('update-available', info => set({state: 'downloading', version: info?.version ?? null, percent: 0}));
      updater.on('update-not-available', () => { if (state.state !== 'downloaded') set({state: 'not-available', checkedAt: now(), percent: null}); });
      updater.on('download-progress', progress => {
        if (state.state === 'downloading') set({percent: Number.isFinite(progress?.percent) ? Math.round(progress.percent) : null});
      });
      updater.on('update-downloaded', info => {
        set({state: 'downloaded', version: info?.version ?? state.version, percent: 100, checkedAt: now()});
        void maybePrompt();
      });
      updater.on('error', error => set({state: state.state === 'downloaded' ? 'downloaded' : 'error', error: shortMessage(error), checkedAt: now()}));
      started = true;
      set({state: 'idle', error: null});
      schedule(firstCheckDelayMs);
      } catch (error) {
        updater = null;
        set({state: 'error', error: shortMessage(error)});
        // Failed initialization is retryable, both manually and on the next timer.
        schedule(intervalMs);
      }
      return snapshot();
    })();
    try { return await starting; }
    finally { starting = null; }
  }

  return {
    enabled, start, check, install, status: snapshot,
    /** Call whenever mouse control turns off so a deferred approval prompt can appear. */
    mouseStateChanged() { if (promptPending && !isMouseActive()) void maybePrompt(); },
    stop() { stopped = true; if (timer) clearTimer(timer); timer = null; },
  };
}
