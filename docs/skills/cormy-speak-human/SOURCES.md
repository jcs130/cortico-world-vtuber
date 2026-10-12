# 自然口语资料筛选

核对日期：2026-10-12。下列是公开技能原始仓库；用于研究表达方法，没有运行其中的安装器、脚本或语音服务。本文与相邻指南独立整理，角色依据是可米现有档案及操作员本次要求。

| 原始资料 | 用在可米哪里 | 不直接照搬的部分 |
| --- | --- | --- |
| [MrGeDiao/shuorenhua](https://github.com/MrGeDiao/shuorenhua/blob/main/SKILL.md) | 清理赘语时保住责任、条件、完成状态和确定程度；正常句子允许不改 | 普通直播不是原样复述技术材料，按操作员要求省略不必口述的坐标和字段 |
| [syw2039/humanizer-zh](https://github.com/syw2039/humanizer-zh/blob/main/SKILL.md) | 中文口播要顺口，长短句自然；文风由本人样本和既有人设校准 | 不把文章审校的步骤、评分或多个草稿放进直播 |
| [blader/humanizer](https://github.com/blader/humanizer/blob/main/SKILL.md) | 根据听众已有语境组织话；避免反复铺垫、固定三段式和夸大普通结果 | 不把英文写作规则变成中文禁词表 |
| [jwynia/agent-skills 的 dialogue](https://github.com/jwynia/agent-skills/blob/main/skills/creative/fiction/character/dialogue/SKILL.md) | 用词、节奏和反应体现角色差异；读起来是否顺口，情境和节奏是否相合 | 这是小说对白诊断；不把隐藏目的、冲突和剧情推进要求套进真实交流，不编造现场 |
| [SilentFleetKK/zuiti-skill](https://github.com/SilentFleetKK/zuiti-skill/blob/main/SKILL.md) | 借鉴口吻校准维度和具体反馈修订方式 | 其主流程是难回消息的分档回复，不采用直播里固定三版回答、揣测恶意或攻防话术 |
| [NoizAI/skills 的 characteristic-voice](https://github.com/NoizAI/skills/blob/main/skills/characteristic-voice/SKILL.md) | 借鉴根据情境调整亲切、兴奋与安静程度的思路 | 不采用固定语气词开头和固定暖场结尾；不把 Noiz/Kokoro 的情绪参数当成 VoxCPM2 支持，不替换 E3 声音 |
| [Shinchan-crayon/thinkai-humanizer](https://github.com/Shinchan-crayon/thinkai-humanizer) | 口播按实际听众和整句节奏检查，润色时保留事实与不确定性 | 不为口语感编造经历或现场，不在每句前增加润色模型调用 |
| [agents365-ai/365-skills 的 ttscn](https://github.com/agents365-ai/365-skills/blob/main/plugins/ttscn/skills/ttscn/SKILL.md) | 多音字提示按词义和具体后端的能力使用 | 不把 Azure、MiniMax 的标记语法直接送给 VoxCPM2，不改换语音供应方 |
| [Pluviobyte/rnskill 的 tts-skill](https://github.com/Pluviobyte/rnskill/blob/main/skills/tts-skill/SKILL.md) | 中英混读保留名字原文，对专名和实际合成做核验 | 不照搬其 IndexTTS 服务地址、固定音色和部署流程 |

读音处理参考 [g2pW 原始项目](https://github.com/GitYCC/g2pW)及
[pypinyin-g2pW](https://github.com/mozillazg/pypinyin-g2pW)的整句上下文判音方式。
VoxCPM2 的拼音、括号控制与分句要求依据其
[官方使用说明](https://voxcpm.readthedocs.io/en/latest/usage_guide.html)。
口播技能负责组织表达，实际读音由语音服务处理；需要分别验证。

笑声与其他非语言发声参考以下一手资料：

| 原始资料 | 使用范围 |
| --- | --- |
| [VoxCPM2 官方 cookbook](https://voxcpm.readthedocs.io/en/latest/cookbook.html) | 推荐的通用笑声标签是 `[laughing]`；少量使用标签，简化描述，实际听感逐样验证 |
| [VoxCPM2 官方使用说明](https://voxcpm.readthedocs.io/en/latest/usage_guide.html) | 区分可控克隆与 Hi-Fi 续写；Hi-Fi 模式忽略控制描述，极短输入也存在稳定性限制 |
| [NVVSpeech Track 2 官方基线](https://github.com/NVVSpeech-Challenge/NVVSpeech-Challenge-Track2-Baseline)、[公开权重](https://huggingface.co/NVVSpeech-Challenge/NVVSpeech-Challenge-Track2-Baseline)与[参考克隆推理脚本](https://github.com/NVVSpeech-Challenge/NVVSpeech-Challenge-Track2-Baseline/blob/main/scripts/test_voxcpm_ft_infer.py) | 以 VoxCPM2 为基座全量微调的 16 类非语言发声模型，可下载权重直接推理并传参考音频；中文标签与该权重配套。整句表达、音色与实时性需要实际核验，不能当成原版服务已有能力 |
| [非语言发声偏好优化研究](https://arxiv.org/abs/2609.11260) | 分别评估发声是否发生、表达效果、自然度、质量及与台词的衔接；参考离线多候选筛选的方法，不在实时口播中增加多轮生成 |
| [CosyVoice 官方推理示例](https://github.com/QwenAudio/CosyVoice/blob/main/example.py)与[分词器控制标记](https://github.com/QwenAudio/CosyVoice/blob/main/cosyvoice/tokenizer/tokenizer.py) | 离线生成笑声候选；模型内部使用 `[laughter]`，演出入口仍使用现有 `[laughing]`，由缓存适配器播放筛选后的音频 |
| [Seed-VC 原始项目](https://github.com/Plachtaa/seed-vc) | 离线将发声候选按同一 E3 参考转换；声音转换结果仍需核对笑法、本人音色与前后衔接 |
| [CLAP 官方模型文档](https://huggingface.co/docs/transformers/model_doc/clap) | 辅助区分笑声、说话与换气等声音事件；相似度不代表自然度、本人身份或听众认可 |

本方法在当前对话内组织台词，无额外逐句润色调用。声音合成继续使用现役演出 World 的服务与固定参考音频；文字表达、人设一致性、声音听感和真实现场执行分别验证。

缓存发声的运行契约由扩展内 `adapters/acoustic/README.md` 说明。素材、来源记录与听感反馈保存在部署中；普通实时口播不加载离线制作模型。

当前两段缓存轻笑均未通过萌悦听感验收，已停用；CosyVoice 与 Seed-VC 保留为候选制作方法，不作为已认可的线上发声方案。
