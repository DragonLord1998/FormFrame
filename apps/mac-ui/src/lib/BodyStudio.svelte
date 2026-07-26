<script>
  import { onMount, tick } from "svelte";
  import { RotateCcw, Search, X } from "@lucide/svelte";
  import {
    defaultSmplxState,
    normalizeProject,
    smplxAxes,
    smplxHandJointNames,
    smplxJointNames
  } from "./project.js";

  export let project;
  export let onClose = () => {};

  const controlsPerPage = 18;
  let dialog;
  let query = "";
  let page = 0;

  const close = () => onClose?.();

  const onKeydown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  };

  const shapeControls = () =>
    Array.from({ length: 10 }, (_, index) => ({
      group: "Shape betas",
      label: `Beta ${index + 1}`,
      scope: "character",
      key: "body_shape",
      index,
      min: -3,
      max: 3,
      step: 0.01
    }));

  const axisControls = (group, key, names, limit = 180) =>
    names.flatMap((joint, jointIndex) =>
      smplxAxes.map((axis, axisIndex) => ({
        group,
        label: `${joint} ${axis}`,
        scope: "pose",
        key,
        index: jointIndex * 3 + axisIndex,
        min: -limit,
        max: limit,
        step: 0.5
      }))
    );

  const globalControls = () =>
    smplxAxes.map((axis, index) => ({
      group: "Global orientation",
      label: `Root ${axis}`,
      scope: "pose",
      key: "smplx_global_orient",
      index,
      min: -180,
      max: 180,
      step: 0.5
    }));

  const ensureSmplx = () => {
    project = normalizeProject(project);
  };

  const resetSmplx = () => {
    const defaults = defaultSmplxState();
    project = {
      ...project,
      character: { ...project.character, body_shape: defaults.body_shape },
      pose: {
        ...project.pose,
        smplx_body_pose: defaults.smplx_body_pose,
        smplx_left_hand_pose: defaults.smplx_left_hand_pose,
        smplx_right_hand_pose: defaults.smplx_right_hand_pose,
        smplx_global_orient: defaults.smplx_global_orient
      }
    };
    page = 0;
  };

  const setValue = (control, value) => {
    const number = Number(value);
    const owner = project[control.scope];
    const next = [...owner[control.key]];
    next[control.index] = Number.isFinite(number) ? number : 0;
    project = {
      ...project,
      [control.scope]: { ...owner, [control.key]: next }
    };
  };

  const formatValue = (value) => Math.round(value * 100) / 100;

  onMount(() => {
    ensureSmplx();
    tick().then(() => dialog?.focus());
  });

  $: controls = [
    ...shapeControls(),
    ...globalControls(),
    ...axisControls("Body joints", "smplx_body_pose", smplxJointNames),
    ...axisControls("Left hand", "smplx_left_hand_pose", smplxHandJointNames, 90),
    ...axisControls("Right hand", "smplx_right_hand_pose", smplxHandJointNames, 90)
  ];
  $: normalizedQuery = query.trim().toLowerCase();
  $: filteredControls = normalizedQuery
    ? controls.filter((control) => `${control.group} ${control.label}`.toLowerCase().includes(normalizedQuery))
    : controls;
  $: pageCount = Math.max(1, Math.ceil(filteredControls.length / controlsPerPage));
  $: if (page >= pageCount) page = pageCount - 1;
  $: visibleControls = filteredControls.slice(page * controlsPerPage, page * controlsPerPage + controlsPerPage);
</script>

<svelte:window on:keydown={onKeydown} />

<div
  class="body-backdrop"
  role="presentation"
  on:click={(event) => event.target === event.currentTarget && close()}
>
  <div
    class="body-modal"
    role="dialog"
    aria-modal="true"
    aria-labelledby="body-title"
    tabindex="-1"
    bind:this={dialog}
  >
    <header class="panel-header body-header">
      <div>
        <span class="eyebrow">SMPL-X Studio</span>
        <h2 id="body-title">SMPL-X Body Studio</h2>
      </div>
      <div class="body-actions">
        <button class="icon-button" type="button" aria-label="Reset SMPL-X body vectors" on:click={resetSmplx}>
          <RotateCcw size={17} />
        </button>
        <button class="icon-button" type="button" aria-label="Close SMPL-X Body Studio" on:click={close}>
          <X size={17} />
        </button>
      </div>
    </header>

    <section class="body-toolbar" aria-label="SMPL-X control search">
      <label>
        <Search size={16} />
        <input bind:value={query} on:input={() => (page = 0)} placeholder="Search shape, body, hand, or orientation" />
      </label>
      <span>{filteredControls.length} controls</span>
    </section>

    <div class="body-layout">
      <aside class="body-summary" aria-label="SMPL-X vector groups">
        <div><strong>10</strong><span>shape betas</span></div>
        <div><strong>63</strong><span>body axes</span></div>
        <div><strong>45</strong><span>left hand axes</span></div>
        <div><strong>45</strong><span>right hand axes</span></div>
        <div><strong>3</strong><span>global axes</span></div>
      </aside>

      <section class="body-controls" aria-label="SMPL-X dense vector controls">
        {#each visibleControls as control}
          <label class="vector-field">
            <span>
              <strong>{control.label}</strong>
              <small>{control.group}</small>
            </span>
            <input
              type="range"
              min={control.min}
              max={control.max}
              step={control.step}
              value={project[control.scope][control.key][control.index]}
              aria-label={`${control.group} ${control.label}`}
              on:input={(event) => setValue(control, event.currentTarget.value)}
            />
            <input
              type="number"
              min={control.min}
              max={control.max}
              step={control.step}
              value={formatValue(project[control.scope][control.key][control.index])}
              aria-label={`${control.group} ${control.label} value`}
              on:input={(event) => setValue(control, event.currentTarget.value)}
            />
          </label>
        {/each}
      </section>
    </div>

    <footer class="body-pagination" aria-label="SMPL-X control pages">
      <button type="button" disabled={page === 0} on:click={() => (page -= 1)}>Previous</button>
      <span>Page {page + 1} / {pageCount}</span>
      <button type="button" disabled={page + 1 >= pageCount} on:click={() => (page += 1)}>Next</button>
    </footer>
  </div>
</div>

<style>
  .body-backdrop {
    position: fixed;
    inset: 0;
    z-index: 60;
    display: grid;
    place-items: center;
    padding: 24px;
    background: rgba(12, 12, 10, 0.74);
    backdrop-filter: blur(14px);
  }

  .body-modal {
    width: min(1060px, 100%);
    max-height: min(820px, calc(100vh - 48px));
    overflow: hidden;
    border: 1px solid var(--line);
    border-radius: 12px;
    background: #1d1d19;
    box-shadow: 0 28px 80px rgba(0, 0, 0, 0.4);
  }

  .body-header {
    margin: 0;
    padding: 18px 20px;
    border-bottom: 1px solid var(--line);
  }

  .body-actions {
    display: flex;
    gap: 8px;
  }

  .body-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 14px 20px;
    border-bottom: 1px solid var(--line);
    background: rgba(255, 255, 255, 0.02);
  }

  .body-toolbar label {
    display: flex;
    align-items: center;
    gap: 10px;
    flex: 1;
    min-width: 0;
    height: 38px;
    padding: 0 12px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: #151512;
    color: var(--muted);
  }

  .body-toolbar input {
    width: 100%;
    min-width: 0;
    border: 0;
    outline: 0;
    background: transparent;
    color: var(--text);
    font: inherit;
  }

  .body-toolbar span {
    color: var(--muted);
    font-size: 12px;
    white-space: nowrap;
  }

  .body-layout {
    display: grid;
    grid-template-columns: 210px 1fr;
    min-height: 500px;
  }

  .body-summary {
    display: grid;
    align-content: start;
    gap: 10px;
    padding: 20px;
    border-right: 1px solid var(--line);
    background: #171714;
  }

  .body-summary div {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  }

  .body-summary strong {
    font-size: 18px;
  }

  .body-summary span {
    color: var(--muted);
    font-size: 12px;
    text-align: right;
  }

  .body-controls {
    display: grid;
    align-content: start;
    gap: 10px;
    max-height: calc(min(820px, 100vh - 48px) - 154px);
    overflow-y: auto;
    padding: 18px 20px;
    scrollbar-width: thin;
    scrollbar-color: #47443d transparent;
  }

  .vector-field {
    display: grid;
    grid-template-columns: minmax(150px, 220px) minmax(160px, 1fr) 86px;
    align-items: center;
    gap: 14px;
    min-height: 48px;
    padding: 8px 10px;
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.025);
  }

  .vector-field span {
    min-width: 0;
  }

  .vector-field strong,
  .vector-field small {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .vector-field strong {
    font-size: 13px;
  }

  .vector-field small {
    color: var(--muted);
    font-size: 11px;
  }

  .vector-field input[type="range"] {
    width: 100%;
  }

  .vector-field input[type="number"] {
    width: 100%;
    min-width: 0;
    padding: 7px 8px;
    border: 1px solid var(--line);
    border-radius: 7px;
    background: #12120f;
    color: var(--text);
    font: inherit;
    font-size: 12px;
  }

  .body-pagination {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 12px;
    padding: 14px 20px;
    border-top: 1px solid var(--line);
  }

  .body-pagination button {
    min-width: 86px;
    padding: 8px 12px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: #2b2a24;
    color: var(--text);
    font: inherit;
    cursor: pointer;
  }

  .body-pagination button:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }

  .body-pagination span {
    color: var(--muted);
    font-size: 12px;
  }

  @media (max-width: 860px) {
    .body-backdrop {
      padding: 12px;
      place-items: stretch;
    }

    .body-modal {
      max-height: calc(100vh - 24px);
    }

    .body-layout {
      grid-template-columns: 1fr;
      min-height: 0;
    }

    .body-summary {
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 0;
      padding: 12px 16px;
      border-right: 0;
      border-bottom: 1px solid var(--line);
    }

    .body-summary div {
      display: block;
      padding: 0 6px;
      border-bottom: 0;
      text-align: center;
    }

    .body-summary span {
      display: block;
      text-align: center;
    }

    .body-controls {
      max-height: calc(100vh - 300px);
      padding: 14px;
    }

    .vector-field {
      grid-template-columns: 1fr 82px;
    }

    .vector-field input[type="range"] {
      grid-column: 1 / -1;
      grid-row: 2;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .body-backdrop,
    .body-modal {
      scroll-behavior: auto;
      transition: none;
      animation: none;
    }
  }
</style>
