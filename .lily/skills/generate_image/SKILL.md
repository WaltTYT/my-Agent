---
name: generate_image
description: Generate an image from a natural-language request by first optimizing it into a CogView prompt, then calling the native generate_image tool, which saves the result to disk. 中文：根据自然语言生成图片、画一张、绘制插画、做海报、配图。
version: 0.4.1
created-at: 2026-10-27T04:31:51Z
user-invocable: false
context: inline
when-to-use: When the user asks to generate, draw, create or render an image/picture from a text prompt or natural-language description. 当用户要求画图、生成图片、绘制插画、做海报或给文章配图时。
last-evolved: 2026-10-27T09:38:34Z
evolution-count: 1
---

# Image Generation Skill

Turn a natural-language request into one CogView image: optimize the prompt, call the native `generate_image`
tool, then report the saved file. The tool owns the API call, the key and the download; this skill never
fabricates results.

## 1. Understand the request

Before writing anything, extract: subject, action, setting, style, colors, mood, any text that must appear,
and the intended aspect (avatar, poster, wide shot). Ask for clarification only if no subject is given at all.

## 2. Optimize the prompt (MANDATORY — run this before calling the tool)

CogView-3-Flash rewards concrete visual descriptions in a stable order. Rewrite the request into ONE prompt string.

Order to follow:

1. subject + action
2. environment / background
3. composition and camera (close-up, wide shot, top-down, centered)
4. lighting (soft daylight, rim light, neon night, overcast)
5. material and style (photo, watercolor, 3D render, flat illustration, oil painting)
6. color palette
7. rendering details that carry information (depth of field, texture, film grain)

Hard rules:

- Keep every explicit user constraint: object count, colors, pose, style, any required text. Never add new
  subjects, characters, logos, watermarks, or text the user did not ask for.
- Write in the user's language. Chinese prompts work well with CogView; do not translate a Chinese request
  into English unless the user asks.
- Target 60-200 characters (Chinese) / 40-120 words (English); never exceed ~300 characters.
- Drop empty booster words ("masterpiece", "best quality", "8K", "ultra detailed"): they consume prompt space
  and add nothing for this model.
- Never emit Midjourney-style flags such as `--ar 16:9` or `--v 6`; aspect ratio belongs in the `size` argument.
- If the request is already specific and well-formed, normalize lightly (remove filler, add missing composition
  or lighting). Do not rewrite for its own sake.
- The optimized string is the ONLY value passed to `prompt`. Copy it character for character; do not
  re-paraphrase at call time.
- If the user explicitly says to submit their text unchanged, skip this step and use it verbatim.
- Report it in the final reply as `Prompt: ...` so the user can correct future runs.

## 3. Resolve the target path

- If the user named a file path, pass it as `save_to` verbatim (absolute, or `~/Desktop/<short-slug>.png`;
  the tool expands `~`).
- Otherwise pass `save_to` yourself with a 2-4 word ASCII slug from the subject, e.g.
  `~/Desktop/ginger-cat-umbrella.png`. Omitting `save_to` is also safe: the tool derives the name from the
  prompt (Chinese prompts use the first 8 Chinese characters).
- The tool never overwrites an existing file. If the target name is taken it appends `-2`, `-3`…, and it
  corrects the suffix to the real byte format. Always report the path it returns, not the one you requested.
- Never pass placeholders such as `<user>` or `%USERPROFILE%` — they are literally invalid on Windows — and
  never pass a bare relative path like `Desktop\x.png`, which resolves against the project directory.

## 4. Call the tool

Call `generate_image` once per image:

```
generate_image
  prompt:  <optimized prompt from step 2>
  size:    <optional; default 1024x1024>
  save_to: <optional; absolute path>
```

- Do not hand-roll `http_request` for image generation. The tool owns the endpoint, reads the API key from the
  environment, downloads the image and writes the file; routing around it re-exposes the key to the conversation.
- `size` defaults to `1024x1024`. Use `768x1344` or `864x1152` for portraits, and `1344x768`, `1152x864` or
  `1440x720` for landscapes. Pass only values the schema documents.
- The user confirms once, covering generation and download. Do not retry more than once; report the returned
  error text verbatim instead of guessing.

## 5. Report

Only report success after the tool output contains `Saved N bytes to <path>`. Then give:

- the absolute saved path,
- the saved byte size,
- the model used,
- the real file format,
- the optimized prompt (`Prompt: ...`).

Use the path exactly as the tool returned it. When the tool chose the filename itself, it may correct a `.png`
suffix to `.jpg` because CogView returns JPEG bytes; do not rename it back.

Never reply with "downloading…" or "已保存" before that line exists. If the tool reports a generated URL but a
failed download, say so explicitly and include the URL — do not pretend the file exists.

## Constraints

- One image per invocation unless the user asks for several.
- Never describe an image as generated unless a file was actually written.
- The `prompt` passed to the tool must equal the optimized prompt from step 2.
- Optimization must never change the user's subject; when in doubt, add less.
- Never print, echo or ask for the API key in conversation.

## Evolution Notes

- 2026-10-27: 默认文件名必须可区分：中文提示词取前 8 个汉字、英文取 2-4 个词，同名时追加 -2/-3，绝不覆盖已存在的图片 Reason: 实测中文提示词全部落成 image.png，随后被改成 image.jpg，下一次生成会覆盖同名文件
