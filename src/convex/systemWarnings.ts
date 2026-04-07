import { v } from 'convex/values';
import { query, mutation } from './_generated/server';

/** Get a system warning by its key. */
export const getByKey = query({
  args: { key: v.string() },
  returns: v.any(),
  handler: async (ctx, args) => {
    return await ctx.db
      .query('systemWarnings')
      .withIndex('by_key', (q) => q.eq('key', args.key))
      .first();
  }
});

/** Get all active (non-dismissed) system warnings. */
export const listActive = query({
  args: {},
  returns: v.array(v.any()),
  handler: async (ctx) => {
    const all = await ctx.db.query('systemWarnings').collect();
    return all.filter((w) => !w.dismissed);
  }
});

/** Create a new system warning. */
export const create = mutation({
  args: {
    key: v.string(),
    message: v.string()
  },
  returns: v.id('systemWarnings'),
  handler: async (ctx, args) => {
    return await ctx.db.insert('systemWarnings', {
      key: args.key,
      message: args.message,
      createdAt: Date.now(),
      dismissed: false
    });
  }
});

/** Update an existing system warning's message. */
export const update = mutation({
  args: {
    id: v.id('systemWarnings'),
    message: v.string()
  },
  returns: v.null(),
  handler: async (ctx, args) => {
    await ctx.db.patch(args.id, {
      message: args.message,
      createdAt: Date.now(),
      dismissed: false
    });
    return null;
  }
});

/** Dismiss a system warning. */
export const dismiss = mutation({
  args: { id: v.id('systemWarnings') },
  returns: v.null(),
  handler: async (ctx, args) => {
    await ctx.db.patch(args.id, { dismissed: true });
    return null;
  }
});
