"""GPIO 诊断常量 — ESP32/STM32 strapping 引脚表与引脚名解析。"""


# ESP32 系列 strapping 引脚（启动时必须为特定电平，误用会导致无法启动）
# 数据来源：各芯片官方 datasheet
STRAPPING_PINS = {
    # ESP32 经典款：GPIO0(BOOT)、GPIO2、GPIO4、GPIO5、GPIO12(VDD_SDIO)、GPIO15
    "esp32": {0, 2, 4, 5, 12, 15},
    # ESP32-S3：GPIO0(BOOT)、GPIO3、GPIO45、GPIO46
    "esp32-s3": {0, 3, 45, 46},
    # ESP32-C3：GPIO2、GPIO8(BOOT)、GPIO9(BOOT)
    "esp32-c3": {2, 8, 9},
    # ESP32-C6：GPIO9(BOOT)、GPIO12、GPIO13
    "esp32-c6": {9, 12, 13},
    # ESP32-S2：GPIO0(BOOT)、GPIO45、GPIO46
    "esp32-s2": {0, 45, 46},
    # ESP32-H2：GPIO9(BOOT)、GPIO12、GPIO13
    "esp32-h2": {9, 12, 13},
    # STM32 系列：BOOT0/BOOT1 引脚（通常是固定引脚，不在 GPIO 上，但 NRST、PA13/PA14/SWO 调试引脚需警告）
    # STM32 的 BOOT0 是专用引脚不是 GPIO，这里列出需要警告的调试引脚
    "stm32": {13, 14},  # PA13(SWDIO)、PA14(SWCLK) — 调试引脚，误用会断调试器
    "stm32f4": {13, 14},
    "stm32f7": {13, 14},
    "stm32h7": {13, 14},
}


def resolve_gpio(pin_name: str, defines: dict[str, int]) -> int | None:
    """解析引脚标识符为 GPIO 编号。

    支持：
    - GPIO + 数字（如 "GPIO5" → 5）
    - 纯数字（如 "5" → 5）
    - 宏定义名（如 "LED_PIN" → defines["LED_PIN"]）
    """
    name = pin_name.strip()
    if name.upper().startswith("GPIO"):
        try:
            return int(name[4:])
        except ValueError:
            return None
    if name.isdigit():
        return int(name)
    if name in defines:
        return defines[name]
    return None
