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

/** POST /worker/createSongAndAssign — create a song and assign recordings to it. */
http.route({
  path: '/worker/createSongAndAssign',
  method: 'POST',
  handler: httpAction(async (ctx, request) => {
    if (!authenticateWorker(request)) {
      return new Response('Unauthorized', { status: 401 });
    }
    const { title, notes, recordingIds } = await request.json();
    const songId = await ctx.runMutation(api.songs.create, {
      title,
      notes
    });
    for (const recordingId of recordingIds) {
      await ctx.runMutation(api.recordings.assignToSong, {
        recordingId,
        songId
      });
    }
    return new Response(JSON.stringify({ ok: true, songId }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  })
});

/** POST /worker/assignToSong — assign a recording to an existing song. */
http.route({
  path: '/worker/assignToSong',
  method: 'POST',
  handler: httpAction(async (ctx, request) => {
    if (!authenticateWorker(request)) {
      return new Response('Unauthorized', { status: 401 });
    }
    const { recordingId, songId } = await request.json();
    await ctx.runMutation(api.recordings.assignToSong, {
      recordingId,
      songId
    });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  })
});

/** POST /worker/listSongs — list all songs for LLM context. */
http.route({
  path: '/worker/listSongs',
  method: 'POST',
  handler: httpAction(async (ctx, request) => {
    if (!authenticateWorker(request)) {
      return new Response('Unauthorized', { status: 401 });
    }
    const songs = await ctx.runQuery(api.songs.list);
    return new Response(JSON.stringify({ songs }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  })
});

/** POST /worker/listUngrouped — list ungrouped recordings. */
http.route({
  path: '/worker/listUngrouped',
  method: 'POST',
  handler: httpAction(async (ctx, request) => {
    if (!authenticateWorker(request)) {
      return new Response('Unauthorized', { status: 401 });
    }
    const recordings = await ctx.runQuery(api.recordings.listUngrouped);
    return new Response(JSON.stringify({ recordings }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  })
});

/** POST /worker/storeMatch — store a riff match result. */
http.route({
  path: '/worker/storeMatch',
  method: 'POST',
  handler: httpAction(async (ctx, request) => {
    if (!authenticateWorker(request)) {
      return new Response('Unauthorized', { status: 401 });
    }
    const body = await request.json();
    await ctx.runMutation(api.riffs.storeMatch, body);
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  })
});

/** POST /worker/getAllRiffs — fetch all riffs for matching. */
http.route({
  path: '/worker/getAllRiffs',
  method: 'POST',
  handler: httpAction(async (ctx, request) => {
    if (!authenticateWorker(request)) {
      return new Response('Unauthorized', { status: 401 });
    }
    const riffs = await ctx.runQuery(api.riffs.listAll);
    return new Response(JSON.stringify({ riffs }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  })
});

/** POST /worker/getRiffsForRecording — fetch riffs for one recording. */
http.route({
  path: '/worker/getRiffsForRecording',
  method: 'POST',
  handler: httpAction(async (ctx, request) => {
    if (!authenticateWorker(request)) {
      return new Response('Unauthorized', { status: 401 });
    }
    const { recordingId } = await request.json();
    const riffs = await ctx.runQuery(api.riffs.listByRecording, {
      recordingId
    });
    return new Response(JSON.stringify({ riffs }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  })
});

/** POST /worker/setSystemWarning — create or update a system warning. */
http.route({
  path: '/worker/setSystemWarning',
  method: 'POST',
  handler: httpAction(async (ctx, request) => {
    if (!authenticateWorker(request)) {
      return new Response('Unauthorized', { status: 401 });
    }
    const { key, message } = await request.json();
    // Upsert: find existing warning by key, update or create
    const existing = await ctx.runQuery(
      api.systemWarnings.getByKey,
      { key }
    );
    if (existing) {
      await ctx.runMutation(api.systemWarnings.update, {
        id: existing._id,
        message,
      });
    } else {
      await ctx.runMutation(api.systemWarnings.create, {
        key,
        message,
      });
    }
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  })
});

export default http;
