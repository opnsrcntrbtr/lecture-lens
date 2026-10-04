# Security

Lecture Lens runs entirely on your own machine. It sends captured text only to
the local model server you configure.

## Reporting a problem

Please report vulnerabilities privately through GitHub: Security tab > Report a
vulnerability. Do not open a public issue for anything that exposes captured
data, credentials, or a way around the capture filters.

## What counts

- Captured content leaving the machine.
- The app running text from a capture as a command (it must never; tools are
  launched with argument arrays, not through a shell).
- Capture of windows the filters should exclude.
- A way to push blocked content past `tools/content_guard.py`.

## What does not

- Recording of copy-protected video is not supported and will not be added.
