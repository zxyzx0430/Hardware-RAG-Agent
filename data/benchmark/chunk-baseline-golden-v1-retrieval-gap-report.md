# chunk-baseline-golden-v1 Retrieval Gap Report

- Total questions: **26**
- Overall relevant gaps: 0
- Overall source-page mismatches: 14
- Overall distance gaps (> 1.0): 1
- Has retrieval gap: **Yes**

## Per-PDF Statistics

| PDF | Questions | Types | Relevant Gaps | Source-Page Mismatches | Distance Gaps | Avg Top-1 Distance | Avg Top-3 Distance |
|-----|-----------|-------|---------------|------------------------|---------------|--------------------|--------------------|
| ch340g | 8 | fact_extraction=3, table_query=2, cross_page=1, image_description=2 | 0 | 5 | 0 | 0.7475 | 0.7958 |
| stm32f4 | 9 | fact_extraction=4, table_query=2, cross_page=2, image_description=1 | 0 | 6 | 1 | 0.8457 | 0.9362 |
| esp32 | 9 | fact_extraction=4, table_query=2, cross_page=2, image_description=1 | 0 | 3 | 0 | 0.8169 | 0.8528 |

## Question Details

### ch340g
- **ch340g-q001** (fact_extraction) top3_dist=[0.7351275086402893, 0.7735891342163086, 0.8237600922584534] → source_page_mismatch
- **ch340g-q002** (fact_extraction) top3_dist=[0.8967297077178955, 0.9422409534454346, 0.9490101337432861] → source_page_mismatch
- **ch340g-q003** (fact_extraction) top3_dist=[0.7638406753540039, 0.9179186224937439, 0.9387999176979065] → OK
- **ch340g-q004** (table_query) top3_dist=[0.6987937688827515, 0.8251832723617554, 0.8850814700126648] → OK
- **ch340g-q005** (table_query) top3_dist=[0.8018358945846558, 0.8222474455833435, 0.8436378240585327] → source_page_mismatch
- **ch340g-q006** (cross_page) top3_dist=[0.7041301727294922, 0.7239723205566406, 0.8227060437202454] → source_page_mismatch
- **ch340g-q007** (image_description) top3_dist=[0.6686404943466187, 0.6692814826965332, 0.6991133689880371] → OK
- **ch340g-q008** (image_description) top3_dist=[0.7110657691955566, 0.7279265522956848, 0.7548743486404419] → source_page_mismatch

### stm32f4
- **stm32f4-q001** (fact_extraction) top3_dist=[0.8401148319244385, 0.9453182816505432, 1.0071382522583008] → source_page_mismatch
- **stm32f4-q002** (fact_extraction) top3_dist=[0.8114360570907593, 0.8954522609710693, 0.9732780456542969] → source_page_mismatch
- **stm32f4-q003** (fact_extraction) top3_dist=[0.7414746284484863, 0.9505689144134521, 0.9900932908058167] → OK
- **stm32f4-q004** (fact_extraction) top3_dist=[0.918114185333252, 0.924089252948761, 1.0120949745178223] → source_page_mismatch
- **stm32f4-q005** (table_query) top3_dist=[0.8176153898239136, 0.916675865650177, 0.9516010880470276] → OK
- **stm32f4-q006** (table_query) top3_dist=[1.0729923248291016, 1.0995084047317505, 1.104266881942749] → source_page_mismatch, distance_gap
- **stm32f4-q007** (cross_page) top3_dist=[0.7820615768432617, 0.9153653979301453, 0.9910346865653992] → source_page_mismatch
- **stm32f4-q008** (cross_page) top3_dist=[0.742415726184845, 0.968075692653656, 1.0215682983398438] → source_page_mismatch
- **stm32f4-q009** (image_description) top3_dist=[0.8849190473556519, 0.9941412806510925, 1.005452036857605] → OK

### esp32
- **esp32-q001** (fact_extraction) top3_dist=[0.9408271312713623, 0.9445112347602844, 0.9787675142288208] → OK
- **esp32-q002** (fact_extraction) top3_dist=[0.8453903794288635, 0.96019446849823, 1.0119279623031616] → OK
- **esp32-q003** (fact_extraction) top3_dist=[0.7551250457763672, 0.7608025670051575, 0.7729341983795166] → source_page_mismatch
- **esp32-q004** (fact_extraction) top3_dist=[0.7946701645851135, 0.8625611066818237, 0.8777324557304382] → source_page_mismatch
- **esp32-q005** (table_query) top3_dist=[0.8013449311256409, 0.8443148732185364, 0.8477463722229004] → OK
- **esp32-q006** (table_query) top3_dist=[0.7140121459960938, 0.7730406522750854, 0.8194806575775146] → OK
- **esp32-q007** (cross_page) top3_dist=[0.6717215776443481, 0.6859636902809143, 0.7312422394752502] → OK
- **esp32-q008** (cross_page) top3_dist=[0.8923455476760864, 0.8945961594581604, 0.9877110719680786] → OK
- **esp32-q009** (image_description) top3_dist=[0.9364253282546997, 0.9373326301574707, 0.9826464653015137] → source_page_mismatch


## Notes

- **Relevant gap**: a marked `relevant_chunk` is not in the top-5 retrieved results.
- **Source-page mismatch**: none of the top-3 retrieved chunks overlap with the manually annotated `source_pages`. This can happen when chunk boundaries cross pages or when page annotations follow the original PDF rather than the chunk's `page_range`.
- **Distance gap**: top-1 retrieval distance exceeds the threshold; indicates lower semantic confidence for that query.