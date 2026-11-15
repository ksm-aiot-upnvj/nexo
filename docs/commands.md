# Command Nexo

Halaman ini berisi command yang dapat digunakan langsung di Discord. Untuk panduan singkat, mulai dari [Mulai menggunakan Nexo](getting-started.md).

Nexo menerima command dengan awalan `$` dan slash command dengan awalan `/`. Command tertentu hanya dapat digunakan oleh pengelola server.

## Command umum

| Command | Contoh | Kegunaan |
| --- | --- | --- |
| `$help`, `/help` | `$help say to` | Membuka panduan interaktif atau cara pakai command tertentu. |
| `$ping` | `$ping` | Memeriksa latensi bot ke Discord Gateway. |
| `$status` | `$status` | Memeriksa status layanan Nexo. |
| `$tools`, `/tools` | `$tools` | Menampilkan kemampuan tambahan yang sedang aktif. |
| Mention atau `/ask` | `Nexo, tampilkan daftar channel.` | Menampilkan daftar channel server melalui AI. |
| `$sync` | `$sync` | Memperbarui daftar slash command; khusus owner. |
| `$clear [limit]` | `$clear 50` | Menghapus pesan jika pengguna memiliki izin Manage Messages. |
| `$reset`, `/reset` | `$reset` | Menghapus riwayat AI milikmu di channel ini. |
| `$reset all` | `$reset all` | Menghapus seluruh riwayat AI persisten seluruh pengguna/server; memerlukan Manage Messages. |

`$help` membuka menu interaktif. Untuk panduan khusus, berikan nama command dan
opsional subcommand/topik. Ini berlaku untuk command prefix dan slash command;
Nexo membaca deskripsi, opsi, dan parameter dari command yang terdaftar.

```text
$help say
$help say to
$help say embed
$help say time
$help say in
$help say list
$help say cancel
$help competition
$help competition add
$help /ask
/help command: say to
```

Topik `say` memberi format, contoh, dan batasan sesuai parser yang digunakan.
Untuk slash command, `$help` menampilkan subcommand dan parameter yang terdaftar.
Menu interaktif tetap hanya dapat digunakan oleh pengguna yang membukanya.

Riwayat AI dipisahkan berdasarkan pengguna dan channel. `$reset` dan `/reset`
hanya menghapus riwayat pengguna yang menjalankan command pada channel aktif.
`$reset all` menghapus seluruh conversation dan pesan tersimpan di database
untuk semua pengguna dan server yang memakai database bot, serta memerlukan
izin `Manage Messages`.

## Percakapan dan statistik AI

Mention Nexo untuk percakapan natural. Untuk pertanyaan eksplisit, gunakan:

```text
/ask question: Bagaimana status project smart hydroponic?
```

Jika Nexo sedang menjawab pertanyaan lain, pertanyaanmu masuk antrean. `/ask` memiliki jeda lima detik untuk setiap pengguna di server yang sama.

### Ringkasan channel

Gunakan `/summarize` untuk merangkum pesan terbaru di channel aktif. Secara
default Nexo memeriksa sampai 100 pesan, lalu memasukkan pesan terbaru secara
utuh selama total prompt masih berada dalam batas aman model. Kamu juga dapat
memberi `message_limit` sebagai batas maksimum pesan yang diperiksa:

```text
/summarize
/summarize message_limit:40
```

Jika seluruh pesan yang diperiksa tidak muat, Nexo memberi tahu berapa pesan
yang benar-benar dirangkum. Isi pesan tidak dipotong untuk memaksa lebih banyak
pesan masuk ke konteks.

Gunakan command berikut untuk melihat konsumsi token:

```text
$my-token-usage
/my-token-usage
$server-token-leaderboard
/server-token-leaderboard
```

`my-token-usage` menampilkan statistik akumulatif milikmu. `server-token-leaderboard`
menampilkan sepuluh anggota dengan penggunaan token akumulatif tertinggi di server.

## Mengirim dan menjadwalkan pesan

`$say` mengirim pesan atas nama Nexo. Command ini memerlukan izin `Manage Messages` atau salah satu role pengelola yang dikonfigurasi, seperti `Leader`, `Co-Leader`, `Staff-Core`, atau `Admin`.

Format umumnya adalah:

```text
$say to <channel> [opsi] <isi pesan>
```

`to` bersifat opsional, tetapi menuliskannya membuat command lebih mudah dibaca. Channel tujuan dapat ditulis dalam tiga bentuk:

- mention channel, misalnya `#pengumuman`;
- ID channel, misalnya `1520017240097951774`;
- nama channel, misalnya `pengumuman`.

Contoh pengiriman langsung:

```text
$say to #pengumuman Rapat dimulai pukul 19.00 WIB.
$say 1520017240097951774 Rapat dimulai pukul 19.00 WIB.
$say to pengumuman Jangan lupa mengisi presensi.
```

### Mengirim pesan embed

Ya, Nexo bisa mengirim pesan embed, termasuk embed yang dijadwalkan. Tambahkan opsi `--embed` pada isi command. Judul embed dapat ditentukan dengan `--title`:

```text
$say to #pengumuman --embed --title "Pengingat rapat" Jangan lupa hadir pukul 19.00 WIB.
```

Nexo mengirim isi setelah opsi sebagai `description` embed. Jika `--title` tidak digunakan, embed tetap dibuat tanpa judul khusus. Contoh tanpa judul:

```text
$say to #pengumuman --embed Pengumuman ini dikirim dalam format embed.
```

Format embed hanya mengubah tampilan pesan; mention di dalam isi tetap diproses oleh Nexo. Gunakan format Discord seperti `@role`, `@nama`, atau mention channel sesuai kebutuhan.

### Menjadwalkan pesan

Pesan dapat dijadwalkan dengan waktu absolut menggunakan `--time` atau waktu relatif menggunakan `--in`. `--time` menerima tanggal dan jam (`YYYY-MM-DD HH:MM`) atau jam saja (`HH:MM`); jika hanya jam diberikan, jadwal dibuat untuk waktu tersebut berikutnya. Untuk saat ini, `--in` menerima angka yang diikuti satu huruf satuan berikut:

| Satuan | Arti | Contoh |
| --- | --- | --- |
| `m` | menit | `--in 30m` |
| `h` | jam | `--in 2h` |
| `d` | hari | `--in 1d` |

Penulisannya harus menggunakan singkatan `m`, `h`, atau `d`. Bentuk kata lengkap seperti `30min`, `2hour`, atau `1day` belum didukung. Satuan detik juga belum tersedia, sehingga `--in 30s` tidak akan dikenali sebagai jadwal. Huruf besar dan kecil diterima, jadi `2H` juga dapat digunakan.

```text
$say to #pengumuman --time "2026-11-01 19:00" Rapat evaluasi.
$say to #pengumuman --in 30m Pengingat: kumpul di ruang meeting.
$say to #pengumuman --in 2h --embed --title "Pengingat" Agenda dimulai dua jam lagi.
$say to #pengumuman --in 1d Pengingat: agenda berlangsung besok.
$say to #pengumuman --time "2026-11-01 19:00" --embed --title "Rapat evaluasi" Jangan lupa hadir.
```

Nilai `--in` dihitung mulai dari waktu saat command diproses. `--in 30m` berarti sekitar 30 menit dari sekarang, sedangkan `--in 2h` berarti sekitar dua jam dari sekarang. Format waktu absolut menggunakan `YYYY-MM-DD HH:MM` dan dipahami sebagai WIB. Nexo mengubahnya ke UTC sebelum menyimpannya sehingga pengiriman tetap konsisten dengan timezone server.

Setelah command diterima, Nexo:

1. memeriksa channel tujuan dan memastikan waktu berada di masa depan;
2. menyimpan isi pesan, format biasa atau embed, judul, channel, pembuat, dan waktu ke database;
3. mengembalikan UUID jadwal dan timestamp Discord;
4. memeriksa jadwal yang sudah jatuh tempo setiap 20 detik;
5. mengirim pesan ke channel tujuan dan menandai jadwal sebagai `sent`.

Karena pemeriksaan berjalan setiap 20 detik, pesan dapat terkirim dalam rentang hingga sekitar 20 detik setelah waktu yang ditentukan. Jika channel tidak ditemukan atau pengiriman gagal, jadwal ditandai `failed`.

Gunakan command berikut untuk mengelola jadwal:

```text
$say list
$say cancel 550e8400-e29b-41d4-a716-446655440000
```

`$say list` menampilkan jadwal yang masih menunggu, termasuk ID, channel, waktu, format, dan cuplikan pesan. Untuk membatalkan jadwal, gunakan UUID yang diberikan saat pembuatan atau ditampilkan oleh `$say list`. Jadwal yang sudah berstatus `sent` atau `failed` tidak dapat dibatalkan.

## Voice channel

Untuk melihat anggota yang sedang berada di voice channel:

```text
$voice
$voice Lab IoT
/voice channel_name: Lab IoT
```

Tanpa nama, Nexo memakai channel tempat command dijalankan atau channel voice yang sedang digunakan pengguna.

Untuk melihat daftar channel yang tersedia di server, gunakan percakapan AI:

```text
Nexo, tampilkan daftar channel di server ini.
/ask question: Apa saja channel teks, voice, dan stage yang tersedia?
```

Nexo memiliki tool `get_server_channels` untuk kebutuhan tersebut. Tool ini bukan command prefix atau slash command mandiri, sehingga `$channels` dan `/channels` belum tersedia.

## Radar kompetisi

Radar kompetisi tersedia sebagai slash command:

```text
/competition add name:"Gemastik XIX 2026" deadline:"2026-10-15 23:59" category:AI
/competition list
/competition list category:IoT
/competition detail competition_id:12
/competition delete competition_id:12
```

Deadline ditulis dalam format `YYYY-MM-DD` atau `YYYY-MM-DD HH:MM` dan diproses sebagai WIB. Reminder otomatis berjalan setiap 30 menit. Penambahan dan penghapusan kompetisi memerlukan izin pengelola.
