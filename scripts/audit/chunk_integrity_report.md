# CH340G Chunk 完整性审计报告
- 审计时间：2026-06-29
- PDF：data/pdfs/interface/ch340g_datasheet.pdf（14页）
- 审计对象：ChromaDB 已索引的 45 个 chunks
- 审计方法：多模态模型逐页阅读 PDF 图像 + chunk 内容人工比对

## 总体统计
- content_type: {'text': 36, 'image_description': 9}
- image_description 覆盖的 source_page: [3, 4, 7, 8, 9, 11, 12, 13, 14]
- 含图页面（page.get_images() > 0）：p7, p8, p12, p13, p14
- 矢量电路图页面（get_images() = 0 但 visually 含图）：p5, p6, p12, p13, p14

## 逐页覆盖情况
| 页 | 预期关键内容 | image_desc | text覆盖 | 状态 | 备注 |
|---|---|---|---|---|---|
| p01 | Title; Overview; Features list | ❌ | ✅ | ✅ 完整 | text chunk 覆盖 |
| p02 | Table of Contents | ❌ | ✅ | ✅ 完整 | text chunk 覆盖 |
| p03 | 3.1 Absolute Maximum Ratings table; 3.2 DC characteristics table; 3.3 AC characteristics table | ✅ | ✅ | ⚠️ 表格打散 | text chunk 覆盖但表格为纯文本，非 Markdown 表格（旧索引） |
| p04 | 4. Pinout table (16 pins); 5. Application Notes text | ✅ | ✅ | ⚠️ 表格打散 | text chunk 覆盖但表格为纯文本，非 Markdown 表格（旧索引） |
| p05 | Application Notes text; 5.1 USB to RS232 adapter schematic (vector diagram) | ❌ | ✅ | ❌ 图片遗漏 | 矢量电路图 page.get_images()=0，无 image_description；仅 text chunk 含 ASCII 打散内容 |
| p06 | 5.2 Optically isolated USB to UART adapter schematic (vector diagram) | ❌ | ✅ | ❌ 图片遗漏 | 矢量电路图 page.get_images()=0，无 image_description；仅 text chunk 含 ASCII 打散内容 |
| p07 | 1 Introduction text; System application block diagram (image); 2 Features list | ✅ | ✅ | ✅ 完整 | image_description + text 均覆盖 |
| p08 | 3 Package section; 6 package pinout diagrams (CH340G/C/B/E/T/R); Package shape table; 4 Pins table start | ✅ | ✅ | ✅ 完整 | image_description + text 均覆盖 |
| p09 | 4 Pins table continuation (cross-package pin functions); 5 Function Description text | ✅ | ✅ | ⚠️ 表格打散 | text chunk 覆盖但表格为纯文本，非 Markdown 表格（旧索引） |
| p10 | EEPROM configuration data area table; Function Description text | ❌ | ✅ | ⚠️ 表格打散 | text chunk 覆盖但表格为纯文本，非 Markdown 表格（旧索引） |
| p11 | Function Description text; 6.1 Absolute maximum rating table | ✅ | ✅ | ⚠️ 表格打散 | text chunk 覆盖但表格为纯文本，非 Markdown 表格（旧索引） |
| p12 | 6.2 Electrical Parameter table; 6.3 Sequence Parameter table; 7.1 USB to RS232 Converter schematic (CH340T) | ✅ | ✅ | ⚠️ 表格打散 | text chunk 覆盖但表格为纯文本，非 Markdown 表格（旧索引） |
| p13 | 7.1.2 USB to RS232 CH340B schematic; 7.2 USB to RS232 3-wire CH340T schematic; Application text | ✅ | ✅ | ✅ 完整 | image_description + text 均覆盖 |
| p14 | 7.3 Simplified USB to RS232 schematic (CH340T); 7.4 USB to Infrared Adapter schematic (CH340R); 7.5 USB to RS485 text | ✅ | ✅ | ✅ 完整 | image_description + text 均覆盖 |

## 发现的问题
- p5: 矢量电路图 page.get_images()=0，无 image_description；仅 text chunk 含 ASCII 打散内容
- p6: 矢量电路图 page.get_images()=0，无 image_description；仅 text chunk 含 ASCII 打散内容

## 关键结论
1. **图片/电路图覆盖**：p7/p8/p12/p13/p14 的嵌入图/电路图均有 image_description；但 p5/p6 的矢量电路图因 `page.get_images()=0` 被遗漏。
2. **表格完整性**：当前 ChromaDB 为旧索引（代码修改前生成），表格在 text chunk 中仍是以打散纯文本形式存在，未出现 Markdown 表格。需重新索引后才能验证 `find_tables()` 效果。
3. **页码标记异常**：大量 chunk 的 `page_start=1`，说明旧代码的页码解析在 restore placeholder 后丢失了 `<!-- PAGE:N -->` 标记，或 `parse_page_index` 回退到了首页。
4. **文本覆盖**：p1-p14 每个页面都有 text chunk 覆盖，无整页文字遗漏。

## 建议修复
1. **矢量电路图检测**：不能仅依赖 `page.get_images()`，需增加基于页面视觉内容或文本特征的图像描述触发条件（例如 section 标题含 'schematic'、'Configuration'、页面含大量大写元件标号如 C1/R1/U1 等）。
2. **重新索引 ch340g**：用当前代码重新生成 chunks，验证 find_tables() 是否产出 Markdown 表格、placeholder 保护是否防止表格断裂。
3. **页码标记修复**：检查 `_build_chunks()` 中 placeholder restore 后 `<!-- PAGE:N -->` 标记是否被正确保留并参与 `parse_page_index`。
