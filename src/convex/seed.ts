import { mutation } from './_generated/server';
import { v } from 'convex/values';

/** Seed test data for development. Clears existing data first. */
export const run = mutation({
  args: {},
  returns: v.null(),
  handler: async (ctx) => {
    // Clear existing data
    for (const table of [
      'songs',
      'recordings',
      'riffs',
      'riffMatches',
      'corrections'
    ] as const) {
      const docs = await ctx.db.query(table).collect();
      for (const doc of docs) {
        await ctx.db.delete(doc._id);
      }
    }

    const now = Date.now();
    const day = 86400000;

    // --- Songs ---
    const carrionThrone = await ctx.db.insert('songs', {
      title: 'Carrion Throne',
      notes:
        'Main riff in drop A, blast beat section at 2:30. Working title from rehearsal banter.',
      createdAt: now - 14 * day
    });

    const voidTremor = await ctx.db.insert('songs', {
      title: 'Void Tremor',
      notes:
        'Slow doom intro, speeds up at 1:45. Key change into the bridge needs work.',
      createdAt: now - 10 * day
    });

    const ossuary = await ctx.db.insert('songs', {
      title: 'Ossuary',
      createdAt: now - 5 * day
    });

    // --- Recordings for Carrion Throne ---
    await ctx.db.insert('recordings', {
      filename: 'ZOOM0042.wav',
      fileHash:
        'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2',
      uploadedAt: now - 14 * day,
      state: 'grouped',
      songId: carrionThrone,
      pathFlac: 'carrion_take1.flac',
      pathSong: 'carrion_take1_song.opus',
      pathPre: 'carrion_take1_pre.opus',
      pathPost: 'carrion_take1_post.opus',
      cutStartSec: 34,
      cutEndSec: 256,
      trimConfidence: 0.92,
      trimMethod: 'energy_wall',
      transcriptPre: 'okay from the top, doom riff',
      tempo: 185,
      dominantKey: 'A',
      durationSec: 222
    });

    await ctx.db.insert('recordings', {
      filename: 'ZOOM0043.wav',
      fileHash:
        'b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3',
      uploadedAt: now - 14 * day + 1800000,
      state: 'grouped',
      songId: carrionThrone,
      pathFlac: 'carrion_take2.flac',
      pathSong: 'carrion_take2_song.opus',
      cutStartSec: 12,
      cutEndSec: 271,
      trimConfidence: 0.88,
      trimMethod: 'rhythmic_regularity',
      transcriptPre: 'again, watch the tempo on the bridge',
      tempo: 188,
      dominantKey: 'A',
      durationSec: 259
    });

    await ctx.db.insert('recordings', {
      filename: 'ZOOM0051.wav',
      fileHash:
        'c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4',
      uploadedAt: now - 7 * day,
      state: 'grouped',
      songId: carrionThrone,
      pathFlac: 'carrion_take3.flac',
      pathSong: 'carrion_take3_song.opus',
      pathPre: 'carrion_take3_pre.opus',
      cutStartSec: 8,
      cutEndSec: 280,
      trimConfidence: 0.95,
      trimMethod: 'energy_wall',
      transcriptPre: 'full run through, no stops',
      tempo: 186,
      dominantKey: 'A',
      durationSec: 272
    });

    // --- Recordings for Void Tremor ---
    await ctx.db.insert('recordings', {
      filename: 'ZOOM0044.wav',
      fileHash:
        'd4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5',
      uploadedAt: now - 10 * day,
      state: 'grouped',
      songId: voidTremor,
      pathFlac: 'void_take1.flac',
      pathSong: 'void_take1_song.opus',
      pathPre: 'void_take1_pre.opus',
      pathPost: 'void_take1_post.opus',
      cutStartSec: 45,
      cutEndSec: 312,
      trimConfidence: 0.72,
      trimMethod: 'pitched_content',
      transcriptPre: 'the slow one, yeah doom thing',
      tempo: 68,
      dominantKey: 'D',
      durationSec: 267
    });

    await ctx.db.insert('recordings', {
      filename: 'ZOOM0052.wav',
      fileHash:
        'e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6',
      uploadedAt: now - 3 * day,
      state: 'grouped',
      songId: voidTremor,
      pathFlac: 'void_take2.flac',
      pathSong: 'void_take2_song.opus',
      cutStartSec: 22,
      cutEndSec: 340,
      trimConfidence: 0.55,
      trimMethod: 'pitched_content',
      tempo: 71,
      dominantKey: 'D',
      durationSec: 318
    });

    // --- Recording for Ossuary ---
    await ctx.db.insert('recordings', {
      filename: 'ZOOM0053.wav',
      fileHash:
        'f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1',
      uploadedAt: now - 5 * day,
      state: 'grouped',
      songId: ossuary,
      pathFlac: 'ossuary_take1.flac',
      pathSong: 'ossuary_take1_song.opus',
      pathPre: 'ossuary_take1_pre.opus',
      cutStartSec: 18,
      cutEndSec: 195,
      trimConfidence: 0.91,
      trimMethod: 'energy_wall',
      transcriptPre: 'new one, just the verse riff for now',
      tempo: 142,
      dominantKey: 'C#',
      durationSec: 177
    });

    // --- Ungrouped recordings ---
    await ctx.db.insert('recordings', {
      filename: 'ZOOM0054.wav',
      fileHash:
        'a7b8c9d0e1f2a7b8c9d0e1f2a7b8c9d0e1f2a7b8c9d0e1f2a7b8c9d0e1f2a7b8',
      uploadedAt: now - 1 * day,
      state: 'ungrouped',
      pathFlac: 'unknown_take1.flac',
      pathSong: 'unknown_take1_song.opus',
      pathPre: 'unknown_take1_pre.opus',
      cutStartSec: 28,
      cutEndSec: 198,
      trimConfidence: 0.83,
      trimMethod: 'rhythmic_regularity',
      transcriptPre: 'just jam on that riff, see where it goes',
      tempo: 156,
      dominantKey: 'B',
      durationSec: 170
    });

    await ctx.db.insert('recordings', {
      filename: 'ZOOM0055.wav',
      fileHash:
        'b8c9d0e1f2a3b8c9d0e1f2a3b8c9d0e1f2a3b8c9d0e1f2a3b8c9d0e1f2a3b8c9',
      uploadedAt: now - 1 * day + 600000,
      state: 'ungrouped',
      pathFlac: 'unknown_take2.flac',
      pathSong: 'unknown_take2_song.opus',
      cutStartSec: 15,
      cutEndSec: 142,
      trimConfidence: 0.67,
      trimMethod: 'pitched_content',
      tempo: 158,
      dominantKey: 'B',
      durationSec: 127
    });

    // --- Currently processing ---
    await ctx.db.insert('recordings', {
      filename: 'ZOOM0060.wav',
      fileHash:
        'c9d0e1f2a3b4c9d0e1f2a3b4c9d0e1f2a3b4c9d0e1f2a3b4c9d0e1f2a3b4c9d0',
      uploadedAt: now,
      state: 'analyzing'
    });

    await ctx.db.insert('recordings', {
      filename: 'ZOOM0061.wav',
      fileHash:
        'd0e1f2a3b4c5d0e1f2a3b4c5d0e1f2a3b4c5d0e1f2a3b4c5d0e1f2a3b4c5d0e1',
      uploadedAt: now,
      state: 'normalizing'
    });

    return null;
  }
});
