# BaggageGO · 預測空間，檢查行李，自信登機 · Predict the space. Check the bag. Board with confidence.

[English](#english) · [中文](#中文)

**Team** Poby · Samuel · Davis · Thomas · Eddie
**Event** Cathay Hackathon — **Wildcard track** · finals pitch: 5 minutes (3 min idea + 2 min tech)
**Livery** HK Express (UO) — the brand we build against. BaggageGO is a student concept, not an affiliate.

> BaggageGO is not a measuring tool. It is a **personalised decision engine** that
> balances **on-time performance (OTP)** against **ancillary revenue**. A passenger
> scans the QR on their boarding pass, answers one question — *which bags are you
> bringing?* — and the same computation returns to them as a sentence: **which overhead
> bin their bag should go in**, while the room watches the cabin fill up on the wall.
>
> **This repository is the demo frontend for the BaggageGO API** — the three surfaces a
> passenger, a presenter and an operator actually touch, plus the small demo server that
> makes them real. It is not a mockup.

---

## English

### The idea

**The blind spot.** A low-cost carrier's profit lives in ancillary revenue, yet the
airline knows only which fare a passenger bought — not what they actually carry on.
Without that behavioural data the only lever left is reactive: catch oversized bags at
the gate, which drags on OTP and misses the most valuable upsell window of the journey.

**The decision engine.** BaggageGO predicts how full the overhead bins will be *before*
boarding, and turns that one prediction into two things at once:

- a **stow instruction** for every passenger — which bin their bag belongs in, so the
  aisle stays clear and boarding turns stay short (OTP);
- a **timed upsell** — when bins look tight, a personalised push 24–48 h before departure
  offers high-risk passengers a discounted checked-bag upgrade; when space is plentiful,
  low-fare passengers are offered priority boarding instead (revenue).

The timing is the whole trick. A traditional LCC upsells at booking, when the passenger
has not packed yet. BaggageGO upsells 24–48 h out, while they are staring at an
overpacked case — the highest-converting moment there is. On the pitch slide this is a
slider between guaranteed OTP and maximum revenue; BaggageGO holds both ends.

**The Wildcard bet.** As a Wildcard entry we are not only optimising boarding — we are
opening a new business pillar. When a bag is oversized, one tap links to city logistics
(*collected at your Hong Kong home, delivered to your Tokyo hotel*); passengers who
travel bag-free earn **Lifestyle Points**. BaggageGO reaches from the airport into the
living room.

### Privacy by design

*"We deliver hyper-personalisation with zero privacy compromise."* Three lines of defence:

1. **On-device AR** — the AR pre-check runs entirely on the phone; no baggage or
   background image is ever uploaded or stored.
2. **Minimal transmission** — only three numbers leave the device: length, width, height,
   plus the item count.
3. **Aggregated prediction** — the capacity engine works on flight-level anonymised
   aggregate data and cannot re-identify anyone; designed to satisfy the PDPO.

### What this repository is

This repo is the **demo frontend for the BaggageGO API**. It is self-contained: a small
FastAPI demo server ships with it so the frontend is genuinely end-to-end rather than a
click-through, and the whole thing runs in one container over one SQLite file.

| Surface | Route | Who | What it does |
|---|---|---|---|
| **Passenger card** | `/bggo/<CODE>` | the audience, via the printed QR | the AR pre-check's stand-in: pick your bags → get your bin, on your own copy of the cabin |
| **Live board** | `/bggo/live` | the presenter, on the projector | manifest **and** cabin heatmap that light up as tickets are scanned, with a boarding log |
| **Operator console** | `/bggo/controls?k=…` | whoever runs the room | check in/out, fix a bag, pre-board a list, snapshot the whole board |

**What it deliberately is not.** The demo reproduces the *decision* — units, zones, the
colour-coded capacity map, the stow sentence — not the product's data plane. There is no
AR capture, no Cathay booking API, no live flight or historical feed, no push-notification
scheduler, no city-logistics hand-off and no payment. Those are the pitch; the demo is the
frontend that makes the decision engine legible and playable.

#### How the demo maps to the product

| The product (the pitch) | This demo |
|---|---|
| On-device AR pre-check in the UO app measures the bag | a tap-through bag picker on the passenger card — no camera |
| Bags priced in **Bin Space Units** | the same four bags at the same unit costs |
| Constraint-based engine forecasts front / mid / rear cabin load | `stow_plan()` + zone capacity, recomputed on every scan |
| Cloud cabin heatmap | the live board's colour-coded zones |
| Personalised stow / upsell push 24–48 h before departure | the card's on-the-spot stow sentence — the same prediction, delivered at the gate instead |
| Cathay booking API + historical flight data | a seeded SQLite database of 240 HK Express passengers |

### The engine the demo makes visible

**Units.** Bags are costed in abstract units of bin space, never centimetres:

| Bag | Units | Why that number |
|---|---|---|
| Handbag | `0.25` | gives most of a bin back |
| Backpack | `0.5` | narrow, squashable |
| **Duffel bag** | `0.75` | **below** the rigid case — softness gives space back |
| Small trolley case | `1.0` | rigid: it takes exactly what it measures |

The odd-looking pair in the middle is the honest one, and it *is* the argument for costing
bags in units rather than in centimetres.

**Zones.** Both walls are split into four-row zones named for the rows they hang over —
`Row 1-4L`, `Row 5-8R`, `Row 17-20L`. Each zone holds **9 units** (two real compartments,
in the picker's own units).

**The rule, in one line.**

> Every declared bag goes in the zone directly above its owner's seat, on its owner's
> wall — until that zone cannot take it, and then the card sends it away.

Bags are placed in **arrival order, first come, first served**, so a later bag can never
displace one already stowed; if the zone above a new passenger is full, the **new** bag is
the one rerouted.

**The line.** At `STOW_AWAY_UNITS = 8.0` of a zone's 9.0 the advice stops pointing at the
zone — and the board's red begins on the same line by decision, so **a red zone is exactly
one the card sends you past**.

| Zone load | Colour | Reads as |
|---|---|---|
| 0 – 2 units | green | mostly empty |
| 2 – 8 units | yellow | filling |
| 8 units and over | red | full — the card sends you past it |

**Where a redirected bag goes** — the order a person would actually walk it: opposite you
in the same rows, then in front on the same wall, then opposite and in front, then behind
on the same wall (last).

**One computation.** The card's sentence and the board's colour both come from
`stow_plan()`, so they cannot disagree: the ticket quotes the very load the map is
colouring.

**What the passenger reads.** A verb, not arithmetic: *Stow your bag in the zone above your
seat*, or *The zone above your seat is full, please use the bin across the aisle.* The card
carries **no unit figures at all** — counts and a verb. The board keeps every figure,
because the projector is where the arithmetic is actionable.

### Try it

| | |
|---|---|
| **Live board** | `<!--repo-base-->https://sophon1.dedyn.io<!--/repo-base-->/bggo/live` |
| Passenger card | the same host + `/bggo/<CODE>` — all 240 codes in [`tickets.md`](tickets.md) |
| Google Cloud instance | `http://35.234.41.164/bggo/live` — a GCP VM with its own board state (see [`deploy/gcp/README.md`](deploy/gcp/README.md)) |
| Locally, one command | `docker compose up` → http://localhost:8091/bggo/live |

*The live-board link is generated from a `repo-base` marker: a copy of the repo rewrites
it to its own deployment via [`deploy/publish.sh`](deploy/publish.sh). The `<CODE>` is
host-independent, so the same boarding pass opens on any host running the app.*

### Run it

```bash
docker compose up                        # the whole demo: board, cards, API
docker compose run --rm demo pytest -q   # 247 tests, inside the image
docker compose up -d                     # how it actually runs
BGGO_HOST_PORT=8095 docker compose up    # beside another process on 8091
docker compose down                      # the bggo-data volume keeps the board
docker compose down -v                   # …and this throws it away
```

No Docker? `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, then
`.venv/bin/uvicorn server_v1:app --host 127.0.0.1 --port 8091`.

- **The volume is the board.** `passengers.db` ships in the image as a *seed*;
  `docker/entrypoint.sh` copies it to `/data/passengers.db` on first start and the app
  writes there. Scans survive `restart`/`down`/`up`; only `down -v` returns to the
  shipped state.
- **Loopback only** by default (`127.0.0.1:8091`), so the demo is never published to the
  LAN or WAN; a reverse proxy on the same host reaches it over loopback.
  `BGGO_HOST_BIND=0.0.0.0` is the opt-in.
- **Runs as uid 10001**, and the db directory must stay writable: SQLite runs in WAL mode,
  so `-wal`/`-shm` land beside the file.
- **The server binds `0.0.0.0` inside the container**, and the port resolves
  `$PORT` → `$BGGO_PORT` → `8091`, so the same image runs unchanged on Cloud Run.
- **In the working tree all 247 tests pass.** In a standalone checkout 12 skip — the
  vendored-kit and boarding-pass guards reporting a missing sibling directory, not a gap.

### API

| Route | Who | Notes |
|---|---|---|
| `GET /bggo/<CODE>` | audience, via the QR | server-rendered; opens the bag picker |
| `POST /bggo/<CODE>/bag` | the picker, form-encoded | `bag-<name>=<count>`; 303 back to the card; works with JS off |
| `GET /bggo/live` | presenter | **open, no token**; `?view=list\|seats`, `?big=1` for theatre mode |
| `POST /bggo/api/scan` | the card (JS) | `{code, bags:{"bag-handbag":2}}` records the check-in |
| `GET /bggo/api/state` | the board | rows + seats + bins + log + counters, polled every second |
| `POST /bggo/api/reset` | the board's Reset | needs `X-Board-Token`; clears every scan **and every bag** |
| `GET /bggo/controls?k=…` | the operator | 403 without the control token |
| `POST /bggo/api/ctl/…` | the console | needs `X-Control-Token`: check in/out, bags, bulk actions |
| `GET/POST /bggo/api/ctl/export\|import` | the console | JSON snapshot out / in |
| `GET /bggo/` | → redirects to the board | |

The board is **open by design**; `Reset` sending `X-Board-Token` is CSRF protection, not a
login. `/api/state` carries `passengers`, `seats`, `seat_rows`, `bins`, `log` and
`counters`. `seat_map()`, `bin_loads()` and `stow_plan()` are the single source for both
the server-rendered first paint and that payload, so the projector and the poll cannot
disagree.

### Repository map

```
server_v1.py              FastAPI app — board, passenger card, scan API, controls, cabin model
static/                   style.css, app.js, board.js, control.css/js — no build step
static/icons/             the four bag glyphs the picker draws
seed_passengers.py        the data script — 240 passengers, seats front to back
passenger_codes.txt       THE LOCK: 240 ticket codes, append-only, never regenerate
passengers.db             seed database — passengers + scans + seed state
tickets.md                every passenger as a clickable link (generated)
make_ticket_links.py      regenerates tickets.md against any base host
make_flight_snapshot.py   deterministic 150-of-240 snapshot for rehearsal import
brand/                    vendored brand kit: brand.css, Montserrat (+OFL), the marks
Dockerfile                the image — this folder is the whole build context
docker-compose.yml        the local stack (loopback only)
docker/entrypoint.sh      seeds the data volume, then runs uvicorn on 0.0.0.0
deploy/gcp/               Cloud deployment: VM + Caddy, Cloud Run, runbooks, backups, sync
docs/img/                 the screenshots used by the pitch surfaces
test_server_v1.py         the suite, against a temp copy of the db
test_brand_vendored.py    fails when ./brand/ drifts from the kit
test_make_ticket_links.py fails when tickets.md would claim to be the printed payload
STAGE1_PLAN.md            the original plan and decision log
STAGE2_SEATMAP_PLAN.md    the seat-map stage plan
```

### Licence

Code: hackathon project, no licence granted for reuse. Font: **Montserrat**, SIL Open Font
License 1.1 — see `brand/fonts/OFL.txt`. HK Express is a trademark of its owner; this is a
student concept demo built against their public brand assets, not an affiliation.

---

## 中文

### 理念

**盲點。** 廉航的利潤來自輔助收入，但航空公司只知道乘客買了哪種票價，不知道他們實際上帶了什麼行李。缺乏這種「行為數據」，剩下的槓桿只有被動：在登機閘口攔下超標行李——拖垮準點率 (OTP)，也錯失整趟旅程中轉換率最高的追加銷售 (Upsell) 時機。

**決策引擎。** BaggageGO 在登機**之前**預測行李架的擁擠程度，並把同一個預測同時變成兩件事：

- 給每位乘客的**置物指令**——行李該放哪個行李架，讓走道保持暢通、登機更快 (OTP)；
- **有時差的 Upsell**——行李架吃緊時，在出發前 24–48 小時向高風險乘客推送個人化優惠，折扣升級寄艙；空間充足時，則向低票價乘客推銷優先登機 (收益)。

時差就是關鍵。傳統廉航在買票時推銷，那時乘客根本還沒執行李；BaggageGO 在出發前 24–48 小時推銷，那時他們正瞪著塞爆的行李箱——轉換率最高的時刻。在台上這是一條滑桿：向左是保證 OTP，向右是極大化收益；BaggageGO 兩端都握住。

**Wildcard 的賭注。** 作為 Wildcard 項目，我們不只在優化登機，而是開創全新業務支柱。行李超標時，一鍵連結市區物流（*香港家中收取，直送東京酒店*）；全程零行李的乘客可賺取 **Lifestyle Points**。BaggageGO 把航空公司的觸角從機場延伸到客廳。

### 私隱優先的設計

*「我們提供極致個人化，但絕不妥協私隱。」* 三大防線：

1. **裝置內 AR**——AR 預檢完全在手機本地端處理，行李與背景影像永不上傳、永不儲存。
2. **最小化傳輸**——只有三個數字離開裝置：長、寬、高，加上件數。
3. **匿名聚合預測**——容量引擎使用航班層級的匿名聚合數據，無法反向追蹤任何個人；設計符合 PDPO。

### 這個倉庫是什麼

本倉庫是 **BaggageGO API 的演示前端**。它自成一體：附帶一個小型 FastAPI 演示伺服器，讓前端真正端到端運作而非點擊流程；整個系統只需要一個容器與一個 SQLite 檔案。

| 介面 | 路由 | 誰用 | 做什麼 |
|---|---|---|---|
| **乘客卡片** | `/bggo/<CODE>` | 觀眾，掃描紙本 QR | AR 預檢的替身：選擇行李 → 取得行李架指示，並在自己那份機艙圖上看到 |
| **實時登機板** | `/bggo/live` | 主講者，投影機 | 名冊**與**機艙熱力圖，隨掃碼亮起，角落有登機日誌 |
| **操作員控制台** | `/bggo/controls?k=…` | 控場的人 | 報到／取消、改行李、預登機、快照整塊板 |

**它刻意不是什麼。** 演示重現的是**決策**——單位、分區、顏色容量圖、置物句子——而不是產品的數據管道。這裡沒有 AR 擷取、沒有 Cathay 訂位 API、沒有即時航班或歷史數據、沒有推送排程、沒有市區物流、也沒有付款。那些是產品願景；演示是讓決策引擎變得可讀、可玩的前端。

#### 演示如何對應產品

| 產品（Pitch） | 這個演示 |
|---|---|
| UO App 的裝置內 AR 預檢量度行李 | 乘客卡片上的點按式行李選擇器——沒有鏡頭 |
| 以 **Bin Space Units** 為行李計價 | 同樣四種行李、同樣的單位成本 |
| 約束式引擎預測前／中／後艙負載 | `stow_plan()` 加分區容量，每次掃碼重算 |
| 雲端機艙熱力圖 | 實時登機板的顏色分區 |
| 出發前 24–48 小時的個人化置物／Upsell 推送 | 卡片上即時的置物句子——同一個預測，改在閘口送達 |
| Cathay 訂位 API 與歷史航班數據 | 內建 240 位 HK Express 乘客的種子 SQLite 資料庫 |

### 演示呈現的引擎

**單位。** 行李以抽象的「空間單位」(Bin Space Units) 計價，從來不用厘米：

| 行李 | 單位 | 為何是這個數 |
|---|---|---|
| 手袋 | `0.25` | 還給行李架大部分空間 |
| 背包 | `0.5` | 窄、可壓 |
| **旅行軟袋** | `0.75` | **低於**硬殼篋——軟身換回空間 |
| 小行李篋 | `1.0` | 硬殼：佔的正是它量出來的體積 |

中間那對看似奇怪的排序才是誠實的，而它本身就是「以單位而非厘米計價」的論證。

**分區。** 兩側牆各切成四排一區，以懸掛的座位行命名——`Row 1-4L`、`Row 5-8R`、`Row 17-20L`。每區容量 **9 單位**（兩個真實置物櫃，以選擇器自己的單位計）。

**規則，一句話。**

> 每件申報的行李，都放在其主人座位正上方、同側牆的區域——直到該區裝不下，卡片才把它送走。

行李按**到達順序、先到先得**放置，所以後來的行李永遠不會擠走已放好的；新乘客上方的區若滿了，被改道的是**新**行李。

**分界線。** 當分區達到 `STOW_AWAY_UNITS = 8.0`（滿分 9.0），建議就不再指向該區——而登機板的紅也從同一條線開始，所以**紅區正是卡片會叫你走過去的區**。

| 區負載 | 顏色 | 讀作 |
|---|---|---|
| 0 – 2 單位 | 綠 | 幾乎全空 |
| 2 – 8 單位 | 黃 | 正在填滿 |
| 8 單位及以上 | 紅 | 滿了——卡片會叫你走過去別處 |

**被改道的行李去哪**——一個人真正會走的順序：對面同排，然後前方同側，然後對面且在前，最後是後方同側。

**同一套計算。** 卡片的句子與登機板的顏色都出自 `stow_plan()`，所以不可能互相矛盾：票上引用的正是地圖正在著色的負載。

**乘客讀到什麼。** 是動詞，不是算術：*將行李放在你座位上方的行李架*，或*你座位上方的行李架已滿，請使用走道對面的行李架*。卡片上**完全沒有單位數字**——只有件數與一個動詞。登機板保留所有數字，因為投影機才是算術可付諸行動的地方。

### 試用它

| | |
|---|---|
| **實時登機板** | [公開的實時登機板][live]（連結指向本倉庫的部署，見英文 Try it 一節） |
| 任何乘客的卡片 | 同一主機 + `/bggo/<CODE>`——全部 240 個代碼在 [`tickets.md`](tickets.md) |
| Google Cloud 實例 | `http://35.234.41.164/bggo/live`——一台有獨立板面狀態的 GCP VM（見 [`deploy/gcp/README.md`](deploy/gcp/README.md)） |
| 本地一行命令 | `docker compose up` → http://localhost:8091/bggo/live |

[live]: #try-it

*實時登機板的連結由 `repo-base` 標記生成：倉庫複本會經 [`deploy/publish.sh`](deploy/publish.sh)
把它改寫成自己的部署。`<CODE>` 與主機無關，所以同一張登機證在任何跑得動這個應用的主機上都能開啟。*

### 執行它

```bash
docker compose up                        # 整個演示：登機板、卡片、API
docker compose run --rm demo pytest -q   # 247 個測試，在鏡像內跑
docker compose up -d                     # 實際執行方式
BGGO_HOST_PORT=8095 docker compose up    # 與 8091 上的其他行程並存
docker compose down                      # bggo-data 卷保留登機板狀態
docker compose down -v                   # ……這個會把它扔掉
```

沒有 Docker？`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`，然後
`.venv/bin/uvicorn server_v1:app --host 127.0.0.1 --port 8091`。

- **卷就是登機板。** 鏡像裡的 `passengers.db` 只是*種子*；`docker/entrypoint.sh` 首次啟動時把它複製到 `/data/passengers.db`，應用寫在那裡。掃碼狀態在 `restart`／`down`／`up` 後仍在；只有 `down -v` 才回到出廠狀態。
- **預設只綁回環**（`127.0.0.1:8091`），演示永不發佈到 LAN／WAN；同主機的反向代理經回環即可取用。`BGGO_HOST_BIND=0.0.0.0` 是主動選擇。
- **以 uid 10001 執行**，且 db 目錄必須可寫：SQLite 走 WAL 模式，`-wal`／`-shm` 落在檔案旁。
- **容器內伺服器綁 `0.0.0.0`**，連接埠解析 `$PORT` → `$BGGO_PORT` → `8091`，所以同一鏡像在 Cloud Run 上原樣可跑。
- **工作區內 247 個測試全數通過。** 獨立 checkout 有 12 個跳過——那是品牌套件與登機證守衛在報告缺少同層目錄，不是缺口。

### API

| 路由 | 誰 | 備註 |
|---|---|---|
| `GET /bggo/<CODE>` | 觀眾，經 QR | 伺服端渲染；打開行李選擇器 |
| `POST /bggo/<CODE>/bag` | 選擇器，表單編碼 | `bag-<名稱>=<數>`；303 回卡片；無 JS 也可用 |
| `GET /bggo/live` | 主講者 | **公開、無令牌**；`?view=list\|seats`、`?big=1` 劇院模式 |
| `POST /bggo/api/scan` | 卡片 (JS) | `{code, bags:{"bag-handbag":2}}` 記錄報到 |
| `GET /bggo/api/state` | 登機板 | 行＋座位＋置物區＋日誌＋計數，每秒輪詢 |
| `POST /bggo/api/reset` | 登機板的 Reset | 需 `X-Board-Token`；清掉所有掃碼**與所有行李** |
| `GET /bggo/controls?k=…` | 操作員 | 無控制令牌則 403 |
| `POST /bggo/api/ctl/…` | 控制台 | 需 `X-Control-Token`：報到／取消、行李、批次 |
| `GET/POST /bggo/api/ctl/export\|import` | 控制台 | JSON 快照導出／導入 |
| `GET /bggo/` | → 重定向到登機板 | |

登機板**刻意公開**；`Reset` 帶 `X-Board-Token` 是 CSRF 防護，不是登入。`/api/state` 帶
`passengers`、`seats`、`seat_rows`、`bins`、`log` 與 `counters`。`seat_map()`、`bin_loads()`
與 `stow_plan()` 是伺服端首繪與該負載的**唯一資料源**，所以投影機與輪詢不可能不一致。

### 倉庫地圖

```
server_v1.py              FastAPI 應用——登機板、乘客卡、掃碼 API、控制台、機艙模型
static/                   style.css, app.js, board.js, control.css/js——無建置步驟
static/icons/             選擇器畫的四個行李字形
seed_passengers.py        資料腳本——240 位乘客，座位由前到後
passenger_codes.txt       鎖死：240 個票務代碼，只可追加，永不重新生成
passengers.db             種子資料庫——乘客＋掃碼＋種子狀態
tickets.md                每位乘客一個可點連結（生成）
make_ticket_links.py      重新生成 tickets.md，可對任何基準主機
make_flight_snapshot.py   確定性的 150／240 快照，供排練導入
brand/                    供應商品牌套件：brand.css、Montserrat（+OFL）、標記
Dockerfile                鏡像——本資料夾就是整個建置上下文
docker-compose.yml        本地堆疊（僅回環）
docker/entrypoint.sh      種子資料卷，然後在 0.0.0.0 上跑 uvicorn
deploy/gcp/               雲端部署：VM + Caddy、Cloud Run、操作手冊、備份、同步
docs/img/                 演示介面所用的截圖
test_server_v1.py        測試套件，對 db 的臨時副本跑
test_brand_vendored.py    ./brand/ 與品牌套件漂移即失敗
test_make_ticket_links.py tickets.md 若聲稱是印刷負載即失敗
STAGE1_PLAN.md            原始計劃與決策日誌
STAGE2_SEATMAP_PLAN.md    座位圖階段計劃
```

### 授權

程式碼：黑客松專案，未授予重用授權。字體：**Montserrat**，SIL Open Font License 1.1——見
`brand/fonts/OFL.txt`。HK Express 為其所有者的商標；這是針對其公開品牌資產打造的學生概念演示，並非隸屬關係。
