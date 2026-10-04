from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import re

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DATASET_PATH = Path(__file__).with_name("golden_dataset.yaml")
ST_F4_GPIO_URL = (
    "https://www.st.com/resource/en/reference_manual/"
    "dm00031020-stm32f405-415-stm32f407-417-stm32f427-437-and-stm32f429-439-"
    "advanced-arm-based-32-bit-mcus-stmicroelectronics.pdf"
)
ST_F1_RM_URL = (
    "https://www.st.com/resource/en/reference_manual/"
    "rm0008-stm32f101xx-stm32f102xx-stm32f103xx-stm32f105xx-and-stm32f107xx-"
    "advanced-armbased-32-bit-mcus-stmicroelectronics.pdf"
)
ST_F4_HAL_URL = (
    "https://raw.githubusercontent.com/STMicroelectronics/stm32f4xx-hal-driver/"
    "master/Inc/stm32f4xx_hal_cortex.h"
)
ST_RM0090_URL = (
    "https://www.st.com/resource/en/reference_manual/"
    "rm0090-stm32f407-advanced-armbased-32bit-mcus-stmicroelectronics.pdf"
)
ST_RM0433_URL = (
    "https://www.st.com/resource/en/reference_manual/"
    "rm0433-stm32h742-stm32h743-753-and-stm32h750-value-line-advanced-"
    "armbased-32-bit-mcus-stmicroelectronics.pdf"
)
ESP32S3_GUIDE_URL = (
    "https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/"
    "latest/esp32s3/schematic-checklist.html"
)
ESP32S3_DATASHEET_URL = (
    "https://www.espressif.com/sites/default/files/documentation/"
    "esp32-s3_datasheet_en.pdf"
)


def _golden_sample(sample_id: str) -> dict:
    dataset = yaml.safe_load(DATASET_PATH.read_text(encoding="utf-8"))
    return next(sample for sample in dataset["samples"] if sample["id"] == sample_id)


def _fixture(sample: dict) -> str:
    source_path = REPOSITORY_ROOT / "data" / "test_docs" / sample["target_doc"]
    return source_path.read_text(encoding="utf-8")


def _markdown_table_after(document: str, marker: str) -> tuple[list[str], list[list[str]]]:
    lines = document.splitlines()
    marker_index = next(index for index, line in enumerate(lines) if marker in line)
    table_lines = []
    started = False
    for line in lines[marker_index + 1 :]:
        if line.lstrip().startswith("|"):
            table_lines.append(line)
            started = True
        elif started:
            break

    assert len(table_lines) >= 2, f"No Markdown table found after {marker!r}"
    parsed = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in table_lines
    ]
    headers = parsed[0]
    rows = [
        row
        for row in parsed[1:]
        if not all(set(cell) <= {"-", ":", " "} for cell in row)
    ]
    return headers, rows


def test_stm32_gpio_modes_scope_moder_encodings_to_f4() -> None:
    sample = _golden_sample("G001")
    source = _fixture(sample)

    assert "STM32F4" in sample["question"]
    assert "MODER" in sample["question"]
    expected_modes = {
        "00": "输入模式",
        "01": "输出模式",
        "10": "复用功能",
        "11": "模拟模式",
    }
    answer_labels = {
        "输入模式": "输入模式",
        "输出模式": "输出模式",
        "复用功能模式": "复用功能",
        "模拟模式": "模拟模式",
    }
    answer_modes = {
        code: answer_labels[label]
        for label, code in re.findall(
            r"^\s*\d+\.\s*(输入模式|输出模式|复用功能模式|模拟模式)（MODER=(00|01|10|11)）",
            sample["standard_answer"],
            flags=re.MULTILINE,
        )
    }
    assert answer_modes == expected_modes

    mode_section = source.split("## 2. GPIO 工作模式详解", 1)[1].split("### 2.1", 1)[0]
    source_modes = {
        code: mode
        for code, mode in re.findall(
            r"^\|\s*(00|01|10|11)\s*\|\s*(输入模式|输出模式|复用功能|模拟模式)\s*\|",
            mode_section,
            flags=re.MULTILINE,
        )
    }
    assert source_modes == expected_modes
    assert "STM32F4" in mode_section
    assert "STM32F1" in mode_section and "CRL/CRH" in mode_section
    assert ST_F4_GPIO_URL in mode_section and ST_F1_RM_URL in mode_section
    assert all(url in sample["notes"] for url in (ST_F4_GPIO_URL, ST_F1_RM_URL))


def test_stm32f4_hal_priority_group_values_match_four_implemented_bits() -> None:
    sample = _golden_sample("G016")
    source = _fixture(sample)
    assert "实现 4 位优先级" in sample["question"]
    assert "STM32F4" in sample["question"]
    assert "Cortex-M3" not in sample["question"]
    assert "NVIC_PRIORITYGROUP_4" in sample["standard_answer"]
    group4_answer = next(
        line for line in sample["standard_answer"].splitlines() if "NVIC_PRIORITYGROUP_4" in line
    )
    group0_answer = next(
        line for line in sample["standard_answer"].splitlines() if "NVIC_PRIORITYGROUP_0" in line
    )
    assert "PRIGROUP=3" in group4_answer and "PRIGROUP=7" not in group4_answer
    assert "PRIGROUP=7" in group0_answer

    expected = {
        0: (7, 0, 4),
        1: (6, 1, 3),
        2: (5, 2, 2),
        3: (4, 3, 1),
        4: (3, 4, 0),
    }
    for marker in ("对 STM32F4 HAL 的 4 位优先级实现", "优先级分组（STM32F4，4 位实现）"):
        _, rows = _markdown_table_after(source, marker)
        actual = {}
        for row in rows:
            match = re.match(
                r"(?:NVIC_PRIORITYGROUP_|GROUP_)(\d)$", row[0].strip()
            )
            if match:
                actual[int(match.group(1))] = tuple(map(int, row[1:4]))
        assert actual == expected

    # AIRCR.PRIGROUP occupies bits [10:8]; the vendor HAL constants encode 3 and 7.
    assert ((expected[4][0] << 8) & 0x700) == 0x300
    assert ((expected[0][0] << 8) & 0x700) == 0x700
    assert ST_F4_HAL_URL in source
    assert ST_F4_HAL_URL in sample["notes"]


def test_esp32s3_strapping_membership_boot_levels_and_vdd_spi_source() -> None:
    sample = _golden_sample("G026")
    source = _fixture(sample)
    section = source.split("### 2.1 ESP32-S3 引脚与 Strapping", 1)[1].split("### 2.2", 1)[0]
    expected_straps = {0, 3, 45, 46}

    membership_line = next(
        line for line in section.splitlines() if "Strapping 引脚：" in line
    )
    source_membership = {int(number) for number in re.findall(r"GPIO(\d+)", membership_line)}
    assert source_membership == expected_straps

    _, boot_rows = _markdown_table_after(section, "启动模式")
    assert boot_rows == [
        ["SPI 启动（默认）", "1", "任意"],
        ["联合下载启动", "0", "0"],
    ]
    assert "GPIO0" in sample["standard_answer"]
    assert "GPIO46" in sample["standard_answer"]
    assert "GPIO0=1" in sample["standard_answer"]
    assert "GPIO46 为任意电平" in sample["standard_answer"]
    assert "GPIO0=0" in sample["standard_answer"]
    assert "GPIO46=0" in sample["standard_answer"]
    assert "GPIO45" in sample["standard_answer"] and "eFuse" in sample["standard_answer"]
    assert "GPIO46 不用于选择 VDD_SPI 电压" in sample["standard_answer"]
    assert not re.search(r"GPIO46.{0,30}控制.{0,20}VDD_SPI", sample["standard_answer"])
    assert all(url in section for url in (ESP32S3_GUIDE_URL, ESP32S3_DATASHEET_URL))
    assert all(url in sample["notes"] for url in (ESP32S3_GUIDE_URL, ESP32S3_DATASHEET_URL))


def _assert_uart_table(
    document: str,
    marker: str,
    *,
    fixed_clock_hz: int | None = None,
    expected_rows: int | None = None,
) -> int:
    headers, rows = _markdown_table_after(document, marker)
    baud_column = next(
        index for index, header in enumerate(headers) if header in ("目标波特率", "波特率")
    )
    brr_column = next(index for index, header in enumerate(headers) if "BRR" in header)
    actual_column = next(
        (index for index, header in enumerate(headers) if header == "实际波特率"), None
    )
    error_column = next(index for index, header in enumerate(headers) if header == "误差")
    clock_column = next(
        (index for index, header in enumerate(headers) if header == "时钟频率"), None
    )

    assert expected_rows is None or len(rows) == expected_rows
    for row in rows:
        clock_hz = fixed_clock_hz
        if clock_column is not None:
            clock_mhz = re.search(r"([\d.]+)\s*MHz", row[clock_column])
            assert clock_mhz
            clock_hz = round(float(clock_mhz.group(1)) * 1_000_000)
        assert clock_hz is not None

        target_baud = int(row[baud_column])
        # For OVER8=0, BRR is the integer divider. Round to nearest, ties upward.
        expected_brr = (2 * clock_hz + target_baud) // (2 * target_baud)
        brr_match = re.search(r"0x([\da-fA-F]+)", row[brr_column])
        assert brr_match, row
        actual_brr = int(brr_match.group(1), 16)
        assert actual_brr == expected_brr, (marker, row, expected_brr)

        ideal_usartdiv = Fraction(clock_hz, 16 * target_baud)
        divider_column = next(
            (index for index, header in enumerate(headers) if header == "USARTDIV"), None
        )
        if divider_column is not None:
            displayed_divider = float(row[divider_column])
            decimal_places = len(row[divider_column].partition(".")[2])
            precision = 0.5 * 10 ** (-decimal_places) if decimal_places else 0.5
            assert abs(displayed_divider - float(ideal_usartdiv)) <= precision + 1e-9

        nominal_baud = clock_hz / actual_brr
        if actual_column is not None:
            displayed_actual = float(row[actual_column].replace(",", ""))
            actual_precision = len(row[actual_column].partition(".")[2])
            tolerance = 0.5 * 10 ** (-actual_precision) if actual_precision else 0.5
            assert abs(displayed_actual - nominal_baud) <= tolerance + 1e-6, (marker, row)

        error_match = re.search(r"([+-]?[\d.]+)%", row[error_column])
        assert error_match, row
        displayed_error = float(error_match.group(1))
        nominal_error = (nominal_baud / target_baud - 1) * 100
        error_precision = len(error_match.group(1).partition(".")[2])
        error_tolerance = 0.5 * 10 ** (-error_precision) if error_precision else 0.5
        assert abs(displayed_error - nominal_error) <= error_tolerance + 1e-6, (marker, row)

    return len(rows)


def test_stm32_uart_tables_use_quantized_brr_and_nominal_clock_math() -> None:
    sample = _golden_sample("G023")
    source = _fixture(sample)
    assert "625 = 0x0271" in sample["standard_answer"]
    assert "115200 bps" in sample["standard_answer"]
    assert "标称分频误差 = 0%" in sample["standard_answer"]
    assert "不包含晶振/振荡器的物理频率误差" in sample["standard_answer"]
    assert "115200" in sample["reference_chunks"][0]
    assert "115384.6" not in sample["standard_answer"]
    assert ST_F1_RM_URL in sample["notes"] and ST_RM0090_URL in sample["notes"]

    assert "BRR 是量化后的整数分频值" in source
    assert "标称误差不包含晶振/振荡器的物理频率误差" in source
    assert "最近整数（恰好半整数时向上取整）" in source
    assert ST_F1_RM_URL in source and ST_RM0090_URL in source
    assert ST_RM0433_URL in source

    counts = [
        _assert_uart_table(source, "STM32F103（USART1，fCK=72MHz）", fixed_clock_hz=72_000_000),
        _assert_uart_table(source, "STM32F103 USART2（fck=36MHz", fixed_clock_hz=36_000_000),
        _assert_uart_table(source, "STM32F407（USART1，fck=84MHz", fixed_clock_hz=84_000_000),
        _assert_uart_table(source, "A.1 常用波特率 BRR 值", fixed_clock_hz=72_000_000),
        _assert_uart_table(source, "STM32F407 USART1 (APB2)", expected_rows=6),
        _assert_uart_table(source, "STM32H7 USART3 (APB1)", expected_rows=10),
    ]
    assert counts == [15, 12, 13, 8, 6, 10]
    assert "21 MHz | 115200" in source and "115385" in source
    assert "168 MHz |" not in source

    # Independently pin the disputed case and retain another valid 115384.6 row.
    assert Fraction(72_000_000, 16) / Fraction(390625, 10000) == 115_200
    assert 21_000_000 / 182 == 115_384.61538461539
    assert "RM0090" in sample["notes"] and "RM0433" in sample["notes"]
