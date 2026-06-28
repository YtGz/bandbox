<script lang="ts">
  let {
    onuploaded
  }: {
    onuploaded?: () => void;
  } = $props();

  let isUploading = $state(false);
  let inputRef = $state<HTMLInputElement | null>(null);

  async function handleFileSelect(e: Event) {
    const target = e.target as HTMLInputElement;
    const file = target.files?.[0];
    if (!file) return;

    isUploading = true;

    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await fetch('/api/upload/web', {
        method: 'POST',
        body: formData
      });

      if (!res.ok) {
        const body = await res.text();
        throw new Error(`Upload failed: ${body}`);
      }

      const result = await res.json();
      if (result.status === 'duplicate') {
        alert('This file has already been uploaded.');
      } else {
        onuploaded?.();
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      isUploading = false;
      if (target) target.value = '';
    }
  }

  function open() {
    inputRef?.click();
  }
</script>

<input
  bind:this={inputRef}
  type="file"
  accept="audio/wav,audio/flac,audio/*"
  onchange={handleFileSelect}
  class="hidden"
/>

<button
  onclick={open}
  disabled={isUploading}
  class="flex items-center gap-2 rounded-md bg-zinc-800 px-3 py-1.5 text-sm font-medium text-zinc-200 transition hover:bg-zinc-700 disabled:opacity-50"
>
  {#if isUploading}
    <span
      class="h-4 w-4 animate-spin rounded-full border-2 border-zinc-500 border-t-zinc-200"
    ></span>
    Uploading...
  {:else}
    <svg
      class="h-4 w-4"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      viewBox="0 0 24 24"
    >
      <path
        stroke-linecap="round"
        stroke-linejoin="round"
        d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5"
      />
    </svg>
    Upload
  {/if}
</button>
