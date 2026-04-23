# Templates

Prompt templates used by background agents, and the template rendering system that substitutes {{VAR}} placeholders at runtime.

| Page | Description |
|---|---|
| [[triage-template]] | The cheap first-pass session-capture prompt that classifies sessions as CAPTURE/PARTIAL/SKIP |
| [[capture-template]] | The full session-capture agent prompt that writes discussion, decision, gotcha, and playbook pages |
| [[ingest-template]] | The post-merge ingest agent prompt that updates wiki pages from git diffs |
| [[template-system]] | How render-prompt.py resolves {{VAR}} placeholders through CLI args, config overrides, and file fallbacks |
