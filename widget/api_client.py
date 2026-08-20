"""挂件用的 llama-server Chat API 客户端（后台线程，不阻塞 UI）。"""
from __future__ import annotations

import base64
import json
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
                       image: QImage | None) -> dict:
    """构造 OpenAI 兼容 /v1/chat/completions 请求体。"""
    if image is not None:
        content: list | str = [
            {"type": "image_url",
             "image_url": {"url": image_to_data_url(image)}},
        ]
        if text.strip():
            content.append({"type": "text", "text": text.strip()})
        else:
            content.append({"type": "text",
                            "text": "Translate the text in this image."})
    else:
        content = text.strip()
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
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
            payload = json.dumps(
                build_chat_payload(self._prompt, self._text, self._image)
            ).encode("utf-8")
            req = urllib.request.Request(
                self._url, data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            reply = extract_reply(data)
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
