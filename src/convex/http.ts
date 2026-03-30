import { httpRouter } from 'convex/server';
import { httpAction } from './_generated/server';
import { api } from './_generated/api';

const http = httpRouter();

/** Authenticate the Python worker via X-Worker-Key header. */
function authenticateWorker(request: Request): boolean {
  const key = request.headers.get('X-Worker-Key');
  const expected = process.env.WORKER_API_KEY;
  if (!expected) return false;
  return key === expected;
}

/** POST /worker/updateState — update a recording's state and metadata. */
http.route({
  path: '/worker/updateState',
  method: 'POST',
  handler: httpAction(async (ctx, request) => {
    if (!authenticateWorker(request)) {
      return new Response('Unauthorized', { status: 401 });
    }
    const body = await request.json();
    await ctx.runMutation(api.recordings.updateState, body);
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  })
});

/** POST /worker/storeRiffs — batch insert riffs for a recording. */
http.route({
  path: '/worker/storeRiffs',
  method: 'POST',
  handler: httpAction(async (ctx, request) => {
    if (!authenticateWorker(request)) {
      return new Response('Unauthorized', { status: 401 });
    }
    const body = await request.json();
    await ctx.runMutation(api.riffs.storeBatch, body);
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  })
});

export default http;
