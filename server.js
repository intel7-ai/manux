import http from 'node:http';
import https from 'node:https';
import path from 'node:path';
import { createReadStream } from 'node:fs';
import { readFile, realpath, stat } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';

const APP_ROOT = path.dirname(fileURLToPath(import.meta.url));
const MIME = new Map(Object.entries({
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.wasm': 'application/wasm',
  '.task': 'application/octet-stream',
  '.bin': 'application/octet-stream',
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
}));

function inside(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

function send(req, res, status, message, headers = {}) {
  const body = Buffer.from(message);
  res.writeHead(status, {
    'Content-Type': 'text/plain; charset=utf-8',
    'Content-Length': body.length,
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    ...headers,
  });
  res.end(req.method === 'HEAD' ? undefined : body);
}

/** Parse one byte range. Multiple ranges are deliberately unsupported. */
function byteRange(value, size) {
  const match = /^bytes=(\d*)-(\d*)$/.exec(value);
  if (!match || (!match[1] && !match[2]) || size === 0) return null;
  let start;
  let end;
  if (!match[1]) {
    const count = Number(match[2]);
    if (!Number.isSafeInteger(count) || count <= 0) return null;
    start = Math.max(0, size - count);
    end = size - 1;
  } else {
    start = Number(match[1]);
    end = match[2] ? Number(match[2]) : size - 1;
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start >= size || end < start) return null;
    end = Math.min(end, size - 1);
  }
  return { start, end };
}

/** Returns an unbound HTTP or HTTPS server; the launcher chooses its interface. */
export function createServer({ rootDir = APP_ROOT, publicDir = path.join(rootDir, 'public'), tls } = {}) {
  if (tls && (!tls.key || !tls.cert)) throw new Error('TLS requires both a private key and certificate.');
  rootDir = path.resolve(rootDir);
  publicDir = path.resolve(publicDir);
  const handler = async (req, res) => {
    try {
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        send(req, res, 405, 'Method not allowed', { Allow: 'GET, HEAD' });
        return;
      }
      let pathname;
      try {
        // Inspect the original path before URL parsers can normalize dot segments.
        pathname = decodeURIComponent((req.url || '/').split('?')[0]);
      } catch {
        send(req, res, 400, 'Invalid URL encoding');
        return;
      }
      if (!pathname.startsWith('/') || pathname.includes('\\') || pathname.includes('\0') || /%(?:2e|2f|5c|00)/i.test(pathname)) {
        send(req, res, 403, 'Forbidden path');
        return;
      }
      if (pathname.split('/').some(segment => segment.startsWith('.'))) {
        send(req, res, 403, 'Forbidden path');
        return;
      }
      if (pathname === '/health') {
        send(req, res, 200, JSON.stringify({ ok: true, app: 'MANUX', version: '0.2.0' }), { 'Content-Type': 'application/json; charset=utf-8' });
        return;
      }

      let directory = publicDir;
      let relativeName = pathname === '/' ? 'index.html' : pathname.slice(1);
      if (pathname.startsWith('/src/')) {
        directory = path.join(rootDir, 'src');
        relativeName = pathname.slice('/src/'.length);
      } else if (pathname.startsWith('/vendor/')) {
        directory = path.join(rootDir, 'node_modules', '@mediapipe', 'tasks-vision');
        relativeName = pathname.slice('/vendor/'.length);
      }
      const candidate = path.resolve(directory, relativeName);
      if (!inside(directory, candidate)) {
        send(req, res, 403, 'Forbidden path');
        return;
      }
      const contentType = MIME.get(path.extname(candidate).toLowerCase());
      if (!contentType) {
        send(req, res, 404, 'Not found');
        return;
      }
      let filePath;
      let fileStat;
      try {
        const canonicalRoot = await realpath(directory);
        filePath = await realpath(candidate);
        if (!inside(canonicalRoot, filePath)) {
          send(req, res, 403, 'Forbidden path');
          return;
        }
        fileStat = await stat(filePath);
      } catch (error) {
        if (['ENOENT', 'ENOTDIR', 'EACCES', 'EPERM'].includes(error.code)) {
          send(req, res, 404, 'Not found');
          return;
        }
        throw error;
      }
      if (!fileStat.isFile()) {
        send(req, res, 404, 'Not found');
        return;
      }

      const headers = {
        'Content-Type': contentType,
        'Accept-Ranges': 'bytes',
        'Content-Length': fileStat.size,
        'Cache-Control': /\.(wasm|task)$/i.test(filePath) ? 'public, max-age=3600' : 'no-store',
        'X-Content-Type-Options': 'nosniff',
      };
      let range;
      if (req.headers.range !== undefined) {
        range = byteRange(req.headers.range, fileStat.size);
        if (!range) {
          send(req, res, 416, 'Range not satisfiable', { 'Content-Range': `bytes */${fileStat.size}`, 'Accept-Ranges': 'bytes' });
          return;
        }
        headers['Content-Range'] = `bytes ${range.start}-${range.end}/${fileStat.size}`;
        headers['Content-Length'] = range.end - range.start + 1;
      }
      res.writeHead(range ? 206 : 200, headers);
      if (req.method === 'HEAD') {
        res.end();
        return;
      }
      const stream = createReadStream(filePath, range);
      stream.on('error', () => res.destroy());
      res.on('close', () => stream.destroy());
      stream.pipe(res);
    } catch {
      if (!res.headersSent) send(req, res, 500, 'Internal server error');
      else res.destroy();
    }
  };
  return tls ? https.createServer(tls, handler) : http.createServer(handler);
}

/** Defaults remain local-only; LAN access and TLS are explicit launch settings. */
export function readServerConfig(env = process.env) {
  const rawPort = env.PORT ?? '4173';
  const port = Number(rawPort);
  if (!/^\d+$/.test(rawPort) || !Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('PORT must be an integer from 1 to 65535.');
  }
  const host = env.HOST ?? '127.0.0.1';
  if (typeof host !== 'string' || !host.trim() || host !== host.trim() || /[\s/]/.test(host)) {
    throw new Error('HOST must be a non-empty IP address or hostname, without a URL scheme.');
  }
  const keyFile = env.TLS_KEY_FILE;
  const certFile = env.TLS_CERT_FILE;
  if ((keyFile !== undefined || certFile !== undefined) && (!keyFile?.trim() || !certFile?.trim())) {
    throw new Error('TLS_KEY_FILE and TLS_CERT_FILE must both be set to enable HTTPS.');
  }
  return { host, port, keyFile, certFile };
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  try {
    const { host, port, keyFile, certFile } = readServerConfig();
    const tls = keyFile ? { key: await readFile(keyFile), cert: await readFile(certFile) } : undefined;
    const server = createServer({ tls });
    server.on('error', error => {
      console.error(error.code === 'EADDRINUSE' ? `Port ${port} is already in use.` : `Server error: ${error.message}`);
      process.exitCode = 1;
    });
    const urlHost = host.includes(':') ? `[${host}]` : host;
    server.listen(port, host, () => console.log(`MANUX is ready: ${tls ? 'https' : 'http'}://${urlHost}:${port}`));
  } catch (error) {
    console.error(`Could not start MANUX: ${error.message}`);
    process.exitCode = 1;
  }
}
