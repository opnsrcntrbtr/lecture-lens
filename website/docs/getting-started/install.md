---
title: Install
---

# Install

About 30 minutes, most of it model downloads you do yourself.

## 1. Prerequisites

| Need | Notes |
| --- | --- |
| macOS 15+, Apple silicon | 32 GB memory or more is realistic |
| screenpipe | Install or build it yourself and accept its licence. Lecture Lens does not ship it |
| A local model server | Any OpenAI-compatible server on `127.0.0.1` with a text model and a vision-capable model. For evaluation, a second model as judge |
| Python 3.11+ | With Pillow |
| `ffmpeg` | For extracting slide frames from compacted video |
| Xcode command line tools | To build the app |

## 2. Get the code

```sh
git clone https://github.com/opnsrcntrbtr/screenpipe.git ~/lecture-lens
cd ~/lecture-lens
tools/setup_hooks.sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

The app looks for the toolkit in `~/lecture-lens`. You can change that in the app's Settings.

## 3. Configure

```sh
cp .env.example .env
cp allowed-domains.example.txt allowed-domains.txt
```

Edit both for your course: see [Configure](configure.md).

## 4. Check

```sh
./check.sh
```

Every line should be green or a yellow note. A red line names what to fix.

## 5. Build the app

```sh
app/build_app.sh --test --open
```

On first start macOS asks for Screen and System Audio Recording, Accessibility and, if you use it, Microphone. Grant them to **Lecture Lens**, then use **Relaunch app** in the Capture screen.

### Keep permissions across rebuilds

macOS ties a permission to the app's code signature. An unsigned rebuild looks like a new app and loses its grants. `build_app.sh` uses, in order: `$SP_SIGN_IDENTITY`, an Apple Development certificate, or a self-signed code-signing certificate named `Lecture Lens Local` that you create once in Keychain Access (Certificate Assistant > Create a Certificate > Code Signing).
