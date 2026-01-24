# PDF Report Generator Service
# Generates professional psychological assessment reports in PDF format

import os
import json
from datetime import datetime
from typing import Dict, Any, Optional

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm, cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.platypus import Image as RLImage
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    print("[WARNING] reportlab not installed. PDF generation disabled.")


class PDFReportGenerator:
    """Generates professional psychological assessment reports in PDF format."""
    
    def __init__(self, font_path: str = None):
        """
        Initialize the PDF generator.
        
        Args:
            font_path: Path to Chinese font file (TTF). If None, uses system font.
        """
        self.font_registered = False
        self.font_name = "SimHei"
        
        if REPORTLAB_AVAILABLE:
            self._register_chinese_font(font_path)
    
    def _register_chinese_font(self, font_path: str = None):
        """Register Chinese font for PDF generation."""
        # Try multiple font paths
        font_paths = [
            font_path,
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\msyh.ttc",  # Microsoft YaHei
            r"C:\Windows\Fonts\simsun.ttc",  # SimSun
        ]
        
        for path in font_paths:
            if path and os.path.exists(path):
                try:
                    pdfmetrics.registerFont(TTFont(self.font_name, path))
                    self.font_registered = True
                    print(f"[INFO] PDF字体已注册: {path}")
                    return
                except Exception as e:
                    print(f"[WARNING] 字体注册失败 {path}: {e}")
        
        print("[WARNING] 未找到中文字体，PDF可能显示乱码")
    
    def _create_styles(self) -> Dict[str, ParagraphStyle]:
        """Create paragraph styles for the report."""
        base_styles = getSampleStyleSheet()
        
        font = self.font_name if self.font_registered else "Helvetica"
        
        # Define brand colors
        primary_color = colors.Color(0.1, 0.2, 0.4) # Navy Blue
        accent_color = colors.Color(0.8, 0.3, 0.3)  # Muted Red
        text_color = colors.Color(0.2, 0.2, 0.2)    # Dark Grey
        
        styles = {
            "title": ParagraphStyle(
                "CustomTitle",
                parent=base_styles["Heading1"],
                fontName=font,
                fontSize=22,
                textColor=primary_color,
                alignment=1,  # Center
                spaceBefore=40, # Push title down
                spaceAfter=25, # Increased to push subtitle down
                leading=26
            ),
            "subtitle": ParagraphStyle(
                "CustomSubtitle",
                parent=base_styles["Normal"],
                fontName=font,
                fontSize=11,
                textColor=colors.darkgray,
                alignment=1,  # Center
                spaceAfter=15 # Reduced
            ),
            "heading": ParagraphStyle(
                "CustomHeading",
                parent=base_styles["Heading2"],
                fontName=font,
                fontSize=14,
                textColor=primary_color,
                spaceBefore=12, # Reduced
                spaceAfter=6, # Reduced
                borderPadding=5,
                borderColor=colors.lightgrey,
                borderWidth=0,
                backColor=None 
            ),
            "body": ParagraphStyle(
                "CustomBody",
                parent=base_styles["Normal"],
                fontName=font,
                fontSize=11,
                leading=15, # Slightly tighter leading
                textColor=text_color,
                spaceAfter=4, # Reduced paragraph spacing
                firstLineIndent=0
            ),
            "small": ParagraphStyle(
                "CustomSmall",
                parent=base_styles["Normal"],
                fontName=font,
                fontSize=9,
                textColor=colors.gray,
                alignment=1
            ),
        }
        
        return styles
    
    def generate_report(self, report_data: Dict[str, Any], output_path: str) -> Optional[str]:
        """
        Generate a PDF report from the given data.
        
        Args:
            report_data: Dictionary containing report information
            output_path: Directory to save the PDF file
            
        Returns:
            Full path to the generated PDF file, or None if generation failed
        """
        if not REPORTLAB_AVAILABLE:
            print("[ERROR] reportlab not available, cannot generate PDF")
            return None
        
        try:
            # Import BaseDocTemplate components here to avoid top-level dependency issues
            from reportlab.platypus import BaseDocTemplate, PageTemplate, Frame
            
            # Ensure output directory exists
            os.makedirs(output_path, exist_ok=True)
            
            # Generate filename
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            subject_id = report_data.get("subject_id", "unknown")
            filename = f"assessment_report_{subject_id}_{date_str}.pdf"
            filepath = os.path.join(output_path, filename)
            
            # Background drawer function
            def on_page(canvas, doc):
                canvas.saveState()
                
                # 1. Draw Background Image (Full Page)
                bg_path = r"D:\program\voice_chat_app\services\PDFbackground.png"
                if os.path.exists(bg_path):
                    try:
                        # Draw image covering the whole page
                        canvas.drawImage(bg_path, 0, 0, width=A4[0], height=A4[1], preserveAspectRatio=False)
                    except Exception as e:
                        print(f"Background draw error: {e}")
                
                # 2. Draw Translucent Content Box - REMOVED as requested
                # Adjusted margins to avoid covering LOGO at the top
                margin_top = 5.0 * cm  # Increased top margin
                margin_bottom = 2.0 * cm
                margin_side = 1.5 * cm
                
                box_width = A4[0] - 2 * margin_side
                box_height = A4[1] - margin_top - margin_bottom
                
                # 3. Add Header/Footer
                canvas.setFont(self.font_name if self.font_registered else "Helvetica", 9)
                canvas.setFillColor(colors.gray)
                # Removed page number as requested
                
                canvas.restoreState()

            # Create Doc Template with custom PageTemplate
            doc = BaseDocTemplate(
                filepath,
                pagesize=A4,
                rightMargin=2*cm,
                leftMargin=2*cm,
                topMargin=2*cm,
                bottomMargin=2*cm
            )
            
            # Create a Frame that sits INSIDE the translucent box we drew
            # Margins must match the box + padding
            padding = 2 * cm 
            frame_w = A4[0] - 2 * padding
            frame_h = A4[1] - 2 * padding
            
            main_frame = Frame(
                padding, # x
                padding, # y
                frame_w,
                frame_h,
                id='normal'
            )
            
            template = PageTemplate(id='background_page', frames=main_frame, onPage=on_page)
            doc.addPageTemplates([template])
            
            # Build story
            styles = self._create_styles()
            story = []
            
            # Title Section
            story.append(Paragraph("心理康复评估报告", styles["title"]))
            
            report_id = report_data.get("report_id", f"RPT-{date_str}")
            report_date = report_data.get("report_date", datetime.now().strftime("%Y年%m月%d日"))
            
            # Split into two lines
            style_subtitle_first = ParagraphStyle(
                "SubtitleFirst", 
                parent=styles["subtitle"],
                spaceAfter=4
            )
            story.append(Paragraph(f"报告编号: {report_id}", style_subtitle_first))
            story.append(Paragraph(f"评估日期: {report_date}", styles["subtitle"]))
            
            story.append(Spacer(1, 10))
            
            # Basic Information Section
            story.append(Paragraph("一、基本信息", styles["heading"]))
            
            # Round session duration to 2 decimal places
            duration = report_data.get('session_duration_minutes', 0)
            if isinstance(duration, (int, float)):
                duration = round(float(duration), 2)
            
            # Gather all items first
            info_items = [
                ("被试编号", report_data.get("subject_id", "未知")),
            ]
            
            user_info = report_data.get("user_info", {})
            if user_info:
                if user_info.get("gender"): info_items.append(("性别", str(user_info.get("gender"))))
                if user_info.get("age"): info_items.append(("年龄", str(user_info.get("age"))))
                if user_info.get("education"): info_items.append(("文化程度", str(user_info.get("education"))))
                if user_info.get("marital_status"): info_items.append(("婚姻状况", str(user_info.get("marital_status"))))
                if user_info.get("drug_type"): info_items.append(("毒品类型", str(user_info.get("drug_type"))))
            
            info_items.append(("会话时长", f"{duration} 分钟"))
            info_items.append(("对话轮次", f"{report_data.get('conversation_rounds', 0)} 轮"))
            # Removed End Type as requested
            
            # Pack into pairs for 2-column layout (Label, Value, Label, Value)
            table_data = []
            for i in range(0, len(info_items), 2):
                row = []
                # First pair
                row.append(info_items[i][0])
                row.append(info_items[i][1])
                
                # Second pair (if exists)
                if i + 1 < len(info_items):
                    row.append(info_items[i+1][0])
                    row.append(info_items[i+1][1])
                else:
                    row.extend(["", ""]) # Padding
                table_data.append(row)
            
            # Two-column table styling
            # Col widths: Label 3cm, Value 5cm, Label 3cm, Value 5cm (Total 16cm)
            col_widths = [3*cm, 5*cm, 3*cm, 5*cm]
            info_table = Table(table_data, colWidths=col_widths)
            info_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), self.font_name if self.font_registered else 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
                # Gray labels
                ('TEXTCOLOR', (0, 0), (0, -1), colors.darkslategray), 
                ('TEXTCOLOR', (2, 0), (2, -1), colors.darkslategray),
                
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.Color(0.9, 0.9, 0.9)),
                ('PADDING', (0, 0), (-1, -1), 8),
            ]))
            story.append(info_table)
            
            story.append(Spacer(1, 10)) # Reduced spacer
            
            # Assessment Results Section
            story.append(Paragraph("二、评估结果", styles["heading"]))
            
            # Get assessment data - handle different data structures
            emotional_state = report_data.get("emotional_assessment", {})
            if isinstance(emotional_state, dict):
                # Could be {primary_emotion, intensity} or {initial_state, final_state, trajectory}
                if "primary_emotion" in emotional_state:
                    emotion_text = f"{emotional_state.get('primary_emotion', '未评估')} (强度: {emotional_state.get('intensity', '未知')})"
                elif "initial_state" in emotional_state:
                    initial = emotional_state.get('initial_state', '未知')
                    final = emotional_state.get('final_state', '未知')
                    trajectory = emotional_state.get('trajectory', '')
                    emotion_text = f"初始: {initial} → 结束: {final}"
                    if trajectory:
                        emotion_text += f" ({trajectory})"
                else:
                    emotion_text = "未评估"
            elif emotional_state:
                emotion_text = str(emotional_state)
            else:
                emotion_text = "未评估"
            
            risk_assessment = report_data.get("risk_assessment", {})
            if isinstance(risk_assessment, dict):
                risk_level = risk_assessment.get("level", "未评估")
                risk_notes = risk_assessment.get("notes", "")
                risk_text = risk_level
                if risk_notes:
                    risk_text += f" - {risk_notes}"
            elif risk_assessment:
                risk_text = str(risk_assessment)
            else:
                risk_text = "未评估"
            
            # Get identified issues
            issues = report_data.get("identified_issues", [])
            if isinstance(issues, list) and issues:
                issues_text = "、".join(issues[:5])  # First 5 issues
            else:
                issues_text = "暂无"
            
            assessment_data = [
                ["情绪状态", Paragraph(emotion_text, styles["body"])],
                ["风险等级", Paragraph(risk_text, styles["body"])],
                ["识别问题", Paragraph(issues_text, styles["body"])],
            ]
            
            # Add recommendations if available
            recommendations = report_data.get("recommendations", [])
            if recommendations:
                if isinstance(recommendations, list):
                    rec_text = "、".join(recommendations[:3])
                else:
                    rec_text = str(recommendations)
                assessment_data.append(["主要建议", Paragraph(rec_text, styles["body"])])
            
            assess_table = Table(assessment_data, colWidths=[4*cm, 10*cm])
            assess_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), self.font_name if self.font_registered else 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('TEXTCOLOR', (0, 0), (0, -1), colors.darkslategray),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.Color(0.9, 0.9, 0.9)),
                ('PADDING', (0, 0), (-1, -1), 10),
            ]))
            story.append(assess_table)
            
            story.append(Spacer(1, 15))
            
            # Conversation Summary Section
            story.append(Paragraph("三、会话摘要", styles["heading"]))
            
            # Try multiple possible field names for summary
            summary = (report_data.get("summary") or 
                      report_data.get("conversation_summary") or 
                      report_data.get("raw_analysis") or
                      "暂无摘要")
            if isinstance(summary, dict):
                summary = summary.get("summary", "暂无摘要")
            
            # Just plain text for summary
            story.append(Paragraph(str(summary), styles["body"]))
            
            story.append(Spacer(1, 15))
            
            # Recommendations Section
            if recommendations:
                story.append(Paragraph("四、具体建议", styles["heading"]))
                
                if isinstance(recommendations, list):
                    for i, rec in enumerate(recommendations, 1):
                        story.append(Paragraph(f"{i}. {rec}", styles["body"]))
                else:
                    story.append(Paragraph(str(recommendations), styles["body"]))
            
            story.append(Spacer(1, 30))
            
            # Disclaimer
            disclaimer = ("声明：本报告由 AI 辅助生成，仅供参考，不构成正式医学诊断。"
                         "如有严重心理问题，请及时寻求专业心理咨询或医疗帮助。")
            story.append(Paragraph(disclaimer, styles["small"]))
            
            # Build PDF
            doc.build(story)
            
            print(f"[INFO] PDF报告已生成: {filepath}")
            return filepath
            
        except Exception as e:
            print(f"[ERROR] PDF生成失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _translate_end_type(self, end_type: str) -> str:
        """Translate end type to Chinese."""
        translations = {
            "GOAL_ACHIEVED": "目标达成",
            "TIME_LIMIT": "时间到达",
            "SAFETY": "安全干预",
            "INVALID": "无效对话",
            "NONE": "未知",
        }
        return translations.get(end_type, end_type or "未知")


# Singleton instance
_pdf_generator = None

def get_pdf_generator() -> PDFReportGenerator:
    """Get the singleton PDF generator instance."""
    global _pdf_generator
    if _pdf_generator is None:
        _pdf_generator = PDFReportGenerator()
    return _pdf_generator
