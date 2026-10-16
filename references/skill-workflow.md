# Watch2Read File Contract And Writing Standard

## Chapters

Read the video description and pinned comment first. When they contain genuine
chapter timestamps, preserve their top-level headings and hierarchy; do not treat
every child timestamp as a top-level chapter. Use `source: "meta"`. Otherwise
read the subtitle overview and choose semantic boundaries (`source: "llm"`).
About 5-15 chapters per hour is a guide, not a quota. A short video can have one.

Write valid JSON, without Markdown fences or comments:

```json
{
  "source": "meta",
  "chapters": [
    {"start_seconds": 0, "title": "开场与核心问题"},
    {"start_seconds": 153, "title": "研究路线"}
  ]
}
```

Start times must be nonnegative integer seconds, strictly increasing, with no
empty chapters. The opening chapter starts at zero or the first subtitle. Add an
opening chapter if the author's first chapter starts later. For semantic
boundaries, use a real subtitle timestamp; original author timestamps may fall
between subtitle lines. Never invent or round a semantic boundary.

`split` retains every subtitle line. It bounds long chapters into work units at
subtitle boundaries (default 20 minutes; configurable via `--max-batch-minutes`),
then merges results back into the original chapters. Choose smaller semantic
chapters where useful; avoid losing an argument at a work-unit boundary.

## Structured Writing

The API's canonical standard is `SYSTEM_PROMPT` in `structure_subtitle.py`.
Apply the same principles as the host agent:

- Each section covers a focused argument, event or discussion point.
- Preserve important claims, quantities, people, technical terms, reasoning,
  examples, disagreements and caveats as independent, complete statements.
- Do not turn long interviews into a few broad bullets. Aim for roughly 2-3
  substantial points per minute when information density supports it; this is a
  review heuristic, not permission to pad repeated claims.
- Write fluent Chinese by default, retaining meaningful original technical
  terms. Remove filler, not evidence. Attribute opinions/predictions to the
  speaker rather than stating them as independently verified facts.
- Correct clear recognition errors using context; mark uncertain names or claims
  cautiously rather than guessing. Summarize only the provided transcript.
- Read every unit fully. Overview sampling is for segmentation only, never for
  writing the actual note. Inspect the end of each unit before saving.

Each `result_file` contains exactly one object:

```json
{
  "batch_title": "本段主题",
  "sections": [
    {
      "title": "聚焦一个具体问题",
      "tldr": "一句话说明这一小节的核心观点。",
      "start_seconds": 153,
      "content": ["完整且具体的要点。", "保留独立论据、数据或例子。"]
    }
  ]
}
```

Every section needs a nonempty title, tldr and string-list content. Its timestamp
must be the earliest relevant subtitle line in this unit; the first section must
start at the unit's first subtitle. Section times are chronological. The manifest
contains allowed `timestamps`. Avoid duplicate coverage in adjoining units.

## Files, Resume And Updates

`prepare` prints an absolute `WORK`, normally
`notes/.watch2read/<video-id>/`. `task.json` records canonical URL, output directory
and stem. `meta.json`, `source.srt`, `entries.json`, `overview.txt` are source data.
The agent writes `chapters.json`; `split` writes `segments.json`, `manifest.json`
and `batches/<plan-hash>/*.txt`. Write to `results/<plan-hash>/*.json` as specified
in the manifest. All paths inside the manifest are relative to `WORK`.

Resume by inspecting the current manifest and completing missing result files.
Do not re-download unnecessarily. Re-running split on the same inputs reuses the
same plan; different subtitles/chapters/batch size produce a different result
directory so outdated JSON cannot silently enter a new note. Do not edit source
or batch files to make results pass validation.

For a requested chapter update, regenerate only its result files, preserve other
units, and finalize with `--overwrite`. For segmentation changes, rerun split and
complete the new manifest. When only flat API artifacts remain, prepare with
`--srt notes/<stem>.srt --meta notes/<stem>.meta.json`, then split using the existing
`notes/<stem>.segments.json`. Reconstruct all required unit results before
finalizing; do not present a partially rewritten note as complete.

Final `.json` is an array of `{batch_title, sections}` matching the API, not a
manifest. `.segments.json` uses `{source, chapters}`; `llm` means semantic
segmentation and does not identify the processing mode. The README's separate
`处理方式` column identifies `api` versus `skill`. Final Markdown is rendered by
the shared `render_document`, not manually reformatted by the agent.

Review the final Markdown for chapter continuity, supported timestamps and detail
density. Shape/timestamp validation cannot prove semantic completeness; do a
content spot check across the beginning, middle and end. Optional PDF failure
must be reported, not silently counted as a complete PDF export.

For multiple videos, parallelize reading and writing, not shared indexing. A
single coordinator must run finalization sequentially. If workers need to render
their own notes, use `--skip-index` there, then rerun identical finalization from
the coordinator without that flag, one video at a time. No `--overwrite` is
needed for identical artifacts. The shared indexes have no cross-process lock.
