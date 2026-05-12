import { error, json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { ConvexHttpClient } from 'convex/browser';
import { api } from '$convex/_generated/api';
import { getPiApiKey, getConvexUrl, getAudioDataPath } from '$lib/server/env';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { join } from 'node:path';

// Allow any printable ASCII except path separators and control bytes —
// keeps clever filenames from escaping the staging dir.
const SAFE_FILENAME = /^[\x20-\x7e]+$/;

export const POST: RequestHandler = async ({ request }) => {
  // ── 1. Authenticate ───────────────────────────────────────
  const apiKey = request.headers.get('X-Api-Key');
  if (!apiKey || apiKey !== getPiApiKey()) {
    error(401, 'Unauthorized');
  }

  // ── 2. Read metadata from headers (no multipart) ──────────
  //
  // The Pi streams the raw WAV in the request body. Multipart would
  // force SvelteKit to buffer the whole thing through `formData()` —
  // unworkable for the 2–4 GB recordings a long rehearsal produces.
  const fileHash = request.headers.get('X-File-Hash');
  const filename = request.headers.get('X-Filename');

  if (!fileHash || !/^[0-9a-f]{64}$/i.test(fileHash)) {
    error(400, 'Missing or invalid X-File-Hash header (expected SHA-256 hex)');
  }
  if (!filename || !SAFE_FILENAME.test(filename) || filename.includes('/') || filename.includes('\\')) {
    error(400, 'Missing or invalid X-Filename header');
  }
  if (!request.body) {
    error(400, 'Missing request body');
  }

  // ── 3. Create recording in Convex (also dedups) ───────────
  const client = new ConvexHttpClient(getConvexUrl());
  const recordingId = await client.mutation(api.recordings.create, {
    filename,
    fileHash
  });

  if (recordingId === null) {
    // Drain the body so the Pi doesn't see a connection reset before
    // it finishes sending — that surfaces as "Broken pipe" up there.
    try {
      // @ts-expect-error - Web ReadableStream is iterable on Bun/Node 22+
      for await (const _ of request.body) {
        /* discard */
      }
    } catch {
      /* ignore */
    }
    return json({ status: 'duplicate', message: 'File already uploaded' });
  }

  // ── 4. Stream body to disk ────────────────────────────────
  const audioPath = getAudioDataPath();
  const incomingDir = join(audioPath, 'incoming');
  const manifestsDir = join(incomingDir, 'manifests');

  await mkdir(incomingDir, { recursive: true });
  await mkdir(manifestsDir, { recursive: true });

  const wavPath = join(incomingDir, `${recordingId}.wav`);

  try {
    await pipeline(
      // Readable.fromWeb adapts the request's Web ReadableStream into a
      // Node Readable; pipeline() handles back-pressure and cleanup.
      Readable.fromWeb(request.body as never),
      createWriteStream(wavPath)
    );
  } catch (err) {
    // Don't leave a half-written WAV on disk for the worker to choke on.
    await rm(wavPath, { force: true });
    throw err;
  }

  // ── 5. Write manifest for the Python worker ───────────────
  const manifest = {
    recordingId,
    filePath: wavPath,
    filename,
    fileHash
  };
  const manifestPath = join(manifestsDir, `${recordingId}.json`);
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2));

  return json({
    status: 'accepted',
    recordingId,
    message: 'Upload received, processing will begin shortly'
  });
};
