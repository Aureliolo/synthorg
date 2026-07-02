export function DependencyMissingBanner() {
  return (
    <div className="rounded-lg border border-danger/30 bg-danger/5 p-card" role="alert">
      <h3 className="text-sm font-medium text-danger">
        Fine-tuning dependencies unavailable
      </h3>
      <p className="mt-1 text-sm text-muted-foreground">
        The preflight probe could not verify the fine-tuning stack. In a
        Docker install the backend boots an ephemeral probe container from
        the fine-tune image
        (<code className="font-mono">synthorg-fine-tune-gpu</code> /
        <code className="font-mono">synthorg-fine-tune-cpu</code>, which
        ships PyTorch + sentence-transformers); to enable it, run:
      </p>
      <code className="mt-2 block rounded bg-muted px-3 py-2 font-mono text-xs text-foreground">
        synthorg config set sandbox true
        <br />
        synthorg config set fine_tuning true
        <br />
        synthorg config set fine_tuning_variant gpu {'  '}# or: cpu
        <br />
        synthorg stop &amp;&amp; synthorg start
      </code>
      <p className="mt-2 text-sm text-muted-foreground">
        Stage containers are spawned on demand and removed when the stage
        finishes; there is no standing fine-tune service. Running a
        hand-managed <code className="font-mono">compose.yml</code> without
        the CLI, or the backend bare-metal? See the{' '}
        <a
          className="text-accent underline underline-offset-2 hover:no-underline"
          href="https://synthorg.io/docs/guides/deployment/#fine-tuning-optional"
          target="_blank"
          rel="noreferrer"
        >
          Fine-Tuning section of the Deployment guide
        </a>{' '}
        for the backend env snippet (or the in-process torch extras).
      </p>
    </div>
  )
}
