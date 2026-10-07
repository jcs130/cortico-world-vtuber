/** IndexTTS 每次合成的声线配置；关闭时沿用适配器偏好。 */
export interface TtsSpeechPreferences {
  enabled: boolean;
  voice: string;
  speed: number;
  emotionMix: number;
  emotionMinConfidence: number;
}

export const TTS_SPEECH_DEFAULTS: TtsSpeechPreferences = {
  enabled: false, voice: '', speed: 1, emotionMix: 0.35, emotionMinConfidence: 0.65,
};
