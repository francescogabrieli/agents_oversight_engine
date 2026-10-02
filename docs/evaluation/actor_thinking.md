# Bedrock actor thinking experiments

This is a Bedrock-only historical experiment reference. Standard live executions
use the OpenAI actor with `gpt-6-luna`; no Bedrock thinking variables are needed.

The actor supports opt-in adaptive thinking. Set these variables in the terminal
that launches the campaign (process variables take precedence over `.env`):

```powershell
$env:LAYER2_ACTOR_PROVIDER = "bedrock"
$env:LAYER2_ACTOR_BEDROCK_MODEL_ID = "eu.anthropic.claude-sonnet-4-6"
$env:LAYER2_ACTOR_BEDROCK_THINKING = "adaptive"
$env:LAYER2_ACTOR_BEDROCK_THINKING_EFFORT = "high"
```

Amazon Nova 2 Lite uses the separate `extended` mode because Bedrock exposes a
different provider request contract:

```powershell
$env:LAYER2_ACTOR_PROVIDER = "bedrock"
$env:LAYER2_ACTOR_BEDROCK_MODEL_ID = "eu.amazon.nova-2-lite-v1:0"
$env:LAYER2_ACTOR_BEDROCK_THINKING = "extended"
$env:LAYER2_ACTOR_BEDROCK_THINKING_EFFORT = "high"
```

Nova maps this configuration to `reasoningConfig.type=enabled` and
`maxReasoningEffort`; supported efforts are `low`, `medium`, and `high`. It retains
forced Bedrock tool selection (`toolChoice=any`). Claude adaptive thinking instead
uses `toolChoice=auto`, so that coupled difference must remain explicit in comparisons.

Preview the pool with one replica per scenario without invoking providers:

```powershell
.\.venv\Scripts\python.exe scripts/run_evaluation_campaign.py --pool realistic --campaign-id realistic_sonnet46_adaptive_high_v1 --replicas 1 --dry-run
```

Set the intended replica count and remove `--dry-run` to execute the campaign.
The two SMTP scenarios require the existing `--allow-external-execution` flag,
which permits real delivery to the manifest recipient.

Run the existing campaign runner with a new `--campaign-id`, for example
`realistic_sonnet46_adaptive_high_v1`, and the same scenario range and replica
count as the baseline. Keep the same governance configuration and reviewer
procedure. Existing external-execution opt-ins still apply. Never resume a
baseline thread as a thinking experiment. Run an initial small pilot before
starting the complete pool; thinking can increase latency and billed output.

The default `off` preserves the prior request without provider thinking fields.
For Sonnet 4.6 this means thinking is disabled. Adaptive requests explicitly send
`thinking.type=adaptive` and `output_config.effort`, and change provider tool
selection from `any` to `auto`. The runtime still rejects responses without a
typed tool action through its existing bounded protocol recovery. Declare the
tool-selection change as a coupled experimental change in baseline comparisons.
No temperature or output-token limit is introduced: all modes retain provider defaults.

Each actor turn reconstructs its context from canonical task state. It does not
replay an assistant/tool message conversation, so returned thinking/signature
blocks are neither persisted nor replayed. Only normalized tool actions and
audit-safe summaries enter runtime state and traces. This experiment enables
reasoning within each call; it does not add persistent interleaved thinking.

The trace actor projection records `actor_model_id`, `actor_thinking_mode`, and
`actor_thinking_effort` (null when off). These optional v6 fields participate in
the offline configuration fingerprint when populated. Missing historical fields
remain unknown and keep their historical fingerprint. The actor adapter contract
version is `actor_v13_bedrock_configurable_thinking`; prompt text is unchanged.
The historical class/name `BedrockNovaActor` remains an import-compatible surface;
the model ID field identifies the actual configured model.

Provider references:
- https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-adaptive-thinking.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-extended-thinking.html
- https://docs.aws.amazon.com/nova/latest/nova2-userguide/extended-thinking.html
