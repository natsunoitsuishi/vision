#!/usr/bin/env python3
"""
将 Markdown 文档转换为简单的 PDF 格式
使用 ReportLab 库，更轻量级
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
import re

# 输入和输出文件路径
input_file = "helloday说明文档.md"
output_file = "helloday说明文档.pdf"

# 读取 Markdown 文件
with open(input_file, 'r', encoding='utf-8') as f:
    content = f.read()

# 解析 Markdown
lines = content.split('\n')

# 创建 PDF 文档
doc = SimpleDocTemplate(output_file, pagesize=A4)
story = []

# 样式
styles = getSampleStyleSheet()
title_style = styles['Heading1']
h2_style = styles['Heading2']
h3_style = styles['Heading3']
body_style = styles['BodyText']
code_style = ParagraphStyle(
    'Code',
    parent=styles['Code'],
    backColor=colors.lightgrey,
    fontSize=10,
    spaceAfter=12
)

# 处理内容
for line in lines:
    line = line.strip()
    
    # 标题
    if line.startswith('# '):
        text = line[2:]
        story.append(Paragraph(text, title_style))
        story.append(Spacer(1, 12))
    elif line.startswith('## '):
        text = line[3:]
        story.append(Paragraph(text, h2_style))
        story.append(Spacer(1, 12))
    elif line.startswith('### '):
        text = line[4:]
        story.append(Paragraph(text, h3_style))
        story.append(Spacer(1, 12))
    
    # 表格
    elif line.startswith('|'):
        # 简单处理表格
        table_data = []
        while line.startswith('|'):
            row = [cell.strip() for cell in line.split('|')[1:-1]]
            table_data.append(row)
            # 读取下一行
            if lines:
                line = lines.pop(0).strip()
            else:
                break
        
        # 创建表格
        if len(table_data) > 1:
            table = Table(table_data)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            story.append(table)
            story.append(Spacer(1, 12))
    
    # 代码块
    elif line.startswith('```'):
        # 简单处理代码块
        code_lines = []
        line = lines.pop(0).strip()
        while line and not line.startswith('```'):
            code_lines.append(line)
            if lines:
                line = lines.pop(0).strip()
            else:
                break
        
        if code_lines:
            code_text = '\n'.join(code_lines)
            story.append(Paragraph(code_text, code_style))
            story.append(Spacer(1, 12))
    
    # 普通文本
    elif line:
        story.append(Paragraph(line, body_style))
        story.append(Spacer(1, 6))
    
    # 空行
    else:
        story.append(Spacer(1, 6))

# 构建 PDF
doc.build(story)

print(f"已成功将 {input_file} 转换为 {output_file}")
