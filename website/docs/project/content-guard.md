---
title: Content guard
---

# Content guard

This is a public repository about a private activity. The guard keeps course material, the names of institutions, platforms and people, captured lectures, credentials and personal data out of it.

## Layers

| Layer | When | What |
| --- | --- | --- |
| `.gitignore` | Always | Capture output, decks, recordings, `.env`, eval results, private session cases |
| `pre-commit` hook | `git commit` | Scans staged files |
| `commit-msg` hook | `git commit` | Scans the message |
| `pre-push` hook | Any push | Scans every commit the remote lacks (message, author, paths, files) and the whole tree |
| `tools/publish.sh` | The only supported push | Same checks, then pushes; a plain `git push` to `origin` fails because its push URL is disabled |
| CI | After a push or on a pull request | Scans the tree and the pushed commits again |

Turn it on once per clone: `tools/setup_hooks.sh`.

## What it looks for

1. **Blocked terms.** Stored only as SHA-256 hashes in `tools/guard/blocked_terms.sha256`, so the repository never spells out what it must not contain. Text is lower-cased and split into words; every run of one to six words is hashed and compared. Words of five letters or more are also found when glued inside a longer word. Ordinary words that contain a blocked one go in `allow_tokens.txt`.
2. **Your plaintext list**, `.guard/terms.local.txt`, if present. It is ignored by git.
3. **Forbidden paths and types:** lecture output, notes, feedback, decks, documents, recordings, databases, `.env`, images outside the docs site.
4. **Secrets:** private keys, common token formats, credential assignments.
5. **Personal data:** absolute home-directory paths, e-mail addresses other than no-reply and example addresses.
6. **Files over 1 MB.**

## Adding a term

```sh
echo "some name" >> .guard/terms.local.txt
tools/guard/make_hashes.py .guard/terms.local.txt
git add tools/guard/blocked_terms.sha256 && git commit -m "guard: add a blocked term"
```

## When it stops you

It prints the file, the line and the text it found in your file. Replace the name with a generic one ("the institute", "the course portal", `learn.example.edu`), or move the file to an ignored folder, and commit again. If the hit is an ordinary word, add that word to `allow_tokens.txt` and say why in the commit message.

## Limits

- **It prevents accidents, not intent.** Hooks can be skipped with `--no-verify` and a push URL can be typed by hand. CI detects that afterwards, but a public push cannot be recalled: anything pushed should be treated as published.
- **Hashes are not secrecy.** Anyone can test a guess against the list. It keeps names out of the text and out of search; it does not hide them from someone who already suspects them.
- **It cannot recognise paraphrase.** A lecture's ideas restated in your own words pass. Judgement is still yours.
- **It is not legal advice.** It implements a cautious policy: name nothing, quote nothing.
