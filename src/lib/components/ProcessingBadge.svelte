<script lang="ts">
  let { state }: { state: string } = $props();

  const labels: Record<string, string> = {
    uploading: 'Uploading',
    normalizing: 'Normalizing',
    trimming: 'Trimming',
    analyzing: 'Analyzing'
  };

  const isProcessing = $derived(
    ['uploading', 'normalizing', 'trimming', 'analyzing'].includes(state)
  );
</script>

{#if isProcessing}
  <span
    class="inline-flex items-center gap-1.5 rounded-full bg-amber-900/40 px-2.5 py-0.5 text-xs font-medium text-amber-300"
  >
    <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400"></span>
    {labels[state] ?? state}
  </span>
{:else if state === 'grouped'}
  <span
    class="inline-flex items-center gap-1 rounded-full bg-emerald-900/40 px-2.5 py-0.5 text-xs font-medium text-emerald-300"
  >
    ✓ Grouped
  </span>
{:else if state === 'ungrouped'}
  <span
    class="inline-flex items-center gap-1 rounded-full bg-zinc-800 px-2.5 py-0.5 text-xs font-medium text-zinc-400"
  >
    Unsorted
  </span>
{/if}
