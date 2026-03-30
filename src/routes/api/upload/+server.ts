import { error, json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { ConvexHttpClient } from 'convex/browser';
import { api } from '$convex/_generated/api';
import { getPiApiKey, getConvexUrl, getAudioDataPath } from '$lib/server/env';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

export const POST: RequestHandler = async ({ request }) => {
  // Authenticate via API key
  const apiKey = request.headers.get('X-Api-Key');
  if (!apiKey || apiKey !== getPiApiKey()) {
    error(401, 'Unauthorized');
  }

  // Parse multipart form data
  const formData = await request.formData();
  const file = formData.get('file');
  const fileHash = formData.get('hash');
  const filename = formData.get('filename');

  if (!(file instanceof File)) {
    error(400, 'Missing file in form data');
  }
  if (typeof fileHash !== 'string' || !fileHash) {
    error(400, 'Missing hash in form data');
  }
  if (typeof filename !== 'string' || !filename) {
    error(400, 'Missing filename in form data');
  }

  // Create recording in Convex (checks for duplicates)
  const client = new ConvexHttpClient(getConvexUrl());
  const recordingId = await client.mutation(api.recordings.create, {
    filename,
    fileHash
  });

  if (recordingId === null) {
    return json({ status: 'duplicate', message: 'File already uploaded' });
  }

  // Save WAV file to incoming directory
  const audioPath = getAudioDataPath();
  const incomingDir = join(audioPath, 'incoming');
  const manifestsDir = join(incomingDir, 'manifests');

  await mkdir(incomingDir, { recursive: true });
  await mkdir(manifestsDir, { recursive: true });

  const wavPath = join(incomingDir, `${recordingId}.wav`);
  const arrayBuffer = await file.arrayBuffer();
  await writeFile(wavPath, Buffer.from(arrayBuffer));

  // Write manifest for the Python worker
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
