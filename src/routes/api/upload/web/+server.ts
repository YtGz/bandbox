import { error, json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { ConvexHttpClient } from 'convex/browser';
import { api } from '$convex/_generated/api';
import { getConvexUrl, getAudioDataPath } from '$lib/server/env';
import { mkdir, rm, writeFile, rename } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import { Readable, Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { join } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';

const SAFE_FILENAME = /^[\x20-\x7e]+$/;

export const POST: RequestHandler = async ({ request }) => {
  const formData = await request.formData();
  const file = formData.get('file');

  if (!file || !(file instanceof File)) {
    error(400, 'Missing file');
  }

  const filename = file.name;
  if (
    !filename ||
    !SAFE_FILENAME.test(filename) ||
    filename.includes('/') ||
    filename.includes('\\')
  ) {
    error(400, 'Invalid filename');
  }

  const audioPath = getAudioDataPath();
  const incomingDir = join(audioPath, 'incoming');
  await mkdir(incomingDir, { recursive: true });

  const tmpPath = join(incomingDir, `${randomUUID()}.tmp`);

  const hash = createHash('sha256');
  const hashStream = new Transform({
    transform(chunk, _encoding, callback) {
      hash.update(chunk);
      callback(null, chunk);
    }
  });

  try {
    await pipeline(
      Readable.fromWeb(file.stream() as never),
      hashStream,
      createWriteStream(tmpPath)
    );
  } catch (err) {
    await rm(tmpPath, { force: true });
    throw err;
  }

  const fileHash = hash.digest('hex');

  const client = new ConvexHttpClient(getConvexUrl());
  const recordingId = await client.mutation(api.recordings.create, {
    filename,
    fileHash,
    recordedAt: file.lastModified
  });

  if (recordingId === null) {
    await rm(tmpPath, { force: true });
    return json({ status: 'duplicate', message: 'File already uploaded' });
  }

  const finalPath = join(incomingDir, `${recordingId}.wav`);
  await rename(tmpPath, finalPath);

  const manifestsDir = join(incomingDir, 'manifests');
  await mkdir(manifestsDir, { recursive: true });

  const manifest = {
    recordingId,
    filePath: finalPath,
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
