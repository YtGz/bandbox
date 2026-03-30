import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { getAudioDataPath } from '$lib/server/env';
import { stat, open } from 'node:fs/promises';
import { join, resolve, extname } from 'node:path';
import { Readable } from 'node:stream';

const MIME_TYPES: Record<string, string> = {
  '.opus': 'audio/opus',
  '.flac': 'audio/flac',
  '.wav': 'audio/wav',
  '.ogg': 'audio/ogg'
};

export const GET: RequestHandler = async ({ params, request }) => {
  const requestedPath = params.path;
  if (!requestedPath) {
    error(400, 'No path specified');
  }

  const audioBase = resolve(getAudioDataPath(), 'processed');
  const filePath = resolve(join(audioBase, requestedPath));

  // Path traversal protection
  if (!filePath.startsWith(audioBase)) {
    error(403, 'Forbidden');
  }

  // Check file exists and get size
  let fileStats;
  try {
    fileStats = await stat(filePath);
  } catch {
    error(404, 'File not found');
  }

  if (!fileStats.isFile()) {
    error(404, 'Not a file');
  }

  const ext = extname(filePath).toLowerCase();
  const contentType = MIME_TYPES[ext] ?? 'application/octet-stream';
  const fileSize = fileStats.size;

  // Handle Range requests for seeking
  const rangeHeader = request.headers.get('Range');

  if (rangeHeader) {
    const match = rangeHeader.match(/bytes=(\d+)-(\d*)/);
    if (!match) {
      error(416, 'Invalid range');
    }

    const start = parseInt(match[1], 10);
    const end = match[2] ? parseInt(match[2], 10) : fileSize - 1;

    if (start >= fileSize || end >= fileSize || start > end) {
      return new Response(null, {
        status: 416,
        headers: {
          'Content-Range': `bytes */${fileSize}`
        }
      });
    }

    const chunkSize = end - start + 1;
    const fileHandle = await open(filePath, 'r');
    const stream = fileHandle.createReadStream({ start, end });
    const webStream = Readable.toWeb(stream) as ReadableStream;

    return new Response(webStream, {
      status: 206,
      headers: {
        'Content-Type': contentType,
        'Content-Length': chunkSize.toString(),
        'Content-Range': `bytes ${start}-${end}/${fileSize}`,
        'Accept-Ranges': 'bytes',
        'Cache-Control': 'public, max-age=31536000, immutable'
      }
    });
  }

  // Full file response
  const fileHandle = await open(filePath, 'r');
  const stream = fileHandle.createReadStream();
  const webStream = Readable.toWeb(stream) as ReadableStream;

  return new Response(webStream, {
    status: 200,
    headers: {
      'Content-Type': contentType,
      'Content-Length': fileSize.toString(),
      'Accept-Ranges': 'bytes',
      'Cache-Control': 'public, max-age=31536000, immutable'
    }
  });
};
