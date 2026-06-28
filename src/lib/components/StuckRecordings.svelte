<script lang="ts">
  import { useConvexClient } from 'convex-svelte';
  import { SvelteSet } from 'svelte/reactivity';
  import { api } from '$convex/_generated/api';
  import type { Id } from '$convex/_generated/dataModel';

  let {
    recordings
  }: {
    recordings: Array<{
      _id: Id<'recordings'>;
      _creationTime: number;
      kind: 'song' | 'set';
      filename: string;
      state: string;
      stuckForMs: number;
      pathFlac?: string;
    }>;
  } = $props();

  const client = useConvexClient();

  let recovering = $state(new SvelteSet<string>());
  let deleting = $state(new SvelteSet<string>());
  let error = $state<string | null>(null);

  function formatDuration(ms: number): string {
    const minutes = Math.floor(ms / 60000);
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ${minutes % 60}m`;
  }

  async function recover(id: Id<'recordings'>) {
    error = null;
    recovering.add(id);
    try {
      await client.mutation(api.recordings.recoverRecording, {
        recordingId: id
      });
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      recovering.delete(id);
    }
  }

  async function remove(id: Id<'recordings'>) {
    if (!confirm('Permanently delete this recording? This cannot be undone.'))
      return;
    error = null;
    deleting.add(id);
    try {
      await client.mutation(api.recordings.deleteRecording, {
        recordingId: id
      });
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      deleting.delete(id);
    }
  }
</script>

<section
  class="flex flex-col gap-3 rounded-xl border border-red-800/40 bg-red-950/30 px-5 py-4"
>
  <div class="flex items-center gap-2">
    <span class="text-sm font-medium text-red-300">⚠ Stuck recordings</span>
  </div>
  <p class="text-xs text-red-200/70">
    These recordings have been in a processing state longer than expected — the
    worker likely crashed mid-pipeline. Recover them to reprocess from the saved
    audio, or delete them if there's nothing to salvage.
  </p>

  {#if error}
    <p class="text-xs text-red-400">{error}</p>
  {/if}

  <div class="flex flex-col gap-2">
    {#each recordings as rec (rec._id)}
      <div
        class="flex items-center justify-between gap-3 rounded-lg bg-zinc-900/50 px-3 py-2"
      >
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm text-zinc-300">{rec.filename}</p>
          <p class="text-xs text-zinc-500">
            {rec.state} · stuck for {formatDuration(rec.stuckForMs)}
            {#if !rec.pathFlac}· no audio saved{/if}
          </p>
        </div>
        <div class="flex shrink-0 items-center gap-2">
          <button
            onclick={() => recover(rec._id)}
            disabled={recovering.has(rec._id) || deleting.has(rec._id)}
            class="rounded-md bg-brand px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-brand-light disabled:opacity-50"
          >
            {recovering.has(rec._id) ? 'Recovering…' : 'Recover'}
          </button>
          <button
            onclick={() => remove(rec._id)}
            disabled={recovering.has(rec._id) || deleting.has(rec._id)}
            class="rounded-md px-2.5 py-1.5 text-xs text-zinc-400 transition hover:bg-zinc-800 hover:text-zinc-200 disabled:opacity-50"
          >
            {deleting.has(rec._id) ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    {/each}
  </div>
</section>
