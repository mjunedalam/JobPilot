# 05 · LLM Gateway (open-source and paid models)

Agents ask for a **role** (`router`, `reasoning`, `extraction`, `writing`, `fast`, `judge`), never a
vendor model. `config.yaml` maps each role to a provider and model, with an ordered fallback chain.
Switching the whole system from Claude to a local Llama/Qwen model is a config change.

```mermaid
flowchart LR
    AG(["Agent via harness"]) -->|"role + messages + schema"| GW["LLMGateway"]
    GW --> RT{"Route for role<br/>(privacy_mode aware)"}
    RT --> P1["AnthropicProvider<br/>official SDK · messages.parse"]
    RT --> P2["OpenAICompatibleProvider<br/>Ollama · vLLM · LM Studio · TGI"]
    P1 -- "retryable error / refusal" --> FB["next provider in fallback chain"]
    P2 -- "invalid JSON" --> REPAIR["one repair retry<br/>with validation error"]
    FB --> P2
    P1 & P2 --> VAL["Pydantic validation"]
    VAL --> USE["Usage + cost → budget"]
    USE --> TR["Langfuse generation"]
    TR --> OUT(["Typed result"])
```

## Example configuration

```yaml
llm:
  privacy_mode: standard          # local_only forces every role to the local provider
  providers:
    anthropic: {type: anthropic}
    local:     {type: openai_compatible, base_url: http://localhost:11434/v1, api_key_env: OLLAMA_API_KEY}
  roles:
    reasoning:  {provider: anthropic, model: claude-opus-5-5, fallbacks: [{provider: local, model: qwen3:14b}]}
    extraction: {provider: anthropic, model: claude-sonnet-5-5}
    writing:    {provider: anthropic, model: claude-opus-5-5}
    fast:       {provider: anthropic, model: claude-haiku-5-5, fallbacks: [{provider: local, model: llama3.2:3b}]}
    router:     {provider: anthropic, model: claude-haiku-5-5}
    judge:      {provider: anthropic, model: claude-sonnet-5-5}
```

Claude is always called through the official `anthropic` SDK. The OpenAI-compatible provider exists
only for self-hosted open-source servers that expose that wire format.
