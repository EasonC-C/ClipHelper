import re


def parse_douyin_text(text):
    """解析抖音商品分享文本，提取 URL 和标题"""
    if not text:
        return None

    # 提取抖音链接
    url_pattern = r'https://v\.douyin\.com/[a-zA-Z0-9_-]+/'
    urls = re.findall(url_pattern, text)

    if not urls:
        return None

    # 用 URL 位置定位标题：在 URL 反引号之后、"长按复制"之前
    title = ""
    for url in urls:
        # 找带反引号的 URL：`https://v.douyin.com/xxx/`
        url_quoted = f"`{url}`"
        idx = text.find(url_quoted)
        if idx >= 0:
            start = idx + len(url_quoted)
        else:
            # 没有反引号，直接用 URL 后面
            idx = text.find(url)
            if idx >= 0:
                start = idx + len(url)
            else:
                continue

        # 找结束标记（"长按复制"或换行）
        end = text.find("长按复制", start)
        if end < 0:
            end = text.find("\n", start)
        if end < 0:
            end = len(text)

        candidate = text[start:end].strip()
        if candidate:
            title = candidate
            break

    # 清理标题
    title = re.sub(r'^@', '', title)
    title = re.sub(r'\s+', ' ', title).strip()

    return {
        'urls': urls,
        'title': title,
    }
