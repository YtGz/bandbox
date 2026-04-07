import type { Doc } from '$convex/_generated/dataModel';

/**
 * Discriminated recording types extracted from the Convex union.
 *
 * Use these instead of `Doc<'recordings'>` when a component only handles
 * one kind of recording.
 */
export type SongRecording = Extract<Doc<'recordings'>, { kind: 'song' }>;
export type SetRecording = Extract<Doc<'recordings'>, { kind: 'set' }>;
