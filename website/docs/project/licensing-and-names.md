---
title: Licensing and names
---

# Licensing and names

Not legal advice. This page records the decisions and the reasoning.

## This project: Apache-2.0

Chosen because it is permissive and widely accepted, includes an express patent grant, disclaims warranty and liability, and says plainly that it grants no rights in names or marks. Contributions are accepted under the same licence.

## The recorder is separate

Lecture Lens works with screenpipe but does not contain it. screenpipe is distributed by its owner under its own licence, which at the time of writing is a source-available commercial licence, not an open-source one. Consequences:

- You install screenpipe yourself and must meet its licence terms. Free use there is limited to personal, educational and similar non-commercial use.
- This repository contains no screenpipe source and no patches to it.
- Some behaviour described in these docs depends on changes the author made to a local copy of the recorder: choosing which pages count as protected, keeping audio during the pause, tapping one app's audio. Those changes are not published here. The intended route is to offer them to the recorder's maintainers.

## Names

- "Lecture Lens" is this project's name.
- "screenpipe" is used only to say what this software works with. This project is not affiliated with or endorsed by its makers.
- No university, course provider, learning platform, course, teacher or learner is named anywhere in this repository, and the [content guard](content-guard.md) enforces that. The software is general: point it at your own course portal in `.env`.

## Your recordings

Recording a class may be restricted by your course's terms, by the meeting host's settings, and by law where you live. The output is for personal study. Do not share or publish it.
