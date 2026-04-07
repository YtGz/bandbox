import { v } from 'convex/values';
import { query, mutation } from './_generated/server';

/** List all sets, ordered by recording date (newest first). */
export const list = query({
  args: {},
  returns: v.array(v.any()),
  handler: async (ctx) => {
    return await ctx.db
      .query('sets')
      .withIndex('by_recorded_at')
      .order('desc')
      .collect();
  }
});

/** Get a single set by ID. */
export const get = query({
  args: { setId: v.id('sets') },
  returns: v.any(),
  handler: async (ctx, args) => {
    return await ctx.db.get(args.setId);
  }
});

/** Create a new set. */
export const create = mutation({
  args: {
    title: v.string(),
    notes: v.optional(v.string()),
    recordedAt: v.number()
  },
  returns: v.id('sets'),
  handler: async (ctx, args) => {
    return await ctx.db.insert('sets', {
      title: args.title,
      notes: args.notes,
      recordedAt: args.recordedAt,
      createdAt: Date.now()
    });
  }
});

/** Rename a set. */
export const rename = mutation({
  args: {
    setId: v.id('sets'),
    title: v.string()
  },
  returns: v.null(),
  handler: async (ctx, args) => {
    await ctx.db.patch(args.setId, { title: args.title });
    return null;
  }
});

/**
 * Find an existing set for the given date, or create one.
 * Groups sets by calendar date (UTC).
 */
export const findOrCreate = mutation({
  args: {
    recordedAt: v.number()
  },
  returns: v.id('sets'),
  handler: async (ctx, args) => {
    // Get the start and end of the day (UTC)
    const date = new Date(args.recordedAt);
    const dayStart = new Date(
      Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate())
    ).getTime();
    const dayEnd = dayStart + 86400000;

    // Look for an existing set on the same day
    const existing = await ctx.db
      .query('sets')
      .withIndex('by_recorded_at', (q) =>
        q.gte('recordedAt', dayStart).lt('recordedAt', dayEnd)
      )
      .first();

    if (existing) return existing._id;

    // Create a new set with auto-generated title from date
    const title = date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      timeZone: 'UTC'
    });

    return await ctx.db.insert('sets', {
      title,
      recordedAt: args.recordedAt,
      createdAt: Date.now()
    });
  }
});
