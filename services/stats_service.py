# Stats Service - Aggregate treatment statistics across subjects

import json
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime

from services.logger import get_logger

logger = get_logger(__name__)


class StatsService:
    """Aggregates treatment statistics across subjects and sessions."""

    def __init__(self, data_root: str):
        self.data_root = Path(data_root)
        self.summaries_dir = self.data_root / "session_summaries"

    def _load_all_progress(self) -> List[Dict[str, Any]]:
        """Load all *_progress.json files."""
        all_progress = []
        if not self.summaries_dir.exists():
            return all_progress
        for path in self.summaries_dir.glob("*_progress.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    all_progress.append(data)
            except Exception as e:
                logger.warning(f"Failed to load {path.name}: {e}")
        return all_progress

    def get_all_subject_stats(self) -> List[Dict[str, Any]]:
        """Get per-subject summary stats."""
        all_progress = self._load_all_progress()
        subjects = []
        for prog in all_progress:
            sessions = prog.get("sessions", [])
            if not sessions:
                continue
            latest = sessions[-1]
            subjects.append({
                "subject_id": prog.get("subject_id", "unknown"),
                "session_count": len(sessions),
                "latest_emotion": latest.get("emotion_summary", {}).get("dominant", "--"),
                "latest_emotion_trend": latest.get("emotion_summary", {}).get("trend", "--"),
                "latest_intensity": latest.get("emotion_summary", {}).get("avg_intensity", 0),
                "latest_scale_scores": latest.get("scale_scores", {}),
                "last_session_date": latest.get("date", "--"),
                "total_duration": sum(s.get("duration_minutes", 0) for s in sessions),
                "end_types": [s.get("end_type", "") for s in sessions],
            })
        return subjects

    def get_group_stats(self) -> Dict[str, Any]:
        """Get aggregate group-level statistics."""
        all_progress = self._load_all_progress()
        total_sessions = 0
        total_duration = 0.0
        crisis_count = 0
        end_type_counts: Dict[str, int] = {}
        improvement_count = 0
        subject_count = len(all_progress)

        for prog in all_progress:
            sessions = prog.get("sessions", [])
            total_sessions += len(sessions)
            for s in sessions:
                total_duration += s.get("duration_minutes", 0)
                et = s.get("end_type", "unknown")
                end_type_counts[et] = end_type_counts.get(et, 0) + 1
                for event in s.get("key_events", []):
                    if "crisis" in event:
                        crisis_count += 1

            # Check improvement: latest scale scores < first scale scores
            if len(sessions) >= 2:
                first_scales = sessions[0].get("scale_scores", {})
                last_scales = sessions[-1].get("scale_scores", {})
                improved = False
                for scale_name in first_scales:
                    if scale_name in last_scales:
                        first_total = first_scales[scale_name].get("total", 0)
                        last_total = last_scales[scale_name].get("total", 0)
                        if last_total < first_total:
                            improved = True
                if improved:
                    improvement_count += 1

        return {
            "total_subjects": subject_count,
            "total_sessions": total_sessions,
            "avg_duration": round(total_duration / max(total_sessions, 1), 1),
            "crisis_count": crisis_count,
            "crisis_rate": round(crisis_count / max(total_sessions, 1), 3),
            "end_type_distribution": end_type_counts,
            "improvement_count": improvement_count,
            "improvement_rate": round(improvement_count / max(subject_count, 1), 3),
        }

    def get_scale_score_progressions(self) -> Dict[str, List[Dict]]:
        """Get scale score progressions across all subjects."""
        all_progress = self._load_all_progress()
        progressions: Dict[str, List[Dict]] = {}
        for prog in all_progress:
            sid = prog.get("subject_id", "unknown")
            for scale_name, entries in prog.get("scale_trend", {}).items():
                if scale_name not in progressions:
                    progressions[scale_name] = []
                for entry in entries:
                    progressions[scale_name].append({
                        "subject_id": sid,
                        "date": entry.get("date", ""),
                        "total": entry.get("total", 0),
                        "severity": entry.get("severity", ""),
                    })
        return progressions

    def get_emotion_trend_aggregation(self) -> Dict[str, List[Dict]]:
        """Aggregate emotion trends across subjects. Returns {subject_id: [entries]}."""
        all_progress = self._load_all_progress()
        result: Dict[str, List[Dict]] = {}
        for prog in all_progress:
            sid = prog.get("subject_id", "unknown")
            result[sid] = prog.get("emotion_trend", [])
        return result

    def export_group_report_pdf(self, output_path: str) -> str:
        """Generate a PDF group report using reportlab."""
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib import colors
            from reportlab.lib.units import cm
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        except ImportError:
            logger.warning("reportlab not available")
            return ""

        doc = SimpleDocTemplate(output_path, pagesize=A4,
                                leftMargin=2*cm, rightMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)
        styles = getSampleStyleSheet()
        try:
            styles.add(ParagraphStyle(name='Chinese', fontName='SimSun', fontSize=10, leading=14))
            styles.add(ParagraphStyle(name='ChineseTitle', fontName='SimSun', fontSize=16, leading=20, alignment=1))
        except Exception:
            pass

        elements = []
        cn_style = styles.get('Chinese', styles['Normal'])
        cn_title = styles.get('ChineseTitle', styles['Title'])

        elements.append(Paragraph("治疗效果统计报告", cn_title))
        elements.append(Spacer(1, 12))
        elements.append(Paragraph(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}", cn_style))
        elements.append(Spacer(1, 20))

        # Group stats
        group = self.get_group_stats()
        elements.append(Paragraph("一、总体统计", cn_style))
        elements.append(Spacer(1, 8))
        stats_data = [
            ["指标", "数值"],
            ["被试总数", str(group["total_subjects"])],
            ["总会话数", str(group["total_sessions"])],
            ["平均时长(分钟)", str(group["avg_duration"])],
            ["危机事件数", str(group["crisis_count"])],
            ["好转率", f"{group['improvement_rate']*100:.1f}%"],
        ]
        t = Table(stats_data, colWidths=[6*cm, 6*cm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 20))

        # Per-subject table
        elements.append(Paragraph("二、被试明细", cn_style))
        elements.append(Spacer(1, 8))
        subject_stats = self.get_all_subject_stats()
        subject_data = [["被试", "会话数", "最近情绪", "趋势", "最近会话"]]
        for s in subject_stats:
            subject_data.append([
                s["subject_id"],
                str(s["session_count"]),
                s["latest_emotion"],
                s["latest_emotion_trend"],
                s["last_session_date"],
            ])
        t2 = Table(subject_data, colWidths=[3*cm, 2*cm, 3*cm, 3*cm, 3*cm])
        t2.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
        ]))
        elements.append(t2)

        doc.build(elements)
        logger.info(f"Group report exported to {output_path}")
        return output_path
