<script lang="ts">
  import RecordingCard from './RecordingCard.svelte';
  import AudioPlayer from './AudioPlayer.svelte';
  import type { Doc } from '$convex/_generated/dataModel';
  import type { SongRecording } from '$lib/types';

  let {
    song,
    recordings
  }: {
    song: Doc<'songs'>;
    recordings: SongRecording[];
  } = $props();

  let expanded = $state(false);

  const latestRecording = $derived(recordings[0]);
  const latestAudioSrc = $derived(
    latestRecording?.pathSong ? `/api/audio/${latestRecording.pathSong}` : null
  );

  function formatDate(ts: number): string {
    return new Date(ts).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric'
    });
  }
</script>

<div class="rounded-xl border border-zinc-800 bg-zinc-900/30">
  <!-- Header — always visible -->
  <button
    class="flex w-full items-center gap-4 px-5 py-4 text-left transition hover:bg-zinc-800/30"
    onclick={() => (expanded = !expanded)}
  >
    <svg
      class="h-4 w-4 shrink-0 text-zinc-500 transition-transform {expanded
        ? 'rotate-90'
        : ''}"
      viewBox="0 0 24 24"
      fill="currentColor"
    >
      <polygon points="8,4 20,12 8,20" />
    </svg>

    <div class="min-w-0 flex-1">
      <h3 class="truncate text-base font-semibold text-white">
        {song.title}
      </h3>
      <p class="text-xs text-zinc-500">
        {recordings.length} take{recordings.length === 1 ? '' : 's'}
        {#if latestRecording}
          · Latest: {formatDate(latestRecording.uploadedAt)}
        {/if}
      </p>
    </div>

    <a
      href="/song/{song._id}"
      class="shrink-0 rounded-md px-2.5 py-1 text-xs text-zinc-500 transition hover:bg-zinc-800 hover:text-zinc-300"
      onclick={(e) => e.stopPropagation()}
    >
      View →
    </a>
  </button>

  <!-- Inline player for latest take -->
  {#if latestAudioSrc && !expanded}
    <div class="px-5 pb-4">
      <AudioPlayer src={latestAudioSrc} />
    </div>
  {/if}

  <!-- Expanded: all takes -->
  {#if expanded}
    <div class="flex flex-col gap-3 border-t border-zinc-800 px-5 py-4">
      {#each recordings as recording, i}
        <RecordingCard {recording} index={i} />
      {/each}
    </div>
  {/if}
</div>
