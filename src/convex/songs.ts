import { v } from 'convex/values';
import { query, mutation } from './_generated/server';

/** List all songs with their recordings, ordered by creation date (newest first). */
export const list = query({
  args: {},
  returns: v.array(
    v.object({
      _id: v.id('songs'),
      _creationTime: v.number(),
      title: v.string(),
      notes: v.optional(v.string()),
      createdAt: v.number(),
      recordings: v.array(v.any())
    })
  ),
  handler: async (ctx) => {
    const songs = await ctx.db.query('songs').order('desc').collect();
    const results = [];
    for (const song of songs) {
      const recordings = await ctx.db
        .query('recordings')
        .withIndex('by_song', (q) => q.eq('songId', song._id))
        .order('desc')
        .collect();
      results.push({ ...song, recordings });
    }
    return results;
  }
});

/** Get a single song with all its takes. */
export const get = query({
  args: { songId: v.id('songs') },
  returns: v.union(
    v.object({
      _id: v.id('songs'),
      _creationTime: v.number(),
      title: v.string(),
      notes: v.optional(v.string()),
      createdAt: v.number(),
      recordings: v.array(v.any())
    }),
    v.null()
  ),
  handler: async (ctx, args) => {
    const song = await ctx.db.get(args.songId);
    if (!song) return null;
    const recordings = await ctx.db
      .query('recordings')
      .withIndex('by_song', (q) => q.eq('songId', song._id))
      .order('desc')
      .collect();
    return { ...song, recordings };
  }
});

/** Create a new song. */
export const create = mutation({
  args: { title: v.string(), notes: v.optional(v.string()) },
  returns: v.id('songs'),
  handler: async (ctx, args) => {
    return await ctx.db.insert('songs', {
      title: args.title,
      notes: args.notes,
      createdAt: Date.now()
    });
  }
});

/** Rename a song. */
export const rename = mutation({
  args: { songId: v.id('songs'), title: v.string() },
  returns: v.null(),
  handler: async (ctx, args) => {
    await ctx.db.patch(args.songId, { title: args.title });
    return null;
  }
});

/** Merge source song into target song. Moves all recordings, deletes source. */
export const merge = mutation({
  args: {
    targetSongId: v.id('songs'),
    sourceSongId: v.id('songs')
  },
  returns: v.null(),
  handler: async (ctx, args) => {
    const recordings = await ctx.db
      .query('recordings')
      .withIndex('by_song', (q) => q.eq('songId', args.sourceSongId))
      .collect();
    for (const rec of recordings) {
      await ctx.db.patch(rec._id, { songId: args.targetSongId });
    }
    await ctx.db.delete(args.sourceSongId);
    return null;
  }
});

/** Dissolve a song — ungroup all its recordings and delete the song. */
export const dissolve = mutation({
  args: { songId: v.id('songs') },
  returns: v.null(),
  handler: async (ctx, args) => {
    const recordings = await ctx.db
      .query('recordings')
      .withIndex('by_song', (q) => q.eq('songId', args.songId))
      .collect();
    for (const rec of recordings) {
      await ctx.db.patch(rec._id, {
        songId: undefined,
        state: 'ungrouped'
      });
    }
    await ctx.db.delete(args.songId);
    return null;
  }
});
