# `music-generation-gateway.py` / `MusicGenerationGateway`

原创点歌请求立即返回任务回执，生成、音色转换和审核在后台串行执行。
完整成品混音的独立转写用于内容审核；启用音色转换时，歌词时间轴由最终转换人声的独立转写校验，保留 `asr.json` 和 `asr-vocals.json` 供检查。
歌词覆盖率、识别置信度或时间戳未通过校验时，任务进入 `review`，不加入可播放曲库。服务不可用时继续游戏和交流，播放已验收曲目。
任务通过审核后发布 `ready`；演出 World 投递 `vtuber.composition`，Persona 选择播放时机和开场、收尾。
曲库说明保留已审核的创作主题和曲风，供 Persona 结合现场选择曲目；请求者标识不写入说明。
