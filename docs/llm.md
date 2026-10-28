# Cara kerja chatbot LLM

Percakapan dapat dimulai dengan mention atau `/tanya`. Nexo memeriksa kesehatan provider LLM, menaruh pertanyaan ke `message_queue`, lalu memberi tahu posisi antrean jika ada permintaan lain. Satu worker mengambil item antrean dan memakai lock bot agar pemrosesan berjalan teratur.

Sebelum worker dipakai, Nexo memuat system prompt dari `prompts/system_prompt.md`, mengambil daftar tools MCP melalui SSE, menggabungkannya dengan local Discord tools, lalu membersihkan schema JSON agar ukuran konteks lebih kecil. Riwayat percakapan dibaca dari database dengan scope pasangan pengguna dan channel. Pesan dari channel atau pengguna lain tidak ikut menjadi konteks conversation tersebut.

```mermaid
sequenceDiagram
    actor User as Pengguna
    participant Discord
    participant Queue as Message queue
    participant Agent as Agent worker
    participant LLM as LLM API
    participant Tool as Local/MCP tool
    participant DB as Conversation DB

    User->>Discord: Mention atau /tanya
    Discord->>Queue: Simpan pertanyaan
    Agent->>DB: Ambil riwayat dan simpan pesan user
    Agent->>LLM: Kirim prompt, konteks, dan function tools
    alt Model meminta tool
        LLM-->>Agent: Tool call + argumen
        Agent->>Tool: Jalankan handler
        Tool-->>Agent: Hasil tool
        Agent->>LLM: Kirim hasil tool
    end
    LLM-->>Agent: Jawaban akhir
    Agent->>DB: Simpan jawaban dan token usage
    Agent-->>Discord: Embed respons Nexo
```

Model memilih tool berdasarkan intent. Misalnya, pertanyaan tentang anggota voice channel dapat memanggil `check_voice_channel`, sedangkan permintaan membuat event dapat memanggil `create_discord_event`. Beberapa tool dalam satu putaran LLM dapat diminta sekaligus. Nexo mengeksekusi panggilan yang bersebelahan secara paralel hanya jika tool tersebut secara eksplisit ditandai read-only. Tool yang mengubah data dan tool MCP tanpa metadata read-only tetap dijalankan berurutan sesuai urutan panggilan model.

Jawaban ditampilkan sebagai embed Discord dengan durasi proses dan informasi token jika provider mengembalikannya. Jika LLM atau tool gagal, pengguna menerima pesan error dan kegagalan dicatat melalui logger.

## Provider LLM

`LLM_BASE_URL` harus menunjuk ke endpoint OpenAI-compatible provider, misalnya:

```env
# Azure OpenAI-compatible endpoint
LLM_BASE_URL=https://latihan-azure-1.services.ai.azure.com/openai/v1
LLM_API_KEY=your-api-key
LLM_MODEL_NAME=your-deployed-model
```

Untuk LiteLLM, `LLM_BASE_URL` dapat memakai route standar antar-container
`http://litellm-gateway:4000/v1` atau route ber-prefix seperti
`https://aiot.fik.upnvj.ac.id/gateway/v1` maupun
`https://aiot.fik.upnvj.ac.id/gateway`. Nexo meminta metadata di
`/v1/model/info?model=<LLM_MODEL_NAME>` (prefix gateway tetap dipertahankan),
lalu memakai `litellm_params.tokenizer_with_hf_repo`. Azure memakai base URL
`/openai/v1` dan tidak diperlakukan sebagai LiteLLM. Untuk tokenizer gated atau
private di Hugging Face, sediakan `HF_TOKEN` read-only secara terpisah dari
`LLM_API_KEY`. Jika tokenizer gagal dimuat, Nexo memakai perkiraan karakter dan
tidak mengulang unduhan gagal pada setiap pesan selama proses yang sama.

Status provider memanggil endpoint `/models` melalui library `openai` /
`AsyncOpenAI`. Dengan begitu, pengecekan tidak perlu menghasilkan output model
dan tidak bergantung pada endpoint `/health` khusus llama.cpp.
