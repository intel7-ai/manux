export function desktopChannel(env = process.env) {
  const channel = env.MANUX_BUILD_CHANNEL || 'stable';
  if (!['stable', 'preview'].includes(channel)) throw new Error('MANUX_BUILD_CHANNEL must be stable or preview.');
  return channel === 'preview'
    ? {channel, appId:'ai.intel7.manux.preview', productName:'MANUX Preview', executableName:'MANUX-Preview', packageName:'manux-preview', artifactName:'MANUX-Preview-Setup-${version}.${ext}'}
    : {channel, appId:'ai.intel7.manux', productName:'MANUX', executableName:'MANUX', packageName:'manux-motion-player', artifactName:'MANUX-Setup-${version}.${ext}'};
}

export function desktopVersion(version, channel) {
  if (!/^\d+\.\d+\.\d+(?:-preview\.[0-9A-Za-z.-]+)?$/.test(version)) throw new Error('Unsupported desktop version.');
  return channel === 'preview' && !version.includes('-preview.') ? `${version}-preview.local` : version;
}
