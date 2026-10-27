---
name: generate_image
description: Generate an image from a text prompt through the Zhipu (BigModel) CogView API and save the result to disk.
version: 0.2.0
created-at: 2026-10-27T04:31:51Z
user-invocable: false
context: inline
when-to-use: When the user asks to generate, draw, create or render an image/picture from a text prompt.
---

# Image Generation Skill

Generate one image per request with the CogView API, then download it to a local file.
This skill only works because Lily has the `http_request` tool; it never fabricates results.

## 1. Resolve the prompt and target path

- Use the user's prompt verbatim. Ask for clarification only if no subject is given at all.
- Target file: use the absolute path the user asked for. Otherwise save into the desktop directory
  listed in the Environment section of your system prompt (the `Desktop:` line), e.g.
  `C:\Users\Walt\Desktop\<short-slug>.png`. `<short-slug>` is 2-4 English words from the prompt.
- Hard rules for `save_to`: no placeholders such as `<user>` or `%USERPROFILE%` (they are literally
  invalid on Windows), and no bare relative path like `Desktop\x.png` (it resolves against the
  project directory). If you do not know the desktop path, pass `~/Desktop/<short-slug>.png`;
  the tool expands `~` to the real home directory.
- Default model: `cogview-3-flash`. Use another model only if the user names one.

## 2. Resolve the API key

Ask the user for the key only if it is not already available. Do not guess and do not
invent an endpoint.

- If the key was provided earlier in this conversation, reuse it.
- Otherwise ask: "请提供 BigModel 的 API Key（或告诉我它在哪个环境变量/文件里）".
- Never echo the full key back. When mentioning it, mask it as `sk-...` plus the last 4 chars.

## 3. Call the API

Call `http_request` with:

- method: `POST`
- url: `https://open.bigmodel.cn/api/paas/v4/images/generations`
- headers: `{"Authorization": "Bearer <api_key>", "Content-Type": "application/json"}`
- json_body: `{"model": "<model>", "prompt": "<prompt>"}`

Each `http_request` call requires user confirmation. Do not retry more than once on failure.

## 4. Read the response

- On `HTTP 200`, parse the JSON body and take `data[0].url`.
- On any other status, report the API error text verbatim and stop. Do not claim an image was produced.

## 5. Download and save (MANDATORY — the task fails if you skip this)

Stop and only reply after this step has actually run. The URL from step 4 is not the image; the image
only exists once this call returns `Saved N bytes`.

Call `http_request` again with method `GET` on `data[0].url`, and set `save_to` to the target path
from step 1. Then report:

- the absolute saved path,
- the saved byte size,
- the model used.

Never reply with "downloading…" or "已保存" before this call returns. If you catch yourself about to
end the turn after step 4, send this download request instead.

If the response contains base64 data instead of a URL, write it with `write_file` after
decoding, and say so explicitly.

## Constraints

- One image per invocation unless the user asks for several.
- Never describe an image as generated unless a file was actually written.
- Never send the API key anywhere except `open.bigmodel.cn`.
