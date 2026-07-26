<script>
  import { onMount } from "svelte";
  import {
    Aperture,
    Box,
    Check,
    CircleUserRound,
    Clapperboard,
    Cloud,
    CloudOff,
    Menu,
    PersonStanding,
    Save,
    Sparkles
  } from "@lucide/svelte";
  import { api, connectEvents } from "./lib/api.js";
  import BodyStudio from "./lib/BodyStudio.svelte";
  import ExpressionStudio from "./lib/ExpressionStudio.svelte";
  import Inspector from "./lib/Inspector.svelte";
  import RenderHistory from "./lib/RenderHistory.svelte";
  import Viewport from "./lib/Viewport.svelte";
  import { newProject, normalizeProject } from "./lib/project.js";

  const modes = [
    { label: "Character", icon: CircleUserRound },
    { label: "Pose", icon: PersonStanding },
    { label: "Scene", icon: Aperture },
    { label: "Render", icon: Sparkles }
  ];

  let mode = "Character";
  let project = normalizeProject(newProject());
  let runtime = {
    status: "offline",
    label: "Offline",
    progress: 0,
    detail: "Local editing is available.",
    provider: "local-preview"
  };
  let jobs = [];
  let notice = "";
  let saving = false;
  let socket;
  let geometryUrl = "";
  let geometryTimer;
  let uploadingReference = "";
  let bodyStudioOpen = false;
  let expressionStudioOpen = false;

  $: rendering = jobs.some((job) => ["queued", "freezing", "exporting", "packaging", "rendering"].includes(job.status));
  $: canRender = ["ready", "rendering"].includes(runtime.status);
  $: geometryKey = JSON.stringify({
    character: project.character,
    pose: project.pose,
    scene: project.scene,
    size: [project.render.width, project.render.height]
  });
  $: if (geometryKey) {
    window.clearTimeout(geometryTimer);
    geometryTimer = window.setTimeout(refreshGeometry, 350);
  }

  onMount(async () => {
    try {
      const [health, projects, existingJobs] = await Promise.all([
        api.health(),
        api.listProjects(),
        api.listJobs()
      ]);
      runtime = health;
      jobs = existingJobs;
      if (projects.length) project = normalizeProject(projects[0]);
      else project = normalizeProject(await api.createProject(project));
    } catch (error) {
      notice = `Controller unavailable: ${error.message}`;
    }
    socket = connectEvents((event) => {
      if (event.type === "runtime") runtime = event.runtime;
      if (event.type === "job") {
        const index = jobs.findIndex((item) => item.job_id === event.job.job_id);
        jobs = index === -1
          ? [event.job, ...jobs]
          : jobs.map((item) => (item.job_id === event.job.job_id ? event.job : item));
      }
    });
    return () => {
      socket?.close();
      window.clearTimeout(geometryTimer);
    };
  });

  async function refreshGeometry() {
    try {
      const result = await api.evaluateGeometry(project);
      geometryUrl = result.mesh_url;
    } catch {
      geometryUrl = "";
    }
  }

  async function saveProject() {
    saving = true;
    notice = "";
    try {
      project = normalizeProject(await api.saveProject(project));
      notice = "Project saved locally";
      window.setTimeout(() => (notice = ""), 2200);
    } catch (error) {
      notice = error.message;
    } finally {
      saving = false;
    }
  }

  async function startBackend() {
    notice = "";
    try {
      runtime = await api.startBackend("colab");
    } catch (error) {
      notice = error.message;
    }
  }

  async function addReference(role, file) {
    uploadingReference = role;
    notice = "";
    try {
      project = await api.uploadReference(project.project_id, role, file);
      notice = "Reference stored locally";
      window.setTimeout(() => (notice = ""), 2200);
    } catch (error) {
      notice = error.message;
    } finally {
      uploadingReference = "";
    }
  }

  async function removeReference(referenceId) {
    notice = "";
    try {
      project = await api.removeReference(project.project_id, referenceId);
      notice = "Reference removed from character";
      window.setTimeout(() => (notice = ""), 2200);
    } catch (error) {
      notice = error.message;
    }
  }

  async function renderFrame() {
    mode = "Render";
    notice = "";
    try {
      const job = await api.createJob(project, runtime.provider);
      jobs = [job, ...jobs.filter((item) => item.job_id !== job.job_id)];
    } catch (error) {
      notice = error.message;
    }
  }

  async function cancelJob(jobId) {
    try {
      const job = await api.cancelJob(jobId);
      jobs = jobs.map((item) => (item.job_id === job.job_id ? job : item));
    } catch (error) {
      notice = error.message;
    }
  }
</script>

<svelte:head>
  <title>{project.name} — FormFrame Studio</title>
</svelte:head>

<div class="app-shell">
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark"><Box size={19} strokeWidth={1.8} /><i></i></div>
      <div><strong>FormFrame</strong><span>Studio</span></div>
    </div>
    <div class="project-title">
      <span>Project</span>
      <input bind:value={project.name} aria-label="Project name" />
      <i>Local</i>
    </div>
    <div class="topbar-actions">
      {#if notice}<span class="toast" role="status">{notice}</span>{/if}
      <button class="save-button" on:click={saveProject} disabled={saving}>
        {#if saving}<span class="mini-loader"></span>{:else}<Save size={16} />{/if}
        {saving ? "Saving" : "Save"}
      </button>
      <button class="runtime-button {runtime.status}" on:click={startBackend} disabled={["provisioning","installing","restoring","starting","loading","warming","ready","rendering"].includes(runtime.status)}>
        {#if runtime.status === "offline"}<CloudOff size={16} />{:else if runtime.status === "ready"}<Check size={16} />{:else}<Cloud size={16} />{/if}
        <span><small>Backend</small><strong>{runtime.label}</strong></span>
        {#if !["offline", "ready", "rendering"].includes(runtime.status)}
          <i style={`--runtime-progress:${runtime.progress}%`}></i>
        {/if}
      </button>
    </div>
  </header>

  <main class="studio-grid">
    <nav class="mode-rail" aria-label="Studio modes">
      {#each modes as item}
        <button class:active={mode === item.label} on:click={() => (mode = item.label)} aria-label={item.label}>
          <svelte:component this={item.icon} size={19} strokeWidth={1.7} />
          <span>{item.label}</span>
        </button>
      {/each}
      <div class="rail-spacer"></div>
      <button aria-label="Project menu"><Menu size={19} /></button>
    </nav>

    <div class="stage-column">
      <Viewport {project} meshUrl={geometryUrl} />
      <div class="conditioning-strip">
        <div>
          <span class="eyebrow">Conditioning contract v1</span>
          <strong>One camera. One frame. Every guide aligned.</strong>
        </div>
        <div class="pass"><span class="rgb"></span><p><strong>RGB</strong><small>composition</small></p></div>
        <div class="pass"><span class="depth"></span><p><strong>Depth</strong><small>geometry</small></p></div>
        <div class="pass"><span class="pose"></span><p><strong>Pose</strong><small>joints</small></p></div>
        <div class="pass muted"><span></span><p><strong>Normals</strong><small>off</small></p></div>
      </div>
    </div>

    <Inspector
      bind:project
      {mode}
      {canRender}
      {rendering}
      {uploadingReference}
      onRender={renderFrame}
      onReference={addReference}
      onRemoveReference={removeReference}
      onOpenBody={() => (bodyStudioOpen = true)}
      onOpenExpression={() => (expressionStudioOpen = true)}
    />
  </main>

  {#if bodyStudioOpen}
    <BodyStudio
      bind:project
      onClose={() => (bodyStudioOpen = false)}
    />
  {/if}

  {#if expressionStudioOpen}
    <ExpressionStudio
      bind:project
      meshUrl={geometryUrl}
      onClose={() => (expressionStudioOpen = false)}
    />
  {/if}

  <RenderHistory {jobs} onCancel={cancelJob} />

  <footer class="statusbar">
    <span><i class="status-dot {runtime.status}"></i>{runtime.detail}</span>
    <span><Clapperboard size={14} /> controlled-character-v1</span>
    <span>Project data stays on this Mac</span>
  </footer>
</div>
