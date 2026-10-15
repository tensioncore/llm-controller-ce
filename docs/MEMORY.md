# Conversational Memory: v1.5 controls and management

[Documentation](README.md) · [Administration](ADMIN.md)

Memory is optional and **off by default**. The Admin settings form has an
**Enable conversational Memory** checkbox, saved with **Save Settings**. The
persistent key is `llm.memory.enabled`. Missing settings or unavailable Memory
providers leave ordinary browser chat working.

## Operator setup

Stop CE and use its existing application environment. These are manual operator
steps; neither application startup nor chat installs packages or artifacts.

1. Install the updated `requirements.txt` into CE's existing environment:

   ```text
   python -m pip install -r requirements.txt
   ```

2. For an existing installation, follow the [ordered v1.5 upgrade procedure](../INSTALL.md#existing-installation-upgrades),
   including the finalized `install/upgrade_v1_5.sql`. It preserves existing
   Memory choices while adding the default-off master setting and Personal
   Memory preference where absent. Fresh installations receive these through
   the web installer and do not need upgrade scripts. Project mode is added by
   normal SQLite startup initialization, with `project` as the default and
   existing selections preserved. Prepare the local artifacts below before
   enabling Memory.

3. **Manually download/copy the dense model.** This is the supported installation
   method; CE and the installer never fetch it. Create
   `data\memory\artifacts\dense` beneath the directory containing `app.py`.
   On LLM1 this is `E:\LLM-Controller\data\memory\artifacts\dense`.

   Open the [Qdrant/bge-small-en-v1.5-onnx-Q files page](https://huggingface.co/Qdrant/bge-small-en-v1.5-onnx-Q/tree/main)
   in a browser on a machine with network access. Download the actual files and
   copy them directly into `dense`, keeping their filenames:

   | File | Use |
   | --- | --- |
   | `model_optimized.onnx` | Required quantized BGE model |
   | `tokenizer.json` | Required tokenizer |
   | `tokenizer_config.json` | Required tokenizer/context configuration |
   | `config.json` | Published model metadata; read when present |
   | `special_tokens_map.json` | Published special-token metadata; read when present |

   Verify these files are present and nonempty. Save the actual ONNX binary,
   not an HTML page or a Git LFS pointer. CE's preflight requires the first three
   files; FastEmbed also reads the last two when present. `ort_config.json` and
   `vocab.txt` are not required by this FastEmbed loading path. There is no
   dense-model cache-layout requirement: the files belong directly in `dense`.
   Also create `data\memory\artifacts\bm25`. Manually download `english.txt`
   from the [Qdrant/bm25 files page](https://huggingface.co/Qdrant/bm25/tree/main)
   and place it directly in that directory. Verify it is nonempty. An existing
   copy of that same file can be copied into the new directory instead.
   Mem0 uses FastEmbed BM25's default English stopwords and stemming; with an
   explicit local path, no `mock.file` or other-language files are required.
   This changes only asset loading, not sparse retrieval/scoring.

4. Use the pinned CPU `onnxruntime` package in CE's environment. CE's local
   constructor explicitly passes `providers=["CPUExecutionProvider"]`,
   `cuda=False`, and `specific_model_path` for the dense model. It never calls
   Mem0's stock downloading constructor. No GPU availability check or process
   device mask is used; training and llama-server GPU behavior are unchanged.

   Run this **local-only PowerShell validation command** with CE's Python
   environment active on LLM1. It blocks Python network connection/DNS/send
   attempts before importing FastEmbed, loads the local model, checks CPU
   execution, and produces a 384-dimensional embedding. It does not import CE,
   open its databases, or launch a service:

   ```powershell
   @'
   import sys
   from pathlib import Path

   def block_network(event, args):
       if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
           raise RuntimeError("Network access is disabled for this local validation")

   sys.addaudithook(block_network)
   from fastembed import TextEmbedding

   path = Path(r"E:\LLM-Controller\data\memory\artifacts\dense")
   model = TextEmbedding(
       model_name="BAAI/bge-small-en-v1.5",
       specific_model_path=str(path), cache_dir=str(path),
       providers=["CPUExecutionProvider"], cuda=False, local_files_only=True,
   )
   vector = next(model.embed(["Local BGE validation"]))
   assert len(vector) == 384
   assert model.model.model.get_providers() == ["CPUExecutionProvider"]
   print("Local BGE loaded: CPUExecutionProvider, 384 dimensions")
   '@ | python -
   ```

5. The supported minimal environment does not install spaCy or `mem0ai[nlp]`.
   Mem0 then falls back without optional NLP entity extraction/lemmatization.
   Mem0 2.2.1 has no supported switch to disable its spaCy loader. If an existing
   CE environment already uses spaCy, prepare its `en_core_web_sm` model before
   enabling Memory (`python -m spacy download en_core_web_sm`, manually while
   online). Without that model, upstream Mem0 can attempt a download during a
   request. CE neither rejects this environment nor patches the loader. Do not
   enable Memory in that incomplete optional-NLP environment when offline
   operation is required. No spaCy dependency or model is added by CE.

6. Start CE normally, load the main model, then enable Memory and save settings.
   Initialization runs in the background when Memory is enabled from Admin or
   when CE starts with Memory already enabled. Watch the CE application
   console/service logs for initialization, disabled state, failures, and capture
   completion counts. CE does not deliberately log Memory contents.

The pins remain `mem0ai==2.2.1`, `fastembed==0.8.1`,
`onnxruntime==1.30.0`, `qdrant-client==1.19.1`, and `httpx==0.28.1`.
ONNX Runtime is an explicit CPU installation/reproducibility constraint for
FastEmbed, not a CE direct import. Qdrant and HTTPX are directly used for shared
local storage and proxy-free loopback requests. Requirements define the
supported versions; CE does not reject version differences at runtime.
No Torch/Transformers requirement is added for Memory. Existing document
dependencies are unchanged.

## Storage and local operation

Paths are relative to the installed CE source directory, **not the working
directory or service user's home**. This follows CE's application-relative
storage/artifact convention and the existing ignored `data/` directory:

| Path | Contents |
| --- | --- |
| `data/memory/qdrant/` | Active facts, vectors, metadata, and any entity collection |
| `data/memory/history.sqlite` | Mem0 change history and recent conversational source messages |
| `data/memory/artifacts/dense/` | Manually downloaded/copied dense BGE files |
| `data/memory/artifacts/bm25/` | Manually downloaded/copied BM25 `english.txt` |
| `data/memory/config.json` | Mem0's local library configuration bookkeeping |

Give the CE service account read/write access to this directory. Treat it as
private application data. Use CE's existing single-worker deployment; multiple
processes cannot share one embedded Qdrant directory. Stop CE before backing up
or restoring the **whole** `data/memory/` directory. The Admin settings export
and chat export do not include these stores. Keep the same package pins and
embedding model/dimensions when restoring; do not delete only the vector index
and assume history will rebuild it.

Before importing Mem0, CE sets `MEM0_TELEMETRY=False` and `MEM0_DIR` to its
persistent Memory directory. `FASTEMBED_CACHE_PATH` is no longer set or required:
both embedding constructors receive explicit local paths and cache directories.
CE does not set or change `HF_HUB_OFFLINE` or `HF_HUB_DISABLE_TELEMETRY`; Docling
and other Hugging Face consumers retain their existing environment policy.

Before initializing providers and before each Memory operation, CE checks the
required dense files directly in `data/memory/artifacts/dense`. Missing/empty
required files fail Memory only. Invalid model/tokenizer contents fail during
local loading, also within Memory's existing failure boundary. There is no dense
snapshot lookup or download fallback. The preflight also requires nonempty
`data/memory/artifacts/bm25/english.txt`, and BM25 loads that directory directly.
Neither embedding loader performs a Hugging Face/cache search. Do not remove
artifacts while CE runs. BM25 remains enabled with its existing scoring/defaults.
The optional spaCy caveat above still applies.

During initialization, CE maps Mem0's `fastembed` provider class to
`memory_providers.LocalFastEmbedEmbedding` before `Memory.from_config()`.
This small class inherits Mem0's `embed()` and `embed_batch()` behavior and
replaces only construction of `TextEmbedding`, passing the explicit local path,
CPU provider, `cuda=False`, and `local_files_only=True`. Mem0's original dense
constructor never runs. CE also maps the `qdrant` class to
`memory_providers.LocalQdrant`, which inherits storage/retrieval operations,
loads the lazy BM25 encoder from its explicit local path, and skips Mem0's
payload-index creation for an embedded local Qdrant client. Those indexes have
no effect locally; filtering and scope enforcement are unchanged. Remote clients
retain Mem0's index creation behavior.
This mapping also covers Mem0's entity store. The existing sparse-encoder failure
fallback is retained. No installed package files are patched or vendored.

Runtime extraction uses the existing main llama-server loopback endpoint through
Mem0's `lmstudio` provider. Proxy environment variables are ignored for those
requests. There is no cloud fallback, public-API dependency, Title Model
dependency, new server, or new model manager.

CE's own Memory messages follow the existing `[INFO]`/`[WARN]` console convention
and contain status, counts, or exception classes with single-line messages bounded
to 300 characters. Exception messages can contain upstream-supplied content. CE does not configure
provider log levels, handlers, or redaction filters. Upstream diagnostics follow
their own logging behavior and can contain content; they are not covered by a
blanket content-free logging guarantee. Review service logs during validation.

## Scope and chat behavior

- Personal: `user_id=str(CE user ID)`, `agent_id=personal`.
- Project: the same user ID, `agent_id=project-<owned Project ID>`.
- CE resolves Project ownership from the stored conversation association. A
  missing owned conversation or broken Project ownership bypasses Memory. An
  owned conversation without a Project association uses Personal scope. Browser
  Project IDs do not define chat scope. Recall namespaces and the single capture
  target are resolved for that generation; changes apply to subsequent turns.
- Account Settings > Memory controls Personal recall/capture. Existing and new
  users default to enabled; a missing preference row also means enabled. The
  global Admin switch remains mandatory. Turning Personal off also excludes it
  from combined Project recall, but does not disable Project Memory.
- Edit Project > Memory offers Off, Project Memory only (the existing/new Project
  default), and Personal + Project Memory. Off disables both recalls and capture
  for that Project. Both active modes capture only into the Project namespace.
  Combined mode performs two exact namespace searches, never a user-only search.
  Results are interleaved Project-first, deduplicated ignoring case/whitespace,
  and capped at three total facts, not three per scope. Both searches share one
  two-second caller wait; a failed/slow scope does not discard results already
  returned by the other. Disabling any scope does not erase its saved memories.
- Recall runs once, outside history trimming, with up to three facts of 500
  characters each and a maximum 2,000-character labeled block. CE first builds
  its normal validated prompt and adds Memory only if spare character allowance
  remains. Memory never causes additional history trimming or another system
  message. Uninitialized Memory is bypassed while a single background task warms
  it up. A busy provider is bypassed; the submitted recall result has a
  two-second wait limit. Settings/scope lookup and worker submission are outside
  that timer. The timeout is cooperative: non-yielding CPU work (including local
  embeddings) can delay the hub and timer delivery. It is not a hard wall-clock
  ceiling. The character allowance is not a model-token-budget guarantee.
- Capture starts only after a successfully saved, completed browser response.
  It receives the actual user message and final assistant text—not thoughts,
  instructions, recalled context, attachment envelopes, or converted documents.
  Empty authored-user or assistant text is skipped. CE imposes no separate
  capture-size cutoff; extraction/context failures remain isolated from chat.
  An explicit backend `finish_reason="length"` excludes capture without changing
  the displayed completion or saved response. Missing finish reasons remain
  eligible, subject to the existing completion, Stop, and error checks.
- Capture stores session/prompt IDs, action, source prompt ID, and model label
  as provenance metadata. These fields do not define scope.
- Capture also passes `run_id=session_id` to Mem0 ADD. Its recent-message buffer
  and internal extraction lookup use `user_id + agent_id + run_id`, separating
  conversations even inside the same Personal or Project namespace. The run is
  the stable CE conversation ID, never a Project ID. Normal recall, browsing and
  Slice 5 candidate searches still filter only by `user_id + agent_id`, so saved
  facts remain available across conversations within the allowed scope.
  Provenance metadata and manual-edit markers remain separate and unchanged.
  Mem0's built-in hash deduplication now sees only its conversation-filtered
  extraction candidates (plus the current batch). Cross-conversation duplicates
  can therefore reach the existing namespace-wide Slice 5 reconciliation.
  Pre-existing facts without `run_id` remain recallable but are absent from the
  conversation-filtered extraction lookup. Public update preserves a retained
  record's original `run_id`; CE source metadata still tracks the latest assertion.
- Automatic Memory is user-grounded: CE's `custom_instructions` require durable
  facts explicitly stated or clearly confirmed by the current user message.
  Assistant responses remain context, not authoritative sources; assistant-only
  claims and question-only turns are not intended to create persistent Memory.
  CE submits only the current user/assistant turn. Mem0 also supplies its own
  conversation-local stored context; the policy forbids extracting new facts from
  that context alone.
  Temporary forgetfulness, knowledge/uncertainty, complaints/reactions,
  acknowledgements and session state are excluded. Changes should produce the
  concise current fact, not a narrative retaining the superseded value. Slice 5
  also checks newly added facts and reconciles confident same-slot matches below.
  These semantic decisions depend on the main model, not deterministic truth checks.
- With no eligible Memory capture, first-turn title generation uses its existing
  independent background thread. With eligible capture, one per-turn background
  greenlet runs any required title operation first, then capture. Dedicated Title
  Model selection/fallback is unchanged. There is no global title lock and the
  response path does not wait or join. Different turns can overlap.
  Memory initialization and provider/storage operations run in gevent greenlets,
  keeping Mem0's HTTPX networking on CE's hub. A cooperative semaphore serializes
  Memory operations; this lock does not cover titles. No job
  service or durable queue is introduced. Capture waits for background
  initialization rather than dropping the first fact.
- SDK extraction requests have a 60-second timeout and no automatic retries.
  Initialization failure is latched until CE restart; repair artifacts or
  dependencies and restart. Capture/recall errors do not invalidate saved chats.

## View and manage Memory

Account Settings > Memory lists only your Personal memories. Edit Project > Memory
lists only that owned Project's memories, even in combined mode. These authenticated
routes derive the user from the session and verify Project ownership before reading.
Edits and deletions also fetch the target record internally and require its exact
`user_id` and `agent_id` to match that scope before mutation. There is no admin
cross-user view. Text is displayed as plain
text, with dates and existing source details when available. No vectors, scores,
entity records, or history messages are exposed.

Each refresh loads a bounded list of up to 50 memories through Mem0's scoped
`get_all()`, displayed five at a time. This is not a full-store pagination API or
a guaranteed newest-first list; the view explicitly says when more records exist.
Stored memories remain viewable with Personal/Project capture switched off, but
global OFF bypasses interactive Memory reads and management. Lifecycle cleanup of
existing data, described below, still runs. Disabled, starting, empty, and unavailable
states stay within the panel; other Account and Project controls remain usable.

- **Edit** changes only the trimmed, nonempty fact text. Mem0's public `update()`
  preserves the ID, scope, original creation time, and provenance, refreshes the
  dense/BM25 index, and updates the timestamp. CE adds `ce_manual_edit=true`,
  displayed as **Edited manually**. It does not delete and recreate the record.
- **Forget** requires confirmation and uses Mem0's public `delete()` to remove
  the saved fact from active retrieval.
- **Clear Personal Memory** and **Clear Project Memory** require typing `CLEAR`;
  the Project confirmation names the Project. They use public `delete_all()`
  with both exact identity keys, removing the entire scope, including records
  beyond the browser's 50-record cap. Other users/scopes are unaffected.
- These actions never delete chats or Projects, change Personal/Project settings,
  or change the Admin switch. Disabling Memory keeps data; Forget/Clear removes
  saved facts. The relevant browser refreshes after success or failure without
  closing the surrounding settings dialog.

### Source context, entity links, and partial writes

In pinned [Mem0 2.2.1](https://github.com/mem0ai/mem0/blob/v2.2.1/mem0/memory/main.py),
mutations retain audit history and do not purge recent extraction-source messages.
The separate [`messages` table](https://github.com/mem0ai/mem0/blob/v2.2.1/mem0/memory/storage.py)
holds up to ten recent messages for a `session_scope`; it is not the `history`
audit table. After every authorized Edit, Forget, or Clear, CE deletes only that
namespace's `messages` rows using Mem0's existing connection/lock and its pinned
`_build_session_scope()` key encoder. Conversation-local keys have the form
`agent_id=personal&run_id=<CE-session-id>&user_id=123`. Cleanup matches the exact
encoded agent prefix and user suffix, across all runs of that namespace; it also
removes the exact namespace-only key if present. This keeps management/lifecycle
cleanup effective without sharing extraction context or changing the schema.
There is no upstream scoped purge API. Other namespaces and mutation history
remain; audit history is neither active recall nor extraction input and is not
shown in the browser. No schema change or store reset is required.

Upstream removes entity links/orphans on mutation, but skips cleanup until its
entity store is opened, limits its scan, and swallows cleanup errors. CE opens
that existing store before mutation. A narrow scoped helper removes residual
links, deleting an entity only when its link list becomes empty. Before Edit it
removes old links; public update then builds links for the new text using Mem0's
existing best-effort entity extraction. Clear removes the companion entity
namespace after verifying its active fact namespace is empty. Cleanup repeats
bounded pages until empty, with a no-progress error instead of silent truncation.

Authorization, writes, and source cleanup share CE's existing operation semaphore.
The response waits for completion; it does not use recall's two-second timeout.
Success requires active-record readback and completed cleanup. Qdrant and SQLite
are **not one transaction**: a fact may already have changed when history or
cleanup fails. CE still attempts source cleanup, returns a safe failure rather
than partial success, and refreshes the browser. Inspect the result before
retrying. A lost HTTP response also leaves the result uncertain.

Finish pending title/capture work before managing facts. Purging removes the
existing source buffer; it does not cancel queued captures, delete chat text,
or prevent a later explicit user statement from adding the fact again. Slice 5's
capture policy and automatic reconciliation are described below. CE adds no
content logs and suppresses Mem0's two raw-text INFO update diagnostics. Other
upstream diagnostics retain the logging caveat above.

## Manual validation on LLM1

Use harmless, distinctive synthetic facts. Allow the background title/capture
to finish and look for a capture completion count before testing recall in a
different conversation. Extraction is probabilistic: a zero count does not
prove a fact was stored, and asking a model whether it "remembers" is not a
reliable storage inspection method.

1. Start CE with Memory off. Verify ordinary sends, instructions, attachments,
   Stop, editing, and regeneration still behave normally; no Memory data should
   be initialized. Check the disabled log entry at startup. Start
   first turns in two conversations and verify titles have no global Memory
   scheduling dependency. Repeat with a dedicated Title Model and main fallback.
2. Enable Memory and save settings. Verify background initialization starts and
   succeeds without sending a chat first. Restart CE with Memory enabled and
   verify the same behavior. A chat sent during initialization should continue
   without recalled Memory; later recall retains its two-second wait limit.
3. In a non-Project conversation, state a distinctive personal fact. Complete
   the response, wait for capture, then ask a relevant question in a new
   non-Project conversation. Confirm the answer uses that fact.
4. Store a different distinctive fact in Project A. Complete/capture the turn,
   then verify relevant recall in another conversation within Project A.
5. Ask corresponding questions in a new non-Project conversation and Project B.
   Neither should receive Project A's fact. Repeat with a second CE user to
   verify user separation. Avoid retyping the answer into the query itself.
6. Stop a generation containing a new distinctive fact. Verify no capture is
   scheduled for that partial turn and it is not recalled in another chat.
   Also force an output-limited response with backend `finish_reason="length"`:
   it should retain normal completion display/persistence but schedule no
   capture. Verify ordinary completion still captures, including a backend that
   omits finish_reason. Check that an interrupted stream is still excluded.
7. Disable Memory, stop CE, temporarily move the **dense directory only**
   aside, then restart and enable Memory. With the main model available, confirm
   chat still completes, initialization fails once, and CE makes no artifact
   download attempt. Restore artifacts while stopped and restart CE.
8. Restart CE with intact data and verify previously captured Personal/Project
   facts still recall in the correct scopes.
9. Inspect CE-authored Memory status/failure messages for the absence of fact
   text, raw prompts, and assistant responses; review upstream diagnostics
   separately. Test operation with outbound network blocked after artifact
   preparation; loopback stays allowed. Check document conversion both before
   and after Memory initialization. CE must not alter shared Hugging Face flags.
10. Confirm first-turn title generation completes before capture. A subsequent
    chat can still share the same main-model slot with background extraction;
    observe that latency under realistic use before broader v1.5 rollout.
    Verify later turns capture without a title task. Try a long completed turn
    that fits the main model; CE should attempt capture without a separate size
    skip, and any extraction failure must leave the saved answer intact.

### Controls and visibility checks

These checks assume the required schema is already present. For an existing
installation, complete the [v1.5 upgrade procedure](../INSTALL.md#existing-installation-upgrades)
before checking these controls.
The normal SQLite startup upgrade adds Project mode; no Memory store reset is
needed. Restart CE and refresh the browser to load the updated UI.

1. Turn global Memory off. Both Memory panels should explain the Admin switch;
   ordinary Account/Project controls and chat should still work. No Memory reads
   or capture should occur. Personal and Project preferences remain saveable.
2. Turn global Memory on. Account Settings > Memory defaults to Personal enabled.
   Turn Personal off, save, reopen the dialog, and verify persistence. Subsequent
   non-Project turns should neither recall nor capture; saved records stay visible.
3. Turn Personal on. Capture a distinctive synthetic personal fact, wait for the
   capture count, and recall it from another non-Project chat. Check its text,
   dates, and available source details in the Personal browser.
4. Set Project A to Off and save. A subsequent A turn should neither recall
   Personal/Project facts nor capture new facts. Its existing records stay visible.
5. Set A to Project Memory only. Capture a distinct A fact and recall it in another
   A conversation. Personal facts should be excluded. Reopen the Project editor
   to verify the mode persisted; new Projects should default to Project-only.
6. Set A to Personal + Project Memory. Ask a question relevant to both saved facts.
   Inspect the controlled request/logging or resulting behavior for both sources
   and the unchanged three-fact/2,000-character total. Store a new A fact and
   confirm it appears only in A's browser, not Personal. Turn Personal off and
   confirm combined mode still uses A while excluding Personal.
7. Use Project B and a non-Project chat to check that A's facts do not cross scopes.
   Browser Memory for B must contain only B, even when its mode is combined.
8. Inspect both browsers: empty scopes, refresh, five-item pages, long text, and
   the explicit 50-record bound. Confirm Edit/Forget controls and a separate red
   Clear action, including Clear on an empty scope to erase recent source context.
   Check mobile-width dialogs and keyboard navigation through the Memory tabs.
9. If another user is available, repeat the Personal check and try A's browser URL
   while signed in as that user. It must return Project not found and no Memory.
   Saving a Project mode with an unowned ID must likewise fail. Missing/invalid
   CSRF on preference/mode saves must be rejected.
10. During initialization or a controlled Memory-local failure, refresh the panels:
    show starting/unavailable without exception details; other page controls work.
    A failed combined search scope must not discard the other's completed results.
    Restore normal operation; restart only if the existing initialization latch
    requires it. No raw Memory content should be added to CE capture-count logs.

### Management checks (Slice 3)

Copy the changed Python/static files and restart CE, then refresh the browser.
Slice 3 adds no dependencies or schema changes. Use synthetic facts and wait for
all outstanding capture completion logs first; avoid concurrent generations
during these checks. Keep another Personal fact and facts in Projects A and B
as scope-isolation controls.

1. Edit Personal "User's name is Nick" to a synthetic alternative. Confirm the
   same record ID, updated timestamp, manual-edit indicator, and retained Source
   details. Ask about it in a fresh non-Project conversation to check recall.
   Cancel and reject an empty edit; neither should change the fact.
2. Cancel Forget once, then confirm it. Verify the Personal fact disappears.
3. Continue with unrelated questions without restating the forgotten fact. Check
   that it is not recreated from the old recent-source buffer. Do not rely only
   on a model's claim to remember; inspect the browser after capture completes.
4. Type an incorrect Clear confirmation, then `CLEAR` for Personal. Confirm the
   empty state, unchanged Personal preference, and intact A/B Memory and chats.
   For a scope with more than 50 records, verify Clear empties the whole scope.
5. Edit a fact in Project A; confirm its new text and subsequent A recall.
6. Forget another A fact and continue an unrelated A chat; the old fact should
   not reappear from recent source context.
7. Confirm Personal Memory and its enabled preference were unaffected.
8. Confirm Project B's Memory is unaffected by A's Edit/Forget.
9. Clear A after checking the named confirmation. Confirm its empty state,
   unchanged mode, and preserved A chats/Project, Personal Memory, and B Memory.
10. While authenticated, use browser request tools to submit another user's or
    another scope's record ID to Edit/Forget. Expect a generic 404 and no change
    in either scope. An unowned Project route must also return 404. Missing CSRF
    must be rejected; missing authentication must follow CE's existing login flow.
    The scoped POST routes are `/settings/personal_memory/manage` and
    `/projects/<owned-id>/memory/manage`; payloads use `action`, `id`, and edit
    `text`, or `confirmed: true` for Forget/Clear. Browser IDs never confer ownership.

Also verify management with Personal/Project capture off, global Memory off,
starting/unavailable states, and a controlled provider failure. The UI must show
safe failure text, remain usable, and never claim complete success after failed
cleanup. Repeat Forget after a CE restart to exercise persisted entity cleanup.

## Chat and Project lifecycle (Slice 4)

- **Edit a user prompt:** after a replacement response completes and is saved,
  CE removes automatic memories sourced from earlier versions of that logical
  turn in the edit's exact owned namespace. It cleans entity links and purges
  that namespace's recent source buffer before normal replacement capture.
  A replacement with no durable fact can therefore remove the old fact without
  adding another. Stopped, failed, and unsaved edits do not reconcile.
- **Manual Memory edits survive:** records marked `ce_manual_edit=true` are
  excluded from automatic prompt reconciliation. Explicit Forget/Clear and
  Project deletion still remove them when requested.
- **Regenerate an assistant response:** no provenance cleanup or rewrite of
  existing facts merely because of regeneration. Eligible capture follows the
  same user-grounded extraction and Slice 5 reconciliation as other captures.
  **Switch a response variant:** no Memory operation at all.
- **Delete a chat:** saved Memory and the recent-source buffer remain intact.
  Use Forget/Clear to erase saved facts. Source disclosure retains plain IDs
  and labels; it has no link/action requiring the conversation to still exist.
- **Move a conversation:** existing memories stay where captured. Subsequent
  turns resolve the new trusted scope. No migration or deletion occurs. An edit's
  cleanup namespace is fixed at submission, like capture scope, so a concurrent
  move cannot redirect destructive cleanup into another namespace.
- **Delete a Project:** CE first runs the same scoped Clear used by Memory
  management, including manual records, entity cleanup and source-buffer purge.
  Chats remain under Unassigned; Personal and other Projects' Memory are kept.
  The Memory lock stays held through Project deletion. Queued captures recheck
  Project ownership under that lock and cannot recreate a deleted namespace.

Reconciliation uses existing `session_id`, `prompt_id`, `source_prompt_id`, and
`action` metadata. The owned chat rows' `turn_id`, `prompt_version`, and
`source_row_id` establish the prompt lineage; no provenance table/column is added.
Mem0's `prompt_id` identifies the capture, including captures from earlier
assistant regenerations. The completed edit's `source_prompt_id` must match its
persisted predecessor. Matching includes all earlier prompt versions of that
same turn, with both namespace keys and the conversation ID. Missing/unmatched
provenance is not guessed; ambiguous duplicate prompt IDs fail reconciliation
and skip replacement capture. Manually edited facts are excluded by filter and
checked again before deletion. Cleanup is not limited to the browser's 50 items.

Prompt-edit cleanup and replacement capture are serialized together. A queued
capture from an older prompt version is skipped once a later user edit is saved;
response-version changes and selecting an older variant alone do not trigger
deletion. Cleanup failure leaves the saved chat intact, logs bounded details,
and skips replacement capture. Partial Memory writes can still have occurred.

Lifecycle cleanup runs even with global Memory, Personal Memory, or Project mode
off; new capture keeps its existing enablement/completion rules. A successfully
saved token-limited edit can clean old facts without capturing the limited answer.
If no Qdrant/history storage exists, cleanup does not initialize Memory. Otherwise
it opens the existing configured backend, even while off, and requires the same
local dependencies/artifacts. Initialization failure retains the restart latch.
Project cleanup failure prevents Project deletion and returns its safe failure
message. If Memory cleanup succeeds but the later chat-database commit fails,
the Project may remain with its Memory already cleared; there is no cross-store
transaction or automatic rollback of deleted facts.

### Manual lifecycle checks on LLM1

Copy the changed Python/static files, restart CE, and refresh the browser. No new
dependencies or migrations are required. Use synthetic facts, wait for background
capture/reconciliation logs, and inspect the Memory browsers as well as recall.

1. State `My server is Blueberry.` and confirm Personal Memory captures it.
2. Edit that original prompt to `My server is Strawberry.` and complete the reply.
3. Confirm Blueberry disappears and Strawberry can be captured/recalled.
4. Edit again to a question with no durable assertion. Confirm the old automatic
   fact disappears and unrelated later turns do not recreate it from source context.
5. Manually edit a stored Memory, then edit its source prompt. The manual fact survives.
6. Regenerate an assistant answer; existing user-grounded Memory stays intact.
7. Switch response variants; Memory stays intact.
8. Delete a chat containing a Personal fact; the fact remains visible/recallable.
9. Move a chat from Project A to B. Old Memory stays in A; subsequent capture uses B.
10. Delete A containing automatic and manual Memory. Confirm A disappears, its
    chats become Unassigned, and Personal/B Memory remain intact. In a disposable
    validation setup, operator inspection of the old exact namespace should show
    no active facts, entity records, or recent source messages; audit history may remain.
11. Repeat Project deletion with its mode Off, Personal off, and global Memory off,
    including after restart. It must still clean stored data. Also edit a source
    prompt while Memory is off: old automatic facts are removed without new capture.

Check failed/stopped prompt edits, an edit after assistant regeneration, repeated
edits, an unrelated turn's facts, and a foreign Project deletion request. Exercise
queued captures around a prompt edit/Project deletion to check the stale-write
guards. A controlled cleanup failure must retain the Project and report failure;
repair the provider/artifacts and restart if latched before retrying. Confirm an
installation with no Memory stores can still delete a Project while Memory is off.

## Current facts and automatic reconciliation (Slice 5)

Mem0 2.2.1's automatic extraction is ADD-only; its text-hash deduplication does
not replace a changed value. CE now applies a narrow post-add reconciliation
inside the existing capture operation/semaphore, after any Slice 4 prompt-edit
cleanup. No extra model, service, schema, dependency or background worker is added.

The capture policy excludes temporary forgetfulness, uncertainty, questions,
complaints/reactions, acknowledgements, current knowledge/lack of knowledge,
transient session state and assistant-only claims. `I forgot what I named my
server` should create no fact. `My server is now Strawberry` or `I renamed
Blueberry to Strawberry` should store `User's server is named Strawberry.`
Historical transitions are retained only when explicitly important and useful
independently of the current fact.

For each returned ADD that still exists, CE checks its exact `user_id` +
`agent_id` and capture metadata. Scoped Mem0 search requests nine results to
allow for the addition itself, then supplies at most eight other candidates to
one focused JSON classification through the existing main llama-server provider.
`RECONCILE_CANDIDATE_LIMIT = 8` bounds candidate context; it does not change recall's
three-result limit, ranking or combined-scope behavior. Candidate records and the
authored user message are supplied without truncation; assistant text is not
supplied to this classifier. No similarity threshold authorizes a mutation.

The classifier must identify the same specific entity **and** durable property,
with equivalent meaning or an explicitly asserted replacement value. It returns
candidate indexes, a concise current fact, entity/property descriptions and
verbatim evidence. CE validates JSON shape, index bounds/uniqueness, allowed
relations, candidate quotations and a current-value quote present in both the
user's evidence and the canonical fact. Record IDs come only from CE's scoped
lookup, never the model. Model semantics still require operator validation;
quotations validate grounding, not the truth of an entity/property judgment.

Server name changes, a clear preferred-editor change, an office-location change,
and an unambiguous current-name correction qualify. Server name versus RAM,
name versus programming-language preference, different entities, uncertain
referents and independently useful historical facts do not. A candidate
containing additional independent facts must not be discarded as a single-slot
match. Uncertain decisions leave records unchanged. A clearly ineligible new
addition is removed without modifying existing facts.

For a confirmed automatic match, CE prefers a pre-existing ID, cleans its old
entity links, calls public `memory.update(id, text=..., metadata=...)`, and checks
the retained record's text, namespace and provenance. Only then does it remove
the redundant new record and any other confirmed automatic same-slot candidates
using Slice 3's verified Forget/entity cleanup. An equivalent restatement uses
the same consolidation path. An unmatched eligible addition can be normalized
in place. Mem0 updates dense/BM25 data and mutation history; new entity extraction
remains its existing best-effort behavior. Cleanup by retained ID happens before
update so it cannot erase the new valid links. Any attempted mutation also purges
the exact namespace's recent source buffer, including after partial failure.

The retained ID and creation time survive. Capture metadata is updated to the
latest explicit assertion, so Slice 4 prompt edits track the source of the
current value rather than an obsolete earlier statement. This is single-source
attribution, not a multi-source provenance graph. SQLite mutation history keeps
the previous text; this is not a promise to erase historical copies.

Any matching `ce_manual_edit=true` record takes precedence: CE leaves it untouched
and removes the new conflicting/equivalent addition. It logs an aggregate manual
replacement skip. It does not rewrite other existing records in this branch or
introduce a conflict UI. Personal, different Projects and different users are
never mixed, even when a Project can recall Personal Memory.

Permanent CE logs report capture additions, completed existing-record updates,
removed/canonicalized items, manual skips, uncertain additions and incomplete
reconciliation. These are aggregate counts, never fact text, prompts or classifier
output. Invalid classifier JSON, object/decision shape, missing evidence,
inconsistent matches, or quotation/index/relation mismatches produce an INFO
`no confident match` skip with a fixed reason. They never authorize mutation and
are not reported as partial-write failures. Explicit uncertainty does the same.
Provider/read failures before mutation produce an unavailable warning identifying
the stage and stating that no reconciliation writes were attempted for those
decisions. Errors after a mutation is attempted (including source cleanup) produce
an incomplete warning, since storage methods can fail after partial writes.
Diagnostics include the operation stage, exception class, and a single-line
message bounded by the existing 300-character exception formatter. Only known
content-free CE/storage messages and pinned Mem0 record-ID errors are disclosed;
unrecognized provider details are explicitly redacted because they can echo
facts or prompts. No raw classifier output is logged.
The capture count is Mem0's initial ADD count, not the
net number retained after consolidation. A separate incomplete warning takes
precedence over interpreting that count as successful reconciliation. Mem0's
two raw-text INFO update diagnostics are filtered; its other diagnostics and
other dependencies remain subject to the earlier logging caveat.

This is bounded candidate reconciliation of newly returned additions, not a
retroactive whole-store scan. Missed candidates, ambiguous decisions, extraction
omissions or invalid JSON can leave old/new facts active. If Mem0 returns no
addition (including its own exact-hash deduplication), no classifier runs. Search,
classification and storage failures remain Memory-local. Existing facts are not
changed on classification failure; newly added facts remain available. Writes
across Qdrant, history, entities and source cleanup are not atomic: an error can
leave a partial update or redundant record, reported as incomplete. Shutdown can
also interrupt this background work. Inspect the Memory browser before retrying.

Each addition can add one inference request using the existing timeout/client.
The completed response does not wait, and title sequencing is unchanged. Recall
can skip while this existing serialized Memory operation is busy. Large prompts
can exceed the model context; CE adds no separate character cutoff or new retry
loop. Exact matching quality and latency must be checked with LLM1's loaded model.

### Manual Slice 5 checks on LLM1

Copy `memory_service.py` and restart CE; `docs/MEMORY.md` is the updated guide.
No dependencies or migrations are required. Use synthetic facts in a clean
validation scope and wait for the capture/reconciliation logs after each turn.
Inspect the Memory browser as well as the answer; do not rely on model claims alone.

1. Say `My server is Blueberry.`: one durable server-name record.
2. Say `I forgot what I named my server.`: zero new Memory; the name stays intact.
3. Say `My server is Strawberry.`: the original record ID now holds Strawberry,
   with current source metadata and no competing Blueberry record.
4. In a fresh chat in the same scope ask `What is my server called?`: Strawberry
   is recalled, with no question/assistant-derived fact added.
5. Say `My server has 64 GB RAM.`: a separate RAM fact; the name stays Strawberry.
6. Rename Strawberry to Excalibur: the same name record now holds Excalibur,
   without a transition diary or competing Strawberry record.
7. Manually edit that name record, then assert another name in chat: the manual
   record remains unchanged, the conflicting addition disappears, and a skip logs.
8. Repeat an equivalent eligible fact: no unnecessary duplicate remains.

Also check VS Code to Cursor, Vancouver to Surrey, and an unambiguous Nick to
Nicholas correction. Distinguish two separately identified servers, name versus
Python preference, and candidates containing independent properties. Repeat
with Personal, Projects A/B and another user as isolation controls. Recheck a
Slice 4 edit of the latest source prompt and an edit of an older superseded
source; only the current source should own the retained automatic fact. A manual
fact must survive either. Controlled malformed classifier output must leave
existing facts unchanged and produce only a concise skip reason. Provider/read
errors before mutation must report unavailable without suggesting partial writes;
a controlled mutation failure must report its stage and sanitized details as
incomplete. Chat must remain usable in all cases, without CE content logs.

### Conversation isolation checks on LLM1

After copying `memory_service.py`, restart CE. No dependency/schema change or
data migration is required. Existing incorrectly captured facts are not repaired
automatically; use the existing Memory browser to inspect and Edit/Forget them.

- In Personal chat A, discuss a synthetic server name. In separate Personal chat
  B, discuss a TV series and then say `I remembered now, it was Girls Gone Wild`.
  A's source messages must not be used to assign a server-name referent in B.
- A durable fact learned in A must still be recallable from B. Repeat both checks
  with two conversations inside one Project; other Projects/users remain isolated.
- Repeat an equivalent fact in a different conversation and inspect the existing
  Slice 5 consolidation result. Its candidate limit and semantic uncertainty
  still apply; run partitioning is not a new global deduplication guarantee.
- Edit/Forget/Clear in a populated validation scope, including Project deletion,
  must retain their existing source-cleanup behavior across that scope's runs.

The former class-only ValueError warning cannot identify which historical branch
failed. CE had seven explicit ValueError raises for classifier object/decision,
matches, evidence, value grounding and candidate validation; all preceded writes.
JSON parsing and upstream provider operations could also fail. The corrected
status reasons distinguish validation skips from stage-specific storage failures
without weakening the evidence checks or claiming a historical cause not logged.

## Deliberately deferred

Turning Memory off stops recall/new capture
but does not itself erase stored data or cancel an operation already running.
Explicit lifecycle cleanup is separate from these inference switches.

Capture is best-effort, not durable: shutdown can lose pending background work.
The main model must support JSON output and fit Mem0's sizeable built-in
extraction prompt plus its recent-message context. Extraction can contend with
other turns' titles or foreground chats; sequencing applies only within a turn. Long
inputs, provider timeouts, and invalid extraction output may produce no facts.

General contradiction graphs, temporal-history/confidence UI, a Memory-used indicator, public API Memory,
historical-chat indexing, Memory import/export, backup redesign, admin cross-user
management, a MySQL mirror, alternate stores, and automatic account cleanup remain
deferred. There is no semantic reconciliation or migration between namespaces.
Mutation history, chats, service logs, and backups can retain
older text; Forget/Clear is not a promise to erase every historical copy.
