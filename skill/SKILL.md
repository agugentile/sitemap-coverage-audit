---
name: sitemap-coverage-audit
description: Find website pages that the uptime or monitoring catalog does not cover, and monitoring entries for pages that no longer exist. Use when asked to audit monitoring coverage against a sitemap, look for unmonitored pages, clean up stale checks, or propose additions to a monitoring catalog. Runs the sitemap-audit CLI, then opens a pull request with the proposed catalog additions for a human to review.
---

# Sitemap coverage audit

This skill runs `sitemap-audit`, a command line tool that compares a website's sitemap (plus any
extra URL lists) with a monitoring catalog. It reports three things: pages that are live but
unmonitored, catalog entries for pages that are no longer live (stale), and a coverage
percentage.

## Rules that always apply

- **A production catalog is changed only through a reviewed pull request, never directly.**
  Do not edit the catalog on the default branch, do not push to it, and do not merge your own
  pull request.
- Do not call a monitoring provider's API to create, change or delete checks. The catalog file
  in the repository is the only thing you propose changes to.
- `sitemap-audit` never edits the catalog. `--propose` writes a separate new file. Treat that
  file as a draft for a person to review.
- Do not remove stale entries on your own. List them in the pull request and let a person decide.
- Do not invent URLs, sitemaps or extra sources. Use only what the user gave you or what the
  repository contains.

## Before you start

Find out, from the user or from the repository:

1. The sitemap URL (a sitemap index is fine, it is followed recursively).
2. The path of the monitoring catalog in the repository: a JSON list of URL strings, or of
   objects with a `url` field.
3. Any extra URL lists. A sitemap alone usually misses some pages, for example pages that a CMS
   publishes but does not list. A JSON or `.txt` export of those pages is worth adding.
4. URL patterns that should not be monitored (drafts, admin pages, file downloads). These become
   `--ignore` regexes.

If `sitemap-audit` is not installed, install it from a clone of its repository with
`uv tool install .`, or run it there with `uv run sitemap-audit`.

## Steps

1. **Run the audit.** Keep the output outside the repository so it is not committed by mistake.

   ```bash
   out=$(mktemp -d)
   sitemap-audit \
     --sitemap https://www.example.com/sitemap.xml \
     --catalog monitoring/catalog.json \
     --extra cms-export.json \
     --ignore '/draft/' '\.pdf$' \
     --format markdown \
     --propose "$out/proposed.json" > "$out/report.md"
   ```

   Exit codes: `0` the audit ran, `1` gaps found (only with `--fail-on-gaps`), `2` usage or input
   error. On exit code 2, read the message on stderr and fix the input. Do not retry blindly.

2. **Read the report** (`$out/report.md`) before doing anything else.
   - Check the coverage line and the counts. If there are far fewer live pages than the site
     should have, or coverage is 0%, the sitemap URL is probably wrong (a staging site, an old
     file). Stop and ask the user.
   - Unmonitored pages marked "extra sources only" were missing from the sitemap. Make sure they
     are real public pages before proposing them.
   - Unmonitored pages that should not be monitored: add an `--ignore` pattern and run again
     instead of editing the result by hand.
   - Stale entries may point to pages that were removed, renamed, or left out of the sitemap by
     mistake. You cannot tell which from the report. Leave them for the reviewer.
   - Read the warnings, for example a sitemap index that lists itself.

3. **Prepare the change on a new branch.** If there are no unmonitored pages, no proposal file is
   written and there is nothing to change: tell the user and stop.

   ```bash
   git switch -c monitoring/add-unmonitored-pages
   ```

   The proposal is a copy of the catalog with the new URLs added at the end. If the catalog uses
   two-space JSON indentation, `diff monitoring/catalog.json "$out/proposed.json"` shows only
   added lines, and you can copy the proposal over the catalog file. If the diff shows anything
   else, leave the file as it is and add the new entries by hand in the file's own style. Never
   change or reorder existing entries.

   If catalog entries carry more fields than `url` (a name, an interval, who to alert), follow
   the pattern of the neighbouring entries for the name and interval, and do not guess who gets
   alerts. Say in the pull request what the reviewer still has to fill in.

4. **Check the result.** Run the audit again with the changed catalog and `--fail-on-gaps`. It
   should exit with `0`, apart from pages you chose to ignore.

5. **Open a pull request** and ask for review from the catalog owners.

   ```bash
   git add monitoring/catalog.json
   git commit -m "Add unmonitored pages to the monitoring catalog"
   gh pr create --title "Add N unmonitored pages to the monitoring catalog" \
     --body-file "$out/report.md"
   ```

   Add to the description: the exact command you ran, what was added, that stale entries were
   deliberately left alone, and any fields the reviewer must complete. Do not merge.

6. **Tell the user** the coverage figure, how many pages the pull request adds, how many stale
   entries need a decision, and the link to the pull request.
