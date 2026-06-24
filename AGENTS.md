# AGENTS.md - Hardware RAG Agent

> Last reviewed: 2026-06-21

## Project Overview

Hardware RAG Agent 鈥?闈㈠悜宓屽叆寮忓紑鍙戣€呯殑纭欢鐭ヨ瘑搴?AI Agent銆?鐢ㄦ埛鑷厤 API Key + 鑷€夋ā鍨嬶紝鍩轰簬瀹樻柟鑺墖鎵嬪唽鍋?RAG 妫€绱紝鍥炵瓟纭欢鍙傛暟/鎺ョ嚎鏂规銆佺敓鎴愰┍鍔ㄤ唬鐮併€佸鏌ヤ唬鐮侀棶棰樸€?
## Deploy Model

璇ラ」鐩槸**鏈湴鑷儴缃查」鐩紙self-hosted锛?*锛岀敤鎴蜂笅杞藉悗鍦ㄨ嚜宸辩殑鐢佃剳涓婅繍琛屻€傚凡璁″垝寮€婧愬埌 GitHub銆?
### 瀹夊叏绔嬪満

- **闇€瑕侀槻鎶ょ殑**锛氭湰鍦伴闄╋紙XSS銆丄PI Key 娉勯湶銆佹枃浠舵敞鍏ャ€丼QL 娉ㄥ叆锛夆€斺€?鐢ㄦ埛娴忚鍣ㄦ墿灞曞彲鑳界獌鍙栧嚟璇侊紝鎭舵剰鏂囨。鍙兘鍖呭惈鑴氭湰
- **涓嶉渶瑕佺鐨?*锛氱綉缁滄敾鍑伙紙DDoS銆丆SRF銆丠TTPS銆丆ORS 纭寲銆佹毚鍔涚牬瑙ｃ€佽姹傞鐜囬檺鍒讹級鈥斺€?鏈嶅姟鍙洃鍚?127.0.0.1锛屼笉鏆撮湶鍒板叕缃?- **鎬ц兘鏂归潰**锛氫笉闇€瑕侀珮骞跺彂浼樺寲銆佷笉闇€瑕佸垎甯冨紡缂撳瓨銆佷笉闇€瑕?CDN

---

## Start Here锛堟柊绾跨▼寮€宸ュ厛璇伙級

棣栨杩涘叆椤圭洰鐨勭嚎绋嬶紝鎸夎繖涓『搴忚锛?
| 椤哄簭 | 鍋氫粈涔?| 涓轰粈涔?|
|------|--------|--------|
| 1 | plur inject 浠诲姟鎻忚堪 --fast --json | 璇?PLUR 闀挎湡璁板繂 |
| 2 | 璇?docs/completed.md | 鐭ラ亾鍝簺鍔熻兘宸茬粡鍋氫簡 |
| 3 | 璇?docs/pitfalls.md | 鐭ラ亾鍓嶄汉韪╄繃浠€涔堝潙 |
| 4 | 璇?docs/todos/XX-name.md锛堣嚜宸辩殑 TODO锛?| 鐭ラ亾鑷繁瑕佸仛浠€涔?|
| 5 | 鍙 TODO 鍓?2 椤癸紝寮€濮嬪共娲?| 鑱氱劍锛屼笉瑕佷竴娆℃€ф墰澶 |

---

## PLUR 瑙勫垯

姣忔瀵硅瘽寮€濮嬪繀椤诲厛璇?PLUR锛?
```
plur inject "褰撳墠浠诲姟鎻忚堪" --fast --json
```

閲嶈鍐崇瓥蹇呴』鍏堝啓鍏?PLUR 鍐嶆墽琛屻€?
---

## 韪╁潙鏂囦欢浣跨敤瑙勫垯

- **闂瀹氫綅闃舵**锛氬厛鑷繁鍒嗘瀽锛屽崱浣忎簡鍐嶇炕 pitfalls.md 鐪嬫湁娌℃湁鍓嶄汉韪╄繃
- **淇闃舵**锛氱‘瀹氭牴鍥犲悗锛岀炕 pitfalls.md 鐨?涓嬫娉ㄦ剰"鐪嬫湁娌℃湁鍚岀被鍨嬫暀璁?- **淇瀹屾垚**锛氭棤閲嶅鍒欒拷鍔犲埌 pitfalls.md

---

## Role Division

Codex 鍜?Trae 鏉冮檺瀵圭瓑锛屽潎鍙鍐欎唬鐮佸拰鏂囨。銆傚敮涓€鍖哄埆锛?
| 瑙掕壊 | 璐熻矗 |
|------|------|
| **Codex (00-control)** | 鏂瑰悜鎶婃帶銆佹帴鍙ｅ绾︽渶缁堣鍐炽€丳LUR 闀挎湡璁板繂缁存姢銆佽法绾跨▼鍐茬獊鍗忚皟銆丳R 瀹℃牳涓庡悎骞?|
| **Trae** | 浠ヤ笂闄?00-control 涔嬪鐨勬墍鏈夊伐浣?|

鍙屾柟鍏卞悓缁存姢锛氬叏閮ㄦ枃妗ｅ拰鍏ㄩ儴浠ｇ爜銆?
---

## TODO 娓呭崟绯荤粺

姣忎釜绾跨▼鍦?docs/todos/ 涓嬫湁涓€涓嫭绔嬬殑 TODO 鏂囦欢锛?1-app ~ 08-infra锛夛紝鎵€鏈夌嚎绋嬪叡鍚岀淮鎶ゃ€?
### 宸ヤ綔娴?
1. **00-control 鎴栦换鎰忕嚎绋嬪彂鐜伴棶棰?* 鈫?鍏堟洿鏂板搴旂嚎绋嬬殑 TODO 鏂囦欢锛屽啀鍔ㄦ墜鍋?2. **浣犵洿鎺ョ粰绾跨▼涓嬪懡浠?* 鈫?绾跨▼鍏堟洿鏂?TODO锛堝姞鍦ㄦ渶鍓嶉潰锛夛紝鍐嶅仛銆備慨瀹岄€氱煡 00-control
3. **绾跨▼寮€宸?* 鈫?鐩存帴缂栬緫 docs/todos/XX-name.md锛屽仛瀹屼竴椤规妸 [ ] 鏀逛负 [x]
4. **鎵ц涓彂鐜版柊闂** 鈫?杩藉姞鍒拌 TODO 鏂囦欢**鏈€鍓嶉潰**锛堝€掑簭鎺掑垪锛屾渶鏂扮殑鍦ㄦ渶涓婏級
5. **璺宠繃浠诲姟** 鈫?鏀逛负 [-] 骞跺啓鏄庣悊鐢?6. **闇€纭** 鈫?鏀逛负 [?] 骞跺啓鏄庣枒闂紝绛?00-control 鍥炲
7. **鍏ㄩ儴瀹屾垚** 鈫?閫氱煡 00-control 瀹℃煡
8. **00-control 瀹℃煡閫氳繃** 鈫?鍒犻櫎璇?TODO 鏂囦欢锛屾洿鏂?docs/completed.md

### 瀹屾垚璇存槑

姣忛」瀹屾垚鏃讹紝鍦?[x] 鍚庨潰鍐欎竴鍙ヨ鏄庡仛浜嗕粈涔堬細

```
- [x] 鎷嗗垎 routes.py
      鉁?routes.py 鈫?5 涓枃浠讹紙chat_routes.py / kb_routes.py / hardware_routes.py / build_routes.py / tool_routes.py锛夛紝涓昏矾鐢辨敞鍐屽凡鏇存柊锛宎pi-contract.md 鍚屾
```

### 鑱氱劍瑙勫垯

绾跨▼姣忔鍚姩**鍙鍓?2 椤?*锛堟渶涓婇潰鐨?2 鏉★級锛屽仛瀹屽啀鐪嬪悗闈㈢殑銆傞槻姝竴娆℃墰澶銆?
### 00-control 瀹℃煡娓呭崟

瀹℃煡 TODO 鏃舵鏌ワ細
1. 鉁?姣忛」 [x] 閮芥湁瀹屾垚璇存槑锛岃兘鍒ゆ柇鍋氫簡浠€涔?2. 鉁?鏀逛簡鍝簺鏂囦欢鍐欐竻妤氫簡锛堝 routes.py銆乪ndpoints.ts锛?3. 鉁?淇?bug 鐨勯」鏈夋病鏈夊悓姝ヨ pitfalls.md

---

### 鏍囪鍚箟

| 鏍囪 | 鍚箟 |
|------|------|
| [ ] | 寰呭仛 |
| [x] | 宸插畬鎴愶紙鍐欏畬鎴愯鏄庯級 |
| [-] | 璺宠繃锛堝啓鏄庡師鍥狅級 |
| [?] | 闇€纭锛堝啓鏄庣枒闂級 |

### 褰撳墠 TODO 娓呭崟

| 鏂囦欢 | 鍔熻兘 | 鐘舵€?|
|------|------|------|
| docs/todos/01-app.md | 甯冨眬/瀵艰埅/涓婚 | 寰呭紑宸?|
| docs/todos/02-chat.md | 鎷嗗垎 routes.py + 淇 SSE | 杩涜涓?|
| docs/todos/03-knowledge.md | 鐭ヨ瘑搴?RAG | 寰呭紑宸?|
| docs/todos/04-session.md | 鎸佷箙鍖?璁剧疆/浼氳瘽 | 寰呭紑宸?|
| docs/todos/05-agent.md | LangGraph Agent | 寰呭紑宸?|
| docs/todos/06-sandbox.md | 娌欑鎵ц | 寰呭紑宸?|
| docs/todos/07-hardware.md | 淇 monitor 璺緞 + stub 宸ュ叿 | 杩涜涓?|
| docs/todos/08-infra.md | 鍒涘缓 README.md | 杩涜涓?|

---

## Docs Map锛堟枃妗ｇ洰褰曪級

鎵€鏈夋枃妗ｅ湪 docs/ 涓嬶紝鎸夌敤閫斿垎缁勶細

### 寮€宸ュ繀璇?
| 鏂囦欢 | 鍐呭 |
|------|------|
| docs/completed.md | 椤圭洰瀹屾垚璁板綍锛堝悇绾跨▼鍋氫簡鍟ャ€佺己鍟ャ€佸凡鐭ラ棶棰橈級 |
| docs/pitfalls.md | 韪╁潙鍞竴鏉ユ簮锛屼慨 bug 蹇呰拷鍔?|
| docs/api-contract.md | 鎺ュ彛濂戠害锛屾敼鎺ュ彛鍏堟敼杩欓噷 |
| docs/thread-map.md | 绾跨▼褰掑睘涓庤寖鍥?|

### 浠诲姟鍗忚皟

| 鏂囦欢 | 鍐呭 |
|------|------|
| docs/todos/*.md | 鍚勭嚎绋?TODO 娓呭崟锛?1-app ~ 08-infra锛?|
| docs/threads/*.md | 鍚勭嚎绋嬭亴璐ｈ鎯?|
| docs/handoff/*.md | 绾跨▼浜ゆ帴鏂囨。 + 妯℃澘 |
| docs/issue-tracker.md | 宸茬煡闂杩借釜 |

### 瑙勫垝涓庡弬鑰?
| 鏂囦欢 | 鍐呭 |
|------|------|
| docs/plans/roadmap.md | 路线图（含 V1-V3 详细计划 + 架构 + 使用指南） |
| docs/plans/rag-guide.md | RAG 入门与实践指南（含分块策略分析） |
| docs/plans/agent-engineering-guide.md | Agent 实现指南 |
| docs/workflow-trae-codex.md | Codex × Trae 配合流程 |

### 归档
| 鏂囦欢 | 鍐呭 |
|------|------|
| docs/archive/plans/*.md | 旧版 V1-V3 详细计划、架构、使用指南（合并前的原始版） |
| docs/archive/feature-gap-analysis.md | 旧版功能缺口分析 |
| docs/archive/dev-status/*.md | 旧版开发进度状态 |
| docs/review-frontend.md | 前端代码审查 |
| docs/review-backend.md | 后端代码审查 |
| docs/review-result.md | 审查汇总 |
| docs/feature-gap-revised.md | 功能缺口修订版 |
| docs/archive/Week 1代码导读.md | 旧版代码导读（merged into roadmap.md） |
| docs/git-cheatsheet.md | Git 操作备忘 |

---

## Thread Overview

| 绾跨▼ | 鑱岃矗 | 璇︽儏 |
|------|------|------|
| 00-control | 涓绘帶/濂戠害/PLUR | docs/threads/00-control.md |
| 01-app | 甯冨眬/瀵艰埅/涓婚 | docs/threads/01-app.md |
| 02-chat | SSE 娴佸紡鑱婂ぉ | docs/threads/02-chat.md |
| 03-knowledge | 鐭ヨ瘑搴?RAG | docs/threads/03-knowledge.md |
| 04-session | 鎸佷箙鍖?璁剧疆 | docs/threads/04-session.md |
| 05-agent | LangGraph Agent | docs/threads/05-agent.md |
| 06-sandbox | 娌欑鎵ц | docs/threads/06-sandbox.md |
| 07-hardware | 纭欢宸ヤ綔鍙?| docs/threads/07-hardware.md |
| 08-infra | Docker/CI/鏃ュ織 | docs/threads/08-infra.md |

---

## Development

鍓嶇璁块棶 http://127.0.0.1:5173锛孷ite 鑷姩鎶?/api/* 浠ｇ悊鍒板悗绔€?
```powershell
# 鍚庣
cd E:\Desktop\agent\backend
python main.py --web --port 58080

# 鍓嶇
cd E:\Desktop\agent\frontend
npx vite --port 5173
```

## Project Structure

```
agent/
鈹溾攢鈹€ backend/       FastAPI + LangChain + ChromaDB
鈹溾攢鈹€ frontend/      React + TypeScript + Vite + Tailwind + Zustand
鈹溾攢鈹€ scripts/       寮€鍙戣緟鍔╄剼鏈?鈹溾攢鈹€ data/          鐭ヨ瘑搴?PDF + 鍚戦噺鏁版嵁搴?鈹溾攢鈹€ docs/
鈹?  鈹溾攢鈹€ 寮€宸ュ繀璇?  completed.md / pitfalls.md / api-contract.md / thread-map.md
鈹?  鈹溾攢鈹€ 浠诲姟鍗忚皟   todos/ / threads/ / handoff/ / issue-tracker.md
鈹?  鈹溾攢鈹€ 瑙勫垝鍙傝€?  docs/plans/roadmap.md / docs/plans/rag-guide.md / plans/agent-engineering-guide.md / workflow-trae-codex.md
鈹?  鈹溾攢鈹€ 褰掓。       archive/ / review-*.md / feature-gap-revised.md / git-cheatsheet.md
鈹?  鈹斺攢鈹€ 鏈枃浠?    AGENTS.md
鈹斺攢鈹€ .gitignore
```

---

## Commit 瑙勮寖

姣忔鎻愪氦鐢?conventional commits 鏍煎紡锛岀姝㈢敤銆寁0.1銆嶃€寀pdate銆嶃€宖ix bug銆嶈繖绉嶉€氱敤璇存槑銆?
### 鏍煎紡

```
<type>(<scope>): <涓€鍙ヨ瘽璇存竻妤氭敼浜嗕粈涔?
```

### Type 瀵圭収

| type | 浠€涔堟椂鍊欑敤 |
|------|-----------|
| feat | 鏂板姛鑳?|
| fix | 淇?bug |
| docs | 鏂囨。 |
| refactor | 閲嶆瀯锛屼笉鏀瑰彉琛屼负 |
| style | 浠ｇ爜鏍煎紡锛屼笉鏀瑰彉閫昏緫 |
| build | 鏋勫缓/渚濊禆/閰嶇疆 |

### Scope 瀵圭収

| scope | 瀵瑰簲 |
|-------|------|
| frontend | 鍓嶇 React 浠ｇ爜 |
| backend | 鍚庣 Python 浠ｇ爜 |
| docs | 鏂囨。 |
| scripts | 宸ュ叿鑴氭湰 |
| build | 椤圭洰閰嶇疆 |

### 绀轰緥

```
feat(backend): /api/models 浠ｇ悊涓婃父妯″瀷鍒楄〃
fix(frontend): SSE 鏂繛鍚庝笉鑷姩閲嶈繛
docs: 琛ュ厖 api-contract.md 閿欒鐮佽〃
refactor(backend): 鎷嗗垎 routes.py 鍒扮嫭绔嬫ā鍧?```

---

## 娌熼€氬師鍒欙紙閲嶈锛?
### 鐞嗚В闇€姹傜殑鏂瑰紡

鐢ㄦ埛涓嶆槸鎶€鏈汉鍛橈紝鎻忚堪闇€姹傚彲鑳戒笉绮剧‘銆佷笉瀹屾暣銆侀潪鎶€鏈敤璇€傝亴璐ｆ槸鍏堢悊瑙ｆ剰鍥撅紝鍐嶇炕璇戞垚鏂规銆?
- **鍏堢悊瑙ｏ紝鍐嶈鍔?*锛氭敹鍒伴渶姹傚悗锛屽厛鐢ㄥぇ鐧借瘽澶嶈堪涓€閬嶆垜鐨勭悊瑙ｏ紝纭瀵逛簡鍐嶅姩鎵?- **涓嶉棶鎶€鏈棶棰?*锛氫笉闂?鐢ㄦ病鐢ㄨ繃 Git"銆?鎳備笉鎳?SQL"杩欑被闂
- **缁欓€夋嫨锛屼笉缁欓粦鐩?*锛氭柟妗堢被鐨勫喅瀹氾紝鍒楁垚绠€鍗曠殑閫夐」璁╃敤鎴烽€?- **杩涘害閫忔槑**锛氶暱鏃堕棿浠诲姟姣忓仛瀹屼竴姝ヨ涓€澹?- **涓诲姩鍙戠幇**锛氫富鍔ㄥ彂鐜颁复鏃舵枃浠躲€?gitignore 缂烘紡銆佽鍒犵殑鏃т唬鐮侊紝鍒楀嚭鏉ヨ鐢ㄦ埛鍐冲畾
- **瀹瑰繊妯＄硦**锛氱敤鎴疯"鍘?GitHub 鐪嬬湅"鎰忔€濇槸"甯垜鎵炬湁鐢ㄧ殑宸ュ叿"銆傚厛鐚滃啀纭锛屼笉瑕佽鐢ㄦ埛琛ュ厖鎶€鏈粏鑺?
---

## 闃呰鎸囧崡

涓嶅悓瑙掕壊鍏虫敞涓嶅悓鍐呭锛屼笉蹇呬粠澶磋鍒板熬锛?
| 瑙掕壊 | 蹇呰 | 鍙傝€?|
|------|------|------|
| **00-control** | Start Here / PLUR 瑙勫垯 / Role Division / TODO 宸ヤ綔娴?| Docs Map / Thread Overview |
| **01-08 绾跨▼** | Start Here / TODO 娓呭崟绯荤粺 / 韪╁潙瑙勫垯 / Development | Commit 瑙勮寖 / Docs Map |
| **Trae** | 鍏ㄩ儴 | 鈥?|

---

## 缁存姢瑙﹀彂鏉′欢

浠ヤ笅鎯呭喌鍑虹幇鏃讹紝蹇呴』鏇存柊 AGENTS.md锛?
- **鏂板/鍒犻櫎浜嗕竴涓?thread** 鈫?鏇存柊 Thread Overview 鍜?TODO 娓呭崟琛?- **鏂板浜?docs/ 椤跺眰鍐呭** 鈫?鏇存柊 Docs Map 鍜?Project Structure
- **鍒犻櫎 doc 鍓?* 鈫?鍏堟悳 AGENTS.md 鏈夋病鏈夊紩鐢ㄥ畠锛屾湁鍒欐洿鏂?- **椤圭洰杩涘叆鏂伴樁娈碉紙V1鈫扸2鈫扸3锛?* 鈫?鏇存柊闃舵鐩稿叧瑙勫垯
- **pitfalls.md 涓悓涓€妯″紡閲嶅 鈮? 娆?* 鈫?鑰冭檻鍗囨牸涓?AGENTS.md 瑙勫垯
- **TODO 鐘舵€佽〃** 鈫?姣忚疆寮€宸ュ墠鎵嬪姩鍚屾锛岀‘淇濆拰瀹為檯涓€鑷?
---

## AGENTS.md 缁存姢瑙勫垯

- 姣忔淇敼鍚庡繀椤绘洿鏂?Changelog
- 闈?00-control 淇敼鍓嶉』閫氱煡 00-control
- Last reviewed 鏃ユ湡姣忔 review 鍚庢洿鏂?
---

## Changelog

| 鏃ユ湡 | 鏀逛簡浠€涔?| 璋佹敼鐨?|
|------|---------|--------|
| 2026-06-23 | 文档整合：6 个规划文件 → roadmap.md，2 个 RAG 文件 → rag-guide.md，归档旧 plans/、dev-status/、feature-gap-analysis.md | 00-control |
| 2026-06-21 | 初始结构化：新增 Start Here / Docs Map / TODO 工作流 / 阅读指南 / 维护规则 | 00-control |

---

### Changelog 淇壀

淇濇寔鏈€杩?20 鏉°€傝秴鍑烘椂鍒犻櫎鏈€鏃х殑涓€鍗娿€?姣忎釜 V 闃舵缁撴潫鏃舵竻绌?Changelog锛屾敼涓轰竴鏉℃€荤粨銆?
---

## Language

- 鍥炲榛樿涓枃
- 浠ｇ爜鍜屾敞閲婄敤鑻辨枃



