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
  let coefficientBank = "identity";
  let coefficientSearch = "";
  let coefficientPage = 0;

  const pageSize = 24;
  const axisNames = ["x", "y", "z"];
  const gnmJointNames = ["neck", "head", "left_eye", "right_eye"];
  const lockedGnmJoints = new Set(["neck", "head"]);
  const identityCoefficientNames = [
    ...Array.from({ length: 170 }, (_, index) => `head_${String(index).padStart(3, "0")}`),
    ...Array.from({ length: 80 }, (_, index) => `teeth_${String(index).padStart(3, "0")}`),
    ...Array.from({ length: 3 }, (_, index) => `eyes_${String(index).padStart(3, "0")}`)
  ];
  const expressionCoefficientNames = [
    ...Array.from({ length: 100 }, (_, index) => `left_eye_region_${String(index).padStart(3, "0")}`),
    ...Array.from({ length: 100 }, (_, index) => `right_eye_region_${String(index).padStart(3, "0")}`),
    ...Array.from({ length: 150 }, (_, index) => `lower_face_region_${String(index).padStart(3, "0")}`),
    "tongue_mean",
    ...Array.from({ length: 31 }, (_, index) => `tongue_${String(index).padStart(3, "0")}`),
    "pupils_000"
  ];

  const close = () => onClose?.();

  const onKeydown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  };

  const ensurePose = () => {
    project.character = {
      ...project.character,
      identity: normalizeArray(project.character?.identity, 253)
    };
    project.pose = {
      expression: "Quiet confidence",
      expression_strength: 0,
      head_turn: 0,
      head_tilt: 0,
      gaze_x: 0,
      gaze_y: 0,
      gnm_expression: Array(383).fill(0),
      gnm_joint_rotations: Array(12).fill(0),
      ...project.pose
    };
    project.pose.gnm_expression = normalizeArray(project.pose.gnm_expression, 383);
    project.pose.gnm_joint_rotations = normalizeArray(project.pose.gnm_joint_rotations, 12);
    project = project;
  };

  const normalizeArray = (value, size) => {
    const source = Array.isArray(value) ? value : [];
    return Array.from({ length: size }, (_, index) => Number(source[index] || 0));
  };

  const coefficientNamesForBank = (bank) =>
    bank === "expression" ? expressionCoefficientNames : identityCoefficientNames;

  const coefficientValuesForBank = (bank) =>
    bank === "expression"
      ? project?.pose?.gnm_expression || []
      : project?.character?.identity || [];

  const updateCoefficient = (bank, index, value) => {
    const scope = bank === "expression" ? "pose" : "character";
    const field = bank === "expression" ? "gnm_expression" : "identity";
    const next = [...project[scope][field]];
    next[index] = Number(value);
    project = {
      ...project,
      [scope]: { ...project[scope], [field]: next }
    };
  };

  const updateJointRotation = (joint, axis, value) => {
    if (lockedGnmJoints.has(joint)) return;
    const jointIndex = gnmJointNames.indexOf(joint);
    const axisIndex = axisNames.indexOf(axis);
    const index = jointIndex * 3 + axisIndex;
    const next = [...project.pose.gnm_joint_rotations];
    next[index] = Number(value);
    project.pose = {
      ...project.pose,
      gnm_joint_rotations: next
    };
    project = { ...project, pose: project.pose };
  };

  const jointRotation = (joint, axis) => {
    const index = gnmJointNames.indexOf(joint) * 3 + axisNames.indexOf(axis);
    return project.pose.gnm_joint_rotations[index] || 0;
  };

  const resetVisibleCoefficients = () => {
    for (const coefficient of pagedCoefficients) {
      updateCoefficient(coefficientBank, coefficient.index, 0);
    }
  };

  $: activeNames = coefficientNamesForBank(coefficientBank);
  $: activeValues = coefficientValuesForBank(coefficientBank);
  $: normalizedSearch = coefficientSearch.trim().toLowerCase();
  $: filteredCoefficients = activeNames
    .map((name, index) => ({ name, index, value: activeValues[index] || 0 }))
    .filter((coefficient) => !normalizedSearch || coefficient.name.includes(normalizedSearch));
  $: pageCount = Math.max(1, Math.ceil(filteredCoefficients.length / pageSize));
  $: coefficientPage = Math.min(coefficientPage, pageCount - 1);
  $: pageStart = coefficientPage * pageSize;
  $: pagedCoefficients = filteredCoefficients.slice(pageStart, pageStart + pageSize);
  $: activeBankLabel = coefficientBank === "expression" ? "Expression" : "Identity";

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
          <h3>Aligned head pose</h3>
          <p class="section-help">
            These convenience controls rotate the SMPL-X neck and head chain so the separate GNM head stays aligned. They do not rotate GNM neck or head joints.
          </p>
          <Field label="Head turn" bind:value={project.pose.head_turn} min={-60} max={60} step={1} unit="°" />
          <Field label="Head tilt" bind:value={project.pose.head_tilt} min={-35} max={35} step={1} unit="°" />
        </section>

        <section class="control-group">
          <h3>Gaze</h3>
          <Field label="Gaze horizontal" bind:value={project.pose.gaze_x} min={-1} max={1} step={0.01} />
          <Field label="Gaze vertical" bind:value={project.pose.gaze_y} min={-1} max={1} step={0.01} />
        </section>

        <section class="control-group advanced-gnm-controls">
          <h3>Advanced coefficients</h3>
          <div class="coefficient-toolbar">
            <label class="select-field">Bank
              <select
                bind:value={coefficientBank}
                on:change={() => {
                  coefficientPage = 0;
                }}
              >
                <option value="identity">Identity basis · 253</option>
                <option value="expression">Expression · 383</option>
              </select>
            </label>
            <label class="search-field">Search
              <input
                type="search"
                bind:value={coefficientSearch}
                on:input={() => {
                  coefficientPage = 0;
                }}
                placeholder={`${activeBankLabel.toLowerCase()} coefficient`}
                aria-label="Search GNM coefficients"
              />
            </label>
          </div>

          <div class="coefficient-pager" aria-label={`${activeBankLabel} coefficient pages`}>
            <button
              type="button"
              on:click={() => {
                coefficientPage = Math.max(0, coefficientPage - 1);
              }}
              disabled={coefficientPage === 0}
            >
              Previous
            </button>
            <span>{filteredCoefficients.length} controls · page {coefficientPage + 1} of {pageCount}</span>
            <button
              type="button"
              on:click={() => {
                coefficientPage = Math.min(pageCount - 1, coefficientPage + 1);
              }}
              disabled={coefficientPage >= pageCount - 1}
            >
              Next
            </button>
          </div>

          <div class="coefficient-list" aria-label={`${activeBankLabel} coefficient controls`}>
            {#each pagedCoefficients as coefficient (coefficient.name)}
              <label class="coefficient-row">
                <span>{coefficient.name}</span>
                <input
                  type="range"
                  min="-1"
                  max="1"
                  step="0.01"
                  value={coefficient.value}
                  aria-label={coefficient.name}
                  on:input={(event) => updateCoefficient(coefficientBank, coefficient.index, event.currentTarget.value)}
                />
                <input
                  type="number"
                  min="-1"
                  max="1"
                  step="0.01"
                  value={coefficient.value}
                  aria-label={`${coefficient.name} value`}
                  on:input={(event) => updateCoefficient(coefficientBank, coefficient.index, event.currentTarget.value)}
                />
              </label>
            {/each}
          </div>

          <button class="text-button" type="button" on:click={resetVisibleCoefficients}>
            Reset visible
          </button>
        </section>

        <section class="control-group">
          <h3>GNM joint rotations</h3>
          <p class="section-help">
            GNM neck and head joints are locked here because SMPL-X owns global neck and head orientation. Only GNM eye joint rotations are editable in this modal.
          </p>
          <div class="joint-grid">
            {#each gnmJointNames as joint}
              <div class:locked={lockedGnmJoints.has(joint)} class="joint-card">
                <strong>{joint.replace("_", " ")}</strong>
                {#each axisNames as axis}
                  <label class="coefficient-row compact">
                    <span>{axis.toUpperCase()}</span>
                    <input
                      type="range"
                      min="-45"
                      max="45"
                      step="0.5"
                      value={jointRotation(joint, axis)}
                      disabled={lockedGnmJoints.has(joint)}
                      aria-label={`${joint} ${axis} rotation`}
                      on:input={(event) => updateJointRotation(joint, axis, event.currentTarget.value)}
                    />
                    <input
                      type="number"
                      min="-45"
                      max="45"
                      step="0.5"
                      value={jointRotation(joint, axis)}
                      readonly={lockedGnmJoints.has(joint)}
                      disabled={lockedGnmJoints.has(joint)}
                      aria-label={`${joint} ${axis} rotation value`}
                      on:input={(event) => updateJointRotation(joint, axis, event.currentTarget.value)}
                    />
                  </label>
                {/each}
              </div>
            {/each}
          </div>
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

  .coefficient-toolbar,
  .coefficient-pager {
    display: grid;
    gap: 10px;
  }

  .search-field {
    display: grid;
    gap: 8px;
    color: #d5d0c5;
    font-size: 11px;
  }

  .search-field input,
  .coefficient-row input[type="number"] {
    min-width: 0;
    border: 1px solid rgba(238, 229, 211, 0.13);
    border-radius: 6px;
    padding: 8px 9px;
    color: #eee5d3;
    background: #171714;
  }

  .coefficient-pager {
    grid-template-columns: 78px 1fr 78px;
    align-items: center;
    margin: 14px 0 12px;
    color: var(--muted);
    font-size: 10px;
    text-align: center;
  }

  .coefficient-pager button,
  .text-button {
    min-height: 32px;
    border: 1px solid rgba(238, 229, 211, 0.13);
    border-radius: 6px;
    color: #d5d0c5;
    background: #24231f;
  }

  .coefficient-pager button:disabled {
    opacity: 0.45;
  }

  .coefficient-list,
  .joint-grid {
    display: grid;
    gap: 8px;
  }

  .coefficient-row {
    display: grid;
    grid-template-columns: minmax(118px, 1fr) minmax(88px, 112px) 58px;
    gap: 8px;
    align-items: center;
    min-height: 36px;
    color: #d5d0c5;
    font-size: 10px;
  }

  .coefficient-row span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .coefficient-row input[type="range"] {
    width: 100%;
  }

  .coefficient-row input[type="number"] {
    padding: 6px;
    text-align: right;
  }

  .text-button {
    width: 100%;
    margin-top: 12px;
  }

  .joint-card {
    display: grid;
    gap: 6px;
    padding: 10px 0;
    border-top: 1px solid rgba(238, 229, 211, 0.08);
  }

  .joint-card strong {
    color: #cbc6bb;
    font-size: 11px;
    font-weight: 600;
    text-transform: capitalize;
  }

  .joint-card.locked {
    opacity: 0.72;
  }

  .joint-card.locked input {
    cursor: not-allowed;
  }

  .coefficient-row.compact {
    grid-template-columns: 18px minmax(96px, 1fr) 58px;
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

    .coefficient-row,
    .coefficient-row.compact {
      grid-template-columns: minmax(92px, 1fr) minmax(74px, 1fr) 54px;
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
