---
name: film-brief-cleaning
description: Clean Chinese film, TV, or variety public-opinion exports at source level, discover period-specific viewpoint clusters, select traceable excerpts, and generate a verified offline HTML workbench. Use for 影视简报、舆情样本清洗、观点聚类、典型样本筛选和影视观点工作台；historical reports and old cards are evaluation material only.
---

# Film Brief Cleaning

Generate an information workbench for human report writing. Keep every qualified independent expression, organize it into data-derived one-level viewpoint clusters, and preserve exact source evidence. Do not draft the final report.

## Hard execution gate

- Production runs use only `scripts/film_workflow.py`. Call `film_pipeline.py` directly only to diagnose a controller failure.
- During normal `READY_TO_ADVANCE` or `REVIEW_REQUIRED` states, read only the current input, template, and referenced contract. Do not inspect script source, scan every run artifact, or create helper programs; those actions are reserved for a real `BROKEN` diagnosis.
- Never create, edit, copy, or rename an HTML file into the requested output path. The controller writes a staged candidate, verifies it, and publishes the official HTML only after `verification.json` is `PASS`.
- Deliver only when `workflow_status.json` says `COMPLETE`. An existing HTML, `REVIEW_REQUIRED`, `BLOCKED`, or `BROKEN` is not a result.
- Do not replace failed semantic reviews with exclusions merely to finish. Correct the evidence, assignment, cluster definition, or excerpt indicated by the status file.

## Run the controller

Use Python 3.10+ and install `requirements.txt`. On Windows, use a real Python interpreter rather than the Microsoft Store placeholder.

```powershell
<python> scripts/film_workflow.py doctor --source <原始数据目录> --period-config <period_config.json> --workspace <空工作区> --output <单期工作台.html>
<python> scripts/film_workflow.py init --source <原始数据目录> --period-config <period_config.json> --workspace <空工作区> --output <单期工作台.html>
```

If no period config exists, omit `--period-config` from both commands and fill the generated template. After initialization, repeat only:

```powershell
<python> scripts/film_workflow.py advance --workspace <工作区>
```

Follow `workflow_status.json` literally:

- `READY_TO_ADVANCE`: run `advance` again.
- `REVIEW_REQUIRED`: copy the stated template to `required_file`, preserve `_workflow`, and complete only the records in the current `input_file`. For source and final-excerpt review, the controller merges this small submission into a script-owned ledger; do not copy prior answers into the new submission. Run `advance` again to receive the next chunk. Never read all chunks up front. `cluster_discovery` is the sole exception: read its complete `input_files` seed before defining clusters. Use `full_input_file` only for auditing and `full_source_file` only when one item needs more context.
- `BLOCKED` or `BROKEN`: fix the reported cause. Do not bypass the controller or invent another output.
- `COMPLETE`: open the published HTML, visually inspect the period tabs, stance tabs, cluster titles, excerpts, links, scrolling, spacing, and counts, then deliver it.

Never run two controller commands against one workspace simultaneously. Never reuse reviews from another workspace or an older fingerprint.

## Semantic stages

Read [workflow.md](references/workflow.md), [contracts.md](references/contracts.md), and [review-rubric.md](references/review-rubric.md) when completing their corresponding reviews.

1. **Period configuration.** Set `serial_drama` for continuously updated drama and `episodic_variety` for weekly variety. Put exact work names in `strong_terms`, ambiguous bare titles in `weak_terms`, people and roles in `auxiliary_terms`, and other works in `comparison_terms`. Old reports, cards, old clusters, and expected sample counts must not enter the run.
2. **Source review.** Review only the generated compact queue. Decide `retain_core`, `retain_consensus`, or `exclude`, and choose `evidence_candidate_index` whenever possible. Retained sources need no repeated prose reason; exclusions need one short, source-specific reason. Exact text copies share one representative decision. For variety, retain the latest episode, a genuinely prominent new topic from the immediately previous episode, or current program-level discussion.
3. **Link and copy checks.** The controller checks links only for sources still eligible after content review. Delete only a 404 or 410 reproduced by two GET requests. Login walls, 401/403/429, anti-bot responses, 5xx, DNS failures, and timeouts are indeterminate. Exact and high-confidence same-copy families are merged deterministically. Medium-similarity pairs remain independent by default, because similar viewpoints from different authors are useful evidence.
4. **Cluster discovery.** Read every file listed in `input_files` under `cluster_discovery_chunks` and derive current-period positive, objective, and negative clusters. The chunks are a stance- and channel-stratified discovery seed; all retained sources are still assigned and coverage-checked later. Each non-background cluster needs one to four aspect-bearing `required_any` terms; work names, person names, “剧情”, “热度”, or generic praise cannot be the routing basis. If many sources fail aspect alignment, the controller returns `cluster_definition_repair`: repair missing viewpoints or synonym terms before writing individual overrides.
5. **Assignment repair.** For the current residual chunk, align target work, aspect, local stance, and verbatim passage. Submit only the IDs in the current template; the controller merges prior rounds through its ledger. An item still present after an override means that override failed and must change. A valid opinion should move to a fitting cluster or a newly evidenced cluster. Use `__exclude__` only for evidenced cross-work mismatch, out-of-scope text, no reportable viewpoint, abusive noise, pure promotion, or corrupt duplication.
6. **Cluster-set review.** Review titles, stance purity, period or episode scope, source role, and granularity from representative members. Return one decision, an empty `issues` list when passed, one reason, and title-claim support; scripts own counts, range status, fingerprints, and mechanical confirmations. Objective clusters may contain neutral facts, observations, or neutral summaries of opinion distribution; individual positive and negative evaluations belong in their matching stance clusters. A normal period usually has 9–16 one-level clusters, but this is a review range, never a quota.
7. **Final excerpt review.** The controller first returns deterministic excerpt-format failures for targeted repair, then starts the single item-level semantic pass. Scripts preselect aspect, stance and evidence indexes; verify them and change only mistakes, then fill `decision` and `self_contained`. Do not copy text when an indexed segment works, and normal `keep` needs no reason. Fill target or work-consistency fields only when explicitly flagged. A short excerpt is not re-excerpted merely to reach 70 characters: keep it when its selected evidence contains a concrete action, quotation, scene, data point or causal analysis; generic praise is dropped or re-excerpted. Re-excerpt or reassign a repairable item before dropping it. Unchanged item fingerprints keep their completed reviews after a targeted excerpt repair.
8. **Render and verify.** Scripts remove hashtags, mentions, emoji or platform emotes, links, and share boilerplate while preserving raw positions. Positive, objective, and negative evidence must stay separate. The controller stages the page, validates data equality, provenance, unresolved queues, link policy, and offline assets, then atomically publishes the official HTML.

## Domain rules

- Source admission requires a target-work connection plus a usable judgment or relevant factual context. Person names are routing helpers and cannot admit or cluster a source by themselves.
- A mixed article may contribute separate positive and negative passages when each passage is complete, non-overlapping, and locally aligned. A reversal elsewhere in the article does not invalidate a usable passage.
- Preserve independent authors who express the same opinion. Deduplicate actual reposts, copied manuscripts, and clear rewrites of one manuscript.
- Mainstream status comes from exact reviewed account aliases, verified parent entities, or verified first-party domains in `assets/media_subject_registry.json`. Certification is supporting evidence only. Authority changes display priority, not relevance or truth.
- Explicit AI-generation disclosures, leaked drafting prompts, and model meta-output are source-level exclusions. Ordinary discussion about AI is allowed.
- Drama uses the continuous monitoring window. Variety centers the latest episode; the prior episode enters only for a newly prominent topic during the current week.
- Short video without usable ASR is excluded from text evidence. Pure schedules, cast profiles, plot summaries, giveaways, ticketing, QR codes, and campaign instructions cannot support a viewpoint.
- The page is a self-contained local HTML file with no remote assets and no active `*.woa.com` links. A cumulative workbench is built only with the controller's `merge` command from verified standalone pages.

## Division of work

Scripts own extraction, stable IDs, deterministic cleanup, hard AI-draft patterns, reviewed media lookup, safe link checks, exact-copy handling, scoring, fingerprints, evidence indexing and backfill, source-position checks, HTML generation, and verification. AI owns ambiguous source relevance, irony and mixed stance, current-period cluster discovery, residual assignment, cluster-set judgment, and final excerpt semantics.

Completion requires `workflow_status.json: COMPLETE`, `render_summary.json: RENDERED`, `verification.json: PASS`, no unresolved review queues, exact HTML/dataset equality, and a visual inspection. Report final sample count and final one-level cluster count separately.
