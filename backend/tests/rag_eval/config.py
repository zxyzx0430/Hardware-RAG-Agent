"""
RAG Evaluation Config — 测试配置文件

所有测试参数、问题定义、策略变体、评分权重都在这里配置。
修改此文件即可调整测试行为，无需改动 run_eval.py。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ═══════════════════════════════════════════════════════════════════
# 路径配置
# ═══════════════════════════════════════════════════════════════════
TEST_DOCS_DIR = Path(r"E:\Desktop\agent\data\test_docs")
OUTPUT_DIR = Path(r"E:\Desktop\agent\data\test_results")

# 测试文档列表（按顺序上传）
# 06 是故意结构混乱的文档（无标题、代码块跨页、表格无格式），
# 专门测试 AgentChunker 的语义分块能力 vs HybridChunker 的字符切分。
TEST_DOC_FILES = [
    "01-stm32-gpio.md",
    "02-esp32-wifi.md",
    "03-i2c-protocol.md",
    "04-cortexm-interrupt.md",
    "05-uart-serial.md",
    "06-chaotic-embedded-notes.md",
]


# ═══════════════════════════════════════════════════════════════════
# API 配置
# ═══════════════════════════════════════════════════════════════════
API_BASE_URL = "http://127.0.0.1:58080/api"

# LLM 配置（通过命令行参数覆盖，这里设默认值）
DEFAULT_LLM_MODEL = "oc/deepseek-v4-flash"
DEFAULT_LLM_BASE_URL = "https://9router.zxyzx.bbroot.com/v1"

# Embedding 配置（用于创建知识库）
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_EMBEDDING_BASE_URL = "https://9router.zxyzx.bbroot.com/v1"

# 检索参数
TOP_K = 5
RELEVANCE_THRESHOLD = 0.0  # 0.0 = 不过滤，测试全部召回


# ═══════════════════════════════════════════════════════════════════
# 评分权重（总分 100）
# ═══════════════════════════════════════════════════════════════════
SCORE_WEIGHTS = {
    "recall": 30,        # 召回命中率：引用来源是否命中目标文档
    "answer_coverage": 25,  # 回答关键词覆盖：回答中包含期望知识点的比例
    "chunk_completeness": 25,  # chunk 语义完整性：期望关键词组是否在同一 chunk 共现
    "chunk_boundary": 10,  # chunk 边界质量：代码块/表格/列表是否完整
    "cross_section": 10,   # 跨章节关联：关联内容是否在相邻 chunk
}


# ═══════════════════════════════════════════════════════════════════
# 问题定义
# ═══════════════════════════════════════════════════════════════════
@dataclass
class TestQuestion:
    """单个测试问题的定义。"""
    id: str                    # Q1, Q2, ...
    question: str              # 完整问题文本
    target_doc: str            # 期望命中的目标文档文件名
    difficulty: str            # 简单/中等/难

    # 回答中应包含的知识点关键词（用于 answer_coverage 评分）
    # 每个 tuple 是一组同义词，命中任意一个即得分
    expected_keywords: list[list[str]] = field(default_factory=list)

    # chunk 共现关键词组：这些关键词必须同时出现在同一个 chunk 中
    # 用于 chunk_completeness 评分。每组全部命中才算该组通过
    chunk_cooccur_groups: list[list[str]] = field(default_factory=list)

    # 跨章节关联关键词：这些关键词应在相邻 chunk 中出现
    # 用于 cross_section 评分
    cross_section_keywords: list[str] = field(default_factory=list)


QUESTIONS: list[TestQuestion] = [
    TestQuestion(
        id="Q1",
        question="STM32 的 GPIO 引脚可以配置为哪四种工作模式？每种模式对应的寄存器值是什么？",
        target_doc="01-stm32-gpio.md",
        difficulty="简单",
        expected_keywords=[
            ["输入模式", "input"],
            ["输出模式", "output"],
            ["复用功能", "alternate", "AF"],
            ["模拟模式", "analog"],
            ["MODER"],
            ["00", "输入"],
            ["01", "输出"],
            ["10", "复用"],
            ["11", "模拟"],
        ],
        chunk_cooccur_groups=[
            ["输入模式", "输出模式", "复用功能", "模拟模式"],
        ],
        cross_section_keywords=["输入模式", "MODER"],
    ),
    TestQuestion(
        id="Q2",
        question="STM32 GPIO 的输出速度有哪几个等级？在什么场景下应该选择不同的速度？",
        target_doc="01-stm32-gpio.md",
        difficulty="简单",
        expected_keywords=[
            ["OSPEEDR"],
            ["低速", "low speed"],
            ["中速", "medium"],
            ["高速", "high speed"],
            ["EMI"],
            ["信号完整性"],
        ],
        chunk_cooccur_groups=[
            ["OSPEEDR", "低速", "高速"],
        ],
    ),
    TestQuestion(
        id="Q3",
        question="ESP32 的 Station 模式连接 Wi-Fi 超时怎么办？重连策略应该怎么设计？",
        target_doc="02-esp32-wifi.md",
        difficulty="中等",
        expected_keywords=[
            ["Station", "STA"],
            ["超时", "timeout"],
            ["重连", "reconnect"],
            ["指数退避", "backoff", "退避"],
            ["WIFI_EVENT_STA_DISCONNECTED", "STA_DISCONNECTED"],
            ["重试次数"],
        ],
        chunk_cooccur_groups=[
            ["指数退避", "重连", "重试"],
        ],
    ),
    TestQuestion(
        id="Q4",
        question="ESP32 的三种 Wi-Fi 省电模式有什么区别？如果应用需要低延迟 TCP 通信应该用哪种？",
        target_doc="02-esp32-wifi.md",
        difficulty="中等",
        expected_keywords=[
            ["省电模式", "power save", "PS"],
            ["WIFI_PS_NONE", "PS_NONE"],
            ["WIFI_PS_MIN_MODEM", "MIN_MODEM"],
            ["WIFI_PS_MAX_MODEM", "MAX_MODEM"],
            ["延迟", "latency"],
            ["TCP"],
        ],
        chunk_cooccur_groups=[
            ["WIFI_PS_NONE", "WIFI_PS_MIN_MODEM"],
        ],
        cross_section_keywords=["WIFI_PS_NONE", "延迟"],
    ),
    TestQuestion(
        id="Q5",
        question="I2C 多主机通信时如何仲裁？仲裁失败的从机应该做什么？",
        target_doc="03-i2c-protocol.md",
        difficulty="中等",
        expected_keywords=[
            ["仲裁", "arbitration"],
            ["多主机", "multi-master", "multi_master"],
            ["SDA"],
            ["仲裁丢失", "arbitration lost"],
            ["释放总线", "release"],
        ],
        chunk_cooccur_groups=[
            ["仲裁", "SDA", "释放"],
        ],
        cross_section_keywords=["仲裁", "释放"],
    ),
    TestQuestion(
        id="Q6",
        question="从机时钟拉伸导致 I2C 通信阻塞时，主机应该怎么检测和处理？超时时间一般设为多少？",
        target_doc="03-i2c-protocol.md",
        difficulty="难",
        expected_keywords=[
            ["时钟拉伸", "clock stretching", "Clock Stretching"],
            ["SCL"],
            ["超时", "timeout"],
            ["10ms", "10 ms"],
            ["HAL_I2C_Master_Transmit"],
        ],
        chunk_cooccur_groups=[
            ["时钟拉伸", "超时", "100"],
        ],
        cross_section_keywords=["时钟拉伸", "超时"],
    ),
    TestQuestion(
        id="Q7",
        question="Cortex-M3 的 NVIC 优先级分组如何配置？如果 USART1 抢占优先级为 1、TIM2 抢占优先级为 2，当中断同时到达时谁先执行？",
        target_doc="04-cortexm-interrupt.md",
        difficulty="难",
        expected_keywords=[
            ["NVIC"],
            ["优先级分组", "PRIGROUP", "priority group"],
            ["AIRCR"],
            ["抢占优先级", "preemption"],
            ["子优先级", "sub priority"],
            ["嵌套", "nest"],
            ["USART1"],
            ["TIM2"],
        ],
        chunk_cooccur_groups=[
            ["PRIGROUP", "抢占优先级", "子优先级"],
        ],
    ),
    TestQuestion(
        id="Q8",
        question="Cortex-M4 的中断延迟是多少个时钟周期？尾链（Tail-Chaining）和迟来（Late-Arrival）技术分别如何降低延迟？",
        target_doc="04-cortexm-interrupt.md",
        difficulty="难",
        expected_keywords=[
            ["中断延迟", "interrupt latency", "latency"],
            ["12", "12个", "12 个", "12周期", "12 周期"],
            ["尾链", "Tail-Chaining", "Tail Chaining", "tail"],
            ["迟来", "Late-Arrival", "Late Arrival"],
        ],
        chunk_cooccur_groups=[
            ["12", "尾链", "迟来"],
        ],
    ),
    TestQuestion(
        id="Q9",
        question="STM32 UART 使用 DMA 传输时需要注意哪些问题？接收缓冲区的循环模式怎么防止数据覆盖？",
        target_doc="05-uart-serial.md",
        difficulty="中等",
        expected_keywords=[
            ["DMA"],
            ["循环模式", "circular", "Circular"],
            ["半传输", "half transfer", "Half Transfer"],
            ["缓冲区", "buffer"],
            ["数据覆盖", "overwrite"],
            ["中断", "interrupt"],
        ],
        chunk_cooccur_groups=[
            ["DMA", "循环模式", "半传输"],
        ],
    ),
    TestQuestion(
        id="Q10",
        question="UART 通信中出现随机错误位，可能是什么原因？怎么排查和解决？",
        target_doc="05-uart-serial.md",
        difficulty="简单",
        expected_keywords=[
            ["噪声", "noise"],
            ["随机错误", "random error"],
            ["屏蔽线", "shielded"],
            ["串联电阻", "series resistor", "电阻"],
            ["RS-232"],
            ["TTL"],
            ["波特率", "baud rate", "baud"],
        ],
        chunk_cooccur_groups=[
            ["噪声", "屏蔽", "波特率"],
        ],
        cross_section_keywords=["噪声", "屏蔽"],
    ),
    # ── 难度提升：跨文档综合、调试场景、混沌文档检索 ──
    TestQuestion(
        id="Q11",
        question="STM32 和 ESP32 都支持硬件 SPI，它们在 SPI 时钟极性/相位配置方式上有什么区别？如果要用 STM32 做 SPI 主机连接 ESP32 做 SPI 从机，需要注意什么？",
        target_doc="06-chaotic-embedded-notes.md",
        difficulty="难",
        expected_keywords=[
            ["CPOL", "时钟极性"],
            ["CPHA", "时钟相位"],
            ["STM32", "ESP32"],
            ["主机", "master"],
            ["从机", "slave"],
            ["同步", "sync"],
        ],
        chunk_cooccur_groups=[
            ["CPOL", "CPHA"],
        ],
        cross_section_keywords=["STM32", "ESP32"],
    ),
    TestQuestion(
        id="Q12",
        question="在一个嵌入式系统中，DMA 传输完成中断和 UART 接收中断同时发生，如何设计中断优先级确保不丢数据？给出 NVIC 分组方案和具体的优先级数值。",
        target_doc="06-chaotic-embedded-notes.md",
        difficulty="难",
        expected_keywords=[
            ["DMA", "UART"],
            ["NVIC", "优先级"],
            ["抢占", "preemption"],
            ["中断优先级", "priority"],
            ["缓冲区", "buffer"],
            ["数据丢失", "data loss", "丢数据"],
        ],
        chunk_cooccur_groups=[
            ["DMA", "UART", "优先级"],
        ],
        cross_section_keywords=["DMA", "NVIC"],
    ),
    TestQuestion(
        id="Q13",
        question="I2C 总线上挂了 MPU6050（地址 0x68）和 SSD1306 OLED（地址 0x3C），通信时偶尔出现数据错乱。排查步骤是什么？可能的原因有哪些？",
        target_doc="06-chaotic-embedded-notes.md",
        difficulty="难",
        expected_keywords=[
            ["MPU6050", "0x68"],
            ["SSD1306", "0x3C"],
            ["地址冲突", "address conflict"],
            ["上拉电阻", "pull-up"],
            ["总线电容", "capacitance"],
            ["调试", "debug", "排查"],
        ],
        chunk_cooccur_groups=[
            ["MPU6050", "SSD1306", "上拉"],
        ],
        cross_section_keywords=["I2C", "上拉电阻"],
    ),
    TestQuestion(
        id="Q14",
        question="STM32 的 Flash 读写保护机制是什么？如果在 OTA 升级过程中突然断电，怎么设计 bootloader 确保系统可以恢复？双 Bank 切换的原理是什么？",
        target_doc="06-chaotic-embedded-notes.md",
        difficulty="难",
        expected_keywords=[
            ["Flash", "闪存"],
            ["读写保护", "read protect", "write protect", "RDP"],
            ["bootloader", "引导加载"],
            ["OTA", "升级"],
            ["双Bank", "dual bank", "bank"],
            ["断电恢复", "power fail", "恢复"],
        ],
        chunk_cooccur_groups=[
            ["bootloader", "OTA", "Flash"],
        ],
        cross_section_keywords=["bootloader", "双Bank"],
    ),
    TestQuestion(
        id="Q15",
        question="给定一段 STM32 的 GPIO 初始化代码，其中 PA5 配置为推挽输出但无法驱动 LED 亮起。列出所有可能的原因和对应的检测方法，按可能性排序。",
        target_doc="06-chaotic-embedded-notes.md",
        difficulty="难",
        expected_keywords=[
            ["GPIO", "PA5"],
            ["推挽", "push-pull"],
            ["RCC", "时钟", "clock"],
            ["MODER", "模式"],
            ["ODR", "输出数据"],
            ["LED", "灯"],
            ["硬件", "hardware"],
            ["万用表", "multimeter"],
        ],
        chunk_cooccur_groups=[
            ["RCC", "GPIO", "MODER"],
        ],
        cross_section_keywords=["GPIO", "RCC"],
    ),
]


# ═══════════════════════════════════════════════════════════════════
# 策略变体定义（用于对比不同 chunk 策略）
# ═══════════════════════════════════════════════════════════════════
@dataclass
class StrategyVariant:
    """一个 chunk 策略变体。"""
    name: str               # 策略名称（用于报告）
    chunk_method: str       # hybrid / agent
    description: str        # 策略描述
    # 可选：指定 embedding 模型（None 用默认）
    embedding_model: Optional[str] = None
    # 可选：指定 agent chunker 模型（仅 agent 策略）
    agent_chunker_model: Optional[str] = None
    # 可选：覆盖 small_chunk_size（仅 hybrid 策略，通过 upload API 传入）
    small_chunk_size: Optional[int] = None


# 默认测试的策略变体（5种对比）
# hybrid 梯度: 500 → 800 → 1200 → 2000 (观察 chunk 大小对语义完整性的影响)
# agent: 用 deepseek 模型做 LLM 语义分块 (代理不支持 gpt-4o-mini)
STRATEGIES: list[StrategyVariant] = [
    StrategyVariant(
        name="hybrid-500",
        chunk_method="hybrid",
        description="HybridChunker, small_chunk_size=500 (old default, prone to truncation)",
        small_chunk_size=500,
    ),
    StrategyVariant(
        name="hybrid-800",
        chunk_method="hybrid",
        description="HybridChunker, small_chunk_size=800 (current default)",
        small_chunk_size=800,
    ),
    StrategyVariant(
        name="hybrid-1200",
        chunk_method="hybrid",
        description="HybridChunker, small_chunk_size=1200 (larger chunks, better completeness)",
        small_chunk_size=1200,
    ),
    StrategyVariant(
        name="hybrid-2000",
        chunk_method="hybrid",
        description="HybridChunker, small_chunk_size=2000 (very large chunks, max completeness)",
        small_chunk_size=2000,
    ),
    StrategyVariant(
        name="agent-deepseek",
        chunk_method="agent",
        description="AgentChunker with oc/deepseek-v4-flash (LLM-based semantic chunking via proxy)",
        agent_chunker_model="oc/deepseek-v4-flash",
    ),
]
