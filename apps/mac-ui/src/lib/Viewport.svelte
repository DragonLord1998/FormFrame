<script>
  import { onMount } from "svelte";
  import { Focus, RotateCcw, ScanLine } from "@lucide/svelte";

  export let project;
  export let meshUrl = "";

  let canvas;
  let controller;
  let guides = true;

  onMount(() => {
    let disposed = false;
    import("./studioScene.js").then(({ createStudioScene }) => {
      const instance = createStudioScene(canvas);
      if (disposed) {
        instance.dispose();
        return;
      }
      controller = instance;
      controller.update(project);
    });
    return () => {
      disposed = true;
      controller?.dispose();
    };
  });

  $: if (controller && project) controller.update(project);
  $: if (controller) controller.loadProductionMesh(meshUrl);
</script>

<section class="viewport-shell" aria-label="3D character viewport">
  <canvas bind:this={canvas} aria-label="Interactive posed character guide"></canvas>
  <div class="viewport-topline">
    <span class="eyebrow">Live form</span>
    <span class="local-badge"><span></span>{meshUrl ? " GNM + SMPL-X" : " Local guide"}</span>
  </div>
  <div class="viewport-meta">
    <span>{project.scene.focal_length} mm</span>
    <span>{project.scene.frame}</span>
    <span>{project.render.width} × {project.render.height}</span>
  </div>
  {#if guides}
    <div class="frame-guide {project.scene.frame}" aria-hidden="true">
      <i></i><i></i><i></i><i></i>
    </div>
  {/if}
  <div class="viewport-tools">
    <button class:active={guides} on:click={() => (guides = !guides)} aria-label="Toggle framing guides" title="Toggle framing guides">
      <ScanLine size={17} />
    </button>
    <button on:click={() => controller?.resetCamera()} aria-label="Reset camera" title="Reset camera">
      <Focus size={17} />
    </button>
    <button on:click={() => controller?.resetCamera()} aria-label="Reset orbit" title="Reset orbit">
      <RotateCcw size={17} />
    </button>
  </div>
  <p class="viewport-hint">Drag to orbit · Scroll to dolly</p>
</section>
