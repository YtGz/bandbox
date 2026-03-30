<script lang="ts">
  let {
    src,
    compact = false,
    dimmed = false,
    onended
  }: {
    src: string;
    compact?: boolean;
    dimmed?: boolean;
    onended?: () => void;
  } = $props();

  let audio: HTMLAudioElement | undefined = $state();
  let playing = $state(false);
  let currentTime = $state(0);
  let duration = $state(0);
  let playbackRate = $state(1);

  function toggle() {
    if (!audio) return;
    if (playing) {
      audio.pause();
    } else {
      audio.play();
    }
  }

  function seek(e: MouseEvent) {
    if (!audio || !duration) return;
    const target = e.currentTarget as HTMLElement;
    const rect = target.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    audio.currentTime = pct * duration;
  }

  function cycleSpeed() {
    if (!audio) return;
    const speeds = [1, 1.25, 1.5, 2, 0.75];
    const idx = speeds.indexOf(playbackRate);
    playbackRate = speeds[(idx + 1) % speeds.length];
    audio.playbackRate = playbackRate;
  }

  function fmt(s: number): string {
    if (!isFinite(s)) return '0:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  export function seekTo(time: number) {
    if (audio) audio.currentTime = time;
  }

  export function play() {
    audio?.play();
  }

  export function pause() {
    audio?.pause();
  }

  $effect(() => {
    if (audio && onended) {
      audio.onended = onended;
    }
  });
</script>

<div
  class="flex items-center gap-3 rounded-lg px-3 py-2 {dimmed
    ? 'bg-zinc-900/50 opacity-60'
    : 'bg-zinc-900'} {compact ? 'gap-2 px-2 py-1' : ''}"
>
  <audio
    bind:this={audio}
    {src}
    preload="metadata"
    onplay={() => (playing = true)}
    onpause={() => (playing = false)}
    ontimeupdate={() => {
      if (audio) currentTime = audio.currentTime;
    }}
    onloadedmetadata={() => {
      if (audio) duration = audio.duration;
    }}
  ></audio>

  <!-- Play/Pause -->
  <button
    onclick={toggle}
    class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-700 text-white transition hover:bg-zinc-600 {compact
      ? 'h-6 w-6'
      : ''}"
    aria-label={playing ? 'Pause' : 'Play'}
  >
    {#if playing}
      <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor">
        <rect x="6" y="4" width="4" height="16" />
        <rect x="14" y="4" width="4" height="16" />
      </svg>
    {:else}
      <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor">
        <polygon points="5,3 19,12 5,21" />
      </svg>
    {/if}
  </button>

  <!-- Seek bar -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="relative h-1.5 flex-1 cursor-pointer rounded-full bg-zinc-700"
    onclick={seek}
  >
    <div
      class="absolute top-0 left-0 h-full rounded-full bg-brand"
      style="width: {duration ? (currentTime / duration) * 100 : 0}%"
    ></div>
  </div>

  <!-- Time -->
  <span class="min-w-[4.5rem] text-right font-mono text-xs text-zinc-400">
    {fmt(currentTime)} / {fmt(duration)}
  </span>

  <!-- Speed -->
  {#if !compact}
    <button
      onclick={cycleSpeed}
      class="rounded px-1.5 py-0.5 font-mono text-xs text-zinc-500 transition hover:bg-zinc-800 hover:text-zinc-300"
    >
      {playbackRate}×
    </button>
  {/if}
</div>
