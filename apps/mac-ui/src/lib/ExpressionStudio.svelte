<script>
  import { onMount, tick } from "svelte";
  import { X } from "@lucide/svelte";
  import Field from "./Field.svelte";

  export let project;
  export let meshUrl = "";
  export let onClose = () => {};

  const expressions = ["Quiet confidence", "Soft smile", "Focused", "Surprised"];
  let canvas;
  let dialog;
  let controller;
  let loading = true;

  const close = () => onClose?.();

  const onKeydown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  };

  const ensurePose = () => {
    project.pose = {
      expression: "Quiet confidence",
      expression_strength: 0,
      head_turn: 0,
      head_tilt: 0,
      gaze_x: 0,
      gaze_y: 0,
      ...project.pose
    };
    project = project;
  };

  onMount(() => {
    let disposed = false;
    ensurePose();
    tick().then(() => dialog?.focus());

    import("./faceStudioScene.js").then(async ({ createFaceStudioScene }) => {
      if (disposed) return;
      controller = createFaceStudioScene(canvas);
      controller.update(project);
      await controller.loadGnmHead(meshUrl);
      loading = false;
    });

    return () => {
      disposed = true;
      controller?.dispose();
    };
  });

  $: if (controller && project) controller.update(project);
  $: if (controller) controller.loadGnmHead(meshUrl);
</script>

<svelte:window on:keydown={onKeydown} />

<div
  class="expression-backdrop"
  role="presentation"
  on:click={(event) => event.target === event.currentTarget && close()}
>
  <div
    class="expression-modal"
    role="dialog"
    aria-modal="true"
    aria-labelledby="expression-title"
    tabindex="-1"
    bind:this={dialog}
  >
    <header class="panel-header expression-header">
      <div>
        <span class="eyebrow">GNM Studio</span>
        <h2 id="expression-title">Expression</h2>
      </div>
      <button class="icon-button" type="button" aria-label="Close expression studio" on:click={close}>
        <X size={17} />
      </button>
    </header>

    <div class="expression-layout">
      <div class="expression-stage">
        <canvas bind:this={canvas} aria-label="Isolated GNM head expression preview"></canvas>
        {#if loading}
          <span class="local-badge expression-loading"><span></span> Loading head</span>
        {/if}
      </div>

      <aside class="expression-controls" aria-label="Expression controls">
        <section class="control-group">
          <h3>Topology boundary</h3>
          <p class="section-help">
            The GNM head keeps its own expression-ready topology separate from the SMPL-X body. Final composition aligns the head, neck connector and body meshes in the same rig space; it does not remesh the face into the body.
          </p>
        </section>

        <section class="control-group">
          <h3>Expression preset</h3>
          <label class="select-field">Preset
            <select bind:value={project.pose.expression}>
              {#each expressions as expression}
                <option>{expression}</option>
              {/each}
            </select>
          </label>
          <Field label="Strength" bind:value={project.pose.expression_strength} min={0} max={1} step={0.01} />
        </section>

        <section class="control-group">
          <h3>Head pose</h3>
          <Field label="Head turn" bind:value={project.pose.head_turn} min={-60} max={60} step={1} unit="°" />
          <Field label="Head tilt" bind:value={project.pose.head_tilt} min={-35} max={35} step={1} unit="°" />
        </section>

        <section class="control-group">
          <h3>Gaze</h3>
          <Field label="Gaze horizontal" bind:value={project.pose.gaze_x} min={-1} max={1} step={0.01} />
          <Field label="Gaze vertical" bind:value={project.pose.gaze_y} min={-1} max={1} step={0.01} />
        </section>
      </aside>
    </div>
  </div>
</div>

<style>
  .expression-backdrop {
    position: fixed;
    inset: 0;
    z-index: 60;
    display: grid;
    place-items: center;
    padding: 24px;
    background: rgba(12, 12, 10, 0.74);
    backdrop-filter: blur(14px);
  }

  .expression-modal {
    width: min(1040px, 100%);
    max-height: min(760px, calc(100vh - 48px));
    overflow: hidden;
    border: 1px solid var(--line);
    border-radius: 12px;
    background: #1d1d19;
    box-shadow: 0 28px 80px rgba(0, 0, 0, 0.4);
  }

  .expression-header {
    margin: 0;
    padding: 18px 20px;
    border-bottom: 1px solid var(--line);
  }

  .expression-layout {
    display: grid;
    grid-template-columns: minmax(420px, 1fr) 320px;
    min-height: 540px;
  }

  .expression-stage {
    position: relative;
    min-height: 540px;
    background: #171714;
  }

  .expression-stage canvas {
    width: 100%;
    height: 100%;
    display: block;
    touch-action: none;
  }

  .expression-loading {
    position: absolute;
    left: 18px;
    bottom: 18px;
    color: #34312d;
  }

  .expression-controls {
    min-height: 0;
    max-height: calc(min(760px, 100vh - 48px) - 77px);
    overflow-y: auto;
    padding: 0 22px 18px;
    border-left: 1px solid var(--line);
    scrollbar-width: thin;
    scrollbar-color: #47443d transparent;
  }

  @media (max-width: 860px) {
    .expression-backdrop {
      padding: 12px;
      place-items: stretch;
    }

    .expression-modal {
      max-height: calc(100vh - 24px);
    }

    .expression-layout {
      grid-template-columns: 1fr;
      min-height: 0;
    }

    .expression-stage {
      min-height: 360px;
      height: 45vh;
    }

    .expression-controls {
      max-height: 42vh;
      border-left: 0;
      border-top: 1px solid var(--line);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .expression-backdrop,
    .expression-modal,
    .expression-stage canvas {
      scroll-behavior: auto;
      transition: none;
      animation: none;
    }
  }
</style>
