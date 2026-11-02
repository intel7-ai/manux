/** Permit the local app document, including a fragment used to select its tab. */
export function isAppDocument(url, appOrigin) {
  try {
    const parsed = new URL(url);
    return parsed.origin === appOrigin && parsed.pathname === '/' && !parsed.search && !parsed.username && !parsed.password;
  } catch { return false; }
}

/** Stable production origin keeps desktop localStorage; port zero is test-only opt-in. */
export function readDesktopPort(env = process.env) {
  const raw = env.MANUX_DESKTOP_PORT ?? '4175';
  if (typeof raw !== 'string' || !/^\d+$/.test(raw)) throw new Error('MANUX_DESKTOP_PORT must be an integer from 0 to 65535.');
  const port = Number(raw);
  if (!Number.isSafeInteger(port) || port < 0 || port > 65535) throw new Error('MANUX_DESKTOP_PORT must be an integer from 0 to 65535.');
  return port;
}

export function canRequestCamera(contents, appContents, permission, details, appOrigin) {
  return contents === appContents && details?.isMainFrame === true && isAppDocument(details.requestingUrl, appOrigin)
    && permission === 'media' && Array.isArray(details.mediaTypes) && details.mediaTypes.length > 0
    && details.mediaTypes.every(type => type === 'video');
}

export function canCheckCamera(contents, appContents, permission, requestingOrigin, details, appOrigin) {
  let origin;
  try { origin = new URL(requestingOrigin).origin; } catch { return false; }
  return contents === appContents && origin === appOrigin && details?.isMainFrame === true
    && isAppDocument(details.requestingUrl, appOrigin) && permission === 'media' && details.mediaType === 'video';
}
