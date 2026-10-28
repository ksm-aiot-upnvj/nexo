# Konfigurasi dan deployment

Salin `.env.example` menjadi `.env`, isi nilai yang diperlukan, lalu jalankan
migrasi database sebelum memulai bot:

```powershell
Copy-Item .env.example .env
uv sync
uv run alembic upgrade head
uv run python main.py
```

Simpan `.env`, service account Google, dan URL webhook rahasia di luar Git.
Gunakan ID Snowflake Discord untuk pengaturan channel dan role.

## Environment variables

| Variabel | Kegunaan |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Token bot Discord. Wajib untuk menjalankan bot. |
| `DATABASE_URL` | URL PostgreSQL, misalnya `postgresql+asyncpg://user:password@localhost:5432/nexo_db`. |
| `LLM_BASE_URL` | Base URL API OpenAI-compatible. Azure: `https://<resource>.services.ai.azure.com/openai/v1`; LiteLLM standar: `http://litellm-gateway:4000/v1`; LiteLLM di balik prefix gateway: `https://<host>/gateway` atau `https://<host>/gateway/v1`. Jangan tambahkan `/model/info` atau query string. |
| `LLM_API_KEY` | Kunci autentikasi provider LLM atau gateway. Kosongkan hanya jika provider memang mengizinkan akses tanpa kunci. |
| `HF_TOKEN` | Token Hugging Face read-only opsional untuk mengunduh tokenizer private/gated yang ditunjuk metadata LiteLLM. Token ini terpisah dari `LLM_API_KEY`. |
| `LLM_MODEL_NAME` | Nama model atau deployment yang dikirim pada request LLM. Untuk Azure, nama deployment yang tidak menyerupai nama model yang dikenal bisa membuat penghitung token memakai fallback perkiraan. |
| `MCP_SERVER_URL` | Base URL MCP Server; Nexo menambahkan `/sse` untuk koneksi tools. Jangan tambahkan `/sse`. |
| `TOTAL_CONTEXT_WINDOW` | Ukuran context window yang dipakai untuk mengatur pemangkasan riwayat. Default `8192`. |
| `MAX_GROUP_CONTEXT_CHARS` | Batas karakter riwayat channel yang dibaca saat Nexo disebut di percakapan grup. Default `10000`. |
| `GITHUB_SECRET` | Secret HMAC SHA-256 untuk validasi GitHub webhook. |
| `PORT` / `HOST` | Port dan host proses aplikasi. Default `8000` dan `0.0.0.0`. |
| `WEBHOOK_PORT` | Port gateway FastAPI webhook. Default `8000`. |
| `PORTAINER_WEBHOOKS` | Pemetaan project ke webhook Portainer dalam JSON atau daftar dipisahkan titik koma. |
| `WEBHOOK_DEVLOGS_CHANNEL` | URL webhook Discord untuk mengirim notifikasi devlog. |
| `WEBHOOK_DEVLOGS_CHANNEL_ID` | ID channel devlog untuk notifikasi yang dikirim melalui bot. |
| `WEBHOOK_RELEASE_NOTES_CHANNEL_ID` | ID channel release notes. |
| `WELCOME_AND_RULES_CHANNEL_ID` | ID channel welcome dan peraturan. |
| `ANNOUNCEMENT_CHANNEL_ID` | ID channel pengumuman. |
| `PENGURUS_CHANNEL_ID` | ID channel pengurus untuk notifikasi verifikasi dosen dari formulir perkenalan. |
| `PENGURUS_ROLE_ID` | Role yang disebut saat notifikasi verifikasi dikirim; opsional. |
| `CATEGORY_ID` | ID kategori untuk channel sementara. |
| `GENERATOR_CHANNEL_ID` | ID voice channel generator biasa. |
| `GENERATOR_STAGE_ID` | ID stage channel generator. |
| `MEMBER_ROLE_ID` | ID role anggota yang diberikan setelah formulir perkenalan. |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Path file kredensial service account Google. Default `credentials/service_account.json`. |
| `GOOGLE_CALENDAR_ID` | ID kalender Google yang disinkronkan. |

Channel dan role yang tidak dipakai dapat dibiarkan `0` atau kosong sesuai tipe
nilainya. Untuk Docker Compose, isi variabel `.env` dengan alamat layanan yang
dapat dijangkau dari container; `localhost` menunjuk ke container Nexo sendiri.

## Tokenizer dan provider LLM

Untuk LiteLLM, Nexo meminta metadata model lalu mengambil
`litellm_params.tokenizer_with_hf_repo`. Pada base URL `/v1` endpoint yang
dipanggil adalah `/v1/model/info`; pada `/gateway` atau `/gateway/v1`, Nexo
mempertahankan prefix tersebut. `LLM_API_KEY` dipakai untuk autentikasi ke
gateway jika diperlukan. Jika repo tokenizer di Hugging Face private atau gated,
sediakan `HF_TOKEN` dan pastikan akun/token memiliki akses. Tokenizer yang tidak
dapat dimuat akan memakai perkiraan karakter; kegagalan pemuatan tokenizer
diingat selama proses berjalan agar Nexo tidak mengulangi unduhan yang gagal
untuk setiap pesan.

Untuk model OpenAI-compatible seperti GPT, Nexo memakai `tiktoken` bila nama
model dikenali. Jika lookup atau pemuatan tokenizer gagal, Nexo mencatat warning
dan memperkirakan sekitar satu token per empat karakter. Perkiraan tersebut
berguna untuk pengelolaan konteks, tetapi bukan hitungan billing yang akurat.

## File konfigurasi deployment

File berikut berisi konfigurasi spesifik server dan tidak disimpan di repository:

- `config/roles.json`: pemetaan nama role ke ID role Discord untuk RBAC.
- `config/projects.json`: repository GitHub, channel tujuan, dan konfigurasi
  deployment per project.
- `credentials/service_account.json`: service account Google Calendar.

Siapkan file yang sesuai sebelum mengaktifkan fitur terkait. Untuk memeriksa
format webhook dan routing, lihat [Monitoring project melalui GitHub Webhook](github-webhook.md).
