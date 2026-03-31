import { defineSchema, defineTable } from 'convex/server';
import { v } from 'convex/values';

export default defineSchema({
  songs: defineTable({
    title: v.string(),
    notes: v.optional(v.string()),
    createdAt: v.number()
  }),

  recordings: defineTable({
    filename: v.string(),
    fileHash: v.string(),
    uploadedAt: v.number(),
    state: v.union(
      v.literal('uploading'),
      v.literal('normalizing'),
      v.literal('trimming'),
      v.literal('analyzing'),
      v.literal('grouped'),
      v.literal('ungrouped')
    ),
    songId: v.optional(v.id('songs')),
    pathFlac: v.optional(v.string()),
    pathSong: v.optional(v.string()),
    pathPre: v.optional(v.string()),
    pathPost: v.optional(v.string()),
    cutStartSec: v.optional(v.number()),
    cutEndSec: v.optional(v.number()),
    savedCutStartSec: v.optional(v.number()),
    savedCutEndSec: v.optional(v.number()),
    trimConfidence: v.optional(v.number()),
    trimMethod: v.optional(v.string()),
    transcriptPre: v.optional(v.string()),
    transcriptPost: v.optional(v.string()),
    tempo: v.optional(v.number()),
    dominantKey: v.optional(v.string()),
    durationSec: v.optional(v.number())
  })
    .index('by_hash', ['fileHash'])
    .index('by_state', ['state'])
    .index('by_song', ['songId']),

  riffs: defineTable({
    recordingId: v.id('recordings'),
    riffIndex: v.number(),
    startSec: v.number(),
    endSec: v.number(),
    tempo: v.optional(v.number()),
    fingerprint: v.any(),
    contour: v.optional(v.any())
  }).index('by_recording', ['recordingId']),

  riffMatches: defineTable({
    riffAId: v.id('riffs'),
    riffBId: v.id('riffs'),
    score: v.number(),
    breakdown: v.any()
  })
    .index('by_riff_a', ['riffAId'])
    .index('by_riff_b', ['riffBId']),

  corrections: defineTable({
    recordingId: v.id('recordings'),
    fromSongId: v.optional(v.id('songs')),
    toSongId: v.id('songs'),
    correctedAt: v.number()
  }).index('by_recording', ['recordingId']),

  systemWarnings: defineTable({
    key: v.string(),
    message: v.string(),
    createdAt: v.number(),
    dismissed: v.boolean()
  }).index('by_key', ['key'])
});
