<script lang="ts">
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { useQuery, useConvexClient } from 'convex-svelte';
  import { api } from '$convex/_generated/api';
  import RecordingCard from '$lib/components/RecordingCard.svelte';
  import type { Id } from '$convex/_generated/dataModel';

  const client = useConvexClient();
  const songId = $derived(page.params.id as Id<'songs'>);

  const songQuery = useQuery(
    api.songs.get,
    () => ({ songId }) as { songId: Id<'songs'> }
  );
  const allSongsQuery = useQuery(api.songs.list);

  const song = $derived(songQuery.data);
  const otherSongs = $derived(
    (allSongsQuery.data ?? []).filter((s) => s._id !== songId)
  );

  let editing = $state(false);
  let editTitle = $state('');

  function startEdit() {
    if (!song) return;
    editTitle = song.title;
    editing = true;
  }

  async function saveTitle() {
    if (!song || !editTitle.trim()) return;
    await client.mutation(api.songs.rename, {
      songId: song._id,
      title: editTitle.trim()
    });
    editing = false;
  }

  async function mergeSong(sourceSongId: string) {
    if (!song) return;
    if (
      !confirm(
        'Merge all takes from the selected song into this one? The other song will be deleted.'
      )
    )
      return;
    await client.mutation(api.songs.merge, {
      targetSongId: song._id,
      sourceSongId: sourceSongId as Id<'songs'>
    });
  }

  async function dissolveSong() {
    if (!song) return;
    if (!confirm('Dissolve this song? All takes will be moved to Unsorted.'))
      return;
    await client.mutation(api.songs.dissolve, { songId: song._id });
    goto('/');
  }
</script>

<div class="flex flex-col gap-6">
  <!-- Back link -->
  <a
    href="/"
    class="inline-flex items-center gap-1 text-sm text-zinc-500 transition hover:text-zinc-300"
  >
    ← Back to dashboard
  </a>

  {#if songQuery.isLoading}
    <div class="py-12 text-center text-zinc-500">Loading...</div>
  {:else if !song}
    <div class="py-12 text-center text-zinc-500">Song not found</div>
  {:else}
    <!-- Song header -->
    <div>
      <div class="flex items-center gap-3">
        {#if editing}
          <form
            onsubmit={(e) => {
              e.preventDefault();
              saveTitle();
            }}
            class="flex items-center gap-2"
          >
            <input
              type="text"
              bind:value={editTitle}
              class="rounded-md border-zinc-700 bg-zinc-800 px-3 py-1.5 text-lg font-semibold text-white focus:border-brand focus:ring-brand"
            />
            <button
              type="submit"
              class="rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-light"
            >
              Save
            </button>
            <button
              type="button"
              onclick={() => (editing = false)}
              class="rounded-md px-3 py-1.5 text-sm text-zinc-400 hover:text-zinc-200"
            >
              Cancel
            </button>
          </form>
        {:else}
          <h1 class="text-2xl font-bold text-white">{song.title}</h1>
          <button
            onclick={startEdit}
            class="text-zinc-500 transition hover:text-zinc-300"
            aria-label="Edit title"
          >
            ✏️
          </button>
        {/if}
      </div>

      {#if song.notes}
        <p class="mt-1 text-sm text-zinc-500">{song.notes}</p>
      {/if}

      <!-- Actions -->
      <div class="mt-3 flex items-center gap-3">
        {#if otherSongs.length > 0}
          <select
            class="rounded-md border-zinc-700 bg-zinc-800 px-3 py-1.5 text-sm text-zinc-300 focus:border-brand focus:ring-brand"
            onchange={(e) => {
              const target = e.currentTarget as HTMLSelectElement;
              if (target.value) mergeSong(target.value);
              target.value = '';
            }}
          >
            <option value="">Merge with...</option>
            {#each otherSongs as other}
              <option value={other._id}>{other.title}</option>
            {/each}
          </select>
        {/if}

        <button
          onclick={dissolveSong}
          class="rounded-md px-3 py-1.5 text-sm text-red-400 transition hover:bg-red-950/30 hover:text-red-300"
        >
          Dissolve song
        </button>
      </div>
    </div>

    <!-- Takes -->
    <section>
      <h2
        class="mb-3 text-sm font-medium tracking-wide text-zinc-500 uppercase"
      >
        {song.recordings.length} take{song.recordings.length === 1 ? '' : 's'}
      </h2>
      <div class="flex flex-col gap-3">
        {#each song.recordings as recording, i (recording._id)}
          <RecordingCard {recording} index={i} />
        {/each}
      </div>
    </section>
  {/if}
</div>
