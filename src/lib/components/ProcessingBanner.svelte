<script lang="ts">
  let { count }: { count: number } = $props();
</script>

{#if count > 0}
  <div
    class="flex items-center gap-3 rounded-xl border border-amber-800/40 bg-amber-950/30 px-5 py-3"
  >
    <span class="relative flex h-3 w-3">
      <span
        class="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75"
      ></span>
      <span class="relative inline-flex h-3 w-3 rounded-full bg-amber-500"
      ></span>
    </span>
    <span class="text-sm text-amber-200">
      Analyzing... {count} recording{count === 1 ? '' : 's'} remaining
    </span>
  </div>
{/if}
