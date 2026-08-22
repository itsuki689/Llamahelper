"""挂件用的 llama-server Chat API 客户端（后台线程，不阻塞 UI）。"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request

from PySide6.QtCore import QBuffer, QIODevice, QThread, Signal
from PySide6.QtGui import QImage

DEFAULT_TRANSLATE_PROMPT = (
    "You are a professional translation assistant. "
    "Detect the input language: if the input is Chinese, translate it into "
    "English; otherwise translate it into Chinese. "
    "If the input is an image, read the text in the image first, then translate "
    "it the same way. Output only the translation — no explanations, no quotes, "
    "no extra formatting."
)

IMAGE_CHINESE_REQUIREMENT = (
    "For every image input, OCR all readable text and translate it completely "
    "into Simplified Chinese. The final answer must contain the Chinese "
    "translation only. Never merely transcribe or repeat the original English "
    "text. Preserve paragraphs, URLs, email addresses, and proper nouns when "
    "needed, but translate all surrounding prose."
)
IMAGE_REQUEST_TEXT = (
    "请识别图片中的全部可读文字，并完整翻译为简体中文。只输出中文译文，"
    "不要抄录英文原文，不要解释。"
)
IMAGE_RETRY_TEXT = (
    "上一次回答仍以英文为主，没有完成翻译。请重新识别同一张图片，"
    "把全部内容完整翻译为简体中文；只输出中文译文，禁止输出英文原文。"
)


def image_to_data_url(image: QImage, max_side: int = 1568) -> str:
    """QImage 转 base64 data URL（超大图先等比缩小，控制请求体积）。"""
    img = image
    if max(img.width(), img.height()) > max_side:
        from PySide6.QtCore import Qt
        img = img.scaled(max_side, max_side,
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.ReadWrite)
    img.save(buf, "PNG")
    b64 = base64.b64encode(bytes(buf.data())).decode("ascii")
    return f"data:image/png;base64,{b64}"


def build_chat_payload(system_prompt: str, text: str,
                       image: QImage | None, image_retry: bool = False) -> dict:
    """构造 OpenAI 兼容 /v1/chat/completions 请求体。"""
    effective_prompt = system_prompt
    if image is not None:
        effective_prompt = f"{system_prompt.strip()}\n\n{IMAGE_CHINESE_REQUIREMENT}"
        content: list | str = [
            {"type": "image_url",
             "image_url": {"url": image_to_data_url(image)}},
        ]
        if text.strip():
            content.append({"type": "text", "text": text.strip()})
        content.append({
            "type": "text",
            "text": IMAGE_RETRY_TEXT if image_retry else IMAGE_REQUEST_TEXT,
        })
    else:
        content = text.strip()
    return {
        "messages": [
            {"role": "system", "content": effective_prompt},
            {"role": "user", "content": content},
        ],
        "temperature": 0.0,
        "stream": False,
    }


def extract_reply(data: dict) -> str:
    """从响应中取出正文文本。"""
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("响应中没有 choices")
    msg = choices[0].get("message") or {}
    content = msg.get("content", "")
    if isinstance(content, list):  # 某些实现返回分段内容
        content = "".join(str(p.get("text", "")) for p in content
                          if isinstance(p, dict))
    return str(content).strip()


def looks_like_untranslated_english(text: str) -> bool:
    """判断图片回复是否仍以英文为主，用于触发一次纠正重试。"""
    without_links = re.sub(r"(?:https?://|www\.)\S+|\S+@\S+", "", text)
    latin_count = len(re.findall(r"[A-Za-z]", without_links))
    cjk_count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", without_links))
    return latin_count >= 12 and latin_count >= max(12, cjk_count * 5)


class ChatWorker(QThread):
    """后台请求线程：成功发 result，失败发 error。"""

    result = Signal(str)
    error = Signal(str)

    def __init__(self, base_url: str, system_prompt: str, text: str,
                 image: QImage | None = None, timeout: float = 300.0,
                 parent=None) -> None:
        super().__init__(parent)
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._prompt = system_prompt
        self._text = text
        self._image = image
        self._timeout = timeout

    def run(self) -> None:
        try:
            reply = self._request(image_retry=False)
            if (self._image is not None
                    and looks_like_untranslated_english(reply)):
                reply = self._request(image_retry=True)
                if looks_like_untranslated_english(reply):
                    self.error.emit(
                        "图片翻译仍返回英文，已自动重试一次；"
                        "请检查视觉模型或提示词配置")
                    return
            if reply:
                self.result.emit(reply)
            else:
                self.error.emit("模型返回了空内容")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:300]
            except OSError:
                pass
            self.error.emit(f"HTTP {e.code}: {detail or e.reason}")
        except Exception as e:  # 网络/解析错误统一上报
            self.error.emit(str(e))

    def _request(self, image_retry: bool) -> str:
        payload = json.dumps(build_chat_payload(
            self._prompt, self._text, self._image, image_retry=image_retry,
        )).encode("utf-8")
        req = urllib.request.Request(
            self._url, data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return extract_reply(data)
