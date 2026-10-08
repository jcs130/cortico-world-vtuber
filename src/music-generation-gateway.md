# `music-generation-gateway.py` / `MusicGenerationGateway` / `music_asr.py`

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
