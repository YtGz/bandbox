<script lang="ts">
  import AudioPlayer from './AudioPlayer.svelte';
  import ProcessingBadge from './ProcessingBadge.svelte';
  import type { Doc } from '$convex/_generated/dataModel';

  let {
    recording,
    index,
    showAssign = false,
    songs = [],
    onassign,
    oncreatesong
  }: {
    recording: Doc<'recordings'>;
    index?: number;
    showAssign?: boolean;
    songs?: Doc<'songs'>[];
    onassign?: (songId: string) => void;
    oncreatesong?: () => void;
  } = $props();

  const isProcessing = $derived(
    ['uploading', 'normalizing', 'trimming', 'analyzing'].includes(
      recording.state
    )
  );

  const audioSrc = $derived(
    recording.pathSong
      ? `/api/audio/${recording.pathSong}`
      : recording.pathFlac
        ? `/api/audio/${recording.pathFlac}`
        : null
  );

  function formatDate(ts: number): string {
    return new Date(ts).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric'
    });
  }

  function formatDuration(sec: number | undefined): string {
    if (!sec) return '';
    const m = Math.floor(sec / 60);
    const s = Math.round(sec % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  }
</script>

<div
  class="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 transition hover:border-zinc-700"
>
  <!-- Header row -->
  <div class="mb-2 flex items-center justify-between gap-3">
    <div class="flex items-center gap-2">
      {#if index !== undefined}
        <span class="text-sm font-medium text-zinc-500">
          {formatDate(recording.uploadedAt)} — Take {index + 1}
        </span>
      {:else}
        <span class="truncate text-sm text-zinc-400">
          {recording.filename}
        </span>
      {/if}
      <ProcessingBadge state={recording.state} />
    </div>

    {#if recording.durationSec}
      <span class="text-xs text-zinc-500">
        {formatDuration(recording.durationSec)}
      </span>
    {/if}
  </div>

  <!-- Metadata row -->
  {#if recording.tempo || recording.dominantKey}
    <div class="mb-2 flex gap-3 text-xs text-zinc-500">
      {#if recording.tempo}
        <span>{Math.round(recording.tempo)} BPM</span>
      {/if}
      {#if recording.dominantKey}
        <span>{recording.dominantKey}</span>
      {/if}
    </div>
  {/if}

  <!-- Transcript -->
  {#if recording.transcriptPre}
    <p class="mb-2 text-xs text-zinc-500 italic">
      💬 "{recording.transcriptPre}"
    </p>
  {/if}

  <!-- Audio player -->
  {#if audioSrc && !isProcessing}
    <AudioPlayer src={audioSrc} />
  {:else if isProcessing}
    <div
      class="flex h-12 items-center justify-center rounded-lg bg-zinc-900 text-xs text-zinc-500"
    >
      Processing...
    </div>
  {/if}

  <!-- Assign to song dropdown -->
  {#if showAssign && !isProcessing}
    <div class="mt-3 flex items-center gap-2">
      <select
        class="rounded-md border-zinc-700 bg-zinc-800 px-3 py-1.5 text-sm text-zinc-300 focus:border-brand focus:ring-brand"
        onchange={(e) => {
          const target = e.currentTarget as HTMLSelectElement;
          const val = target.value;
          if (val === '__new__') {
            oncreatesong?.();
          } else if (val) {
            onassign?.(val);
          }
          target.value = '';
        }}
      >
        <option value="">Assign to song...</option>
        {#each songs as song}
          <option value={song._id}>{song.title}</option>
        {/each}
        <option value="__new__">+ Create new song</option>
      </select>
    </div>
  {/if}
</div>
