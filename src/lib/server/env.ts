import { env } from '$env/dynamic/private';

export function getConvexUrl(): string {
  const url = env.CONVEX_URL;
  if (!url) throw new Error('CONVEX_URL is not set');
  return url;
}

export function getPiApiKey(): string {
  const key = env.PI_API_KEY;
  if (!key) throw new Error('PI_API_KEY is not set');
  return key;
}

export function getAudioDataPath(): string {
  return env.AUDIO_DATA_PATH ?? '/data/audio';
}
