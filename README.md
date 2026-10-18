<!-- Owner: src/world.ts, src/perform-stream.ts, src/overlay/app.js -->
# cortico-world-vtuber

`GET /identity` 返回当前 bot 的 `sourceId`，供游戏 viewer 选择同一 bot 的演出流。`/overlay` 和 `/stream` 的可选 `source` 参数必须匹配该身份；省略时保留独立 OBS 用法。嵌入游戏的字幕页仅接受同主机父页面，回环地址视作同一主机；缺少来源时不订阅。

演出空闲时，状态行保留本地音频队列空闲起点，排队与播放期间撤下，见底后更新。
静默提醒覆盖启动后尚未提交台词的阶段；提醒和时间戳只提供本地事实，不自动生成台词，也不证明观众端收听状态。

### 持续解说

可选的 `worlds.vtuber.commentary` 在语音空闲而主线程仍在处理活动时，通过宿主认知接口起草短句。
它单独等待模型结果，游戏行动可以继续；没有身体工具或第二份完整对话历史。
主线程开始演出、新交流到达、引用的现场材料更新、引擎失去心跳或进入暂停/空闲时，旧草稿不会进入语音。
未被引用的普通遥测不会让解说不断作废。已提交台词进入当前事实，避免交接后把它当作待播稿。

默认关闭。启用前必须核实宿主 Persona 将 `hint.context: "task"` 解释为只投递本次任务，
而且实际请求使用预期的 Provider。该提示是可选能力，官方 API 6 本身不保证投递上下文大小；
不支持任务上下文或没有认知接口的宿主，应保持关闭。插件不自建模型连接或保存模型密钥。

```jsonc
"commentary": {
  "enabled": false,
  "idleSec": 20,
  "retrySec": 20,
  "factAgeSec": 120,
  "sources": "", // 明确允许的 World id，以空格或逗号分隔
  "style": "",   // 私有角色的简短口吻偏好，不产生事实
  "hostStatusUrl": "" // 可选：该部署的 Cortico /api/status 地址
}
```

只有一项起草在途，不排队；完成后按 `retrySec` 再评估。材料最多保留 24 项、投递最新 6 项，
每项最多 600 字符并注明截断；最近 5 条已提交台词用于避免重复。
模型可以返回不适合开口的原因，不使用固定台词强行填充。
`idleSec` 是起草触发点，不是出声时限；模型排队、生成、TTS 和播放仍有实际延迟。

`RunPhase.idle` 不能区分正常等待与操作员暂停。配置 `hostStatusUrl` 后，每秒只读运行状态，
确认 `loop.running=true`、`loop.paused=false` 才允许在正常等待阶段解说；2 秒超时、未知字段、
超过 5 秒的旧观测和暂停都停止新增起草。地址不带凭据、不跟随重定向，默认不产生此项网络请求。
不配置时只在主线程处理活动的阶段起草，不覆盖长期 idle。停止或重建后旧状态响应不能重新授权。

`vtuber.speech.submitted` 记录受理及来源，`vtuber.act.outcome` 记录演出终态；
二者都不能证明观众设备收到了声音。验收应连续观察实际音频播放间隔、重复、回应和游戏净进展，
不能只数起草请求或演出受理次数。

可选 `worlds.vtuber.waitingMedia` 在主模型长时间等待、演出空闲时，让快模型选择预制短音频或已有歌曲；
默认关闭，普通演出开始即让出，仅取消回退自身媒体。准备、限额和真实播放回执见 [等待媒体](src/README.md#长思考等待媒体)。

[Cortico](https://github.com/Pal-AI-Lab/Cortico) 的 VTuber 演出 World,以独立 npm 包发布。

World 把一段台本变成**连续演出**:文本经流式 TTS 出声,同一段文本解析出的动作记号驱动
Live2D 模型(经 VTube Studio 的 Public API 注入参数),字幕按强制对齐器给出的时间点
跟着念,OBS 里的 overlay 画面由 World 自带的演出流服务直接推。控制台里它有八个面板:
挂载、模型档案、Overlay、动作调参、声线档案、时间点标注、演出日志、演出诊断。

World 内部的分层、演出包格式、台本记号与 Live2D 适配写在 [`src/README.md`](src/README.md)、
[`src/vtuber_performance_module_design.md`](src/vtuber_performance_module_design.md) 与
[`src/models/LIVE2D-ADAPTATION.md`](src/models/LIVE2D-ADAPTATION.md)。

模型专属情绪表情、原生手脚动作和视线平滑见
[`docs/model-performance.md`](docs/model-performance.md)。情绪跟随实际语音开播，显式演出指令优先；
不需要摄像头，旧模型档案继续兼容。

远程语音支持[服务端音色库](docs/server-voice-catalog.md)：参考音频保留在服务端，
在语音面板选择并保存音色后，下一次合成生效。现有单音色配置继续兼容。

## 与 Cortico 的关系

### 绘画创作请求（源码模块，尚未注册运行时工具）

`src/art-brief.ts` 提供不依赖 Cortico、Minecraft 或模型供应商的创作请求协议。
`photo` 用同一次采集的场景截图和游戏状态创作游戏照片，保持可辨认的地点、动作和人物关系，
允许适度裁切、调整取景、光照和材质，不要求像素级一致。截图与状态必须带相同的采集编号和时间，
采样时间差最多五秒；提供场景版本时也要匹配。相同编号只是宿主关联信息，不证明原子采集。
输出计划须同时引用原截图及游戏事实；原图保留，生成的照片不能冒充未处理的截图。
`prepareArtContext` 的 `generatorReferences` 在拍照模式保留场景截图，在自由创作模式只保留人物参考。
`experience` 用带日期和来源的游戏状态、回执及可选图片自由重新构图，创作一段经历；`imagination`
允许 Agent 自己提出想画的场景，游戏状态与截图都可省略，也可明确只画风景。
人物外观参考只决定角色设计，机位、画幅、光线和姿态的调整幅度由创作模式及意图决定。

宿主 Agent 根据自己的意图和已取得的证据调用 `prepareArtContext`，用自己选定的模型规划，
再由 `validateArtPlan` 核对请求身份、模式和引用的事实。动态信息在请求尾部，系统前缀稳定；
预算包含工具描述、宿主提供的图像 token 预算和预留输出，原请求单独保留。模块不会读取游戏、联网、启动模型或加入额外自主循环。
同来源同键的新观察替换旧观察，不同来源的矛盾证据保留给模型判断。未知、未加载区域和在线名单
不能自动解释为确定的环境或附近人物。幻想作品与艺术处理单独记录，不回写为游戏当前事实。

可用 `tsx scripts/prepare-art.ts request.json output-directory [generated-plan.json]`
准备无图请求；带图请求须额外给出宿主估计的 `--image-tokens=N`。
离线准备上下文和验证计划。该模块完成的是创作规划接口；图片生成、取消、归档与作品展示仍由
宿主的绘图执行工具承担，计划通过不代表图片已生成。本轮没有新增自动执行工具或修改现役配置。

这是一个**扩展包**,不是 Cortico 的一部分。它按 Cortico 的扩展契约声明自己:

```jsonc
"cortico": { "kind": "world", "api": 6, "webUiClient": "dist/webui.js", "webUiStyle": "dist/webui.css", "describe": "describe.json" }
```

运行时它以 `cortico/<框架 src 下的路径>` import 框架(`cortico/world.ts`、
`cortico/core/types.ts` …)。这些 specifier 由框架 `src/extensions/runtime.ts` 注册的模块
钩子解析到框架源码本身,**同一份实例**——扩展与框架共用一个 `WorldAssembly`、一套
日志锚点。因此包必须是 `"type": "module"`:CommonJS 包经 require 会拿到框架源码的
第二份副本。

浏览器侧(`src/webui/**`)对 `cortico/*` **只 `import type`**:框架的前端代码不随本包
发布,面板 bundle 也不该把它打进来。要用到的运行时值在包内自带(`console/disposable.ts`
的 `toDisposable`,`console/model.ts` 里那枚目录图标)。

## 安装

先在本目录构建面板产物——`dist/` 不进版本库,没有它控制台的 VTuber 页是空的:

```bash
corepack pnpm install
corepack pnpm build
```

然后二选一装进 Cortico:

- 控制台「扩展」页手动安装,填本目录的绝对路径;
- 或在 `<Cortico>/extensions/` 下 `corepack pnpm add --ignore-workspace <本目录绝对路径>`。

**装完要整进程重启 Cortico**:World 定义在装配表里,热激活开关管不到扩展的装载。

## 开发

开发期 `cortico/*` 经 devDependency `cortico`(npm 上的框架包)解析。要对着本地未发版的框架改动开发,在本目录执行
`pnpm link <框架 checkout>`;它会往 `pnpm-workspace.yaml` 写一条 `overrides`,提交前撤掉。
生产里不靠它:那时解析由框架的模块钩子完成。

```bash
corepack pnpm typecheck   # tsc --noEmit,Node 侧与浏览器侧一份配置一起 check
corepack pnpm test        # vitest run
corepack pnpm build       # esbuild → dist/webui.{js,css}
```

测试全程 mock:不连 VTube Studio、不起真 TTS server、不开声卡。构建脚本**不给
`cortico/*` 配 alias 也不 external**——报 "Could not resolve cortico/…" 就说明浏览器侧
漏了一处运行时依赖,去把它本地化,不要在构建里放行。

## TTS 运行时与权重

语音服务返回 HTTP 410 且 JSON `error:dedup` 时，表示服务端跳过重复文本。本段不播出、不回落整段重试，保留此前的 TTS 状态，演出队列继续处理后续内容；其他 HTTP 错误仍按合成失败报告。

World 不带二进制也不带权重,控制台的 TTS 面板负责把它们取来:

- **运行时**装到 `<运行时根>/llama.cpp-omni/<release>/<平台后端>/`。二进制来自
  [Phantivia/llama.cpp-omni](https://github.com/Phantivia/llama.cpp-omni) 的 `tts-*` release
  (上游 `tc-mb/llama.cpp-omni` 不发这几个可执行文件),Windows CUDA 版另取 ggml-org 的
  cudart 包。自己编的构建填进「TTS 运行时目录」就不再下载。
- **权重**下到 `<模型根>/vtuber/`:VoxCPM2 的两个 GGUF 走 HuggingFace 钉住的 revision,
  面板上点一下就下。对齐器的两个要自己转,见下。来源与许可见
  [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
- **参考音频**属于部署私有资产,放「声线库目录」,留空时落在权重目录旁边。

### 对齐器权重

对齐器给字幕逐字时间点。全网没有现成的 GGUF,本包也不转发 Qwen 的权重,自己转一次:

```bash
# 原始权重,任意目录
huggingface-cli download Qwen/Qwen3-ForcedAligner-0.6B-hf --local-dir ./aligner-hf

# 转成两个 GGUF,直接落进权重目录
tsx scripts/aligner-gguf.ts --src ./aligner-hf --out <模型根>/vtuber
```

出 `Qwen3-Aligner-LM-F16.gguf`(1.20 GB)与 `Qwen3-Aligner-Audio-F16.gguf`(0.66 GB)。
两个文件在 `<模型根>/vtuber/` 里就自动认;放别处就在 `config.json` 的 `worlds.vtuber`
节里指过去:

```jsonc
"ttsAlignerLmFile":    "D:/weights/Qwen3-Aligner-LM-F16.gguf",
"ttsAlignerAudioFile": "D:/weights/Qwen3-Aligner-Audio-F16.gguf"
```

**这两项一旦填了就是必须加载**:文件不在,TTS server 不启动,并报出缺的那个路径。
两项都留空、默认位置也没有文件时,TTS 照常出声,只是没有逐字时间点,字幕与锚点回落按
字符比例估计。

真机跑一遍全流程(对齐器权重要先转好,不然它直接报缺):

```bash
tsx scripts/check-tts-runtime.ts
```

它会装运行时、下 VoxCPM2 权重、起 server,然后打 `/health`、流式合成、对齐各一次。
联网,要显卡,默认装在 `scratch/tts-runtime-check/` 下,不碰真部署。

## 第三方资产

**Live2D 模型、TTS 声学/对齐模型权重、参考声线音频一律不入库。**

`src/models/examples/cortico.profile.json` 是一份写完的接线档案(适配 Type-H1),当读物用;
它**不会被加载**,见 [`src/models/examples/README.md`](src/models/examples/README.md)。
Type-H1 的许可 §4.5 禁止 AI 用途,模型文件本身从不出现在这个仓库里。

## 发布到 npm

`main` 现在指向 `./src/index.ts`:框架进程跑在 tsx 下,TS 入口可直接 import,开发期
省一次构建。真要发到 npm 时把它改成 JS 产物(并把 `src` 换成 `dist` 进 `files`),
否则装到没有 tsx 的宿主上会起不来。

## 许可

AGPL-3.0-or-later,见 [LICENSE](LICENSE)。框架 Cortico 是 MIT,两者经 HTTP 与扩展契约相连,
许可各归各。想提 PR 见 [CONTRIBUTING.md](CONTRIBUTING.md)。
## IndexTTS 语音服务管理

`src/tts-service.ts` 统一管理语音服务。`worlds.vtuber.ttsService.kind` 选择
`voxcpm2`、`indextts` 或 `external`，`ttsUrl` 是客户端连接的根地址。
`autoStart` 默认关闭；开启后随演出扩展启动。

IndexTTS 适配器随本扩展发布，Python 依赖见
[适配器说明](adapters/indextts/README.md)。部署配置示例：

```json
{
  "worlds": {
    "vtuber": {
      "ttsUrl": "http://127.0.0.1:8012",
      "ttsService": {
        "kind": "indextts",
        "autoStart": true,
        "pythonFile": "D:/runtime/python.exe",
        "upstreamUrl": "http://127.0.0.1:8087",
        "preferencesFile": "D:/deployment/voice.json",
        "pronunciationModelDir": "D:/models/reading",
        "pronunciationTokenizerDir": "D:/models/tokenizer"
      }
    }
  }
}
```

服务地址、类型与进程配置变化需重载演出扩展。「挂载」与「声线档案」页显示进程归属，
可启停选定的服务；「声线档案」页可重启托管适配器。
适配器启动前检查现有端点；符合协议的现有服务记为外部服务，不会再启动一份。
其他服务占用端口时报告错误。托管实例通过随机健康凭据确认归属，正常停止或宿主
异常退出时结束子进程。退出及连续三次健康失败触发有界恢复；恢复间隔逐次加倍，
达到 `maxRestarts` 后停止重试，手动启动重置次数。

模型网关通过 `upstreamUrl` 连接，其进程仍由部署者管理，可供其他应用共用。
开启 `worlds.vtuber.ttsSpeech.enabled` 后，网页保存的音色、语速与语气配置通过框架
配置接口持久化，热更新到演出引擎，并应用于下一次普通或流式合成。关闭时使用适配器
偏好文件。可选音色来自模型网关的 `/voices`；VoxCPM2 档案字段仅用于 VoxCPM2。
权重、参考音频和部署偏好不随包发布。

官方 VoxCPM2 的完整 Hi-Fi 服务见
[adapters/voxcpm2](adapters/voxcpm2/README.md)。它缓存固定参考和音频续写特征，
通过 `ttsService.kind: "external"` 连接。模型服务由部署侧管理，支持 PCM 流式合成，
其固定声线与转写在服务配置中指定。

`worlds.vtuber.streamEnabled` 可在网页配置中热切换。关闭时每拍台词收齐后整段合成，
演出层不再按逗号、句号提前切片，也不使用适配器的流式分句端点；字幕正文保持不变。
显式的 `【演出指令】` 仍构成拍边界。开启时恢复分句预合成与流式播放。

控制台静态资源以包版本缓存；更新浏览器代码时同时更新包版本，宿主重载后使用新的资源地址。

## LiteLLM / 远程分块 WAV

在 `worlds.vtuber` 配置服务根地址和传输协议：

```json
{
  "ttsUrl": "http://speech-gateway:4000",
  "ttsService": { "kind": "external", "autoStart": true },
  "ttsTransport": {
    "protocol": "openai-chunked",
    "model": "your-speech-model",
    "voice": "your-voice",
    "speed": 1,
    "sessionId": "my-deployment-tts",
    "prebufferMs": 480,
    "timeoutMs": 180000,
    "healthPath": "/metrics/tts"
  }
}
```

将授权 Key 存入部署 `.env` 的 `CORTICO_TTS_API_KEY`，或同名进程环境变量。
密钥经宿主私有 IPC 传给演出引擎，不进入配置面板、日志、模型上下文或 npm 包。
`sessionId` 在同一部署保持稳定；留空时用部署目录生成，搬迁目录时应显式保存原值。
服务地址填根地址，不附加 `/v1`。配置变更后重载演出扩展；安装新源码后还需让宿主加载新版入口。

该模式向 `POST /v1/audio/speech` 只发送 `model`、`input`、`voice`、`speed`、
`response_format: "wav"`，使用 Bearer 鉴权及 `x-litellm-session-id`。
响应是 HTTP 分块的 48kHz 单声道 PCM16 WAV，不使用 SSE。
累计 480ms 音频后开始交给播放器；不足 480ms 的有效短句在收流完成时播放。
每拍正文交给服务端分句；本地不再附加参考音频、注音、情绪指令或重试生成参数，
也不调用旧 `/health`、`/align`、`/v1/audio/speech/stream`。
演出标签继续控制动作，声音由远程模型和音色配置决定。

打断关闭在途 HTTP 请求、清空当前及排队音频并关闭声卡旧输出流，新台词按需重开输出。
HTTP 完成只表示音频收齐，实际播毕仍由声卡时间线确认。远程请求失败会记录失败，
不会重新提交同一句或启动本地模型。远程音色参与缓存标识，换模型或音色不会复用旧语音。

日志 `tts-http-*` 记录 `callId`（`x-litellm-call-id`）、`sessionId`、`requestId`
（`x-tts-request-id`）、缓冲释放、收流及中断时间。
`GET /metrics/tts` 的 `tts-status-v1` 用于健康状态及终态查询；按请求编号关联
`gateway_last` / `gateway_recent` / `gateway_active`，不把同会话另一条语音当作当前结果。
`metadata.server: null` 表示本次查询未找到关联记录，不能当作服务端已完成。

真实服务验收脚本：

```sh
node --import tsx scripts/tts-acceptance.ts acceptance-config.json output-dir
```

验收配置包含 `url`、`transport`（同上）、`envFile`、`secretName`（默认
`CORTICO_TTS_API_KEY`）、`audioDevice` 及可选 `audioSecondary`。脚本会实际播放一段，
验证开播早于 HTTP 收齐，再于播放中关闭请求和队列，最后发起三个独立会话的并发请求。
`acceptance.json` 保存设备开流、请求追踪、服务端终态和排队采样。三个请求都完成、
观察到两个等待者且得到各自关联元数据后才通过排队验收。测试音频保存在同一输出目录。
普通自动化测试使用本地夹具；该脚本仅在操作员明确要求真实服务验收时运行。
