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

/** List all ungrouped recordings. */
export const listUngrouped = query({
  args: {},
  returns: v.array(v.any()),
  handler: async (ctx) => {
    return await ctx.db
      .query('recordings')
      .withIndex('by_state', (q) => q.eq('state', 'ungrouped'))
      .order('desc')
      .collect();
  }
});

/** List recordings by state. */
export const listByState = query({
  args: {
    state: v.union(
      v.literal('uploading'),
      v.literal('normalizing'),
      v.literal('trimming'),
      v.literal('analyzing'),
      v.literal('grouped'),
      v.literal('ungrouped'),
      v.literal('reprocess')
    )
  },
  returns: v.array(v.any()),
  handler: async (ctx, args) => {
    return await ctx.db
      .query('recordings')
      .withIndex('by_state', (q) => q.eq('state', args.state))
      .collect();
  }
});

/** List all recordings currently being processed. */
export const listProcessing = query({
  args: {},
  returns: v.array(v.any()),
  handler: async (ctx) => {
    const states = [
      'uploading',
      'normalizing',
      'trimming',
      'analyzing'
    ] as const;
    const results = [];
    for (const state of states) {
      const recs = await ctx.db
        .query('recordings')
        .withIndex('by_state', (q) => q.eq('state', state))
        .collect();
      results.push(...recs);
    }
    return results;
  }
});

/**
 * Create a new recording. Returns the new ID, or null if the hash
 * already exists (deduplication).
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
    state: v.union(
      v.literal('uploading'),
      v.literal('normalizing'),
      v.literal('trimming'),
      v.literal('analyzing'),
      v.literal('grouped'),
      v.literal('ungrouped'),
      v.literal('reprocess')
    ),
    pathFlac: v.optional(v.string()),
    pathSong: v.optional(v.string()),
    pathPre: v.optional(v.string()),
    pathPost: v.optional(v.string()),
    cutStartSec: v.optional(v.number()),
    cutEndSec: v.optional(v.number()),
    trimConfidence: v.optional(v.number()),
    trimMethod: v.optional(v.string()),
    transcriptPre: v.optional(v.string()),
    transcriptPost: v.optional(v.string()),
    tempo: v.optional(v.number()),
    dominantKey: v.optional(v.string()),
    durationSec: v.optional(v.number()),
    songId: v.optional(v.id('songs'))
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

/** Schedule a single recording for reprocessing. */
export const scheduleReprocess = mutation({
  args: { recordingId: v.id('recordings') },
  returns: v.null(),
  handler: async (ctx, args) => {
    const recording = await ctx.db.get(args.recordingId);
    if (!recording) throw new Error('Recording not found');
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
