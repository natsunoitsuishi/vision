#!/usr/bin/env python3
"""
将 Markdown 文档转换为 PDF
"""
import markdown
from weasyprint import HTML
import os

# 输入和输出文件路径
input_file = "helloday说明文档.md"
output_file = "helloday说明文档.pdf"

# 读取 Markdown 文件
with open(input_file, 'r', encoding='utf-8') as f:
    markdown_content = f.read()

# 转换为 HTML
html_content = markdown.markdown(markdown_content)

# 添加基本样式
html_template = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>helloday.py 脚本说明文档</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            line-height: 1.6;
            margin: 20px;
            color: #333;
        }
        h1, h2, h3, h4 {
            color: #2c3e50;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            margin: 20px 0;
        }
        th, td {
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
        }
        th {
            background-color: #f2f2f2;
        }
        code {
            background-color: #f4f4f4;
            padding: 2px 4px;
            border-radius: 3px;
        }
        pre {
            background-color: #f4f4f4;
            padding: 10px;
            border-radius: 5px;
            overflow-x: auto;
        }
        .highlight {
            background-color: #fff3cd;
            padding: 10px;
            border-left: 4px solid #ffc107;
            margin: 10px 0;
        }
    </style>
</head>
<body>
'''+html_content+'''
</body>
</html>
'''

# 转换为 PDF
HTML(string=html_template).write_pdf(output_file)

print(f"已成功将 {input_file} 转换为 {output_file}")
