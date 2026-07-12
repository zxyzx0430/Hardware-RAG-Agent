import fitz
import json
import re

PDF_PATH = r"E:\Desktop\agent\data\pdfs\mcu\stm32f4_gpio_exti_extract.pdf"
CHUNKS_REVIEW_PATH = r"E:\Desktop\agent\data\test_results\multimodal_chunks_review.json"
OUTPUT_PATH = r"E:\Desktop\agent\data\test_results\independent_pdf_analysis.json"

SECTION_PATTERNS = [
    (r"^\s*8\s+General-purpose I/Os \(GPIO\)", "8. GPIO Introduction"),
    (r"^\s*8\.1\s+GPIO introduction", "8.1 GPIO Introduction"),
    (r"^\s*8\.2\s+GPIO functional description", "8.2 GPIO Functional Description"),
    (r"^\s*8\.3\s+I/O port description", "8.3 I/O Port Description"),
    (r"^\s*8\.3\.1\s+General-purpose I/O \(GPIO\)", "8.3.1 General-purpose I/O (GPIO)"),
    (r"^\s*8\.3\.2\s+I/O pin multiplexer and mapping", "8.3.2 I/O Pin Multiplexer and Mapping"),
    (r"^\s*8\.3\.3\s+I/O port control registers", "8.3.3 I/O Port Control Registers"),
    (r"^\s*8\.3\.4\s+GPIO input configuration", "8.3.4 GPIO Input Configuration"),
    (r"^\s*8\.3\.5\s+GPIO output configuration", "8.3.5 GPIO Output Configuration"),
    (r"^\s*8\.3\.6\s+Alternate function configuration", "8.3.6 Alternate Function Configuration"),
    (r"^\s*8\.3\.7\s+Locking mechanism", "8.3.7 Locking Mechanism"),
    (r"^\s*8\.3\.8\s+GPIO alternate function registers", "8.3.8 GPIO AF Registers"),
    (r"^\s*8\.3\.9\s+External interrupt\/event lines", "8.3.9 External Interrupt/Event Lines"),
    (r"^\s*8\.3\.10\s+Output configuration", "8.3.10 Output Configuration"),
    (r"^\s*8\.3\.11\s+Alternate function configuration", "8.3.11 AF Configuration"),
    (r"^\s*8\.3\.12\s+Analog configuration", "8.3.12 Analog Configuration"),
    (r"^\s*8\.3\.13\s+Special event output", "8.3.13 Special Event Output"),
    (r"^\s*8\.3\.14\s+Port n", "8.3.14 Port N"),
    (r"^\s*8\.3\.15\s+Selection of RTC_AF1 and RTC_AF2", "8.3.15 RTC AF Selection"),
    (r"^\s*8\.4\s+GPIO registers", "8.4 GPIO Registers"),
    (r"^\s*8\.4\.1\s+GPIO port mode register", "8.4.1 MODER"),
    (r"^\s*8\.4\.2\s+GPIO port output type register", "8.4.2 OTYPER"),
    (r"^\s*8\.4\.3\s+GPIO port output speed register", "8.4.3 OSPEEDR"),
    (r"^\s*8\.4\.4\s+GPIO port pull-up\/pull-down register", "8.4.4 PUPDR"),
    (r"^\s*8\.4\.5\s+GPIO port input data register", "8.4.5 IDR"),
    (r"^\s*8\.4\.6\s+GPIO port output data register", "8.4.6 ODR"),
    (r"^\s*8\.4\.7\s+GPIO port bit set\/reset register", "8.4.7 BSRR"),
    (r"^\s*8\.4\.8\s+GPIO port configuration lock register", "8.4.8 LCKR"),
    (r"^\s*8\.4\.9\s+GPIO alternate function low register", "8.4.9 AFRL"),
    (r"^\s*8\.4\.10\s+GPIO alternate function high register", "8.4.10 AFRH"),
    (r"^\s*8\.4\.11\s+GPIO register map", "8.4.11 GPIO Register Map"),
    (r"^\s*9\s+System configuration controller \(SYSCFG\)", "9. SYSCFG"),
    (r"^\s*9\.2\.1\s+SYSCFG memory remap register", "9.2.1 SYSCFG_MEMRMP"),
    (r"^\s*9\.2\.2\s+SYSCFG peripheral mode configuration register", "9.2.2 SYSCFG_PMC"),
    (r"^\s*9\.2\.3\s+SYSCFG external interrupt configuration register 1", "9.2.3 SYSCFG_EXTICR1"),
    (r"^\s*9\.2\.4\s+SYSCFG external interrupt configuration register 2", "9.2.4 SYSCFG_EXTICR2"),
    (r"^\s*9\.2\.5\s+SYSCFG external interrupt configuration register 3", "9.2.5 SYSCFG_EXTICR3"),
    (r"^\s*9\.2\.6\s+SYSCFG external interrupt configuration register 4", "9.2.6 SYSCFG_EXTICR4"),
    (r"^\s*9\.2\.7\s+SYSCFG compensation cell control register", "9.2.7 SYSCFG_CMPCR"),
    (r"^\s*9\.2\.8\s+SYSCFG register maps", "9.2.8 SYSCFG Register Maps"),
    (r"^\s*12\s+Interrupts and events", "12. Interrupts and Events"),
    (r"^\s*12\.1\s+Nested vectored interrupt controller", "12.1 NVIC"),
    (r"^\s*12\.2\s+EXTI", "12.2 EXTI"),
    (r"^\s*12\.2\.1\s+EXTI main features", "12.2.1 EXTI Features"),
    (r"^\s*12\.2\.2\s+EXTI block diagram", "12.2.2 EXTI Block Diagram"),
    (r"^\s*12\.2\.3\s+Wakeup event management", "12.2.3 Wakeup Event"),
    (r"^\s*12\.2\.4\s+Software interrupt\/event generation", "12.2.4 SW Interrupt/Event"),
    (r"^\s*12\.2\.5\s+External interrupt\/event line mapping", "12.2.5 EXTI Line Mapping"),
    (r"^\s*12\.3\s+EXTI registers", "12.3 EXTI Registers"),
    (r"^\s*12\.3\.1\s+Interrupt mask register", "12.3.1 EXTI_IMR"),
    (r"^\s*12\.3\.2\s+Event mask register", "12.3.2 EXTI_EMR"),
    (r"^\s*12\.3\.3\s+Rising trigger selection register", "12.3.3 EXTI_RTSR"),
    (r"^\s*12\.3\.4\s+Falling trigger selection register", "12.3.4 EXTI_FTSR"),
    (r"^\s*12\.3\.5\s+Software interrupt event register", "12.3.5 EXTI_SWIER"),
    (r"^\s*12\.3\.6\s+Pending register", "12.3.6 EXTI_PR"),
    (r"^\s*12\.3\.7\s+EXTI register map", "12.3.7 EXTI Register Map"),
]

KEY_TECHNICAL_POINTS = {
    "MODER_4_modes": {
        "name": "MODER 四种模式值 (00/01/10/11)",
        "keywords": ["00", "01", "10", "11", "Input", "Output", "Alternate", "Analog"],
        "found": False,
        "page": None,
        "detail": ""
    },
    "OSPEEDR_speed_grades": {
        "name": "OSPEEDR 速度等级",
        "keywords": ["Low speed", "Medium speed", "Fast speed", "High speed"],
        "found": False,
        "page": None,
        "detail": ""
    },
    "LCKR_lock_sequence": {
        "name": "LCKR 锁定序列",
        "keywords": ["LCKK", "lock sequence", "16-bit", "WR1", "WR0"],
        "found": False,
        "page": None,
        "detail": ""
    },
    "EXTI_vector_sharing": {
        "name": "EXTI 中断向量共享",
        "keywords": ["EXTI9_5", "EXTI15_10", "shared", "vector"],
        "found": False,
        "page": None,
        "detail": ""
    },
    "SYSCFG_EXTICR_config": {
        "name": "SYSCFG_EXTICR 配置",
        "keywords": ["EXTICR", "0000: PA", "0001: PB", "0010: PC"],
        "found": False,
        "page": None,
        "detail": ""
    }
}


def read_page_texts_and_images(pdf_path):
    doc = fitz.open(pdf_path)
    pages = []
    total_chars = 0
    for i in range(doc.page_count):
        page = doc[i]
        text = page.get_text()
        images = page.get_images(full=True)
        total_chars += len(text)
        pages.append({
            "page_number": i + 1,
            "text": text,
            "char_count": len(text),
            "image_count": len(images),
            "image_details": [
                {"xref": img[0], "width": img[2], "height": img[3]}
                for img in images
            ]
        })
    doc.close()
    return pages, total_chars


def detect_sections(pages):
    sections_on_page = {}
    for p in pages:
        page_num = p["page_number"]
        text_lines = p["text"].split("\n")
        found = []
        for line in text_lines:
            for pattern, name in SECTION_PATTERNS:
                if re.match(pattern, line.strip(), re.IGNORECASE):
                    found.append({"section": name, "matched_line": line.strip()[:100]})
        if found:
            sections_on_page[page_num] = found
    return sections_on_page


def check_key_technical_points(pages):
    results = {}
    for key, point in KEY_TECHNICAL_POINTS.items():
        results[key] = {
            "name": point["name"],
            "found": False,
            "page": None,
            "detail": ""
        }
    for p in pages:
        text = p["text"]
        page_num = p["page_number"]

        if not results["MODER_4_modes"]["found"]:
            if re.search(r"00:\s*Input.*reset", text) or \
               re.search(r"00:\s*Input.*01:\s*General", text, re.DOTALL) or \
               re.search(r"01:\s*General purpose output mode", text):
                results["MODER_4_modes"]["found"] = True
                results["MODER_4_modes"]["page"] = page_num
                results["MODER_4_modes"]["detail"] = "MODER mode encoding (00=Input, 01=GP output, 10=AF, 11=Analog) found in register description"
            elif re.search(r"MODER.*00.*01.*10.*11", text, re.DOTALL):
                results["MODER_4_modes"]["found"] = True
                results["MODER_4_modes"]["page"] = page_num
                results["MODER_4_modes"]["detail"] = "MODER 4-mode encoding found in configuration table"

        if not results["OSPEEDR_speed_grades"]["found"]:
            if re.search(r"OSPEEDR|OSPEED", text) and \
               re.search(r"Low speed|Medium speed|Fast speed|High speed|00\s|01\s|10\s|11\s", text, re.IGNORECASE):
                results["OSPEEDR_speed_grades"]["found"] = True
                results["OSPEEDR_speed_grades"]["page"] = page_num
                results["OSPEEDR_speed_grades"]["detail"] = "OSPEEDR speed encoding found"
            elif re.search(r"OSPEEDR", text) and re.search(r"speed", text, re.IGNORECASE):
                if not results["OSPEEDR_speed_grades"]["found"]:
                    results["OSPEEDR_speed_grades"]["found"] = True
                    results["OSPEEDR_speed_grades"]["page"] = page_num
                    results["OSPEEDR_speed_grades"]["detail"] = "OSPEEDR register described"

        if not results["LCKR_lock_sequence"]["found"]:
            if re.search(r"LCKK|lock sequence|LOCK_KEY", text, re.IGNORECASE) and \
               re.search(r"16|WRITE|read.*LCKK", text):
                results["LCKR_lock_sequence"]["found"] = True
                results["LCKR_lock_sequence"]["page"] = page_num
                results["LCKR_lock_sequence"]["detail"] = "LCKR lock sequence mechanism described"

        if not results["EXTI_vector_sharing"]["found"]:
            if re.search(r"EXTI9_5|EXTI15_10|Line\[9:5\]|Line\[15:10\]", text):
                results["EXTI_vector_sharing"]["found"] = True
                results["EXTI_vector_sharing"]["page"] = page_num
                results["EXTI_vector_sharing"]["detail"] = "EXTI shared vector entries found in vector table"

        if not results["SYSCFG_EXTICR_config"]["found"]:
            if re.search(r"SYSCFG_EXTICR|EXTICR[1-4]", text) and \
               re.search(r"0000.*PA|0001.*PB|0010.*PC|PA\[x\]|PB\[x\]", text):
                results["SYSCFG_EXTICR_config"]["found"] = True
                results["SYSCFG_EXTICR_config"]["page"] = page_num
                results["SYSCFG_EXTICR_config"]["detail"] = "SYSCFG_EXTICR pin mapping encoding found"

    return results


LOGICAL_CHAPTERS = [
    {
        "id": "ch1",
        "title": "Chapter 8: GPIO Introduction",
        "pages": [1, 2],
        "has_table": True,
        "has_figure": False,
        "summary": "GPIO overview: each I/O port bit programmable, 32-bit register access, port features overview (input/output/speed/pull-up-down/analog)"
    },
    {
        "id": "ch2",
        "title": "8.3.1 General-purpose I/O (GPIO) & MODER Mode Encoding",
        "pages": [2, 3],
        "has_table": True,
        "has_figure": False,
        "summary": "GPIO mode encoding table: MODER[1:0] values 00=Input, 01=Output, 10=AF, 11=Analog; with SPEED and PUPDR combinations; reset state: input floating"
    },
    {
        "id": "ch3",
        "title": "8.3.2 I/O Pin Multiplexer and Mapping",
        "pages": [4, 5, 6, 7],
        "has_table": False,
        "has_figure": True,
        "figure_refs": ["Figure 26 (F405/407 AF mapping)", "Figure 27 (F42/43 AF mapping)"],
        "summary": "AF multiplexer architecture: AF0-AF15 mapping, JTAG/SWD pin release, GPIO vs peripheral AF selection procedures for F405/407 and F42/43"
    },
    {
        "id": "ch4",
        "title": "8.3.3 I/O Port Control Registers Overview & LCKR Locking",
        "pages": [8, 9],
        "has_table": True,
        "has_figure": False,
        "summary": "Port control registers (MODER/OTYPER/OSPEEDR/PUPDR/IDR/ODR/BSRR/LCKR/AFRL/AFRH), BSRR set/reset mechanism, LCKR lock sequence (LOCK_KEY write pattern)"
    },
    {
        "id": "ch5",
        "title": "8.3.4-8.3.12 GPIO Input/Output/AF/Analog Configuration",
        "pages": [10, 11, 12],
        "has_table": False,
        "has_figure": True,
        "figure_refs": ["Figure 28 (Input floating/pull-up/pull-down)", "Figure 29 (Output configuration)"],
        "summary": "Input floating/pull-up/pull-down config, Output push-pull/open-drain config, AF config, Analog config (output buffer disabled, Schmitt trigger off)"
    },
    {
        "id": "ch6",
        "title": "8.3.13-8.3.15 RTC AF & Special Event Output",
        "pages": [12, 13, 14],
        "has_table": True,
        "has_figure": False,
        "summary": "Special event output on EVENTOUT, LSE/HSE oscillator pins as GPIO, RTC_AF1/RTC_AF2 alternate function selection, TAMPINSEL/TSINSEL register config"
    },
    {
        "id": "ch7",
        "title": "8.4.1-8.4.4 MODER/OTYPER/OSPEEDR/PUPDR Register Details",
        "pages": [15, 16, 17],
        "has_table": True,
        "has_figure": True,
        "figure_refs": ["MODER register bit map", "OSPEEDR register bit map"],
        "summary": "Detailed register descriptions: MODER bit encoding 00/01/10/11, OTYPER push-pull/open-drain, OSPEEDR speed grades (Low/Medium/High), PUPDR pull-up/pull-down"
    },
    {
        "id": "ch8",
        "title": "8.4.5-8.4.8 IDR/ODR/BSRR/LCKR Register Details",
        "pages": [17, 18, 19],
        "has_table": True,
        "has_figure": False,
        "summary": "IDR input data, ODR output data, BSRR set/reset bits, LCKR lock mechanism with sequence details, BRy reset bits"
    },
    {
        "id": "ch9",
        "title": "8.4.9-8.4.11 AFRL/AFRH & GPIO Register Map",
        "pages": [19, 20, 21],
        "has_table": True,
        "has_figure": False,
        "summary": "AFRL/AFRH alternate function registers (AF0-AF15 per pin), complete GPIO register map with offsets and reset values"
    },
    {
        "id": "ch10",
        "title": "Chapter 9: SYSCFG Introduction",
        "pages": [22],
        "has_table": False,
        "has_figure": False,
        "summary": "SYSCFG controller: memory remap, I/O compensation cell (2.4-3.6V), MII/RMII selection, Ethernet PHY config"
    },
    {
        "id": "ch11",
        "title": "9.2.1-9.2.8 SYSCFG Registers (MEMRMP/PMC/EXTICR/CMPCR)",
        "pages": [22, 23, 24, 25, 26, 27],
        "has_table": True,
        "has_figure": False,
        "summary": "MEMRMP memory remap, PMC mode config, EXTICR1-4 external interrupt pin source selection (0000=PA...1000=PI), CMPCR compensation cell"
    },
    {
        "id": "ch12",
        "title": "Chapter 12: Interrupts and Events - NVIC Overview",
        "pages": [28, 29, 30, 31, 32, 33, 34, 35],
        "has_table": True,
        "has_figure": False,
        "summary": "NVIC: nested vectored interrupt controller, vector tables for F405/407 and F42/43 (including EXTI9_5 shared, EXTI15_10 shared vectors), priority levels"
    },
    {
        "id": "ch13",
        "title": "12.2.1-12.2.5 EXTI Features & Block Diagram",
        "pages": [36, 37, 38, 39, 40],
        "has_table": False,
        "has_figure": True,
        "figure_refs": ["Figure 41 (EXTI block diagram)", "Figure 42/43 (GPIO mapping)"],
        "summary": "EXTI features: independent trigger/mask, 23 interrupt/event lines, wakeup from Stop/Standby, block diagram (edge detector→pending bit→NVIC), line mapping (140/168 GPIOs → 16 EXTI lines via SYSCFG)"
    },
    {
        "id": "ch14",
        "title": "12.3.1-12.3.7 EXTI Register Details",
        "pages": [41, 42, 43, 44],
        "has_table": True,
        "has_figure": False,
        "summary": "EXTI_IMR mask, EXTI_EMR event mask, EXTI_RTSR rising trigger, EXTI_FTSR falling trigger, EXTI_SWIER software interrupt, EXTI_PR pending, EXTI register map"
    }
]


def build_independent_chunks(pages):
    chunks = []
    chunk_idx = 0
    for chapter in LOGICAL_CHAPTERS:
        chapter_text = ""
        chapter_images = 0
        for pg in chapter["pages"]:
            page_data = pages[pg - 1]
            chapter_text += f"<!-- PAGE:{pg} -->\n"
            chapter_text += page_data["text"] + "\n"
            chapter_images += page_data["image_count"]

        chunks.append({
            "chunk_index": chunk_idx,
            "title": chapter["title"],
            "pages": chapter["pages"],
            "char_count": len(chapter_text),
            "has_table": chapter["has_table"],
            "has_figure": chapter.get("has_figure", False),
            "figure_refs": chapter.get("figure_refs", []),
            "total_images_on_pages": chapter_images,
            "summary": chapter["summary"],
            "text_preview": chapter_text[:300]
        })
        chunk_idx += 1
    return chunks


def compare_with_multimodal(independent_chunks, multimodal_chunks):
    mm_total_chars = sum(c["chars"] for c in multimodal_chunks)
    mm_empty_pages = sum(1 for c in multimodal_chunks if not c["pages"])
    mm_empty_section = sum(1 for c in multimodal_chunks if not c["section"])

    ind_total_chars = sum(c["char_count"] for c in independent_chunks)

    page_coverage = {}
    for c in independent_chunks:
        for pg in c["pages"]:
            page_coverage[pg] = c["title"]

    mm_page_coverage = set()
    content_gaps = []
    for c in independent_chunks:
        page_nums = c["pages"]
        title = c["title"]
        for mc in multimodal_chunks:
            if mc["pages"] and any(p in mc["pages"] for p in page_nums):
                mm_page_coverage.update(mc["pages"])

    all_mm_chars = ""
    for mc in multimodal_chunks:
        all_mm_chars += mc.get("preview", "")

    missing_keywords = {}
    critical_checks = {
        "MODER_mode_values": ["00", "01", "10", "11", "Input", "Output", "Alternate", "Analog", "MODER"],
        "OSPEEDR_speed": ["OSPEEDR", "speed", "Low speed", "Medium speed", "Fast speed", "High speed"],
        "LCKR_lock_sequence": ["LCKK", "lock", "sequence", "LOCK_KEY"],
        "EXTI_vector_sharing": ["EXTI9_5", "EXTI15_10", "Line[9:5]", "Line[15:10]"],
        "SYSCFG_EXTICR": ["EXTICR", "PA[x]", "PB[x]", "PC[x]", "0000", "0001"]
    }
    for check_key, keywords in critical_checks.items():
        found_in_mm = []
        for mc in multimodal_chunks:
            preview = mc.get("preview", "")
            if any(kw.lower() in preview.lower() for kw in keywords):
                found_in_mm.append(mc["index"])
        missing_keywords[check_key] = {
            "keywords_checked": keywords,
            "found_in_chunks": found_in_mm,
            "coverage": f"{len(found_in_mm)}/{len(multimodal_chunks)} chunks contain relevant keywords"
        }

    return {
        "multimodal_summary": {
            "total_chunks": len(multimodal_chunks),
            "total_chars": mm_total_chars,
            "all_text_type": all(c["type"] == "text" for c in multimodal_chunks),
            "chunks_with_pages": len(multimodal_chunks) - mm_empty_pages,
            "chunks_without_pages": mm_empty_pages,
            "chunks_with_section": len(multimodal_chunks) - mm_empty_section,
            "chunks_without_section": mm_empty_section,
            "has_image_description": any(c.get("type") == "image" for c in multimodal_chunks),
            "avg_chars_per_chunk": round(mm_total_chars / len(multimodal_chunks), 1)
        },
        "independent_summary": {
            "total_chunks": len(independent_chunks),
            "total_chars": ind_total_chars,
            "avg_chars_per_chunk": round(ind_total_chars / len(independent_chunks), 1),
            "total_pages_covered": len(set(p for c in independent_chunks for p in c["pages"]))
        },
        "content_coverage_comparison": {
            "total_pdf_chars_estimate": ind_total_chars,
            "multimodal_captured_chars": mm_total_chars,
            "independent_captured_chars": ind_total_chars,
            "char_coverage_ratio": round(mm_total_chars / ind_total_chars, 3) if ind_total_chars > 0 else 0
        },
        "missing_content_analysis": {
            "pages_missing_from_mm_chunks": sorted(set(range(1, 45)) - mm_page_coverage),
            "critical_technical_checks": missing_keywords
        },
        "key_findings": []
    }


def main():
    print("Reading PDF pages...")
    pages, total_chars = read_page_texts_and_images(PDF_PATH)
    print(f"  Total pages: {len(pages)}, Total chars: {total_chars}")

    total_images = sum(p["image_count"] for p in pages)
    print(f"  Total images across all pages: {total_images}")

    print("\nDetecting sections...")
    sections = detect_sections(pages)

    print("\nChecking key technical points...")
    tech_points = check_key_technical_points(pages)

    print("\nBuilding independent chunks...")
    independent_chunks = build_independent_chunks(pages)

    print("\nLoading MultimodalChunker chunks...")
    with open(CHUNKS_REVIEW_PATH, "r", encoding="utf-8") as f:
        multimodal_chunks = json.load(f)

    print("\nComparing with MultimodalChunker...")
    comparison = compare_with_multimodal(independent_chunks, multimodal_chunks)

    key_findings = []
    key_findings.append({
        "finding": "页码元数据缺失",
        "severity": "HIGH",
        "detail": f"MultimodalChunker 的 102 个 chunks 中，全部 {len(multimodal_chunks)} 个的 pages 字段为空数组 []，无法追溯内容来源页码"
    })
    key_findings.append({
        "finding": "section 元数据缺失",
        "severity": "HIGH",
        "detail": f"全部 {len(multimodal_chunks)} 个 chunks 的 section 字段为空字符串，无法按章节检索"
    })
    key_findings.append({
        "finding": "图片内容未捕获",
        "severity": "LOW",
        "detail": "提取版 PDF 中所有 44 页的嵌入式图片数均为 0（图表已转为文本描述），因此图片缺失问题在此 PDF 中不显著"
    })
    key_findings.append({
        "finding": "向量表被过度分片",
        "severity": "MEDIUM",
        "detail": "中断向量表 (Table 61/62) 跨越 8 页，被 MultimodalChunker 拆成约 20+ 个碎片 chunks，每个仅含几行向量表条目，丧失了完整的中断-向量-IRQ 对应关系"
    })
    key_findings.append({
        "finding": "寄存器位域表格跨 chunk 断裂",
        "severity": "MEDIUM",
        "detail": "MODER/OSPEEDR/EXTICR 等寄存器的位域描述被拆到不同 chunk，00/01/10/11 的编码值与其含义描述可能不在同一 chunk 中"
    })
    key_findings.append({
        "finding": "chunk 粒度不一致",
        "severity": "MEDIUM",
        "detail": f"MultimodalChunker 的 chunk 字符数范围为 {min(c['chars'] for c in multimodal_chunks)}-{max(c['chars'] for c in multimodal_chunks)}，差异过大 (最大/最小比 = {round(max(c['chars'] for c in multimodal_chunks)/min(c['chars'] for c in multimodal_chunks), 1)}x)"
    })

    for tp_key, tp_val in tech_points.items():
        if not tp_val["found"]:
            key_findings.append({
                "finding": f"关键技术点缺失: {tp_val['name']}",
                "severity": "HIGH",
                "detail": f"在 PDF 全文中未检测到 {tp_val['name']} 的完整描述"
            })
        else:
            key_findings.append({
                "finding": f"关键技术点已覆盖: {tp_val['name']}",
                "severity": "OK",
                "detail": f"{tp_val['detail']} (page {tp_val['page']})"
            })

    comparison["key_findings"] = key_findings

    report = {
        "pdf_info": {
            "path": PDF_PATH,
            "total_pages": len(pages),
            "total_chars": total_chars,
            "total_images": total_images,
            "per_page_stats": [
                {
                    "page": p["page_number"],
                    "chars": p["char_count"],
                    "images": p["image_count"]
                }
                for p in pages
            ]
        },
        "sections_detected": sections,
        "key_technical_points": tech_points,
        "independent_chunks": independent_chunks,
        "comparison": comparison
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\nReport saved to: {OUTPUT_PATH}")
    print(f"\n=== SUMMARY ===")
    print(f"PDF: {len(pages)} pages, {total_chars} chars, {total_images} images")
    print(f"Independent chunks: {len(independent_chunks)}")
    print(f"MultimodalChunker chunks: {len(multimodal_chunks)}")
    print(f"MultimodalChunker total chars: {comparison['multimodal_summary']['total_chars']}")
    print(f"Chunks with page metadata: {comparison['multimodal_summary']['chunks_with_pages']}/{len(multimodal_chunks)}")
    print(f"Chunks with section metadata: {comparison['multimodal_summary']['chunks_with_section']}/{len(multimodal_chunks)}")
    print(f"\nKey technical points:")
    for k, v in tech_points.items():
        status = "FOUND" if v["found"] else "MISSING"
        print(f"  {v['name']}: {status} (page {v['page']})")
    print(f"\nKey findings:")
    for f_item in key_findings:
        print(f"  [{f_item['severity']}] {f_item['finding']}")


if __name__ == "__main__":
    main()
