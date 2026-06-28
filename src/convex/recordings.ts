import { v } from 'convex/values';
import { query, mutation } from './_generated/server';

/**
 * How long a recording may stay in a processing state before it's considered
 * stuck. The worker polls every POLL_INTERVAL seconds and normalizes/trims/
 * analyzes can each take a few minutes, so 10 minutes is a safe ceiling —
 * anything older almost certainly means the worker died mid-pipeline.
 */
export const STUCK_THRESHOLD_MS = 10 * 60 * 1000;

/** Processing states for the song and set pipelines. */
const SONG_PROCESSING_STATES = [
  'uploading',
  'normalizing',
  'trimming',
  'analyzing'
] as const;
const SET_PROCESSING_STATES = ['uploading', 'normalizing'] as const;

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

/**
 * List recordings currently being processed, excluding any that have been in a
 * processing state long enough to be considered stuck (worker died). Stuck
 * recordings are surfaced separately via `listStuck` so the UI can offer
 * recovery instead of showing "Analyzing..." forever.
 */
export const listProcessing = query({
  args: {},
  returns: v.array(v.any()),
  handler: async (ctx) => {
    const now = Date.now();
    const results = [];
    for (const state of SONG_PROCESSING_STATES) {
      const recs = await ctx.db
        .query('recordings')
        .withIndex('by_kind_and_state', (q) =>
          q.eq('kind', 'song').eq('state', state)
        )
        .collect();
      results.push(...recs);
    }
    for (const state of SET_PROCESSING_STATES) {
      const recs = await ctx.db
        .query('recordings')
        .withIndex('by_kind_and_state', (q) =>
          q.eq('kind', 'set').eq('state', state)
        )
        .collect();
      results.push(...recs);
    }
    return results.filter((r) => {
      const updatedAt = r.stateUpdatedAt ?? r.uploadedAt ?? now;
      return now - updatedAt < STUCK_THRESHOLD_MS;
    });
  }
});

/**
 * List recordings stuck in a processing state longer than STUCK_THRESHOLD_MS.
 * These are likely orphaned by a worker that died mid-pipeline.
 */
export const listStuck = query({
  args: {},
  returns: v.array(v.any()),
  handler: async (ctx) => {
    const now = Date.now();
    const results = [];
    for (const state of SONG_PROCESSING_STATES) {
      const recs = await ctx.db
        .query('recordings')
        .withIndex('by_kind_and_state', (q) =>
          q.eq('kind', 'song').eq('state', state)
        )
        .collect();
      results.push(...recs);
    }
    for (const state of SET_PROCESSING_STATES) {
      const recs = await ctx.db
        .query('recordings')
        .withIndex('by_kind_and_state', (q) =>
          q.eq('kind', 'set').eq('state', state)
        )
        .collect();
      results.push(...recs);
    }
    return results
      .filter((r) => {
        const updatedAt = r.stateUpdatedAt ?? r.uploadedAt ?? now;
        return now - updatedAt >= STUCK_THRESHOLD_MS;
      })
      .map((r) => {
        const updatedAt = r.stateUpdatedAt ?? r.uploadedAt ?? now;
        return { ...r, stuckForMs: now - updatedAt };
      });
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
      state: 'uploading',
      stateUpdatedAt: Date.now()
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
    pathFull: v.optional(v.string()),
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
    // Stamp the state change time so we can detect stuck recordings
    if (cleanPatch.state !== undefined) {
      cleanPatch.stateUpdatedAt = Date.now();
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
      stateUpdatedAt: Date.now(),
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
      state: 'grouped',
      stateUpdatedAt: Date.now()
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

    await ctx.db.patch(args.recordingId, {
      state: 'reprocess',
      stateUpdatedAt: Date.now()
    });
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

        await ctx.db.patch(rec._id, {
          state: 'reprocess',
          stateUpdatedAt: Date.now()
        });
        count++;
      }
    }
    return count;
  }
});

/**
 * Recover a single stuck recording. Called by the UI when a user taps
 * "Recover" on a recording that has been in a processing state too long.
 *
 * - Song with a FLAC: move to `reprocess` so the worker re-runs analysis
 *   from the existing normalized FLAC (deletes stale riffs first).
 * - Song without a FLAC (stuck in uploading/normalizing before normalize
 *   ran): move to `ungrouped` so it's visible and the user can delete it.
 * - Set with a FLAC: move to `ready` (sets have no analysis step); without a
 *   FLAC, leave as-is — there's nothing to show.
 *
 * Returns the new state so the caller can confirm what happened.
 */
export const recoverRecording = mutation({
  args: { recordingId: v.id('recordings') },
  returns: v.string(),
  handler: async (ctx, args) => {
    const recording = await ctx.db.get(args.recordingId);
    if (!recording) throw new Error('Recording not found');

    const isProcessingState =
      (recording.kind === 'song' &&
        SONG_PROCESSING_STATES.includes(
          recording.state as (typeof SONG_PROCESSING_STATES)[number]
        )) ||
      (recording.kind === 'set' &&
        SET_PROCESSING_STATES.includes(
          recording.state as (typeof SET_PROCESSING_STATES)[number]
        ));

    if (!isProcessingState) {
      throw new Error('Recording is not in a processing state');
    }

    if (recording.kind === 'song') {
      if (recording.pathFlac) {
        const riffs = await ctx.db
          .query('riffs')
          .withIndex('by_recording', (q) =>
            q.eq('recordingId', args.recordingId)
          )
          .collect();
        for (const riff of riffs) {
          await ctx.db.delete(riff._id);
        }
        await ctx.db.patch(args.recordingId, {
          state: 'reprocess',
          stateUpdatedAt: Date.now()
        });
        return 'reprocess';
      }
      await ctx.db.patch(args.recordingId, {
        state: 'ungrouped',
        stateUpdatedAt: Date.now()
      });
      return 'ungrouped';
    }

    // Set recording
    if (recording.pathFlac) {
      await ctx.db.patch(args.recordingId, {
        state: 'ready',
        stateUpdatedAt: Date.now()
      });
      return 'ready';
    }
    throw new Error('Set recording has no FLAC — cannot recover');
  }
});

/**
 * Recover all stuck recordings in one shot. Called by the worker on startup
 * to clear any recordings orphaned by a previous crash. Returns the count of
 * recordings recovered.
 */
export const recoverStuck = mutation({
  args: {},
  returns: v.number(),
  handler: async (ctx) => {
    const now = Date.now();
    const results = [];
    for (const state of SONG_PROCESSING_STATES) {
      const recs = await ctx.db
        .query('recordings')
        .withIndex('by_kind_and_state', (q) =>
          q.eq('kind', 'song').eq('state', state)
        )
        .collect();
      results.push(...recs);
    }
    for (const state of SET_PROCESSING_STATES) {
      const recs = await ctx.db
        .query('recordings')
        .withIndex('by_kind_and_state', (q) =>
          q.eq('kind', 'set').eq('state', state)
        )
        .collect();
      results.push(...recs);
    }

    let count = 0;
    for (const rec of results) {
      const updatedAt = rec.stateUpdatedAt ?? rec.uploadedAt ?? now;
      if (now - updatedAt < STUCK_THRESHOLD_MS) continue;

      if (rec.kind === 'song') {
        if (rec.pathFlac) {
          const riffs = await ctx.db
            .query('riffs')
            .withIndex('by_recording', (q) => q.eq('recordingId', rec._id))
            .collect();
          for (const riff of riffs) {
            await ctx.db.delete(riff._id);
          }
          await ctx.db.patch(rec._id, {
            state: 'reprocess',
            stateUpdatedAt: Date.now()
          });
        } else {
          await ctx.db.patch(rec._id, {
            state: 'ungrouped',
            stateUpdatedAt: Date.now()
          });
        }
      } else if (rec.pathFlac) {
        await ctx.db.patch(rec._id, {
          state: 'ready',
          stateUpdatedAt: Date.now()
        });
      } else {
        // No FLAC and no way forward — skip, recoverRecording can still handle
        // individual ones from the UI if needed.
        continue;
      }
      count++;
    }
    return count;
  }
});

/**
 * Permanently delete a recording and its riffs. Intended for stuck recordings
 * that have no recoverable audio (e.g. stuck in uploading with no FLAC).
 */
export const deleteRecording = mutation({
  args: { recordingId: v.id('recordings') },
  returns: v.null(),
  handler: async (ctx, args) => {
    const riffs = await ctx.db
      .query('riffs')
      .withIndex('by_recording', (q) => q.eq('recordingId', args.recordingId))
      .collect();
    for (const riff of riffs) {
      await ctx.db.delete(riff._id);
    }
    await ctx.db.delete(args.recordingId);
    return null;
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
