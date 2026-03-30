import { v } from 'convex/values';
import { query, mutation } from './_generated/server';

/** Get all riffs across all recordings. Used by the worker for batch matching. */
export const listAll = query({
  args: {},
  returns: v.array(v.any()),
  handler: async (ctx) => {
    return await ctx.db.query('riffs').collect();
  }
});

/** Get all riffs for a recording. */
export const listByRecording = query({
  args: { recordingId: v.id('recordings') },
  returns: v.array(v.any()),
  handler: async (ctx, args) => {
    return await ctx.db
      .query('riffs')
      .withIndex('by_recording', (q) => q.eq('recordingId', args.recordingId))
      .collect();
  }
});

/** Batch insert riffs for a recording. Used by the Python worker. */
export const storeBatch = mutation({
  args: {
    recordingId: v.id('recordings'),
    riffs: v.array(
      v.object({
        riffIndex: v.number(),
        startSec: v.number(),
        endSec: v.number(),
        tempo: v.optional(v.number()),
        fingerprint: v.any(),
        contour: v.optional(v.any())
      })
    )
  },
  returns: v.null(),
  handler: async (ctx, args) => {
    for (const riff of args.riffs) {
      await ctx.db.insert('riffs', {
        recordingId: args.recordingId,
        ...riff
      });
    }
    return null;
  }
});

/** Store a riff match result. */
export const storeMatch = mutation({
  args: {
    riffAId: v.id('riffs'),
    riffBId: v.id('riffs'),
    score: v.number(),
    breakdown: v.any()
  },
  returns: v.id('riffMatches'),
  handler: async (ctx, args) => {
    return await ctx.db.insert('riffMatches', args);
  }
});

/** Get all matches involving a specific riff. */
export const matchesForRiff = query({
  args: { riffId: v.id('riffs') },
  returns: v.array(v.any()),
  handler: async (ctx, args) => {
    const asA = await ctx.db
      .query('riffMatches')
      .withIndex('by_riff_a', (q) => q.eq('riffAId', args.riffId))
      .collect();
    const asB = await ctx.db
      .query('riffMatches')
      .withIndex('by_riff_b', (q) => q.eq('riffBId', args.riffId))
      .collect();
    return [...asA, ...asB];
  }
});
