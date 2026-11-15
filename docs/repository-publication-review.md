# Repository publication review

Review date: 2026-11-15. Working branch: `dev-pillars`.

## Scope

This cleanup covers repository presentation, tracked local working material and
privacy screening. It does not change application code, tests, executable
configuration, governance policies or runtime contracts. It does not merge into
`main`, change repository visibility, choose a license or rewrite Git history.

## Repository hygiene

- Downloaded `.tools/opa_windows_amd64.exe` is removed from version control;
  `.tools/` is ignored. The local executable is retained. No tracked caller was
  found for this machine-specific binary.
- The 24 generated slide prompt jobs, `slide_jobs.json` and
  `slide_run_state.json` under `docs/presentations/governed_json_contracts/`
  are removed from version control and ignored. Local copies remain available.
  They include machine-specific paths and generation bookkeeping rather than
  architecture contracts. Editorial sources (`deck_spec.json`, `outline.md`,
  `speech.md`), slide images and the finished presentation remain tracked.
- The three curated chapter 3 vector PDFs are tracked under `figures/pdf/`,
  beside their canonical SVG/PNG sources. `output/` is reserved for ignored
  local exports. Figure links follow the curated location.
- The root README provides a repository guide. The documentation index uses
  relative links instead of paths into an author's Windows checkout.
- `AGENTS.md` records the publication hygiene convention. The existing runtime,
  environment and cache exclusions remain in place.

Thesis material, historical design notes, agent instructions and controlled
fictional evaluation data remain available because they explain the research
and its reproducibility. Historical material is not a replacement for the
maintained architecture and runtime contracts.

## Privacy screening and limits

A pattern scan inspected all 2,772 blobs reachable from the locally available
Git refs, including historical versions, and 199 text/XML members inside ZIP
containers such as PowerPoint presentations. Patterns covered recognizable
provider tokens, private-key headers and credentials embedded in connection
URLs. Four matches were inspected: a binary string in OPA and three historical
versions of a public NIST URL. All four were false positives. No actual secret
was identified by these checks. This is a bounded pattern scan, not a guarantee
that every possible credential format or confidential statement is absent.
Unfetched/deleted remote objects, GitHub issues, pull requests, Actions artifacts
and other service-side data are outside this local review.

Tracked environment examples and external-capability/database profiles were
inspected: they refer to environment variable names rather than credential
values. No tracked `.env`, runtime database or checkpoint was identified.
Text email references and PowerPoint core metadata were also inspected.
The inspected dashboard screenshots use an example-domain review recipient.
The review does not certify every pixel or all embedded binary metadata in
all historical visual artifacts.

## Matters to resolve before making the repository public

1. A personal evaluation recipient remains in
   `configs/evaluation_realistic_pool_v1.json` and
   `tests/test_evaluation_campaign_runner.py`. It is intentionally left unchanged
   because replacing it changes executable evaluation configuration and tests,
   outside the requested documentation-only cleanup. Decide whether to publish
   that address or authorize a separate coordinated anonymization.
2. Removing local material from the current tree does not remove old copies
   from Git history. Personal paths in the slide generation files, the large
   OPA binary, the evaluation address and commit attribution remain recoverable
   from historical commits. If any must be concealed, a separate history-cleanup
   decision is required before publication; this review performs no force push.
3. No repository-level license file was found. The owner should choose the
   intended reuse terms separately; this cleanup does not assign a license or
   infer rights for third-party tutorial/reference material.

## Validation

Validation for this change checks whitespace, local documentation links,
ignored-but-retained working files, PDF byte identity after relocation and an
unchanged code/configuration tree relative to the initial figure commit.
Application tests, lint, dead-code analysis and frontend builds are not run
because application code, tests, dependencies and executable configuration are
unchanged. No live agent, external capability or evaluation campaign is run.
