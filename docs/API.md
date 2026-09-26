# Connect through the API

[← Documentation](README.md) · [API Analytics](MONITORING.md#api-analytics) · [Administration](ADMIN.md)

CE provides an optional, deliberately limited **OpenAI-compatible API** on its existing application server. Use it to connect your own tools to the active local model—not to administer CE or manage model lifecycles.

[Enable access](#enable-access) · [Requests](#make-a-request) · [Streaming](#stream-a-response) · [Images and limits](#images-and-request-limits) · [Troubleshooting](#troubleshooting)

## Supported surface

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/models` | Discover the active model exposed by CE. |
| `POST /v1/chat/completions` | Request a streaming or non-streaming chat completion. |

Only the **active model** is served. API calls do not start, switch, or download models. Model lifecycle and administrative routes are not exposed through this API surface. Compatibility refers to these supported endpoints, not every feature or endpoint offered by another API provider.

Use the browser-visible **CE application host and port**, not the private `llama-server` ports. For a compatible client that asks for a base URL, use your CE origin followed by `/v1`.

## Enable access

API access is disabled by default. An administrator enables it in **Admin Settings → API Access**:

1. Generate the single CE API key and copy it immediately. The plaintext key is not shown again; CE stores its SHA-256 hash.
2. Enable API access and save settings.
3. Load a compatible language model through CE's normal browser controls.

Regenerating the key invalidates the previous key. Revoking it disables access and clears the stored hash. Keep the key outside source files, screenshots, and public logs. This is a CE-issued key, not an external provider's API key.

Requests use `Authorization: Bearer <API_KEY>`. Use HTTPS when carrying credentials across an untrusted network. CE's managed runtimes remain private and loopback-bound.

## Make a request

The examples below use Bash and an already configured CE installation on the same host. For a remote installation, replace the origin with the actual CE HTTPS origin. Set `CE_API_KEY` in your local shell or secret-management mechanism; do not place a real key in this guide.

```bash
export CE_BASE_URL='http://127.0.0.1:5000'
# CE_API_KEY must already contain your CE-issued key.
: "${CE_API_KEY:?Set CE_API_KEY locally before continuing}"

curl --fail-with-body "$CE_BASE_URL/v1/models" \
  -H "Authorization: Bearer $CE_API_KEY"
```

Copy the exact model ID returned by `/v1/models`. Replace `ACTIVE_MODEL_ID` in the request body below; the example is not an instruction to change the running model.

```bash
curl --fail-with-body "$CE_BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer $CE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"ACTIVE_MODEL_ID","messages":[{"role":"user","content":"Explain what a local language model does in three sentences."}],"stream":false}'
```

In Windows PowerShell, use the native request commands rather than relying on Bash quoting. The key must already be set in the local `CE_API_KEY` environment variable:

```powershell
$CeBaseUrl = 'http://127.0.0.1:5000'
if (-not $env:CE_API_KEY) { throw 'Set CE_API_KEY locally before continuing.' }
$Headers = @{ Authorization = "Bearer $env:CE_API_KEY" }
Invoke-RestMethod -Method Get -Uri "$CeBaseUrl/v1/models" -Headers $Headers

$Body = @{
    model = 'ACTIVE_MODEL_ID' # Replace with the exact ID returned above.
    messages = @(@{ role = 'user'; content = 'Explain what a local language model does in three sentences.' })
    stream = $false
} | ConvertTo-Json -Depth 6

Invoke-RestMethod -Method Post -Uri "$CeBaseUrl/v1/chat/completions" `
    -Headers $Headers -ContentType 'application/json' -Body $Body
```

## Stream a response

For a streaming request, set `stream` to `true`. This Bash example tells curl not to buffer the arriving output:

```bash
curl --fail-with-body --no-buffer "$CE_BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer $CE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"ACTIVE_MODEL_ID","messages":[{"role":"user","content":"Give me a short outline for a project brief."}],"stream":true}'
```

These requests supply their own messages. Personal System Instructions and Project Instructions are documented for browser chat; do not assume those preferences are automatically added to API requests.

## Images and request limits

Inline image data URLs are accepted only while the active runtime has a valid compatible projector. Supported image formats are PNG, JPEG, WebP, GIF, HEIC/HEIF, AVIF, TIFF, and BMP. The same [image-conversion rules](CHAT.md#image-understanding) apply to browser and API requests.

Use supported OpenAI-style `image_url` content containing inline data. **Remote image URLs are rejected.** CE does not fetch them on a caller's behalf.

The API has a separate **16 MiB request-body limit**. This is the serialized request body, including encoded image data—not the browser's combined-attachment setting. Keep requests within the limit and within the active model's practical context and memory capacity.

## Activity and privacy

Administrator-only [API Analytics](MONITORING.md#api-analytics) shows counts, errors, duration, backend-provided token usage, and recent request metadata.

Request tracking records operational metadata such as source, status, duration, and reported usage. It does **not** store prompts, responses, images, API keys, or authorization headers. Separately configured client, proxy, and server logging should be reviewed according to the deployment's needs.

## Troubleshooting

| Symptom | Check first |
| --- | --- |
| Unauthorized request | API access is enabled, the Bearer header is present, and the key has not been regenerated or revoked. |
| No usable active model | Load a model through CE and wait for readiness; the API does not start one. |
| Model selection rejected | Use the exact ID returned by `/v1/models`, not an arbitrary registry filename or a previous model's ID. |
| Image request rejected | Confirm the running model/projector pair, use inline supported data, and check body size. |
| Incomplete or failed completion | Inspect runtime logs and API Analytics; confirm model compatibility and available resources. |

Do not bypass CE authentication by exposing raw runtime ports to work around an API error.

---

[Model management](MODELS.md) · [API Analytics](MONITORING.md#api-analytics) · [Administration](ADMIN.md)
