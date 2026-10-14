# `music-generation-gateway.py` / `MusicGenerationGateway` / `music_asr.py`

## YuE2 原生音乐与纯器乐

部署可设置 `musicModel: "yue2"`、`generationMode: "text-to-music"`、
`voiceConversion.enabled: false`，并将 `comfy.endpoint` 指向专用的本地 ComfyUI。
`yue2.checkpoint` 默认为 `yue2_3b_int8_convrot.safetensors`。旧配置不声明
`musicModel` 时仍使用 ACE-Step；不会在失败后偷偷换模型。YuE2 不需要旧的 ACE 提示模板。

`POST /jobs` 及 `vtuber_music generate` 增加布尔参数 `instrumental`。
背景音乐使用 `instrumental: true`，省略 `lyrics` 或传空字符串；混入唱词会拒绝受理。
只有明确要求演唱时才传原创歌词。时长为 30–120 秒，也是生成上限，不保证完整自然收尾。
`/health` 返回当前 `musicModel` 和 `instrumentalSupported`；任务回执保留模型和纯器乐标识。

YuE2 先作曲，再把纯器乐谱面的 Vocal 音符迁移到 Ins，逐项校验音高、时值、拍号、
速度和和弦。保留原始谱、转换谱、校验记录、请求图及返回图；结构错误最多重新作曲一次，
不将华尔兹强改成四拍。渲染沿用试听的 32 步、DPM2 / SGM uniform、零负条件及
-6 dB 导出增益。`generation.json` 标记时间上限，Comfy API 未提供终止 token 状态。

纯器乐也执行音频质量检查及独立 Qwen3 转写。只接受正常解码终止且转写为空的结果；
识别到词语或识别失败进入 `review`，不改写为歌词、不自动入库。空转写不能排除哼唱，
谱面检查也不等于人耳验收。纯器乐歌词文件保留空 `lines`。歌曲仍沿用实际转写、
内容审核、歌词覆盖率和时间轴校验。原生 YuE2 不做参考音色或后置换声。

两个阶段分别持久化提交意图和 promptId。丢失提交应答不会自动重复提交；可确认的任务
重启后继续查询。取消和停止会阻止后续渲染与入库，不中断共享显卡上的其他服务。

### 专用离线运行器

`src/music-yue-worker.py --config worker.json` 使用预先安装的 ComfyUI 和权重，
启动前检查 checkpoint SHA256，不下载依赖或模型。JSON 键：`comfyRoot`、`checkpoint`、
`checkpointSha256`、`stateDir`、`port`（例如 8288）、`host`（仅 127.0.0.1）。
Windows 可通过 `scripts/music-yue-watch.ps1 -ConfigPath ... -PythonExe ...` 托管，
应使用有 GPU 访问权的登录用户，独立于游戏宿主和 TTS。CPU VAE、低进程优先级、
保留 3 GiB 显存、禁用自定义节点、生成后释放缓存沿用已验收的本机设置。
权重、ComfyUI 与 Python 环境不打入 npm；本地路径必须显式配置。第三方曲谱工具
来源与 MIT 许可证见 `src/music_yue/NOTICE.txt` 和 `score/LICENSE`。

服务进程重载与宿主工具加载是两件事：音乐网关重载即可切换生成模型；新增工具参数
需要宿主重新导入 VTuber 源码，普通 World stop/start 不会清除 Node 模块缓存。

原创点歌请求立即返回任务回执，生成、音色转换和审核在后台串行执行。
`requestText` 保存点歌要求和相关虚构游戏场景，`requesterKey` 单独保存来源标识。来源标识不进入审核；观众昵称不写入创作文本。
完整成品混音的独立转写用于内容审核；启用音色转换时，歌词时间轴在最终转换人声上校验，保留 `asr.json` 和 `asr-vocals.json` 供检查。
歌词覆盖率、识别置信度或时间戳未通过校验时，任务进入 `review`，不加入可播放曲库。服务不可用时继续游戏和交流，播放已验收曲目。
歌词先按字形比对；字形覆盖不足时，可用 `requirements-music-gateway.txt` 中的离线拼音词典按音节复核同音字。歌唱旋律不保留词汇声调，复核仍区分声母与韵母，沿用相同的逐行、全曲和转写覆盖阈值。复核不改写独立转写及送审内容；缺少词典时只接受字形校验。
转写的重复程度、置信度和时间戳在内容审核前校验；不可靠的识别结果进入 `review`，保留原始转写供核对。Whisper 保留低质量窗口的解码回退；两个后端都不使用创作稿提示识别结果。
生成音频的末尾静音可裁至最后一个有效音频窗口之后 0.5 秒；原始音频保留，裁剪后仍校验时长、有效音频占比和全部歌词。短前奏加静音不能通过此步骤。
本机 `POST /jobs/<jobId>/revalidate` 可重新校验 `review/rejected` 的成品，要求输入已通过审核且原始生成音频存在。它复用该音频并重新执行音色转换、转写、内容审核及歌词校验，不重新作曲；保留上次状态与原始音频摘要。重复提交正在重新校验的同一任务返回已有回执；输入被拒绝的任务不能使用此入口。

`asr.backend` 默认 `whisper`，使用本地 `modelFile`。选择 `qwen3` 时提供离线 `modelDir`、`alignerDir` 和隔离的 `pythonFile`；两个目录须包含模型配置、分词器、聊天模板和权重。依赖版本见 `requirements-music-asr.txt`，CUDA 版本由部署环境选择。模型加载使用 `local_files_only`，不在任务中下载。
`qwen3` 独立识别完整混音，再将同一识别文本对齐到转换后的人声；创作稿不进入识别器或时间对齐器。置信度来自实际解码分数，不声称拥有未测量的静音概率；字符时间戳按识别短句的实际边界分组，避免将量化产生的零宽字符伪造为正时长。字幕仍使用通过覆盖率校验的创作稿。
人声对齐出现整句零时长时，使用混音和人声中相邻短句的实测边界交集截取该段人声，单独对齐一次。`asr-vocals.json` 的 `alignmentWindows` 保存重试窗口；仍无有效时长或时间轴冲突时进入 `review`。
任务通过审核后发布 `ready`；演出 World 投递 `vtuber.composition`，Persona 选择播放时机和开场、收尾。
曲库说明保留已审核的创作主题和曲风，供 Persona 结合现场选择曲目；请求者标识不写入说明。
共用显卡时可启用 `comfy.releaseMemoryAfterGeneration`。生成音频取回后，等待生成队列为空，再释放该服务的模型缓存；确认其保留显存不超过 `maxPostprocessReservedMb` 后开始歌声转换和转写。`memoryReleaseTimeoutSec` 内未确认释放则进入 `review`；不打断共享队列中的任务。

ComfyUI 启用 MultiGPU 检查点插件时，插件必须透传 `disable_dynamic` 与模型元数据；[上游兼容实现](https://github.com/pollockjj/ComfyUI-MultiGPU/commit/2aaa033f7009b41e73c21df70ea0358a9d0711eb)可供部署核对。更新插件后，仅在生成队列和网关任务均空闲时重启 ComfyUI。上线验收需完成生成、音色转换、独立转写与审核，确认任务进入 `ready`；`/health` 的 `ok` 仅表示网关工作线程存活。
