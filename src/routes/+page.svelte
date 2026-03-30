<script lang="ts">
  import { useQuery, useConvexClient } from 'convex-svelte';
  import { api } from '$convex/_generated/api';
  import SongGroup from '$lib/components/SongGroup.svelte';
  import RecordingCard from '$lib/components/RecordingCard.svelte';
  import ProcessingBanner from '$lib/components/ProcessingBanner.svelte';
  import type { Id } from '$convex/_generated/dataModel';

  const client = useConvexClient();

  const songsQuery = useQuery(api.songs.list);
  const ungroupedQuery = useQuery(api.recordings.listUngrouped);
  const processingQuery = useQuery(api.recordings.listProcessing);

  const songs = $derived(songsQuery.data ?? []);
  const ungrouped = $derived(ungroupedQuery.data ?? []);
  const processing = $derived(processingQuery.data ?? []);

  const songsOnly = $derived(
    songs.map((s) => ({
      _id: s._id,
      _creationTime: s._creationTime,
      title: s.title,
      notes: s.notes,
      createdAt: s.createdAt
    }))
  );

  async function assignToSong(recordingId: Id<'recordings'>, songId: string) {
    await client.mutation(api.recordings.assignToSong, {
      recordingId,
      songId: songId as Id<'songs'>
    });
  }

  async function createSongAndAssign(recordingId: Id<'recordings'>) {
    const title = prompt('Song title:');
    if (!title) return;
    const songId = await client.mutation(api.songs.create, { title });
    await client.mutation(api.recordings.assignToSong, {
      recordingId,
      songId
    });
  }
</script>

<div class="flex flex-col gap-6">
  <!-- Processing banner -->
  <ProcessingBanner count={processing.length} />

  <!-- Loading state -->
  {#if songsQuery.isLoading}
    <div class="py-12 text-center text-zinc-500">Loading...</div>
  {:else}
    <!-- Song groups -->
    {#if songs.length > 0}
      <section>
        <h2
          class="mb-3 text-sm font-medium tracking-wide text-zinc-500 uppercase"
        >
          Songs
        </h2>
        <div class="flex flex-col gap-3">
          {#each songs as song (song._id)}
            <SongGroup {song} recordings={song.recordings} />
          {/each}
        </div>
      </section>
    {/if}

    <!-- Ungrouped recordings -->
    {#if ungrouped.length > 0}
      <section>
        <h2
          class="mb-3 text-sm font-medium tracking-wide text-zinc-500 uppercase"
        >
          Unsorted
        </h2>
        <div class="flex flex-col gap-3">
          {#each ungrouped as recording (recording._id)}
            <RecordingCard
              {recording}
              showAssign={true}
              songs={songsOnly}
              onassign={(songId) => assignToSong(recording._id, songId)}
              oncreatesong={() => createSongAndAssign(recording._id)}
            />
          {/each}
        </div>
      </section>
    {/if}

    <!-- Empty state -->
    {#if songs.length === 0 && ungrouped.length === 0 && processing.length === 0}
      <div class="py-20 text-center">
        <p class="text-lg text-zinc-500">No recordings yet</p>
        <p class="mt-1 text-sm text-zinc-600">
          Upload recordings from the Pi to get started
        </p>
      </div>
    {/if}
  {/if}
</div>
