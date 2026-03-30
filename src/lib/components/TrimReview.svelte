<script lang="ts">
  import { useConvexClient } from 'convex-svelte';
  import { api } from '$convex/_generated/api';
  import AudioPlayer from './AudioPlayer.svelte';
  import type { Doc } from '$convex/_generated/dataModel';

  let { recording }: { recording: Doc<'recordings'> } = $props();

  const client = useConvexClient();

  // Trim was undone if savedCutStartSec exists but cutStartSec doesn't
  const trimUndone = $derived(
    recording.savedCutStartSec !== undefined &&
      recording.cutStartSec === undefined
  );

  const hasTrim = $derived(
    recording.cutStartSec !== undefined && recording.cutEndSec !== undefined
  );

  const preDuration = $derived(
    hasTrim ? Math.round(recording.cutStartSec!) : 0
  );
  const postDuration = $derived(
    hasTrim && recording.durationSec
      ? Math.round(
          (recording.durationSec ?? 0) +
            (recording.cutStartSec ?? 0) -
            (recording.cutEndSec ?? 0) +
            (recording.cutEndSec ?? 0) -
            (recording.cutStartSec ?? 0)
        )
      : 0
  );

  // Simpler: pre = cutStartSec, post = total original duration - cutEndSec
  // We don't have total original duration stored, so use what we have
  const preSeconds = $derived(Math.round(recording.cutStartSec ?? 0));
  const postSeconds = $derived(0); // We'd need original duration for this

  const confidence = $derived(recording.trimConfidence ?? 0);
  const lowConfidence = $derived(confidence < 0.6);
  const medConfidence = $derived(confidence >= 0.6 && confidence < 0.85);

  let expanded = $state(false);
  let prePlayer: AudioPlayer | undefined = $state();
  let songPlayer: AudioPlayer | undefined = $state();

  // Auto-expand for low confidence trims
  $effect(() => {
    if (lowConfidence && hasTrim) {
      expanded = true;
    }
  });

  function playTransition() {
    if (!prePlayer || !songPlayer) return;
    // Seek pre player to last 10 seconds and play
    const preAudio = recording.pathPre
      ? `/api/audio/${recording.pathPre}`
      : null;
    if (!preAudio) {
      // No pre segment, just play song from start
      songPlayer.seekTo(0);
      songPlayer.play();
      return;
    }
    // We'll seek to near the end of pre and chain to song
    prePlayer.seekTo(Math.max(0, preSeconds - 10));
    prePlayer.play();
  }

  function onPreEnded() {
    // Chain: when pre finishes, start song
    songPlayer?.seekTo(0);
    songPlayer?.play();
  }

  async function undoTrim() {
    if (
      !confirm(
        'This will use the full uncut recording instead of the trimmed version. You can restore the trim later.'
      )
    )
      return;
    await client.mutation(api.recordings.undoTrim, {
      recordingId: recording._id
    });
  }

  async function restoreTrim() {
    await client.mutation(api.recordings.restoreTrim, {
      recordingId: recording._id
    });
  }
</script>

{#if trimUndone}
  <!-- Trim was undone -->
  <div
    class="mt-2 flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900/50 px-4 py-2"
  >
    <span class="text-xs text-zinc-500">
      Trim removed — playing full recording
    </span>
    <button
      onclick={restoreTrim}
      class="rounded px-2 py-1 text-xs text-brand transition hover:bg-brand/10 hover:text-brand-light"
    >
      Restore trim
    </button>
  </div>
{:else if hasTrim}
  <!-- Collapsed: summary row -->
  {#if !expanded}
    <button
      class="mt-2 flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-left text-xs text-zinc-500 transition hover:bg-zinc-800/50"
      onclick={() => (expanded = true)}
    >
      <svg
        class="h-3 w-3 text-zinc-600"
        viewBox="0 0 24 24"
        fill="currentColor"
      >
        <polygon points="8,4 20,12 8,20" />
      </svg>
      <span>
        {preSeconds}s before
        {#if recording.pathPost}
          · trimmed after
        {/if}
      </span>

      {#if lowConfidence}
        <span class="ml-auto text-red-400">⚠ review recommended</span>
      {:else if medConfidence}
        <span class="ml-auto text-amber-400">
          ⚠ {preSeconds}s trimmed
        </span>
      {/if}
    </button>
  {/if}

  <!-- Expanded: full trim review -->
  {#if expanded}
    <div class="mt-2 rounded-lg border border-zinc-800 bg-zinc-950/50 p-4">
      <!-- Confidence indicator -->
      {#if lowConfidence}
        <div class="mb-3 flex items-center gap-2 text-xs text-red-400">
          <span>⚠</span>
          <span>
            Low confidence trim ({Math.round(confidence * 100)}%) — review
            recommended
          </span>
        </div>
      {:else if medConfidence}
        <div class="mb-3 flex items-center gap-2 text-xs text-amber-400">
          <span>⚠</span>
          <span>
            {Math.round(confidence * 100)}% confidence —
            {preSeconds}s trimmed from start
          </span>
        </div>
      {:else}
        <div class="mb-3 flex items-center gap-2 text-xs text-emerald-400">
          <span>✓</span>
          <span>
            {Math.round(confidence * 100)}% confidence
          </span>
        </div>
      {/if}

      <!-- Three-segment timeline -->
      <div class="flex gap-2">
        <!-- Pre -->
        {#if recording.pathPre}
          <div class="min-w-0 flex-shrink-0 basis-1/5">
            <div class="mb-1 text-[10px] font-medium text-zinc-600 uppercase">
              Before
            </div>
            <AudioPlayer
              bind:this={prePlayer}
              src="/api/audio/{recording.pathPre}"
              compact={true}
              dimmed={true}
              onended={onPreEnded}
            />
          </div>
        {/if}

        <!-- Song -->
        <div class="min-w-0 flex-1">
          <div class="mb-1 text-[10px] font-medium text-zinc-400 uppercase">
            Song
          </div>
          <AudioPlayer
            bind:this={songPlayer}
            src="/api/audio/{recording.pathSong}"
            compact={true}
          />
        </div>

        <!-- Post -->
        {#if recording.pathPost}
          <div class="min-w-0 flex-shrink-0 basis-1/5">
            <div class="mb-1 text-[10px] font-medium text-zinc-600 uppercase">
              After
            </div>
            <AudioPlayer
              src="/api/audio/{recording.pathPost}"
              compact={true}
              dimmed={true}
            />
          </div>
        {/if}
      </div>

      <!-- Transcript -->
      {#if recording.transcriptPre}
        <p class="mt-3 text-xs text-zinc-500 italic">
          💬 "{recording.transcriptPre}"
        </p>
      {/if}

      <!-- Actions -->
      <div class="mt-3 flex items-center gap-3">
        {#if recording.pathPre}
          <button
            onclick={playTransition}
            class="flex items-center gap-1.5 rounded-md bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 transition hover:bg-zinc-700"
          >
            ▶ Play from 10s before cut
          </button>
        {/if}

        <button
          onclick={undoTrim}
          class="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs text-zinc-500 transition hover:bg-zinc-800 hover:text-zinc-300"
        >
          ↩ Undo trim
        </button>

        <button
          onclick={() => (expanded = false)}
          class="ml-auto text-xs text-zinc-600 transition hover:text-zinc-400"
        >
          Collapse
        </button>
      </div>
    </div>
  {/if}
{/if}
