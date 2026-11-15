# BaggageGO

<p align="center">
  <img src="brand/icons/svg/baggagego-icon.svg" alt="BaggageGO" width="300">
</p>

Lightweight passenger boarding demo for Cathay Hackathon 2026. Audience members scan the QR code on their
boarding pass, choose their bags, and the live board updates for the pitch demo. It
runs as a FastAPI application backed by SQLite and is packaged as a single Docker
container.

**English** · [繁體中文](#繁體中文)

## Demo

| Surface | Route | For |
|---|---|---|
| Live board | `/bggo/live` | the projector |
| Passenger card | `/bggo/<CODE>` | the audience, via a printed QR |

> [!NOTE]
> **Hosted base URL** — <!--repo-base-->https://sophon1.dedyn.io<!--/repo-base-->
>
> - Live board: append `/bggo/live`
> - Passenger card: append `/bggo/<CODE>` — sample links are listed in [`tickets.md`](tickets.md)
>
> The server is hosted on a homelab environment. For the pitch demo, boarding passes
> with ticket links are printed.

## Run with Docker

Docker Compose builds and starts the app on `http://localhost:8091`:

```bash
docker compose up --build
```

Open the board at <http://localhost:8091/bggo/live>. Passenger cards use
`http://localhost:8091/bggo/<CODE>`; use a code from [`tickets.md`](tickets.md).

The Compose port is bound to `127.0.0.1` by default. To choose another host port:

```bash
BGGO_HOST_PORT=8095 docker compose up --build
```

### Data persistence

Compose stores live scans, bag choices, and settings in the `bggo-data` named volume.
They survive container restarts and recreation.

> [!NOTE]
> `passengers.db` in the repository is a seed copied into that volume on first start; it
> is not the live database after startup.

To stop the app without removing its data, press `Ctrl+C` or run `docker compose down`.
`docker compose down -v` also deletes the volume and its live data.

## Run locally

Python 3.13 or newer is recommended (the Docker image uses Python 3.13).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn server_v1:app --host 127.0.0.1 --port 8091
```

Then visit <http://localhost:8091/bggo/live>. The application defaults to
`passengers.db` in the project directory when run directly; set `BGGO_DB` to use a
different SQLite file.

## Tests

Run the test suite in the same container environment used by the demo:

```bash
docker compose run --rm demo pytest -q
```

Or, after installing the requirements locally:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python test_brand_vendored.py
.venv/bin/python test_make_ticket_links.py
```

## Configuration

| Variable | Purpose |
|---|---|
| `BGGO_DB` | SQLite database file (default: `passengers.db` locally; the Compose container uses `/data/passengers.db`) |
| `PORT` | Listening port when supplied by a hosting platform; otherwise the container uses `BGGO_PORT` (default `8091`) |
| `BGGO_BRAND_DIR` | Vendored brand asset directory (default `brand/`) |
| `BGGO_PUBLIC_BASE` | Base URL used for generated ticket links — it does **not** change the URL encoded in already-printed QR codes |

## Project layout

```text
server_v1.py            FastAPI application and SQLite-backed board
seed_passengers.py      passenger seed data
passengers.db           seed database (passengers + scans + settings)
passenger_codes.txt     the ticket codes (read-only)
make_ticket_links.py    regenerates tickets.md
tickets.md              every passenger as a clickable link
static/                 app assets
brand/                  vendored brand kit
docker/                 container entrypoint
Dockerfile              the image
docker-compose.yml      the local stack
test_*.py               automated tests
deploy/                 host and cloud deployment notes and scripts
```

For host operations, see [`deploy/HOMELAB.md`](deploy/HOMELAB.md). For Google Cloud
options and their persistence trade-offs, see [`deploy/gcp/README.md`](deploy/gcp/README.md).

---

## 繁體中文

<p align="center">
  <img src="brand/icons/svg/baggagego-icon.svg" alt="BaggageGO" width="300">
</p>

**繁體中文** · [English](#baggagego)

為 Cathay Hackathon 2026 而設的輕量級旅客登機示範。觀眾掃描登機證上的 QR code、選擇行李，投影幕
上的儀表板隨即更新。應用程式以 FastAPI 編寫、使用 SQLite，並打包成單一 Docker 容器。

### 示範

| 介面 | 路徑 | 對象 |
|---|---|---|
| 儀表板 | `/bggo/live` | 投影幕 |
| 乘客登機證 | `/bggo/<CODE>` | 觀眾（經印刷的 QR code）|

> [!NOTE]
> **主機網址**見上方英文說明（Hosted base URL）。
>
> - 儀表板：在該網址後加上 `/bggo/live`
> - 乘客登機證：在該網址後加上 `/bggo/<CODE>` — 範例連結見 [`tickets.md`](tickets.md)
>
> 伺服器架設於自家環境。示範當日會派發印有票券連結的登機證。

### 以 Docker 執行

Docker Compose 會建置並在 `http://localhost:8091` 啟動應用程式：

```bash
docker compose up --build
```

在 <http://localhost:8091/bggo/live> 開啟看板。乘客登機證使用
`http://localhost:8091/bggo/<CODE>`；`<CODE>` 可從 [`tickets.md`](tickets.md) 取得。

Compose 的連接埠預設綁定於 `127.0.0.1`。如需改用其他主機連接埠：

```bash
BGGO_HOST_PORT=8095 docker compose up --build
```

#### 資料保存

Compose 會將即時掃描記錄、行李選擇與設定儲存在名為 `bggo-data` 的具名磁碟區，容器重新
啟動或重建後仍然保留。

> [!NOTE]
> 專案中的 `passengers.db` 只是首次啟動時複製到磁碟區的種子資料；啟動後它並非即時資料庫。

若只想停止應用程式而不刪除資料，按 `Ctrl+C` 或執行 `docker compose down`；
`docker compose down -v` 會同時刪除磁碟區及其即時資料。

### 在本機執行

建議使用 Python 3.13 或以上（Docker 映像使用 Python 3.13）。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn server_v1:app --host 127.0.0.1 --port 8091
```

然後瀏覽 <http://localhost:8091/bggo/live>。直接執行時，應用程式預設使用專案目錄下的
`passengers.db`；設定 `BGGO_DB` 可改用其他 SQLite 檔案。

### 測試

在示範所用的同一容器環境中執行測試套件：

```bash
docker compose run --rm demo pytest -q
```

或在本機安裝依賴後：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python test_brand_vendored.py
.venv/bin/python test_make_ticket_links.py
```

### 設定

| 環境變數 | 用途 |
|---|---|
| `BGGO_DB` | 選擇 SQLite 資料庫檔案（本機預設：`passengers.db`；Compose 容器使用 `/data/passengers.db`）|
| `PORT` | 由託管平台提供的接聽連接埠；否則容器使用 `BGGO_PORT`（預設 `8091`）|
| `BGGO_BRAND_DIR` | 選擇內嵌的品牌素材目錄（預設 `brand/`）|
| `BGGO_PUBLIC_BASE` | 更改產生票券連結所用的網址基底 — **不會**改變已列印 QR code 內的網址 |

### 專案結構

```text
server_v1.py            FastAPI 應用程式與以 SQLite 支撐的看板
seed_passengers.py      乘客種子資料
passengers.db           種子資料庫（passengers + scans + settings）
passenger_codes.txt     票券代碼（唯讀）
make_ticket_links.py    重新產生 tickets.md
tickets.md              每位乘客的可點擊連結
static/                 應用程式素材
brand/                  內嵌的品牌套件
docker/                 容器進入點
Dockerfile              映像
docker-compose.yml      本機堆疊
test_*.py               自動化測試
deploy/                 主機與雲端部署筆記及指令碼
```

主機維運請參閱 [`deploy/HOMELAB.md`](deploy/HOMELAB.md)；Google Cloud 的選項及其資料保存
取捨請參閱 [`deploy/gcp/README.md`](deploy/gcp/README.md)。
