<script>
  import { ChevronDown, Dice5, Dumbbell, Sparkles } from "@lucide/svelte";
  import Field from "./Field.svelte";
  import {
    characterPresets,
    hairProxyLibrary,
    normalizeProject,
    outfitLibrary,
    posePresets
  } from "./project.js";

  export let mode;
  export let project;
  export let onRender;
  export let canRender;
  export let rendering;
  export let onReference;
  export let onRemoveReference;
  export let onIdentityLora;
  export let onRemoveIdentityLora;
  export let onOpenBody;
  export let onOpenExpression;
  export let uploadingReference = "";
  export let uploadingIdentityLora = false;

  let pendingLoraTriggerToken = "ff_identity";
  let pendingLoraStrength = 0.75;
  $: identityLora = project.character.identity_lora;

  const referenceSlots = [
    ["face_front", "Face front"],
    ["face_left", "Face left"],
    ["face_right", "Face right"],
    ["outfit", "Outfit"]
  ];

  const referenceFor = (role) =>
    project.character.references?.find((reference) => reference.role === role);

  const chooseReference = (event, role) => {
    const file = event.currentTarget.files?.[0];
    if (file) onReference(role, file);
    event.currentTarget.value = "";
  };

  const chooseIdentityLora = (event) => {
    const file = event.currentTarget.files?.[0];
    if (file) {
      onIdentityLora(file, {
        trigger_token: pendingLoraTriggerToken.trim(),
        strength: pendingLoraStrength
      });
    }
    event.currentTarget.value = "";
  };

  const loraSizeLabel = (bytes) => {
    const value = Number(bytes);
    if (!Number.isFinite(value) || value <= 0) return "size unknown";
    if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  };

  const applyPreset = (name) => {
    project.pose = { ...project.pose, preset: name, ...posePresets[name] };
    project = project;
  };

  const applyCharacterPreset = (name) => {
    const preset = characterPresets[name];
    project = normalizeProject({
      ...project,
      character: {
        ...project.character,
        ...preset,
        preset: name,
        appearance: {
          ...project.character.appearance,
          ...preset.appearance
        }
      }
    });
  };

  const applyHairProxy = (name) => {
    project.character.appearance = {
      ...project.character.appearance,
      hair_style: name,
      ...hairProxyLibrary[name]
    };
    project = project;
  };

  const applyOutfit = (name) => {
    project.character.appearance = {
      ...project.character.appearance,
      outfit: name,
      ...outfitLibrary[name]
    };
    project = project;
  };

  const randomizeSeed = () => {
    project.render.seed = Math.floor(Math.random() * 9999999);
    project = project;
  };
</script>

<aside class="inspector">
  {#if mode === "Character"}
    <header class="panel-header">
      <div><span class="eyebrow">Form</span><h2>Character</h2></div>
      <button class="icon-button" aria-label="Character menu"><ChevronDown size={17} /></button>
    </header>
    <div class="identity-card">
      <div class="avatar">{project.character.name.slice(0, 1)}</div>
      <div>
        <label for="character-name">Character name</label>
        <input id="character-name" class="bare-input" bind:value={project.character.name} />
      </div>
      <span>Guide 01</span>
    </div>
    <section class="control-group">
      <h3>Presence</h3>
      <label class="select-field">Character preset
        <select bind:value={project.character.preset} on:change={(event) => applyCharacterPreset(event.currentTarget.value)}>
          {#each Object.keys(characterPresets) as preset}
            <option value={preset}>{preset}</option>
          {/each}
        </select>
      </label>
      <Field label="Apparent age" bind:value={project.character.appearance.apparent_age} min={18} max={90} step={1} />
      <Field label="Height" bind:value={project.character.height} min={0.85} max={1.15} step={0.01} />
      <Field label="Build" bind:value={project.character.build} />
      <Field label="Shoulders" bind:value={project.character.shoulder_width} />
      <Field label="Leg proportion" bind:value={project.character.leg_length} />
      <button class="expression-launch" type="button" on:click={onOpenBody}>
        <Dumbbell size={17} />
        Open SMPL-X Body Studio
      </button>
    </section>
    <section class="control-group">
      <h3>Appearance</h3>
      <label class="select-field">Skin tone
        <span><input type="color" bind:value={project.character.appearance.skin_tone} />{project.character.appearance.skin_tone}</span>
      </label>
      <label class="select-field">Hair
        <select bind:value={project.character.appearance.hair_style} on:change={(event) => applyHairProxy(event.currentTarget.value)}>
          {#each Object.keys(hairProxyLibrary) as hair}
            <option value={hair}>{hair}</option>
          {/each}
        </select>
      </label>
      <label class="select-field">Guide outfit
        <select bind:value={project.character.appearance.outfit} on:change={(event) => applyOutfit(event.currentTarget.value)}>
          {#each Object.keys(outfitLibrary) as outfit}
            <option value={outfit}>{outfit}</option>
          {/each}
        </select>
      </label>
      <label class="text-field">Outfit direction
        <textarea bind:value={project.character.appearance.outfit_prompt} rows="3"></textarea>
      </label>
    </section>
    <section class="control-group">
      <h3>Identity training set</h3>
      <p class="section-help">Stored privately with this local project. These images are packaged by hash for a trained character LoRA; Z-Image Turbo does not support zero-shot face references.</p>
      <div class="reference-grid">
        {#each referenceSlots as [role, label]}
          {@const reference = referenceFor(role)}
          <div class:complete={reference} class="reference-slot">
            <div>
              <strong>{label}</strong>
              <small>{reference ? `${reference.width} × ${reference.height}` : "Optional WebP / PNG / JPEG"}</small>
            </div>
            {#if reference}
              <button type="button" on:click={() => onRemoveReference(reference.reference_id)}>Remove</button>
            {:else}
              <label>
                {uploadingReference === role ? "Adding…" : "Add"}
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  disabled={Boolean(uploadingReference)}
                  on:change={(event) => chooseReference(event, role)}
                />
              </label>
            {/if}
          </div>
        {/each}
      </div>
    </section>
    <section class="control-group">
      <h3>Identity LoRA</h3>
      <p class="section-help">Attach an already-trained Z-Image Turbo identity LoRA. This metadata is saved with the project and sent with render jobs; training remains outside the studio.</p>
      <div class:complete={identityLora} class="reference-slot">
        <div>
          <strong>{identityLora ? identityLora.filename : "No LoRA attached"}</strong>
          <small>
            {#if identityLora}
              {loraSizeLabel(identityLora.bytes)} · {identityLora.sha256?.slice(0, 12) || "hash pending"}
            {:else}
              .safetensors only
            {/if}
          </small>
        </div>
        {#if identityLora}
          <button type="button" on:click={onRemoveIdentityLora}>Remove</button>
        {:else}
          <label>
            {uploadingIdentityLora ? "Attaching…" : "Attach"}
            <input
              type="file"
              accept=".safetensors,application/octet-stream"
              disabled={uploadingIdentityLora}
              on:change={chooseIdentityLora}
            />
          </label>
        {/if}
      </div>
      {#if identityLora}
        <label class="select-field">Trigger token
          <input
            class="bare-input"
            aria-label="Identity LoRA trigger token"
            bind:value={project.character.identity_lora.trigger_token}
            on:input={() => (project = project)}
          />
        </label>
        <Field
          label="LoRA strength"
          bind:value={project.character.identity_lora.strength}
          min={0}
          max={1.5}
          step={0.05}
        />
      {:else}
        <label class="select-field">Trigger token
          <input
            class="bare-input"
            aria-label="Pending identity LoRA trigger token"
            bind:value={pendingLoraTriggerToken}
          />
        </label>
        <Field label="LoRA strength" bind:value={pendingLoraStrength} min={0} max={1.5} step={0.05} />
      {/if}
    </section>
  {:else if mode === "Pose"}
    <header class="panel-header">
      <div><span class="eyebrow">Direct</span><h2>Pose</h2></div>
      <span class="count">12 joints</span>
    </header>
    <section class="control-group">
      <h3>Pose language</h3>
      <div class="preset-grid">
        {#each Object.keys(posePresets) as preset}
          <button class:active={project.pose.preset === preset} on:click={() => applyPreset(preset)}>{preset}</button>
        {/each}
      </div>
    </section>
    <section class="control-group">
      <h3>Body</h3>
      <Field label="Torso twist" bind:value={project.pose.torso_twist} min={-45} max={45} step={1} unit="°" />
      <Field label="Hip shift" bind:value={project.pose.hip_shift} min={-0.4} max={0.4} step={0.01} />
      <Field label="Left arm" bind:value={project.pose.left_arm} min={-90} max={90} step={1} unit="°" />
      <Field label="Right arm" bind:value={project.pose.right_arm} min={-90} max={90} step={1} unit="°" />
      <Field label="Left elbow" bind:value={project.pose.left_elbow} min={0} max={130} step={1} unit="°" />
      <Field label="Right elbow" bind:value={project.pose.right_elbow} min={0} max={130} step={1} unit="°" />
      <button class="expression-launch" type="button" on:click={onOpenBody}>
        <Dumbbell size={17} />
        Open SMPL-X Body Studio
      </button>
    </section>
    <section class="control-group">
      <h3>Face & gaze</h3>
      <div class="expression-summary">
        <span><strong>{project.pose.expression}</strong><small>GNM head · {Math.round(project.pose.expression_strength * 100)}% expression</small></span>
        <Sparkles size={17} />
      </div>
      <p class="section-help">Edit the GNM face in its own focused workspace. The head and SMPL-X body stay as aligned meshes rather than being remeshed into one topology.</p>
      <button class="expression-launch" type="button" on:click={onOpenExpression}>
        <Sparkles size={17} />
        Open GNM Expression Studio
      </button>
    </section>
  {:else if mode === "Scene"}
    <header class="panel-header">
      <div><span class="eyebrow">Frame</span><h2>Scene</h2></div>
      <span class="count">Studio 01</span>
    </header>
    <section class="control-group">
      <h3>Camera</h3>
      <div class="segmented">
        {#each ["portrait", "square", "landscape"] as frame}
          <button class:active={project.scene.frame === frame} on:click={() => (project.scene.frame = frame)}>{frame}</button>
        {/each}
      </div>
      <Field label="Orbit" bind:value={project.scene.camera_yaw} min={-180} max={180} step={1} unit="°" />
      <Field label="Elevation" bind:value={project.scene.camera_pitch} min={-45} max={45} step={1} unit="°" />
      <Field label="Distance" bind:value={project.scene.camera_distance} min={3.5} max={9} step={0.1} />
      <Field label="Focal length" bind:value={project.scene.focal_length} min={24} max={135} step={1} unit=" mm" />
    </section>
    <section class="control-group">
      <h3>Light</h3>
      <Field label="Key" bind:value={project.scene.key_light} />
      <Field label="Fill" bind:value={project.scene.fill_light} />
      <label class="select-field">Environment
        <select bind:value={project.scene.background}>
          <option>Warm seamless</option><option>Slate studio</option><option>Night cyclorama</option>
        </select>
      </label>
      <label class="toggle-row">
        <span><strong>Ground plane</strong><small>Anchor the character with a soft shadow</small></span>
        <input type="checkbox" bind:checked={project.scene.floor_visible} />
      </label>
    </section>
  {:else}
    <header class="panel-header">
      <div><span class="eyebrow">Generate</span><h2>Render</h2></div>
      <span class="local-chip">Local preview</span>
    </header>
    <div class="notice">
      <Sparkles size={16} />
      <p><strong>Conditioning preview</strong><br />This build exports a real job bundle. Neural rendering activates when the Colab adapter is configured.</p>
    </div>
    <section class="control-group">
      <label class="text-field">Prompt
        <textarea bind:value={project.render.prompt} rows="5"></textarea>
      </label>
      <label class="text-field subtle">Negative prompt
        <textarea bind:value={project.render.negative_prompt} rows="3"></textarea>
      </label>
      <div class="seed-row">
        <label>Seed<input type="number" bind:value={project.render.seed} /></label>
        <button on:click={randomizeSeed} aria-label="Randomize seed"><Dice5 size={17} /></button>
      </div>
    </section>
    <section class="control-group">
      <h3>Control stack</h3>
      <Field label="Depth" bind:value={project.render.depth_strength} />
      <Field label="Pose" bind:value={project.render.pose_strength} />
      <Field label="Denoise" bind:value={project.render.denoise} />
      <p class="section-help">Identity is applied only when a trained character LoRA is attached. Uploaded reference images are not silently treated as an unsupported adapter.</p>
    </section>
    <section class="control-group">
      <h3>Output</h3>
      <div class="segmented quality">
        {#each ["Draft", "Studio", "Final"] as quality}
          <button class:active={project.render.quality === quality} on:click={() => (project.render.quality = quality)}>{quality}</button>
        {/each}
      </div>
      <div class="output-summary">
        <span>{project.render.width} × {project.render.height}</span>
        <span>RGB + depth + pose</span>
        <span>PNG final</span>
      </div>
    </section>
    <button class="render-button" disabled={!canRender || rendering} on:click={onRender}>
      <Sparkles size={18} />
      {rendering ? "Rendering frame…" : canRender ? "Render frame" : "Start backend first"}
    </button>
  {/if}
</aside>
