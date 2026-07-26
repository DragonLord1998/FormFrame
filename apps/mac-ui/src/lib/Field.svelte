<script>
  export let label;
  export let value;
  export let min = 0;
  export let max = 1;
  export let step = 0.01;
  export let unit = "";
  export let hint = "";
</script>

<label class="field">
  <span class="field-heading">
    <span>{label}</span>
    <output>{typeof value === "number" ? Math.round(value * (step < 1 ? 100 : 1)) / (step < 1 ? 100 : 1) : value}{unit}</output>
  </span>
  <input type="range" bind:value min={min} max={max} {step} aria-label={label} />
  {#if hint}<small>{hint}</small>{/if}
</label>

