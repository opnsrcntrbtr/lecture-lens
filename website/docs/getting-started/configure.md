---
title: Configure for your course
---

# Configure for your course

Everything specific to you lives in two files that git ignores: `.env` and `allowed-domains.txt`.

## Models

```sh
OMLX_BASE_URL=http://127.0.0.1:8001/v1
OMLX_MODEL=<generator model id>
OMLX_VL_MODEL=<vision-capable model id>
OMLX_JUDGE_MODEL=<judge model id>     # evaluation only; must differ from the generator
```

## Your course portal

```sh
STUDY_LECTURE_SITE=learn.example.edu      # lecture sessions are found by this host
STUDY_PORTAL_NAME="Example Learn"         # as shown in window titles; optional
STUDY_COURSE_NAME="My course"
SP_DRM_URLS="learn.example.edu/player"    # pages with copy-protected video
```

`allowed-domains.txt` lists the sites captured in portal mode, one per line. `host:subdomains` includes every subdomain. Manage it with `./sp-domains add|remove|list`.

## Capture modes

| Mode | Captures | Use for |
| --- | --- | --- |
| `portal` | The browser, only on allowed sites | Recorded lectures, reading |
| `live` | The meeting app named in `SP_LIVE_APPS`, plus browser windows whose title matches `SP_LIVE_BROWSER_TITLES` | Live classes |

`./sp-mode live`, `./sp-mode portal` or `./sp-mode auto`. With `SP_AUTO_SWITCH=true` the mode follows whether a meeting is running.

## Where output goes

```sh
STUDY_LECTURE_DIR=$HOME/lecture-lens/lectures
STUDY_VAULT=$HOME/vaults/my-course        # an Obsidian vault; a note per lecture links to the files
```

Both are ignored by git and blocked by the [content guard](../project/content-guard.md).

Full list: [Configuration reference](../reference/configuration.md).
