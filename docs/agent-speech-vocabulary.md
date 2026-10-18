# Agent-side speech localization

The model should use natural localized domain names in narration, using names supplied by its World. Tool IDs and commands keep their required spelling. This stable instruction belongs to the Agent prompt; do not insert the complete vocabulary into every model call or start a second translation model loop.

`worlds.vtuber.speechVocabularyFile` optionally selects a local UTF-8 JSON file. This is a **presentation fallback inside the Agent process**, before audio synthesis, subtitles and alignment. The remote speech endpoint receives only the final text with the ordinary speech request. There is no per-voice vocabulary, game lookup, or game dependency in the TTS service.

```json
{"schema":"speech-vocabulary-v1","language":"zh-CN","terms":{"domain_item_id":"本地化物品名"},"preserve":[]}
```

Empty file configuration disables the fallback. No file is searched for automatically. Invalid explicit files fail visibly on World initialization. Changing the file or path requires reloading the performance World; an active stream keeps its existing text. The source tool call remains in the session log; changed text and vocabulary SHA256 are recorded in local `speech-rendered` diagnostics. Inline animation anchors are remapped to the final subtitle text. The fallback also covers generated-song introductions and endings, not song audio or lyrics.

Only registered complete aliases are matched, longest first. Unknown namespaces, player-name introductions, @mentions, code, URLs, file paths and commands are preserved. `preserve` can be supplied by the host for proper names that collide with vocabulary words. An unmarked ambiguous bare name cannot reliably be distinguished from an item; model wording and host-provided exclusions remain authoritative.

## Minecraft example: consume the existing independent core

The optional exporter consumes an **installed public package**, never extracts core source:

```text
node scripts/export-minecraft-speech-vocabulary.mjs <packed-consumer-root> <local-output.json> 1.21.1
```

The consumer must explicitly install `agent-mc-world` and `minecraft-data`. Names come from `agent-mc-world/extension#zhName`; the registry supplies actual game IDs and English display names. Ambiguous aliases and untranslated IDs are excluded and reported. Version, source, count and SHA256 accompany the exported file. No Minecraft dependency is added to this extension or the speech service. Keep source/license notices with the generated data; other Worlds can supply their own versioned vocabulary using the same renderer.

For a Chinese Minecraft Agent the stable prompt may say:

> 自然对话和直播讲解使用游戏中的中文名称，优先采用 World 返回的中文显示名。不要照读内部英文物品 ID；明确讨论原名、代码或命令时保留原文。工具参数继续使用精确 ID；玩家名字保持本人称呼。不认识的物品先查资料，不编造译名。

The vocabulary stays local and out of the prompt. This is not a new model call, target-completion policy, or gameplay action.
