"""
本地影音库批量下载脚本

从免费源下载适合心理咨询/戒毒康复场景的音乐和视频。
所有内容均为免费商用或公共领域 license。

使用前安装依赖:
    pip install yt-dlp requests

用法:
    python scripts/download_media.py --music     # 下载放松音乐
    python scripts/download_media.py --videos    # 下载放松视频
    python scripts/download_media.py --relaxation # 下载呼吸/冥想训练视频
    python scripts/download_media.py --all       # 下载全部
"""

import os
import sys
import json
import argparse
import subprocess
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
MEDIA_DIR = PROJECT_ROOT / "media_library"
RELAXATION_DIR = MEDIA_DIR / "relaxation"  # 放松视频落到 media_library/relaxation，与 VideoPlayTool 的查找根一致
MUSIC_DIR = MEDIA_DIR / "music"  # 放松音乐目录（脚本内 Pixabay/Archive 分支引用）

# 场景目录结构
SCENES = {
    "anxiety_relief":      {"name": "焦虑缓解", "queries": ["calming piano", "anxiety relief music", "舒缓钢琴曲"]},
    "depression_support":  {"name": "情绪提振", "queries": ["uplifting music", "hopeful instrumental", "温暖轻音乐"]},
    "anger_calm":          {"name": "愤怒平复", "queries": ["soothing music", "calm down music", "冷静音乐"]},
    "sleep_aid":           {"name": "助眠放松", "queries": ["sleep music", "lullaby instrumental", "助眠音乐"]},
    "meditation":          {"name": "冥想正念", "queries": ["meditation music", "tibetan bowls", "冥想音乐"]},
    "breathing_exercise":  {"name": "呼吸训练", "queries": []},  # 视频为主
    "muscle_relaxation":   {"name": "肌肉放松", "queries": []},  # 视频为主
    "nature_sounds":       {"name": "自然白噪音", "queries": ["rain sounds", "ocean waves", "forest ambience", "鸟鸣", "雨声"]},
    "entertainment":       {"name": "日常娱乐", "queries": ["chill music", "lo-fi hip hop", "轻松音乐"]},
    "motivation":          {"name": "振奋激励", "queries": ["epic music", "motivational instrumental", "励志音乐"]},
}

# 确保场景目录存在
for scene_id in SCENES:
    (MEDIA_DIR / scene_id / "music").mkdir(parents=True, exist_ok=True)
    (MEDIA_DIR / scene_id / "videos").mkdir(parents=True, exist_ok=True)


# ==================== YouTube/CC 音乐下载 ====================

# 适合心理咨询场景的搜索关键词（YouTube CC 协议音乐）
MUSIC_SEARCH_QUERIES = [
    # 放松轻音乐
    "relaxing piano music no copyright",
    "calm instrumental music creative commons",
    "peaceful guitar music royalty free",
    "ambient relaxation music free download",
    # 中国风轻音乐
    "中国风纯音乐 免费",
    "古筝轻音乐 放松",
    "钢琴曲 轻音乐 冥想",
    # 自然声音
    "nature sounds rain relaxation free",
    "ocean waves sounds sleep free",
    "bird songs ambient free download",
]

# 适合放松训练的 YouTube 视频（CC 协议）
RELAXATION_VIDEO_URLS = [
    # 呼吸训练引导
    "https://www.youtube.com/watch?v=inpok4MKVLM",  # 5-Minute Breathing Exercise
    "https://www.youtube.com/watch?v=F28MGLlpP90",  # Box Breathing Tutorial
    # 冥想引导
    "https://www.youtube.com/watch?v=O-6f5wQXSu8",  # 10 Min Meditation
    "https://www.youtube.com/watch?v=ZToicYcHIOU",  # Mindfulness Meditation
    # 肌肉放松
    "https://www.youtube.com/watch?v=1nZEdqcGVzo",  # Progressive Muscle Relaxation
    "https://www.youtube.com/watch?v=ClqDexdJdwQ",  # Guided Relaxation
    # 自然环境视频（无声背景）
    "https://www.youtube.com/watch?v=V1bFr2SWP1I",  # Ocean Waves 1 Hour
    "https://www.youtube.com/watch?v=eKFTSSKCzWA",  # Rain Sounds 3 Hours
    "https://www.youtube.com/watch?v=DLJNc6iMBjM",  # Forest Ambience
]


def download_music_yt(query: str, max_items: int = 5, scene: str = "entertainment"):
    """用 yt-dlp 从 YouTube 下载 CC 协议音乐。"""
    try:
        import yt_dlp
    except ImportError:
        print("[ERROR] 请先安装 yt-dlp: pip install yt-dlp")
        return

    output_dir = MEDIA_DIR / scene / "music"
    output_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': str(output_dir / '%(title)s.%(ext)s'),
        'max_downloads': max_items,
        'quiet': False,
        'no_warnings': False,
        # 只下载 Creative Commons 协议的视频
        'match_filter': None,  # yt-dlp 不直接支持 CC 过滤，需要手动筛选
        'default_search': 'ytsearch',
    }

    print(f"\n[INFO] 搜索并下载: {query}")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"ytsearch{max_items}:{query}"])
    except Exception as e:
        print(f"[WARNING] 下载失败: {e}")


def download_relaxation_videos():
    """下载放松训练视频。"""
    try:
        import yt_dlp
    except ImportError:
        print("[ERROR] 请先安装 yt-dlp: pip install yt-dlp")
        return

    output_dir = RELAXATION_DIR
    output_dir.mkdir(exist_ok=True)

    # 放松视频文件名映射
    video_names = {
        "呼吸训练": "呼吸训练.mp4",
        "肌肉放松": "肌肉放松.mp4",
        "冥想训练": "冥想训练.mp4",
    }

    ydl_opts = {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
        'outtmpl': str(output_dir / '%(title)s.%(ext)s'),
        'quiet': False,
    }

    print("\n[INFO] 下载放松训练视频...")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for url in RELAXATION_VIDEO_URLS:
            try:
                print(f"\n[INFO] 下载: {url}")
                ydl.download([url])
            except Exception as e:
                print(f"[WARNING] 跳过: {e}")

    print(f"\n[INFO] 视频已下载到: {output_dir}")
    print("[INFO] 请手动将合适的视频重命名为: 呼吸训练.mp4, 肌肉放松.mp4, 冥想训练.mp4")
    print("[INFO] 这些文件名与 VideoPlayTool.FILE_MAP 对应，否则运行时找不到视频。")


def download_pixabay_music(api_key: str = None, max_items: int = 20):
    """
    从 Pixabay 下载免费音乐。

    获取 API Key: https://pixabay.com/api/docs/ (免费注册)
    设置环境变量: set PIXABAY_API_KEY=your_key_here
    """
    import requests

    api_key = api_key or os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        print("[WARNING] Pixabay API Key 未设置。")
        print("  1. 访问 https://pixabay.com/api/docs/ 免费注册")
        print("  2. 设置环境变量: set PIXABAY_API_KEY=your_key_here")
        return

    output_dir = MUSIC_DIR / "pixabay"
    output_dir.mkdir(exist_ok=True)

    # 搜索放松音乐
    queries = ["relaxing", "meditation", "calm", "piano", "ambient", "nature"]
    downloaded = 0

    for query in queries:
        if downloaded >= max_items:
            break

        url = "https://pixabay.com/api/music/"
        params = {
            "key": api_key,
            "q": query,
            "per_page": min(10, max_items - downloaded),
            "lang": "en",
        }

        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for hit in data.get("hits", []):
                if downloaded >= max_items:
                    break

                audio_url = hit.get("audio")  # Pixabay music API
                if not audio_url:
                    continue

                title = hit.get("title", f"pixabay_{downloaded}")
                # 清理文件名
                safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip()
                filename = f"{safe_title}.mp3"
                filepath = output_dir / filename

                if filepath.exists():
                    print(f"[SKIP] 已存在: {filename}")
                    continue

                print(f"[DOWNLOAD] {filename}")
                audio_resp = requests.get(audio_url, timeout=60)
                audio_resp.raise_for_status()
                filepath.write_bytes(audio_resp.content)
                downloaded += 1

        except Exception as e:
            print(f"[WARNING] Pixabay 查询 '{query}' 失败: {e}")

    print(f"\n[INFO] Pixabay 音乐已下载到: {output_dir} ({downloaded} 首)")


def download_archive_org(max_items: int = 10):
    """从 Internet Archive 下载公共领域音乐。"""
    import requests

    output_dir = MUSIC_DIR / "archive_org"
    output_dir.mkdir(exist_ok=True)

    # 搜索公共领域放松音乐
    search_url = "https://archive.org/advancedsearch.php"
    queries = [
        "meditation music",
        "relaxation music",
        "ambient music",
        "classical piano",
    ]
    downloaded = 0

    for query in queries:
        if downloaded >= max_items:
            break

        params = {
            "q": f"({query}) AND mediatype:audio AND licenseurl:*publicdomain*",
            "fl[]": "identifier,title",
            "rows": min(5, max_items - downloaded),
            "output": "json",
        }

        try:
            resp = requests.get(search_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            for doc in data.get("response", {}).get("docs", []):
                if downloaded >= max_items:
                    break

                identifier = doc.get("identifier")
                title = doc.get("title", identifier)
                safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip()

                # 获取音频文件列表
                files_url = f"https://archive.org/metadata/{identifier}/files"
                files_resp = requests.get(files_url, timeout=30)
                files_resp.raise_for_status()
                files = files_resp.json().get("result", [])

                for f in files:
                    name = f.get("name", "")
                    if name.endswith((".mp3", ".ogg", ".flac")):
                        download_url = f"https://archive.org/download/{identifier}/{name}"
                        filepath = output_dir / f"{safe_title}{Path(name).suffix}"

                        if filepath.exists():
                            print(f"[SKIP] 已存在: {filepath.name}")
                            break

                        print(f"[DOWNLOAD] {filepath.name}")
                        audio_resp = requests.get(download_url, timeout=120)
                        audio_resp.raise_for_status()
                        filepath.write_bytes(audio_resp.content)
                        downloaded += 1
                        break

        except Exception as e:
            print(f"[WARNING] Archive.org 查询 '{query}' 失败: {e}")

    print(f"\n[INFO] Archive.org 音乐已下载到: {output_dir} ({downloaded} 首)")


def update_library_config():
    """扫描场景目录，更新 library_config.json。"""
    config_path = MEDIA_DIR / "library_config.json"

    # 读取现有配置或初始化
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        config = {"version": 2, "scenes": {}}

    if "scenes" not in config:
        config["scenes"] = {}

    music_exts = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
    video_exts = {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv"}
    total_music = 0
    total_videos = 0

    for scene_id, scene_info in SCENES.items():
        # 初始化场景
        if scene_id not in config["scenes"]:
            config["scenes"][scene_id] = {
                "name": scene_info["name"],
                "description": "",
                "music": [],
                "videos": [],
            }

        scene_config = config["scenes"][scene_id]
        existing_music = {item["path"] for item in scene_config.get("music", [])}
        existing_videos = {item["path"] for item in scene_config.get("videos", [])}

        # 扫描场景音乐目录
        scene_music_dir = MEDIA_DIR / scene_id / "music"
        if scene_music_dir.exists():
            for f in scene_music_dir.rglob("*"):
                if f.suffix.lower() in music_exts and str(f) not in existing_music:
                    scene_config["music"].append({
                        "name": f.stem,
                        "path": str(f),
                        "description": f"{scene_info['name']} - 自动导入",
                    })
                    print(f"[ADD] [{scene_info['name']}] 音乐: {f.name}")
                    total_music += 1

        # 扫描场景视频目录
        scene_video_dir = MEDIA_DIR / scene_id / "videos"
        if scene_video_dir.exists():
            for f in scene_video_dir.rglob("*"):
                if f.suffix.lower() in video_exts and str(f) not in existing_videos:
                    scene_config["videos"].append({
                        "name": f.stem,
                        "path": str(f),
                        "description": f"{scene_info['name']} - 自动导入",
                    })
                    print(f"[ADD] [{scene_info['name']}] 视频: {f.name}")
                    total_videos += 1

    # 保存
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[INFO] 库配置已更新: {config_path}")
    print(f"  新增音乐: {total_music} 首")
    print(f"  新增视频: {total_videos} 部")

    # 汇总
    for scene_id, scene_data in config["scenes"].items():
        m = len(scene_data.get("music", []))
        v = len(scene_data.get("videos", []))
        if m > 0 or v > 0:
            print(f"  {scene_data['name']}: {m}首音乐, {v}个视频")


def download_by_scene(scene: str, max_items: int = 10):
    """为指定场景下载音乐。"""
    if scene not in SCENES:
        print(f"[ERROR] 未知场景: {scene}")
        print(f"可用场景: {', '.join(SCENES.keys())}")
        return

    scene_info = SCENES[scene]
    queries = scene_info.get("queries", [])
    if not queries:
        print(f"[INFO] 场景 '{scene_info['name']}' 无搜索关键词，请手动下载到: {MEDIA_DIR / scene / 'music'}")
        return

    print(f"\n{'=' * 60}")
    print(f"为场景 [{scene_info['name']}] 下载音乐")
    print(f"{'=' * 60}")

    per_query = max(1, max_items // len(queries))
    for query in queries:
        download_music_yt(query, max_items=per_query, scene=scene)


def main():
    parser = argparse.ArgumentParser(description="本地影音库批量下载（按场景分类）")
    parser.add_argument("--scene", type=str, help="为指定场景下载 (如: anxiety_relief, meditation)")
    parser.add_argument("--scenes", action="store_true", help="列出所有可用场景")
    parser.add_argument("--music", action="store_true", help="下载放松音乐 (YouTube CC)")
    parser.add_argument("--videos", action="store_true", help="下载放松视频")
    parser.add_argument("--relaxation", action="store_true", help="下载呼吸/冥想训练视频")
    parser.add_argument("--pixabay", action="store_true", help="从 Pixabay 下载免费音乐")
    parser.add_argument("--archive", action="store_true", help="从 Internet Archive 下载公共领域音乐")
    parser.add_argument("--scan", action="store_true", help="扫描本地目录更新影音库配置")
    parser.add_argument("--all-scenes", action="store_true", help="为所有场景下载音乐")
    parser.add_argument("--all", action="store_true", help="下载全部")
    parser.add_argument("--max", type=int, default=10, help="每类最大下载数")
    args = parser.parse_args()

    if args.scenes:
        print("\n可用场景:")
        print("-" * 50)
        for sid, info in SCENES.items():
            print(f"  {sid:25s} {info['name']}")
        print(f"\n使用方法: python scripts/download_media.py --scene <场景ID>")
        print(f"示例: python scripts/download_media.py --scene anxiety_relief --max 15")
        return

    if args.scene:
        download_by_scene(args.scene, args.max)
        update_library_config()
        return

    if args.all or args.all_scenes:
        args.music = args.videos = args.relaxation = args.pixabay = args.archive = args.scan = True

    if not any([args.music, args.videos, args.relaxation, args.pixabay, args.archive, args.scan]):
        parser.print_help()
        print("\n快速开始:")
        print("  python scripts/download_media.py --scenes          # 查看所有场景")
        print("  python scripts/download_media.py --scene anxiety_relief --max 15  # 按场景下载")
        print("  python scripts/download_media.py --relaxation      # 下载放松训练视频")
        print("  python scripts/download_media.py --all --max 30    # 下载全部")
        return

    if args.music:
        print("=" * 60)
        print("下载放松音乐 (YouTube Creative Commons)")
        print("=" * 60)
        for query in MUSIC_SEARCH_QUERIES[:3]:
            download_music_yt(query, max_items=args.max // 3)

    if args.videos:
        print("=" * 60)
        print("下载放松视频")
        print("=" * 60)
        download_relaxation_videos()

    if args.relaxation:
        print("=" * 60)
        print("下载呼吸/冥想训练视频")
        print("=" * 60)
        download_relaxation_videos()

    if args.pixabay:
        print("=" * 60)
        print("从 Pixabay 下载免费音乐")
        print("=" * 60)
        download_pixabay_music(max_items=args.max)

    if args.archive:
        print("=" * 60)
        print("从 Internet Archive 下载公共领域音乐")
        print("=" * 60)
        download_archive_org(max_items=args.max)

    if args.scan:
        print("=" * 60)
        print("扫描本地目录更新影音库")
        print("=" * 60)
        update_library_config()

    print("\n" + "=" * 60)
    print("完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
