import {pathToFileURL} from 'node:url';

// Local, lockfile-pinned dependency is the default on every teammate's PC and CI.
// An explicit override remains useful for managed/offline development machines.
export async function loadPlaywright() {
  return process.env.PLAYWRIGHT_MODULE
    ? import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href)
    : import('playwright');
}

export function browserOptions(options = {}) {
  return {
    headless: true,
    ...(process.env.PLAYWRIGHT_CHANNEL ? {channel: process.env.PLAYWRIGHT_CHANNEL} : {}),
    ...options,
  };
}
