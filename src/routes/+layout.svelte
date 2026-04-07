<script lang="ts">
  import { page } from '$app/state';
  import { locales, localizeHref } from '$lib/paraglide/runtime';
  import { setupConvex } from 'convex-svelte';
  import { env } from '$env/dynamic/public';
  import './layout.css';
  import favicon from '$lib/assets/favicon.svg';

  let { children } = $props();

  setupConvex(env.PUBLIC_CONVEX_URL);
</script>

<svelte:head>
  <link rel="icon" href={favicon} />
  <title>BandBox</title>
</svelte:head>

<div class="min-h-screen bg-zinc-950 text-zinc-100">
  <header class="border-b border-zinc-800 px-6 py-4">
    <a href="/" class="text-xl font-bold tracking-tight text-white">
      BandBox
    </a>
  </header>
  <main class="mx-auto max-w-5xl px-4 py-6">
    {@render children()}
  </main>
</div>

<div style="display:none">
  {#each locales as locale}
    <a href={localizeHref(page.url.pathname, { locale })}>{locale}</a>
  {/each}
</div>
