# HF Grass Widget

Generate a Hugging Face activity heatmap SVG you can embed in a GitHub README.

## Quick start

```bash
python3 scripts/hf_grass.py --user YOUR_HF_USERNAME --out assets/hf-grass.svg
```

## Embed in README

```md
![Hugging Face activity](assets/hf-grass.svg)
```

The SVG is generated; run the script or workflow to create it.

## Example (kbsooo)

![Hugging Face activity for kbsooo](assets/hf-grass.svg)

## Automation (GitHub Actions)

1. Set a repository variable named `HF_USERNAME`.
2. Run the workflow in `.github/workflows/hf-grass.yml` (or wait for the schedule).

For public repos, this workflow is free on GitHub-hosted runners.

## Notes

- The generator uses `https://huggingface.co/api/recent-activity`.
- Use `--activity-type` to filter: `all`, `discussion`, `upvote`, or `like`.
- Add `--show-legend` for a Less/More legend.
- Add `--plot` to save a preview PNG (requires `matplotlib`).
