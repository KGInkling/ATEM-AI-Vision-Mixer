# Automatic Claude review

Keep a pull request **draft** while its checks and review run. After a successful
`ci` run, `Schedule Claude Review` verifies that the PR is open, draft, in this
repository, targeting the default branch, and still at the commit CI checked.
It waits up to five minutes for all six deterministic required contexts, including
CodeQL, to pass, then dispatches `claude-review.yml` from the default branch.

Opening, updating, reopening, or converting a PR back to draft triggers CI and
therefore this scheduling path. Re-running CI also retries scheduling. Closed,
ready, forked, stale, wrong-base, or failing PRs do not launch a model review.
Missing checks remain unavailable; no passing status is fabricated.

The dispatcher runs on `workflow_run` using trusted default-branch code. It reads
GitHub metadata only, with Actions write permission to dispatch and PR, check, and
status read permissions to verify eligibility. It has no checkout, artifact download, or Claude
credential. The original four-job reviewer remains the sole owner of review
validation, model invocation, and publication. Its concurrency group serializes
requests for the same PR. An existing marked review for the same base/head skips
another model call without changing its status or verdict.

The Claude action explicitly allows `github-actions[bot]`, the dispatcher identity.
Other bot actors retain the action's default rejection policy.

## Model assets

The only binary exception is
`atem_ai_vision_mixer/perception/models/silero_vad.onnx`, exactly 2,327,524 bytes,
with SHA-256 `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`
(Silero v6.2.3). These values live in the trusted workflow, never in PR-controlled
approval metadata. Both existing diff-side versions must be regular non-executable
Git blobs matching the pin.

The model is excluded from diff text and source snapshots even if Git attributes
force it to be treated as text. Claude receives only path, revision, size, checksum,
and an explicit content-omitted marker. Its adjacent license and provenance remain
reviewable text. Claude reviews integration code and provenance, not model weights.

Other binary changes, unknown model versions, symlinks, submodules, sensitive paths,
and detected credential content remain blocked. Rename detection stays disabled.
A new model version needs a separately reviewed trusted-policy change covering the
old and new versions before a model-update PR can pass.

## Results and retries

The `claude-review` status becomes successful only after a marked review is published
and read back for the frozen base/head. Read its verdict: successful publication can
contain findings and is not merge approval. The historical
`<!-- manual-claude-review:v1 -->` marker is retained for deduplication.

If scheduling times out waiting for checks, rerun CI after they finish. If the model
or publisher fails, use GitHub's **Re-run failed jobs** on that review run. Keep the
PR draft and the reviewed revision unchanged during the run. A new commit starts a
new review scope. Manual dispatch remains available for recovery.

The dispatcher and asset exception become active only after this workflow change
is merged to the default branch. Existing PRs can then be updated from main or have
CI rerun. Rollback is a revert of this workflow change: automatic scheduling stops
and model-bearing PRs again encounter the binary guard. Branch protections stay on.
