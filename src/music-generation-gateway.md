# `music-generation-gateway.py` / `MusicGenerationGateway`

原创点歌请求立即返回任务回执，生成、音色转换和审核在后台串行执行。
完整成品混音的独立转写用于内容审核；启用音色转换时，歌词时间轴由最终转换人声的独立转写校验，保留 `asr.json` 和 `asr-vocals.json` 供检查。
歌词覆盖率、识别置信度或时间戳未通过校验时，任务进入 `review`，不加入可播放曲库。服务不可用时继续游戏和交流，播放已验收曲目。
任务通过审核后发布 `ready`；演出 World 投递 `vtuber.composition`，Persona 选择播放时机和开场、收尾。
曲库说明保留已审核的创作主题和曲风，供 Persona 结合现场选择曲目；请求者标识不写入说明。
共用显卡时可启用 `comfy.releaseMemoryAfterGeneration`。生成音频取回后，等待生成队列为空，再释放该服务的模型缓存；确认其保留显存不超过 `maxPostprocessReservedMb` 后开始歌声转换和转写。`memoryReleaseTimeoutSec` 内未确认释放则进入 `review`；不打断共享队列中的任务。
