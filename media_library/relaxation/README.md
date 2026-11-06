# 放松训练媒体

## 正式视频（用户于 2026-11-05 置入）

- 文件：呼吸训练.mp4 / 肌肉放松.mp4 / 冥想训练.mp4
- 来源：用户提供（咨询师出镜实拍/AI 生成系列，与 背景图.jpg 同一风格，
  画面含水印“南昌市强制隔离戒毒所 心理咨询中心”）
- 规格：960x544 (16:9)、24fps、H.264 (Main) + AAC 44.1kHz 立体声
- 时长：约 293 秒（4 分 53 秒），均在目录 max_duration_seconds 限制内
  （呼吸训练 ≤300s、肌肉放松 ≤600s、冥想训练 ≤600s）
- 音轨响度：mean -23 ~ -26 dB / max -4 ~ -5 dB，非静音
- 2026-11-06 经 ffmpeg 探测与启动器预检验证通过（relaxation media OK）

## 备用视频（本地生成）

- 文件：呼吸训练备用.mp4
- 语音：本机 VoxCPM2 + 音色提示 s1.mp3 真实合成（scripts/generate_relaxation_media.py）
- 画面：PIL 渲染静态背景 + imageio-ffmpeg 合成 H.264/AAC
- 时长：约 189 秒（目录定义 recommended_duration_seconds=180）
- 生成日期：2026-11-04
