# Analytics and monitoring

[← Documentation](README.md) · [Models](MODELS.md) · [API](API.md)

See what CE has recorded and what the host is doing now. Usage analytics, benchmark results, runtime logs, and GPU telemetry answer different questions; none should be mistaken for a universal model-performance guarantee.

[Analytics](#analytics) · [API activity](#api-analytics) · [Benchmarks](#benchmarks) · [Logs](#runtime-logs) · [GPU and memory](#gpu-and-memory)

![CE Analytics](images/analytics.png)

## Analytics

Use Analytics to review recorded activity and model-level usage and performance. Administrators can filter model rows to enabled models using the existing model-management state. This changes the visible model rows; **overall totals remain unchanged**.

A quiet or newly prepared installation may have little history. Missing measurements are not zero-performance results, and historical values do not establish what another host will achieve.

Use the model library's recorded **Max TPS** as an observed high-water mark. Benchmark results and everyday chat measurements may represent different workloads; retain that context when comparing them.

## API Analytics

API Analytics is **administrator-only**. It reports request counts, errors, durations, recorded token usage, and recent request metadata, with pagination for the request history.

**Avg Completion Time** includes only completed chat-completion events with an HTTP status below 400 and a recorded duration. Failed or cancelled completions and `/v1/models` requests do not belong in that average. Error and request counts remain separate measures.

Token usage is based on information reported by the backend. Do not substitute invented values when a runtime does not supply a measurement.

Request tracking stores operational metadata—not prompts, responses, image content, API keys, or authorization headers. This describes the request-tracking path; it is not a blanket statement about every log or independently configured proxy in a deployment.

[API setup, supported endpoints, and examples →](API.md)

## Benchmarks

Administrators can run benchmarks across eligible models, edit the prompt set, inspect best-run summaries, and open the detailed saved outputs.

![Benchmark results and controls](images/benchmark.png)

Changing benchmark prompts can make earlier results stale. Use CE's current/stale distinction rather than presenting unlike runs as a directly comparable score.

Benchmark eligibility is managed in the model library. Speech checkpoints and projector assets are not ordinary chat benchmark targets. Running a benchmark is a real workload: account for the host's available memory and other activity before starting one.

A useful comparison records the model, runtime build, prompt set, settings, and hardware. Faster throughput does not by itself demonstrate better answer quality.

## Runtime logs

Open **Logs** to inspect the relevant runtime. The **Language Model** and **Speech-to-Text** views use the same readable log presentation while keeping their output separate.

<details>
<summary>See runtime logs</summary>

![CE runtime log viewer](images/logs.png)

</details>

For speech, the bounded log refreshes while its tab is active. The main CE console also mirrors lifecycle messages as `[S2T]` and child-process output as `[S2T runtime]`, including startup/import progress and readiness.

A process being launched is not the same as the runtime being ready. Check status and the actual error when a model fails to load. Missing dependencies, an occupied port, an incompatible file, and insufficient GPU memory need different remedies.

For speech-specific diagnosis, use the [voice troubleshooting guide](../VOICE_INSTALL.md#troubleshooting). For a language runtime, the installation guide includes a [direct, loopback-only troubleshooting example](../INSTALL.md#optional-troubleshooting-llama-server).

Keep private paths, personal content, and secrets out of public log screenshots and problem reports.

## GPU and memory

CE provides runtime/process visibility and system RAM used/total. The **GPU Monitor** supports NVIDIA and AMD telemetry paths when the appropriate local tools and drivers are available and compatible.

<details>
<summary>See the GPU Monitor</summary>

![Host GPU telemetry in CE](images/gpu-monitor.png)

</details>

Relevant host tools include `nvidia-smi`, `rocm-smi`, and `rocminfo`. These are operator-installed dependencies, not software that CE installs for you.

Unavailable telemetry is not proof that inference failed, and available GPU telemetry does not establish that every runtime feature supports that GPU. In particular, the validated v1.4 voice path is Windows/NVIDIA CUDA; AMD telemetry is not an AMD speech-support claim.

Watch resource use alongside runtime errors when diagnosing capacity. Other workloads may occupy memory even when they were not started by CE.

---

[Model controls](MODELS.md) · [API integration](API.md) · [Administration and data](ADMIN.md)
