# 🚀 Peta Jalan & Fitur (Roadmap) KSM AIoT Bot

Dokumen ini adalah gabungan cetak biru (blueprint) arsitektur, fase pengembangan, rencana DevOps, daftar tugas, dan ide fitur untuk Nexo Bot KSM AIoT.

---

## 1. Komparasi Arsitektur (Current vs Future State)

Berikut adalah perbandingan fungsionalitas bot saat ini dengan rencana arsitektur berstandar *Enterprise/DevOps*:

| Layer / Komponen | 🔴 Kondisi Saat Ini (Current State) | 🟢 Rencana Kedepan (Future State) |
| :--- | :--- | :--- |
| **1. Ingress Layer** | Hanya merespons dari interaksi chat Discord via WebSocket (`discord.py`). | Merespons Discord **ditambah** endpoint khusus HTTP Webhook eksternal (GitHub Actions & Sensor). |
| **2. Bot Gateway** | Belum ada web server mumpuni. | Menggunakan **FastAPI** dengan perlindungan *HMAC Webhook Signature*, *Deduplication*, dan *Guardrails*. |
| **3. Agent Orchestrator** | - *Memory chat* in-memory (hilang jika *restart*).<br>- Semua tools dikirim ke LLM sekaligus. | - *Memory Manager* terhubung ke Database.<br>- **Dynamic Tool Router** dengan Vector Search (`pgvector`). |
| **4. Inference Engine** | `llama-server` (AsyncOpenAI) + Gemma 4 E2B. | Sistem pencegahan error yang memvalidasi *context length* maksimal **8,192 token**. |
| **5. Tool Executor** | Eksekusi via `json.loads` sederhana. | Validasi ketat menggunakan **Pydantic**. |
| **6. Persistence (DB)** | **Tidak ada database.** | Menggunakan **PostgreSQL + pgvector** untuk riwayat, sistem RAG, dan *Audit Logging*. |

---

## 2. Fase Pengembangan Utama

Berdasarkan pertimbangan perangkat keras (4 Core CPU, sisa RAM 5 GB), pengembangan dibagi menjadi 3 fase:

### 🟢 Fase 1: Inti (Core Bot & MCP Integration) - *Rampung*
- [x] Integrasi `llama-server` (Gemma) dengan antarmuka Discord.
- [x] MCP Integration untuk membaca skema sensor hidroponik secara langsung (*Direct Injection*).
- [x] Pydantic Validation (Dasar).

### 🟢 Fase 2: Gateway, WebHooks & Auto-Deploy (Pigeon Style) - *Rampung*
- [x] **FastAPI WebHook Gateway:** Menerima *payload* dari GitHub dengan perlindungan otentikasi HMAC SHA-256 (`verify_signature`).
- [x] **Pydantic & Jinja Templating:** Memvalidasi struktur JSON Webhook secara ketat dengan Pydantic, lalu merender notifikasi yang rapi menggunakan Jinja Template sebelum dikirim ke Discord.
- [x] **Auto-Deploy Engine:** Eksekusi otomatis sintaks deployment (`docker compose pull`, `up -d`, `prune -f`, `ps --format json`) pada *path* proyek yang sesuai ketika workflow GitHub Actions selesai.
- [x] **Observability API:** Endpoint `/health` untuk memantau status sistem dan kesehatan bot.

### 🔴 Fase 3: Skalabilitas Vektor - *Ditunda (Sampai Tools Melebihi Batas Context Window)*
- PostgreSQL Database & Vector DB (`pgvector`).
- Tool Router Berbasis RAG (menyaring tools dengan BGE-small sebelum dikirim ke LLM).

---

## 3. Rencana DevOps & Observability

### Fase 1: Smart CI/CD
- **Hybrid Approach:** GitHub Actions mem-build image -> SSH ke VPS -> Eksekusi `docker compose pull && up`.
- **Smart Error Catching:** Jika deploy gagal, log stderr dilempar via webhook ke Discord. LLM (Gemma 4 E2B) akan menerjemahkan log error tersebut menjadi pesan teknis yang mudah dipahami.

### Fase 2: Web Dashboard (Observability)
- **Pemisahan:** Web Dashboard bertindak untuk *Observability* (Melihat antrean, health check, log), sedangkan Discord untuk *Operability* (Eksekusi /Tanya bot).
- Mini web-server berjalan berdampingan dengan `discord.py` di *background task* menggunakan `asyncio`.

### Fase 3: KSM AIoT Dashboard Center
- **Trigger Eksternal:** Aksi di Web (misal: membagi tugas rapat di kalender) men-trigger API internal bot.
- Bot Discord akan memproses data JSON lewat LLM agar kalimatnya ramah dan tidak kaku, lalu mengirimkannya sebagai *Direct Message* (DM) ke anggota KSM yang ditugaskan.

---

## 4. Ide Fitur Bot (Dioptimasi untuk *Context Window*)

> ⚠️ **PERHATIAN: BATASAN CONTEXT WINDOW (Dibatasi 8,192 Tokens)**
> Model **Gemma 4 E2B (GGUF Q4_K_M)** yang digunakan sebenarnya memiliki kapasitas token yang besar (bisa di-*push* hingga 16K token di *environment* ini), namun **sengaja dibatasi maksimal 8K token** (gabungan *system prompt*, *chat history*, skema *tools*, dan *output*) agar tidak memicu *Out of Memory* (OOM) pada RAM server (5GB). Oleh karena itu, fitur-fitur di bawah ini **wajib dirancang dengan efisien**.

### A. Summarizer & AI Assistant
- **Chat Summarizer (Bertingkat/Paginated):** Bot **dilarang keras** menarik 500 chat sekaligus. Bot hanya mengambil 20-30 pesan terakhir per eksekusi, atau merangkum secara parsial lalu digabung (Rangkuman dari Rangkuman).
- **Research Assistant (Jurnal/Artikel PDF):** Bot tidak bisa membaca 1 PDF utuh di *context window*. Wajib menggunakan RAG (*Retrieval-Augmented Generation*). PDF dipecah (*chunking*), lalu saat user bertanya, bot men-search vektor untuk menarik 3-4 paragraf yang relevan saja.
- **Notulensi Pintar:** Menerima *pointer* ringkas dari pengurus di akhir rapat, lalu merapikannya menjadi format notula (Keputusan & Action Items).
- **AI Weekly Standup & Progress Summarizer (Gemma 4):** Sistem rekap progres mingguan per divisi via thread Discord. Setiap pekan, anggota/lead divisi mengisi progres mingguan dan kendala (*blockers*). Nexo (Gemma 4) merangkumnya menjadi *Executive Summary* terstruktur untuk BPH / Ketua KSM. *(Status: Rencana masa depan ketika aktivitas KSM sudah stabil).*

### B. Komunitas & Produktivitas
- **Pengingat Rapat & Kalender:** *Blast* notifikasi rapat (bisa statis tanpa LLM agar hemat *context window*).
- **VC Meeting Attendance Tracker (Koordinasi PSDM & Akademik):** Peningkatan dari sekadar cek VC pasif menjadi pencatatan presensi rapat dinamis. Pengurus memulai sesi (`/meeting start [nama_rapat]`) dan mengakhiri (`/meeting end`), Nexo secara otomatis mencatat waktu join, durasi kehadiran per anggota di Voice Channel, dan menghasilkan rekap absensi rapat (Embed/CSV) untuk keperluan administrasi presensi organisasi.
- **Interactive Role Picker & Onboarding Divisi (Koordinasi PSDM):** Komponen interaktif terpadu (Discord Select Menu & Buttons) di channel `#pilih-role` / `#welcome`. Memungkinkan anggota baru memilih divisi minat (`🤖 Divisi AI`, `📡 Divisi IoT`, `💻 Divisi Web/Mobile`, `🎨 Humas/Media`) secara mandiri tanpa membebani admin server.
- **Gamifikasi & Poin Keaktifan:** Penghitungan poin (Leaderboard) dari frekuensi menjawab di *help channel* atau dari kontribusi/komit di GitHub.
- **Sistem Onboarding:** Sambutan anggota baru otomatis via DM/Channel.
- **Competition & Hackathon Radar:** Manajemen info lomba dan kompetisi teknologi (Gemastik, Hackathon, PKM, IoT Challenge) dengan reminder countdown berkala menuju batas akhir pendaftaran.

### C. Alat Bantu Riset & Lab
- **Inventaris Lab IoT:** Cek ketersediaan sensor (ESP32, DHT22) langsung ke database.
- **Live Sensor Monitoring:** Slash command untuk menarik cuplikan ringkas (*snapshot*) data *real-time* kelembaban/suhu lab IoT.
- **Code Assistant Terarah:** Menjawab *error code* spesifik mikrokontroler. *Chat history* akan otomatis dipangkas (*rolling window*) jika menyentuh 6,000 token agar sisa ruang cukup untuk menampung jawaban.
- **Learning Assistant (RAG Engine):** Memasukkan kumpulan *datasheet* komponen (ESP32, IC, sensor) dan kumpulan materi dari KSM AIoT sendiri, serta bisa dari sumber eksternal (web search). Anggota KSM dapat bertanya ke bot dan mendapatkan jawaban beserta referensinya.
- **Eksekusi Fisik / Hardware-in-the-Loop (Target Masa Depan):** Bot diberi kapabilitas mengeksekusi *actuator* di lab fisik, seperti me-restart node server atau menghidupkan *relay* MQTT langsung lewat command Discord.

### D. Utilitas Server & Manajemen Proyek
- **Polling Dinamis:** Memfasilitasi musyawarah atau penentuan jadwal lewat sistem *voting* yang rapi dan bisa dibatasi khusus *role* tertentu.
- **Scheduled Announcement (`$say` Scheduler):** Penjadwalan pengumuman dan pesan siaran bot di channel tertentu menggunakan parameter waktu `--time` atau `--in`.
- **Ruang Proyek Sementara (Discord Threads):** Daripada membuat *Text Channel* baru yang membuat server berantakan, anggota bisa *request* pembuatan **Discord Thread** khusus tim lomba/proyek. Kelebihannya, *Thread* akan **otomatis diarsipkan (auto-archive)** oleh Discord sendiri jika tidak ada yang *chat* lagi (misal setelah 1 minggu), sehingga server KSM tetap bersih tanpa membebani memori bot.

---

## 5. Daftar Tugas Implementasi (Task List)

**INGRESS & GATEWAY**
- [x] Setup endpoint GitHub Webhooks dengan FastAPI (`utils/gateway_server.py`).
- [x] Implementasi *Pydantic Schema* (`utils/webhook_schemas.py`) untuk parsing payload GitHub dan *Jinja Templating* (`templates/*.j2`) untuk notifikasi Discord.
- [x] Modernisasi *Webhook Embed Templates* dengan *Discord Action Link Buttons* & filter `compact_markdown` untuk merapatkan gap teks.
- [x] Implementasi sistem *Auto-Deploy* (menjalankan sintaks shell `docker compose pull`, `up -d`, `prune -f` berdasarkan payload).
- [x] Implementasi HMAC Webhook Signature (`verify_signature`), Deduplication, & Guardrails.

**AGENT & INFERENCE**
- [x] Buat *Context & Date Resolver* (Pydantic & Dynamic System Context).
- [x] Rombak *in-memory history* menjadi modul Database PostgreSQL *async* dengan sistem *Rolling Summaries* & *24-Hour Sliding TTL Expiration* (`db/repositories/` dan `services/llm/context.py`).
- [x] Validasi *context length* maksimal 8,192 token sebelum request dikirim ke llama-server (dengan batasan karakter & rolling history limit).
- [x] Implementasi **Multi-Hop & Multi-Tool Calling Loop** (iterative max 3 iterations dengan sanitasi skema).
- [x] Implementasi **Pydantic Token-Efficient Schema Cleaner** (`get_clean_schema`) untuk membuang metadata `$defs` dan `title` agar menghemat token prompt.
- [x] Protokol *Anti-Chatter Direct Tool Calling* di System Prompt Nexo.
- [ ] Pembuatan knowledge base *Datasheet Perangkat IoT* dan *Modul Ajar* (chunking PDF dokumen AIoT ke dalam `pgvector`).
- [ ] *(Low Priority)* Implementasi **Streaming Responses** (`stream=True`). **Catatan:** Berisiko tinggi memicu *Rate Limit* Discord, sehingga bukan prioritas utama saat ini.
- [ ] Implementasi **Semantic Tool Routing (Tool Retrieval / RAG Router)**. Mencegah penuhnya *context window* dengan cara memfilter dan hanya memasukkan skema/deskripsi *tools* MCP yang relevan (menggunakan *vector search*) ke dalam *system prompt*.
- [ ] Tambahkan **Robust Error Handling (Exponential Backoff)** menggunakan library `tenacity` saat berkomunikasi dengan LLM atau MCP.

**TOOL EXECUTOR & DB (CORE)**
- [x] Integrasi Pydantic untuk validasi skema input/output *tool* (`DiscordEventSchema`, `CheckVoiceChannelSchema`, `ListDiscordEventsSchema`, `EndDiscordEventSchema`, dll).
- [x] Setup koneksi PostgreSQL *async* (`SQLAlchemy 2.0` + `asyncpg` + `Alembic`) & buat tabel utama (`conversations`, `messages`, `scheduled_events`, `token_usage`, `token_cache`).
- [x] Sistem **Token Cache LRU** (MD5 prompt hash) & **Observability Token Metrics** (pencatatan token per guild/user dan leaderboard pemakaian).
- [x] Tambah *Audit & Execution Logging* dengan format timestamp ISO standar.
- [ ] Implementasi **Parallel Tool Execution** menggunakan `asyncio.gather` saat model mengeluarkan lebih dari satu tool call serentak.

**DISCORD COGS & USER FEATURES**
- [x] Pembuatan Cog *Community*: Sistem Onboarding (auto-role & welcome message) dan Absensi Voice Channel (`list_voice_channel_members`, `$vc`, `/voice`).
- [x] Pembuatan Cog *Utilities*: Polling dinamis (`create_discord_poll`), Manajemen *Discord Threads* (pembuatan & auto-archive), dan Perintah Reset Konteks Memori (`$reset`, `/reset`).
- [x] Pembuatan Cog *Webhook & Deployment*: FastAPI Gateway (`/webhook`), Jinja2 embed notification, dan otomatisasi Docker Compose Deployment (`cogs/webhook_deploy.py`).
- [x] Pembuatan Cog *Scheduler & Events Lifecycle*:
  - [x] Manajemen siklus hidup event Discord berbasis database (`manage_event_lifecycle` loop).
  - [x] Dynamic interval reminder scheduler (H-7d hingga H-10m) dengan filter duplikasi.
  - [x] Dynamic LLM One-Shot Copywriting (`generate_dynamic_event_message`) dengan timeout 120s dan fallback Jinja2.
  - [x] Tool `list_discord_events` (output markdown padat token dalam zona waktu WIB).
  - [x] Tool `end_discord_event` dengan 3-tier target resolution & Discord lifecycle state machine.
- [ ] Pembuatan Cog *Lab Assistant*: *Tool* untuk query Inventaris Lab IoT dan monitoring status Sensor (*Snapshot* suhu/kelembaban).
- [ ] **VC Meeting Attendance Tracker** (Koordinasi Divisi PSDM & Akademik): Monitoring sesi Voice Channel, kalkulasi durasi kehadiran per anggota secara dinamis, dan rekapitulasi presensi rapat otomatis.
- [ ] **Interactive Role Picker & Onboarding Divisi** (Koordinasi Divisi PSDM): Implementasi persistent UI View (Select Menu/Buttons) untuk self-assign role divisi anggota baru.
- [ ] **AI Weekly Standup & Progress Summarizer** (Gemma 4): Pipeline ekstraksi progres divisi mingguan dan automated executive summary untuk BPH / Ketua KSM.
- [ ] **Scheduled Announcement (`$say` Scheduler)**: Dukungan pengiriman pengumuman terjadwal berbasis parameter `--time` atau `--in` (WIB).
- [ ] **Competition & Hackathon Radar (`/competition`)**: Manajemen info lomba/kompetisi AI & IoT dengan sistem countdown reminder berkala (H-7, H-3, H-1).
- [ ] (Future) Pembuatan *tool* eksekusi fisik (contoh: MQTT *publish* ke relay ESP32) berserta *role permission checker*.

**WEB UI DASHBOARD (ADMIN PANEL)**
- [ ] Rancang dan bangun **Web Dashboard GUI** untuk memanajemen konfigurasi Nexo tanpa harus menyentuh kode.
- [ ] **Knowledge Base Manager**: Fitur UI untuk *upload*, hapus, dan kelola dokumen/materi yang akan masuk ke sistem RAG (Vektor DB).
- [ ] **MCP Server Manager**: Fitur antarmuka untuk menambahkan, mengedit, atau menghapus berbagai *endpoint URL* MCP Server IoT secara dinamis dan menyimpannya langsung ke database.

### Backlog Peningkatan Ketahanan dan Pemanfaatan Model

Status mencerminkan progres implementasi yang sudah diperiksa. `slow_purge` menghapus pesan berumur kurang dari 14 hari dalam batch maksimal 100, serta menghapus pesan yang lebih lama satu per satu.

| # | Prioritas | Usulan | Manfaat / catatan | Status |
| :---: | :---: | :--- | :--- | :---: |
| 1 | P1 | Batasi maksimum permintaan tool `delete_channel_messages` menjadi 100 pesan | Default dan batas maksimum tool sama-sama 100; pesan lama tetap dihapus satu per satu. | ✅ Done |
| 2 | P1 | Tambahkan retry terbatas dengan jeda bertahap untuk layanan eksternal | Retry SDK LLM dibatasi; efek webhook memakai backoff eksponensial dengan maksimum percobaan yang dapat dikonfigurasi. | ✅ Done |
| 3 | P1 | Terapkan idempotensi pada webhook dan pekerjaan yang bisa diulang | Kunci effect stabil mencegah enqueue ganda saat GitHub mengirim ulang delivery yang sama. | ✅ Done |
| 4 | P2 | Tambahkan pemulihan pekerjaan yang macet dalam status `processing` | Lease kedaluwarsa memungkinkan worker mengambil pekerjaan kembali setelah restart. | ✅ Done |
| 5 | P2 | Rancang persistent outbox berbasis database | Job webhook tersimpan secara durable bersama status, retry, dan pencegahan duplikasi. | ✅ Done |
| 6 | P2 | Tambahkan metrik operasional dan ekspor yang kompatibel dengan **Prometheus** | Endpoint `/metrics` menampilkan request HTTP, webhook/outbox, dan durasi/error inference LLM. | ✅ Done |
| 7 | P2 | Sediakan ringkasan diskusi Discord berdasarkan permintaan, misalnya `/summarize` | `/summarize` menghitung prompt aman dan mengambil pesan utuh terbaru; opsi `message_limit` opsional membatasi jumlah pesan yang diperiksa. | ✅ Done |
| 8 | P2 | Sediakan bantuan membaca error/log di channel developer | Nexo membawa konteks pesan yang di-reply beserta metadata sumber saat disebut; isi pesan diperlakukan sebagai data tidak tepercaya. | ✅ Done |
| 9 | P2 | Bangun tanya jawab dokumentasi internal dengan RAG | Memerlukan kurasi dan pengindeksan dokumen KSM/Nexo. | ⬜ Open |
| 10 | P3 | Tambahkan analisis gambar/screenshot sesuai permintaan | Memerlukan dukungan input gambar di runtime Nexo. | ⬜ Open |
| 11 | P3 | Evaluasi transkripsi/ringkasan audio | Integrasi bergantung pada format input dan dukungan runtime. | ⬜ Open |
| 12 | P3 | Pisahkan health check menjadi liveness dan readiness | Endpoint `/nexo/health/live` dan `/nexo/health/ready` tersedia. | ✅ Done |
| 13 | P3 | Tambahkan timeout dan batas konkurensi untuk pekerjaan | Batas antrean dan timeout request/job LLM dikonfigurasi melalui environment variables. | ✅ Done |
| 14 | P3 | Uji skenario pemulihan saat restart dan kegagalan layanan | Test memeriksa pengambilan ulang lease kedaluwarsa, retry terbatas, kegagalan terminal, serta status delivery akhir. | ✅ Done |
| 15 | P2 | Standarkan nama Discord tools dan slash commands dalam bahasa Inggris | Tool dan opsi kompetisi memakai nama yang eksplisit; token pribadi dibedakan dari leaderboard server. | ✅ Done |

---

## 6. Perhitungan & Arsitektur Batasan Rate Limit (Discord API Limits)

Untuk memastikan bot berjalan stabil tanpa terkena pemblokiran IP atau pencabutan *token* dari Discord, berikut adalah batasan Discord API beserta perhitungan kuota dan strategi mitigasi arsitektur Nexo.

> ⚠️ **DISCLAIMER:** Discord secara resmi menyatakan bahwa angka *per-route rate limit* **bersifat dinamis dan dapat berubah tanpa pemberitahuan**. Dokumentasi resmi Discord ([Rate Limits](https://discord.com/developers/docs/topics/rate-limits)) mewajibkan pengembang **tidak meng-*hardcode* angka ini** dan sebagai gantinya mem-*parse* header respons HTTP (`X-RateLimit-*`). Angka-angka di bawah ini diambil dari **dokumentasi resmi** (untuk Global & Invalid Request) serta **community benchmark yang konsisten** (untuk per-route) dan digunakan **hanya sebagai dasar perhitungan arsitektural**, bukan sebagai konstanta tetap.

### A. Tabel Batasan Discord API

**Sumber Resmi (Official — Didokumentasikan Discord):**

| Komponen | Batasan Resmi | Sumber | Strategi Penanganan Nexo |
| :--- | :--- | :--- | :--- |
| **Global REST Rate Limit** | **50 request / detik** (seluruh endpoint REST) | [Docs: Global Rate Limit](https://discord.com/developers/docs/topics/rate-limits#global-rate-limit) | Antrean global via `asyncio.Queue` & `asyncio.Lock` |
| **Invalid Request Limit** | **10,000 invalid request / 10 menit** (status 401, 403, 429) | [Docs: Invalid Request Limit](https://discord.com/developers/docs/topics/rate-limits#invalid-request-limit-aka-cloudflare-bans) | Validasi skema ketat menggunakan **Pydantic** |
| **Gateway (WebSocket)** | **120 event / 60 detik** per koneksi | [Docs: Gateway](https://discord.com/developers/docs/events/gateway#rate-limiting) | Pembatasan frekuensi `change_presence` & event |

**Benchmark Komunitas (Non-Guaranteed — Dapat Berubah Sewaktu-waktu):**

| Komponen / Route | Benchmark Umum | Scope | Strategi Penanganan Nexo |
| :--- | :--- | :--- | :--- |
| **Send Message** | ~5 request / 5 detik | Per channel | Penggabungan pesan (*chunking*) max 2,000 karakter |
| **Edit Message** | ~5 request / 5 detik | Per channel | Mengedit pesan *wait message* tunggal |
| **Delete Message (Individual)** | ~5 request / 1 detik | Per channel | `asyncio.sleep()` antar operasi; prioritaskan Bulk Delete |
| **Add/Remove Reaction** | ~1 request / 0.25 detik | Per channel | `asyncio.sleep()` antar operasi |
| **Bulk Delete Messages** | ~1 request / 1 detik (max 100 pesan, umur < 14 hari) | Per channel | Filter otomatis via `channel.purge()` |

### B. Model Matematis Formulasi Delay Aman ($\Delta t_{\text{safe}}$)

Untuk setiap endpoint API dengan kuota maksimal $L$ request dalam jendela waktu $W$ detik, kecepatan rata-rata teoritis maksimal dan interval minimum dihitung sebagai:
$$R_{\text{teoritis}} = \frac{L}{W} \quad (\text{request/detik}), \qquad \Delta t_{\text{min}} = \frac{W}{L} \quad (\text{detik/request})$$

Untuk mengantisipasi *network jitter*, *latency spike*, dan *race condition*, Nexo menerapkan **Safety Margin Factor** ($S$) secara seragam. Formulasi delay aman ($\Delta t_{\text{safe}}$) dirumuskan sebagai:
$$\Delta t_{\text{safe}} = \Delta t_{\text{min}} \cdot S = \left( \frac{W}{L} \right) \cdot S$$

Nexo menggunakan $S = 1.25$ (buffer 25%) secara konsisten untuk semua aksi, memprioritaskan stabilitas di atas kecepatan maksimal.

---

### C. Perhitungan Parameter Delay per Komponen Aksi ($S = 1.25$)

| # | Aksi | $L$ | $W$ (detik) | $\Delta t_{\text{min}}$ | $\Delta t_{\text{safe}}$ | Throughput Aman |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| 1 | Send Message | 5 | 5 | 1.00 s | **1.25 s** | ~0.80 msg/s |
| 2 | Edit Message | 5 | 5 | 1.00 s | **1.25 s** | ~0.80 edit/s |
| 3 | Delete Message (Individual) | 5 | 1 | 0.20 s | **0.25 s** | ~4.00 del/s |
| 4 | Add/Remove Reaction | 1 | 0.25 | 0.25 s | **0.3125 s** | ~3.20 react/s |
| 5 | Bulk Delete Messages | 1 | 1 | 1.00 s | **1.25 s** | ~0.80 batch/s |
| 6 | Global REST (semua endpoint) | 50 | 1 | 0.02 s | **0.025 s** | ~40 req/s |

**Catatan Formulasi:**

1. **Total Waktu Pengiriman $K$ Buah Pesan Chunk** (dimana $K = \lceil N / 2000 \rceil$, $N$ = jumlah karakter balasan LLM):
   $$T_{\text{total, send}} = (K - 1) \cdot \Delta t_{\text{safe, send}} = (K - 1) \cdot 1.25 \text{ detik}$$
   *Pesan pertama tidak memerlukan delay, hanya pesan ke-2, ke-3, dst.*

2. **Total Waktu Penghapusan Individual $M$ Buah Pesan:**
   $$T_{\text{total, delete}} = M \cdot \Delta t_{\text{safe, delete}} = M \cdot 0.25 \text{ detik}$$
   *Sebagai perbandingan: Bulk Delete menghapus 100 pesan dalam 1 request vs individual yang memerlukan $100 \cdot 0.25 = 25$ detik.*

3. **Formula Backoff Dinamis saat Terjadi HTTP 429:**
   Jika server Discord mengembalikan respons HTTP 429 dengan durasi `retry_after` ($R_{\text{after}}$ dalam detik), interval penundaan dinamis dirumuskan:
   $$\Delta t_{\text{backoff}} = R_{\text{after}} + \text{Jitter} \quad \text{dimana } \text{Jitter} \sim \text{Uniform}(0.1, 0.5) \text{ detik}$$

4. **Catatan Penting `discord.py`:** Pustaka `discord.py` yang digunakan Nexo **sudah menangani rate limit secara otomatis** (*auto-sleep* saat menerima HTTP 429 dan mem-*parse* header `X-RateLimit-*` secara internal). Formulasi di atas berfungsi sebagai **aturan arsitektural tambahan** untuk mencegah bot mengirimkan *burst* request yang tidak perlu sebelum terkena 429.



