"""
硬件 Datasheet PDF 下载模块。

从已知 URL 列表下载官方硬件文档到本地 data/pdfs/ 目录。
支持断点续传（跳过已下载文件）、超时控制、User-Agent 伪装。
"""

import os
import time
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import logging
import httpx

# ─── 默认数据目录 ───
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
PDF_DIR = DATA_DIR / "pdfs"
MARKDOWN_DIR = DATA_DIR / "markdown"
CHROMA_DIR = DATA_DIR / "chroma"


@dataclass
class DocumentSource:
    """一篇待下载的文档源信息。"""

    doc_id: str  # 唯一标识，用作文件名
    title: str  # 中文标题
    category: str  # dev-boards | sensors | protocols | peripherals
    url: str  # PDF 下载链接
    tags: list[str] = field(default_factory=list)
    last_updated: str = ""


# ─── 首批 10 篇文档源 ───
# 这些 URL 来自官方/主流硬件厂商，确保文档来源可靠
FIRST_BATCH: list[DocumentSource] = [
    DocumentSource(
        doc_id="esp32-wroom-32",
        title="ESP32-WROOM-32  Datasheet",
        category="dev-boards",
        url="https://www.espressif.com/sites/default/files/documentation/esp32-wroom-32_datasheet_en.pdf",
        tags=["ESP32", "WiFi", "Bluetooth", "MCU"],
        last_updated="2024-08",
    ),
    DocumentSource(
        doc_id="esp32-c3",
        title="ESP32-C3 Datasheet",
        category="dev-boards",
        url="https://www.espressif.com/sites/default/files/documentation/esp32-c3_datasheet_en.pdf",
        tags=["ESP32-C3", "RISC-V", "WiFi", "Bluetooth LE"],
        last_updated="2024-07",
    ),
    DocumentSource(
        doc_id="stm32f103c8t6",
        title="STM32F103C8T6 中容量增强型 Datasheet",
        category="dev-boards",
        url="https://www.mouser.com/datasheet/2/389/stm32f103c8-1851068.pdf",
        tags=["STM32", "ARM Cortex-M3", "MCU"],
        last_updated="2024-05",
    ),
    DocumentSource(
        doc_id="arduino-uno-r3",
        title="Arduino Uno R3 (ATmega328P) Datasheet",
        category="dev-boards",
        url="https://ww1.microchip.com/downloads/en/DeviceDoc/ATmega48A-PA-88A-PA-168A-PA-328-P-DS-DS40002061B.pdf",
        tags=["Arduino", "ATmega328P", "AVR", "MCU"],
        last_updated="2024-06",
    ),
    DocumentSource(
        doc_id="arduino-nano",
        title="Arduino Nano (ATmega328P) Datasheet",
        category="dev-boards",
        url="https://ww1.microchip.com/downloads/en/DeviceDoc/ATmega48A-PA-88A-PA-168A-PA-328-P-DS-DS40002061B.pdf",
        tags=["Arduino", "ATmega328P", "AVR", "Nano"],
        last_updated="2024-06",
    ),
    DocumentSource(
        doc_id="dht22",
        title="DHT22 温湿度传感器 Datasheet",
        category="sensors",
        url="https://www.sparkfun.com/datasheets/Sensors/Temperature/DHT22.pdf",
        tags=["DHT22", "temperature", "humidity", "sensor"],
        last_updated="2023-12",
    ),
    DocumentSource(
        doc_id="hc-sr04",
        title="HC-SR04 超声波测距模块 Datasheet",
        category="sensors",
        url="https://cdn.sparkfun.com/datasheets/Sensors/Proximity/HCSR04.pdf",
        tags=["HC-SR04", "ultrasonic", "distance", "sensor"],
        last_updated="2023-10",
    ),
    DocumentSource(
        doc_id="mpu6050",
        title="MPU6050 六轴加速度计/陀螺仪 Datasheet",
        category="sensors",
        url="https://invensense.tdk.com/wp-content/uploads/2015/02/MPU-6000-Datasheet1.pdf",
        tags=["MPU6050", "accelerometer", "gyroscope", "IMU"],
        last_updated="2023-08",
    ),
    DocumentSource(
        doc_id="bmp280",
        title="BMP280 气压传感器 Datasheet",
        category="sensors",
        url="https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmp280-ds001.pdf",
        tags=["BMP280", "pressure", "temperature", "sensor"],
        last_updated="2024-01",
    ),
    DocumentSource(
        doc_id="lcd1602-i2c",
        title="I2C LCD1602 字符型液晶模块 Datasheet",
        category="peripherals",
        url="https://www.waveshare.com/datasheet/LCD1602.pdf",
        tags=["LCD1602", "I2C", "display", "peripheral"],
        last_updated="2023-11",
    ),
]


logger = logging.getLogger(__name__)


class DocumentLoader:
    """PDF 下载器，支持断点续传、重试、超时。"""

    def __init__(self, pdf_dir: Optional[Path] = None, timeout: float = 60.0):
        self.pdf_dir = pdf_dir or PDF_DIR
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                },
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
        self.logger = logging.getLogger(__name__)

    def _pdf_path(self, doc_id: str) -> Path:
        return self.pdf_dir / f"{doc_id}.pdf"

    def _checksum_path(self, doc_id: str) -> Path:
        return self.pdf_dir / f"{doc_id}.sha256"

    def is_downloaded(self, doc_id: str) -> bool:
        """检查 PDF 是否已下载。"""
        return self._pdf_path(doc_id).exists()

    def verify_checksum(self, doc_id: str) -> bool:
        """验证文件校验和。"""
        sha_path = self._checksum_path(doc_id)
        pdf_path = self._pdf_path(doc_id)
        if not sha_path.exists() or not pdf_path.exists():
            return False
        expected = sha_path.read_text().strip()
        actual = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        return expected == actual

    async def download_one(self, source: DocumentSource) -> Path:
        """下载单篇 PDF，返回本地路径。"""
        pdf_path = self._pdf_path(source.doc_id)

        # 断点续传：已下载且校验通过则跳过
        if pdf_path.exists():
            if self.verify_checksum(source.doc_id):
                self.logger.info("已下载: %s", source.doc_id)
                return pdf_path
            else:
                self.logger.warning("校验和不匹配，重新下载: %s", source.doc_id)

        self.logger.info("下载中: %s (%s)", source.title, source.url)
        try:
            response = await self.client.get(source.url)
            response.raise_for_status()
            pdf_path.write_bytes(response.content)
            # 保存 SHA256 校验和
            sha256 = hashlib.sha256(response.content).hexdigest()
            self._checksum_path(source.doc_id).write_text(sha256)
            self.logger.info("下载完成: %s (%d bytes)", source.doc_id, len(response.content))
            return pdf_path
        except httpx.HTTPStatusError as e:
            self.logger.error("HTTP 错误 %d: %s", e.response.status_code, source.url)
            raise
        except httpx.TimeoutException:
            self.logger.error("超时: %s", source.url)
            raise
        except Exception as e:
            self.logger.error("下载失败: %s - %s", source.doc_id, e)
            raise

    async def download_batch(
        self, sources: list[DocumentSource], max_concurrent: int = 3
    ) -> dict[str, Path]:
        """批量下载，控制并发数。"""
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)

        async def _limited_download(source: DocumentSource) -> tuple[str, Path]:
            async with semaphore:
                path = await self.download_one(source)
                return source.doc_id, path

        tasks = [_limited_download(s) for s in sources]
        results = {}
        for coro in asyncio.as_completed(tasks):
            doc_id, path = await coro
            results[doc_id] = path
        return results
