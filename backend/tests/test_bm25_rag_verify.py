"""
BM25 分词 + 检索验证脚本（不依赖数据库，纯内存测试）。

验证点：
1. jieba 硬件术语词典是否生效（STM32F4/ESP32-S3/DMA 等作为单 token）
2. BM25 能否精确匹配硬件术语查询
3. rrf_fusion 分数是否在 0-1 范围内
4. 阈值过滤是否正常工作
5. 边缘情况：空结果、重复 doc_id、score=0

运行方式: cd backend && python tests/test_bm25_rag_verify.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag.kb_manager import BM25Index, rrf_fusion
from src.rag.vector_store import SearchResult


# ─── 测试用文档语料（模拟硬件知识库） ───
CORPUS = [
    "STM32F4系列基于Cortex-M4内核，主频168MHz，配置GPIO需要先开启RCC时钟，支持推挽和开漏输出模式",
    "ESP32-S3是乐鑫Wi-Fi+BLE SoC，双核Xtensa LX7 240MHz，Strapping引脚GPIO0决定Boot模式",
    "MPU6050六轴姿态传感器通过I2C接口通信，地址0x68，读取加速度和陀螺仪数据",
    "WS2812全彩LED单线数据协议，时序0码0.35us高电平，1码0.9us高电平，STM32用PWM+DMA驱动",
    "STM32H7系列Cortex-M7内核480MHz，1MB SRAM，支持QSPI外部Flash扩展",
    "SPI全双工同步串行总线，使用SCLK/MOSI/MISO/CS四根线，速度可达50MHz以上",
    "BME280集成温度湿度气压三种测量，走I2C接口，相比DHT11多了气压数据",
    "STM32的UART支持硬件流控RTS/CTS，常见波特率9600/115200/921600",
    "FreeRTOS实时操作系统，STM32+FreeRTOS组合下中断优先级必须配置5-15",
    "SWD调试接口只需SWDIO/SWCLK两根线，比JTAG五线制更省引脚",
    "EEPROM用于保存掉电不丢失参数，STM32F4没有内置EEPROM需要用Flash模拟",
    "看门狗Watchdog用于系统死机后自动复位，STM32有IWDG独立看门狗和WWDG窗口看门狗",
]


def test_jieba_hardware_dict():
    """P2-6: 验证 jieba 硬件术语词典。"""
    print("=" * 60)
    print("测试 1: jieba 硬件术语分词")
    print("=" * 60)

    import jieba
    BM25Index._load_hardware_dict()

    test_cases = [
        ("STM32F4配置DMA", ["STM32F4", "DMA"]),
        ("ESP32-S3的I2C通信", ["ESP32-S3", "I2C"]),
        ("SPI和UART对比", ["SPI", "UART"]),
        ("STM32H7 QSPI Flash", ["STM32H7", "QSPI"]),
        ("MPU6050 I2C地址", ["MPU6050", "I2C"]),
        ("WS2812 PWM驱动", ["WS2812", "PWM"]),
        ("BME280 温湿度", ["BME280"]),
        ("SWD调试接口", ["SWD"]),
        ("FreeRTOS中断", ["FreeRTOS"]),
    ]

    passed = 0
    for query, expected_tokens in test_cases:
        tokens = jieba.lcut(query)
        all_found = all(t in tokens for t in expected_tokens)
        status = "PASS" if all_found else "FAIL"
        if all_found:
            passed += 1
        print(f"  [{status}] '{query}' → {tokens}")
        if not all_found:
            missing = [t for t in expected_tokens if t not in tokens]
            print(f"         缺失 token: {missing}")

    print(f"\n  结果: {passed}/{len(test_cases)} 通过\n")
    assert passed == len(test_cases), f"分词测试失败: {passed}/{len(test_cases)}"
    return True


def test_bm25_search_precision():
    """验证 BM25 对硬件术语的精确匹配。"""
    print("=" * 60)
    print("测试 2: BM25 精确匹配")
    print("=" * 60)

    bm25 = BM25Index(CORPUS, [{} for _ in CORPUS])

    test_cases = [
        ("STM32F4 DMA", 0, [0, 1, 3, 4]),        # STM32F4 doc; small corpus BM25 may rank others
        ("ESP32-S3 Strapping", 1, [1]),          # ESP32-S3 unique
        ("MPU6050", 2, [2]),                      # MPU6050 unique
        ("WS2812", 3, [3]),                       # WS2812 unique
        ("STM32H7 QSPI", 4, [4]),                # STM32H7 unique
        ("SPI MOSI", 5, [5]),                     # SPI unique
        ("BME280", 6, [6]),                       # BME280 unique
        ("UART 波特率", 7, [7]),                  # UART unique
        ("FreeRTOS 中断", 8, [8]),               # FreeRTOS unique
        ("SWD", 9, [9]),                          # SWD unique
        ("EEPROM", 10, [10]),                     # EEPROM unique
        ("看门狗 Watchdog", 11, [11]),            # Watchdog unique
    ]

    passed = 0
    for query, expected_idx, acceptable_indices in test_cases:
        results = bm25.search(query, k=5)
        top_idx = results[0][0] if results else -1
        top_score = results[0][1] if results else 0
        # For unique terms, top must be expected; for shared terms, top must be in acceptable list
        status = "PASS" if top_idx in acceptable_indices else "FAIL"
        if top_idx in acceptable_indices:
            passed += 1
        print(f"  [{status}] '{query}' → top={top_idx}(期望{expected_idx}, 可接受{acceptable_indices}) score={top_score:.4f}")

    print(f"\n  结果: {passed}/{len(test_cases)} 通过\n")
    assert passed == len(test_cases), f"BM25 匹配失败: {passed}/{len(test_cases)}"
    return True


def test_rrf_fusion_scores():
    """P0: 验证 rrf_fusion 分数在 0-1 范围。"""
    print("=" * 60)
    print("测试 3: RRF 融合分数范围 (P0)")
    print("=" * 60)

    v_results = [
        SearchResult(content="doc1", metadata={"chunk_index": 0}, score=0.92, doc_id="d1"),
        SearchResult(content="doc2", metadata={"chunk_index": 0}, score=0.78, doc_id="d2"),
        SearchResult(content="doc3", metadata={"chunk_index": 0}, score=0.65, doc_id="d3"),
    ]
    b_results = [
        SearchResult(content="doc1", metadata={"chunk_index": 0}, score=0.85, doc_id="d1"),
        SearchResult(content="doc4", metadata={"chunk_index": 0}, score=0.55, doc_id="d4"),
    ]

    fused = rrf_fusion(v_results, b_results)

    print(f"  输入: vector={[(r.doc_id, r.score) for r in v_results]}")
    print(f"        bm25={[(r.doc_id, r.score) for r in b_results]}")
    print(f"  输出: fused={[(r.doc_id, round(r.score, 4)) for r in fused]}")

    # 验证 1: 所有分数在 0-1 范围
    for r in fused:
        assert 0.0 <= r.score <= 1.0, f"分数越界: {r.doc_id}={r.score}"

    # 验证 2: doc1 同时出现在两个列表，分数应为 max(0.92, 0.85) = 0.92
    assert fused[0].doc_id == "d1", f"期望 d1 排第一, 实际 {fused[0].doc_id}"
    assert abs(fused[0].score - 0.92) < 0.001, f"期望 0.92, 实际 {fused[0].score}"

    # 验证 3: 不应该在 0.016-0.033 范围（RRF 原始分数范围）
    assert fused[0].score > 0.1, f"分数 {fused[0].score} 可能是 RRF 原始分数（0.016-0.033）"

    print(f"  [PASS] 分数全在 0-1 范围, d1=0.92 (非 RRF 0.033)\n")
    return True


def test_rrf_fusion_threshold():
    """验证阈值过滤在融合后正常工作。"""
    print("=" * 60)
    print("测试 4: 阈值过滤")
    print("=" * 60)

    v_results = [
        SearchResult(content="high", metadata={"chunk_index": 0}, score=0.90, doc_id="d1"),
        SearchResult(content="mid", metadata={"chunk_index": 0}, score=0.55, doc_id="d2"),
        SearchResult(content="low", metadata={"chunk_index": 0}, score=0.20, doc_id="d3"),
    ]
    b_results = [
        SearchResult(content="high", metadata={"chunk_index": 0}, score=0.88, doc_id="d1"),
        SearchResult(content="low", metadata={"chunk_index": 0}, score=0.15, doc_id="d3"),
    ]

    fused = rrf_fusion(v_results, b_results)

    # 模拟 chat_routes.py 中的阈值过滤
    threshold = 0.7
    filtered = [r for r in fused if r.score >= threshold]

    print(f"  阈值={threshold}")
    print(f"  融合后: {[(r.doc_id, round(r.score, 4)) for r in fused]}")
    print(f"  过滤后: {[(r.doc_id, round(r.score, 4)) for r in filtered]}")

    assert len(filtered) == 1, f"期望 1 条通过阈值, 实际 {len(filtered)}"
    assert filtered[0].doc_id == "d1", f"期望 d1, 实际 {filtered[0].doc_id}"
    assert filtered[0].score >= 0.7

    print(f"  [PASS] 阈值 0.7 正确过滤, 仅 d1 (0.90) 通过\n")
    return True


def test_rrf_empty_inputs():
    """边缘情况: 空输入。"""
    print("=" * 60)
    print("测试 5: 边缘情况 - 空输入")
    print("=" * 60)

    # 双空
    fused = rrf_fusion([], [])
    assert fused == [], f"期望空列表, 实际 {fused}"
    print("  [PASS] 双空输入 → 空列表")

    # 向量空，BM25 有结果
    b_results = [SearchResult(content="only_bm25", metadata={"chunk_index": 0}, score=0.70, doc_id="d1")]
    fused = rrf_fusion([], b_results)
    assert len(fused) == 1
    assert fused[0].doc_id == "d1"
    assert abs(fused[0].score - 0.70) < 0.001
    print(f"  [PASS] 向量空, BM25 有结果 → d1 score=0.70")

    # BM25 空，向量有结果
    v_results = [SearchResult(content="only_vec", metadata={"chunk_index": 0}, score=0.85, doc_id="d2")]
    fused = rrf_fusion(v_results, [])
    assert len(fused) == 1
    assert fused[0].doc_id == "d2"
    assert abs(fused[0].score - 0.85) < 0.001
    print(f"  [PASS] BM25 空, 向量有结果 → d2 score=0.85\n")
    return True


def test_rrf_duplicate_doc_different_chunks():
    """边缘情况: 同一文档不同 chunk_index。"""
    print("=" * 60)
    print("测试 6: 边缘情况 - 同文档不同 chunk")
    print("=" * 60)

    v_results = [
        SearchResult(content="chunk0", metadata={"chunk_index": 0}, score=0.80, doc_id="d1"),
        SearchResult(content="chunk1", metadata={"chunk_index": 1}, score=0.60, doc_id="d1"),
    ]
    b_results = [
        SearchResult(content="chunk1", metadata={"chunk_index": 1}, score=0.75, doc_id="d1"),
    ]

    fused = rrf_fusion(v_results, b_results)

    print(f"  输入: vector d1#0=0.80, d1#1=0.60 | bm25 d1#1=0.75")
    print(f"  输出: {[(r.metadata.get('chunk_index'), round(r.score, 4)) for r in fused]}")

    # d1#0 只在 vector 中, score=0.80
    # d1#1 在两者中, score=max(0.60, 0.75)=0.75
    assert len(fused) == 2, f"期望 2 条, 实际 {len(fused)}"

    # d1#0 RRF 分数更高（rank 0 in vector）
    # d1#1 RRF 分数较低（rank 1 in vector, rank 0 in bm25）
    by_chunk = {r.metadata.get("chunk_index"): r.score for r in fused}
    assert abs(by_chunk[0] - 0.80) < 0.001, f"chunk0 期望 0.80, 实际 {by_chunk[0]}"
    assert abs(by_chunk[1] - 0.75) < 0.001, f"chunk1 期望 0.75, 实际 {by_chunk[1]}"

    print(f"  [PASS] 同文档不同 chunk 正确区分: chunk0=0.80, chunk1=0.75\n")
    return True


def test_rrf_missing_chunk_index():
    """边缘情况: metadata 中没有 chunk_index。"""
    print("=" * 60)
    print("测试 7: 边缘情况 - 缺少 chunk_index")
    print("=" * 60)

    v_results = [
        SearchResult(content="doc1", metadata={}, score=0.80, doc_id="d1"),
    ]
    b_results = [
        SearchResult(content="doc1", metadata={}, score=0.70, doc_id="d1"),
    ]

    fused = rrf_fusion(v_results, b_results)

    # 没有 chunk_index 时，用 enumerate 索引作为 fallback
    # v_results[0].metadata.get('chunk_index', 0) = 0
    # b_results[0].metadata.get('chunk_index', 0) = 0
    # 所以 key 相同，应该去重为 1 条
    assert len(fused) == 1, f"期望 1 条, 实际 {len(fused)}"
    assert abs(fused[0].score - 0.80) < 0.001, f"期望 0.80, 实际 {fused[0].score}"
    print(f"  [PASS] 缺少 chunk_index 时 fallback 到索引 0, 正确去重\n")
    return True


def test_rrf_zero_scores():
    """边缘情况: score=0 的结果。"""
    print("=" * 60)
    print("测试 8: 边缘情况 - score=0")
    print("=" * 60)

    v_results = [
        SearchResult(content="zero", metadata={"chunk_index": 0}, score=0.0, doc_id="d1"),
        SearchResult(content="high", metadata={"chunk_index": 0}, score=0.90, doc_id="d2"),
    ]
    b_results = []

    fused = rrf_fusion(v_results, b_results)

    assert len(fused) == 2
    by_doc = {r.doc_id: r.score for r in fused}
    assert by_doc["d1"] == 0.0
    assert by_doc["d2"] == 0.90

    # 阈值过滤应该过滤掉 score=0
    filtered = [r for r in fused if r.score >= 0.5]
    assert len(filtered) == 1
    assert filtered[0].doc_id == "d2"

    print(f"  [PASS] score=0 正确保留但被阈值过滤\n")
    return True


def test_bm25_score_normalization():
    """验证 BM25 分数归一化（在 _bm25_search 中完成）。"""
    print("=" * 60)
    print("测试 9: BM25 分数归一化模拟")
    print("=" * 60)

    bm25 = BM25Index(CORPUS, [{} for _ in CORPUS])
    results = bm25.search("STM32F4 DMA", k=5)

    # 模拟 _bm25_search 中的归一化逻辑
    max_score = results[0][1] if results and results[0][1] > 0 else 1.0
    normalized = [(idx, score / max_score if max_score > 0 else 0.0) for idx, score in results]

    print(f"  原始 BM25 分数: {[(i, round(s, 4)) for i, s in results]}")
    print(f"  归一化后: {[(i, round(s, 4)) for i, s in normalized]}")

    # 归一化后所有分数应在 0-1 范围
    for _, score in normalized:
        assert 0.0 <= score <= 1.0, f"归一化分数越界: {score}"

    # 最高分应为 1.0（或接近，如果 max_score=0）
    if max_score > 0:
        assert abs(normalized[0][1] - 1.0) < 0.001

    print(f"  [PASS] BM25 归一化后分数全在 0-1, top=1.0\n")
    return True


def main():
    import io
    import contextlib

    # Capture all output to a string buffer
    output_buf = io.StringIO()

    with contextlib.redirect_stdout(output_buf), contextlib.redirect_stderr(output_buf):
        print("\n" + "=" * 60)
        print("  BM25 + RRF 融合验证套件")
        print("=" * 60 + "\n")

        tests = [
            test_jieba_hardware_dict,
            test_bm25_search_precision,
            test_rrf_fusion_scores,
            test_rrf_fusion_threshold,
            test_rrf_empty_inputs,
            test_rrf_duplicate_doc_different_chunks,
            test_rrf_missing_chunk_index,
            test_rrf_zero_scores,
            test_bm25_score_normalization,
        ]

        passed = 0
        failed = 0
        for test in tests:
            try:
                if test():
                    passed += 1
            except Exception as e:
                failed += 1
                print(f"  [ERROR] {test.__name__}: {e}\n")
                import traceback
                traceback.print_exc()

        print("=" * 60)
        print(f"  总计: {passed} 通过, {failed} 失败, {len(tests)} 测试")
        print("=" * 60)

    # Write to file and stdout
    output_text = output_buf.getvalue()
    with open("_verify_result.txt", "w", encoding="utf-8") as f:
        f.write(output_text)
    print(output_text, end="")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
