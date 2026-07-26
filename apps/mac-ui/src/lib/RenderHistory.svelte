<script>
  import { Download, Package, X } from "@lucide/svelte";

  export let jobs = [];
  export let onCancel;
</script>

<section class="history-panel" aria-labelledby="history-title">
  <header>
    <div>
      <span class="eyebrow">Output</span>
      <h2 id="history-title">Frames</h2>
    </div>
    <span>{jobs.length ? `${jobs.length} saved` : "No renders yet"}</span>
  </header>
  {#if jobs.length === 0}
    <div class="history-empty">
      <div class="empty-mark">FF</div>
      <p>Your composed frames will collect here, with the pose, seed, workflow and bundle kept together.</p>
    </div>
  {:else}
    <div class="history-track">
      {#each jobs as job (job.job_id)}
        <article class:pending={job.status !== "completed"} class:failed={job.status === "failed"}>
          <div class="thumb">
            {#if job.status === "completed"}
              <img src={`/v1/jobs/${job.job_id}/preview?at=${job.updated_at}`} alt="Completed conditioning preview" />
              <span class="simulation-label">Local preview</span>
            {:else}
              <div class="progress-visual"><i style={`--progress:${job.progress}%`}></i><span>{job.progress}%</span></div>
            {/if}
          </div>
          <div class="job-copy">
            <strong>{job.stage}</strong>
            <span>{job.job_id.slice(-8)} · seed {job.manifest?.seed ?? "—"}</span>
            <small>{job.workflow}</small>
          </div>
          <div class="job-actions">
            {#if job.status === "completed"}
              <a href={`/v1/jobs/${job.job_id}/bundle`} aria-label="Download job bundle" title="Download .ffjob"><Package size={16} /></a>
              <a href={`/v1/jobs/${job.job_id}/result`} aria-label="Download final image" title="Download PNG"><Download size={16} /></a>
            {:else if !["failed", "cancelled"].includes(job.status)}
              <button on:click={() => onCancel(job.job_id)} aria-label="Cancel render"><X size={16} /></button>
            {/if}
          </div>
        </article>
      {/each}
    </div>
  {/if}
</section>
