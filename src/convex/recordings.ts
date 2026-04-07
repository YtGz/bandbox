import { v } from 'convex/values';
import { query, mutation } from './_generated/server';

/** Get a single recording by ID. */
export const get = query({
  args: { recordingId: v.id('recordings') },
  returns: v.any(),
  handler: async (ctx, args) => {
    return await ctx.db.get(args.recordingId);
  }
});

/** List all ungrouped song recordings. */
export const listUngrouped = query({
  args: {},
  returns: v.array(v.any()),
  handler: async (ctx) => {
    return await ctx.db
      .query('recordings')
      .withIndex('by_kind_and_state', (q) =>
        q.eq('kind', 'song').eq('state', 'ungrouped')
      )
      .order('desc')
      .collect();
  }
});

/** List song recordings by state. */
export const listByState = query({
  args: {
    kind: v.union(v.literal('song'), v.literal('set')),
    state: v.union(
      v.literal('uploading'),
      v.literal('normalizing'),
      v.literal('trimming'),
      v.literal('analyzing'),
      v.literal('grouped'),
      v.literal('ungrouped'),
      v.literal('reprocess'),
      v.literal('ready')
    )
  },
  returns: v.array(v.any()),
  handler: async (ctx, args) => {
    return await ctx.db
      .query('recordings')
      .withIndex('by_kind_and_state', (q) =>
        q.eq('kind', args.kind).eq('state', args.state)
      )
      .collect();
  }
});

/** List all recordings currently being processed (both song and set pipelines). */
export const listProcessing = query({
  args: {},
  returns: v.array(v.any()),
  handler: async (ctx) => {
    const songStates = [
      'uploading',
      'normalizing',
      'trimming',
      'analyzing'
    ] as const;
    const setStates = ['uploading', 'normalizing'] as const;
    const results = [];
    for (const state of songStates) {
      const recs = await ctx.db
        .query('recordings')
        .withIndex('by_kind_and_state', (q) =>
          q.eq('kind', 'song').eq('state', state)
        )
        .collect();
      results.push(...recs);
    }
    for (const state of setStates) {
      const recs = await ctx.db
        .query('recordings')
        .withIndex('by_kind_and_state', (q) =>
          q.eq('kind', 'set').eq('state', state)
        )
        .collect();
      results.push(...recs);
    }
    return results;
  }
});

/** List all set recordings in ready state. */
export const listSets = query({
  args: {},
  returns: v.array(v.any()),
  handler: async (ctx) => {
    return await ctx.db
      .query('recordings')
      .withIndex('by_kind_and_state', (q) =>
        q.eq('kind', 'set').eq('state', 'ready')
      )
      .order('desc')
      .collect();
  }
});

/**
 * Create a new recording. Returns the new ID, or null if the hash
 * already exists (deduplication).
 *
 * Defaults to kind: 'song'. The worker reclassifies to 'set' after
 * checking duration (>= 17 min threshold).
 */
export const create = mutation({
  args: {
    filename: v.string(),
    fileHash: v.string()
  },
  returns: v.union(v.id('recordings'), v.null()),
  handler: async (ctx, args) => {
    const existing = await ctx.db
      .query('recordings')
      .withIndex('by_hash', (q) => q.eq('fileHash', args.fileHash))
      .first();
    if (existing) return null;

    return await ctx.db.insert('recordings', {
      kind: 'song',
      filename: args.filename,
      fileHash: args.fileHash,
      uploadedAt: Date.now(),
      state: 'uploading'
    });
  }
});

/** Update recording state and optional metadata fields. Used by the Python worker. */
export const updateState = mutation({
  args: {
    recordingId: v.id('recordings'),
    state: v.string(),
    pathFlac: v.optional(v.string()),
    pathSong: v.optional(v.string()),
    pathPre: v.optional(v.string()),
    pathPost: v.optional(v.string()),
    pathOpus: v.optional(v.string()),
    cutStartSec: v.optional(v.number()),
    cutEndSec: v.optional(v.number()),
    trimConfidence: v.optional(v.number()),
    trimMethod: v.optional(v.string()),
    transcriptPre: v.optional(v.string()),
    transcriptPost: v.optional(v.string()),
    tempo: v.optional(v.number()),
    dominantKey: v.optional(v.string()),
    durationSec: v.optional(v.number()),
    songId: v.optional(v.id('songs')),
    setId: v.optional(v.id('sets'))
  },
  returns: v.null(),
  handler: async (ctx, args) => {
    const { recordingId, ...patch } = args;
    // Strip undefined values so we only patch what's provided
    const cleanPatch: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(patch)) {
      if (value !== undefined) {
        cleanPatch[key] = value;
      }
    }
    await ctx.db.patch(recordingId, cleanPatch);
    return null;
  }
});

/**
 * Classify a recording as a set. Called by the worker after duration check.
 * Replaces the document with set-specific fields.
 */
export const classifyAsSet = mutation({
  args: {
    recordingId: v.id('recordings'),
    durationSec: v.number()
  },
  returns: v.null(),
  handler: async (ctx, args) => {
    const recording = await ctx.db.get(args.recordingId);
    if (!recording) throw new Error('Recording not found');

    // Replace the document: keep common fields, switch to set kind
    await ctx.db.replace(args.recordingId, {
      kind: 'set',
      filename: recording.filename,
      fileHash: recording.fileHash,
      uploadedAt: recording.uploadedAt,
      state: 'normalizing',
      durationSec: args.durationSec
    });
    return null;
  }
});

/** Assign a recording to a song. Logs a correction if it was previously assigned. */
export const assignToSong = mutation({
  args: {
    recordingId: v.id('recordings'),
    songId: v.id('songs')
  },
  returns: v.null(),
  handler: async (ctx, args) => {
    const recording = await ctx.db.get(args.recordingId);
    if (!recording) throw new Error('Recording not found');
    if (recording.kind !== 'song')
      throw new Error('Cannot assign a set recording to a song');

    // Log correction if reassigning
    if (recording.songId !== undefined || recording.state === 'ungrouped') {
      await ctx.db.insert('corrections', {
        recordingId: args.recordingId,
        fromSongId: recording.songId,
        toSongId: args.songId,
        correctedAt: Date.now()
      });
    }

    await ctx.db.patch(args.recordingId, {
      songId: args.songId,
      state: 'grouped'
    });
    return null;
  }
});

/** Undo trim — saves original cut points and clears them. */
export const undoTrim = mutation({
  args: { recordingId: v.id('recordings') },
  returns: v.null(),
  handler: async (ctx, args) => {
    const recording = await ctx.db.get(args.recordingId);
    if (!recording) throw new Error('Recording not found');
    if (recording.kind !== 'song') throw new Error('Sets do not have trims');

    await ctx.db.patch(args.recordingId, {
      savedCutStartSec: recording.cutStartSec,
      savedCutEndSec: recording.cutEndSec,
      cutStartSec: undefined,
      cutEndSec: undefined,
      trimMethod: undefined
    });
    return null;
  }
});

/** Restore a previously undone trim. */
export const restoreTrim = mutation({
  args: { recordingId: v.id('recordings') },
  returns: v.null(),
  handler: async (ctx, args) => {
    const recording = await ctx.db.get(args.recordingId);
    if (!recording) throw new Error('Recording not found');
    if (recording.kind !== 'song') throw new Error('Sets do not have trims');
    if (
      recording.savedCutStartSec === undefined ||
      recording.savedCutEndSec === undefined
    ) {
      throw new Error('No saved trim to restore');
    }

    await ctx.db.patch(args.recordingId, {
      cutStartSec: recording.savedCutStartSec,
      cutEndSec: recording.savedCutEndSec,
      savedCutStartSec: undefined,
      savedCutEndSec: undefined
    });
    return null;
  }
});

/** Schedule a single song recording for reprocessing. */
export const scheduleReprocess = mutation({
  args: { recordingId: v.id('recordings') },
  returns: v.null(),
  handler: async (ctx, args) => {
    const recording = await ctx.db.get(args.recordingId);
    if (!recording) throw new Error('Recording not found');
    if (recording.kind !== 'song')
      throw new Error('Sets cannot be reprocessed through the song pipeline');
    if (!recording.pathFlac) throw new Error('No FLAC file — cannot reprocess');

    // Delete existing riffs for this recording (will be re-extracted)
    const riffs = await ctx.db
      .query('riffs')
      .withIndex('by_recording', (q) => q.eq('recordingId', args.recordingId))
      .collect();
    for (const riff of riffs) {
      await ctx.db.delete(riff._id);
    }

    await ctx.db.patch(args.recordingId, { state: 'reprocess' });
    return null;
  }
});

/** Schedule all recordings with processing flags for reprocessing. */
export const scheduleReprocessFlagged = mutation({
  args: {},
  returns: v.number(),
  handler: async (ctx) => {
    const all = await ctx.db.query('recordings').collect();
    let count = 0;
    for (const rec of all) {
      if (
        rec.processingFlags &&
        Array.isArray(rec.processingFlags) &&
        rec.processingFlags.length > 0 &&
        rec.pathFlac
      ) {
        // Delete existing riffs
        const riffs = await ctx.db
          .query('riffs')
          .withIndex('by_recording', (q) => q.eq('recordingId', rec._id))
          .collect();
        for (const riff of riffs) {
          await ctx.db.delete(riff._id);
        }

        await ctx.db.patch(rec._id, { state: 'reprocess' });
        count++;
      }
    }
    return count;
  }
});

/** Set processing quality flags on a recording. */
export const setProcessingFlags = mutation({
  args: {
    recordingId: v.id('recordings'),
    flags: v.array(v.string())
  },
  returns: v.null(),
  handler: async (ctx, args) => {
    await ctx.db.patch(args.recordingId, {
      processingFlags: args.flags
    });
    return null;
  }
});

/** List recordings with processing quality flags. */
export const listFlagged = query({
  args: {},
  returns: v.array(v.any()),
  handler: async (ctx) => {
    const all = await ctx.db.query('recordings').collect();
    return all.filter(
      (r) =>
        r.processingFlags &&
        Array.isArray(r.processingFlags) &&
        r.processingFlags.length > 0
    );
  }
});
