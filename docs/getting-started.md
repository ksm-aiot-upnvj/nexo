# Mulai menggunakan Nexo

Nexo digunakan langsung dari server Discord. Pastikan kamu berada di server yang sudah memasang Nexo, lalu buka channel tempat bot dapat membaca dan mengirim pesan.

## Tiga langkah pertama

1. Ketik `$help` atau gunakan `/help` untuk membuka menu.
2. Untuk instruksi langsung, ketik `$help <command>` atau `$help <command> <topik>`.
3. Jalankan command yang sesuai, atau mention Nexo dan jelaskan kebutuhanmu.

Contoh bantuan khusus:

```text
$help say
$help say to
$help say time
$help competition add
/help command: say to
```

Format `/help` untuk parameter `command` akan menyajikan panduan yang sama.

Contoh percakapan:

```text
Nexo, siapa saja yang sedang ada di voice channel Lab IoT?
```

Nexo akan mencoba memahami permintaan tersebut dan memilih bantuan yang sesuai. Untuk membuat agenda, sertakan tanggal, jam, topik, dan lokasi agar Nexo tidak perlu menebak.

## Mencari dan men-tag member

Kamu dapat meminta Nexo mencari akun berdasarkan username, display name, global name, atau nickname:

```text
Nexo, siapa itu John Doe?
```

Nexo hanya menampilkan maksimal lima kandidat yang cocok. Pencarian ini tidak mengirim mention dan tidak mengirim pesan kepada siapa pun.

Untuk mengirim pesan kepada member tertentu, gunakan `/mention-member` dan pilih target langsung dari daftar member Discord:

```text
/mention-member target:@User message:"Tolong jangan lupa mandi!"
```

Ganti `@User` dengan mention member yang ingin ditag. Jika kamu ingin men-tag beberapa member, buat command `/mention-member` terpisah untuk masing-masing target.

Pesan dikirim di channel tempat command dijalankan. Jika menggunakan percakapan AI, Nexo hanya boleh mengirim pesan ketika kamu secara jelas meminta Nexo untuk men-tag atau memberi tahu seseorang. Jika ada beberapa akun yang cocok, Nexo akan meminta kamu memilih target terlebih dahulu.

## Command yang sering dipakai

| Kebutuhan | Gunakan | Contoh |
| --- | --- | --- |
| Membuka bantuan | `$help` atau `/help` | `$help` |
| Bertanya kepada Nexo | `/ask` atau mention | `/ask question: Apa kabar project hidroponik?` |
| Merangkum pesan channel | `/summarize` | `/summarize message_limit:40` |
| Memeriksa bot | `$ping` | `$ping` |
| Melihat tools yang aktif | `$tools` atau `/tools` | `$tools` |
| Melihat daftar channel | Mention Nexo atau `/ask` | `Nexo, tampilkan daftar channel di server.` |
| Mencari akun member | Mention Nexo atau `/ask` | `Nexo, siapa itu John Doe?` |
| Mengirim pesan dengan tag | `/mention-member` | `/mention-member target:@Cocolele message:"Tolong mandi!"` |
| Melihat anggota voice channel | `$voice` atau `/voice` | `$voice Lab IoT` |
| Menghapus ingatan percakapan | `$reset` atau `/reset` | `$reset` |

Riwayat AI terpisah untuk tiap pasangan pengguna dan channel. `$reset` hanya
menghapus riwayatmu di channel aktif. Pengelola dengan izin `Manage Messages`
dapat memakai `$reset all` untuk menghapus seluruh riwayat tersimpan.

## Membuat pengumuman

Pengelola server dapat mengirim pesan melalui Nexo:

```text
$say to #pengumuman Rapat dimulai pukul 19.00 WIB.
```

Pesan juga dapat dibuat dalam bentuk embed:

```text
$say to #pengumuman --embed --title "Pengingat rapat" Jangan lupa hadir.
```

Untuk mengirimnya nanti, tambahkan `--in`:

```text
$say to #pengumuman --in 30m Pengingat: kumpul di ruang meeting.
```

Detail format waktu, embed, daftar jadwal, dan pembatalan tersedia di halaman [Command](commands.md).

## Membuat event

Kamu dapat meminta Nexo membuat event menggunakan bahasa natural:

```text
Nexo, buat rapat evaluasi besok jam 8 malam di Voice Channel Lab IoT.
```

Jika informasi penting belum disebutkan, Nexo akan meminta detail yang kurang. Berikan tanggal, jam, topik, dan lokasi pada balasan berikutnya.

## Menggunakan channel sementara

Nexo menyediakan dua generator channel. Keduanya dipakai dengan cara masuk ke channel generator yang sudah disediakan oleh pengelola server. Setelah kamu masuk, Nexo membuat channel baru dan memindahkanmu ke sana.

### Generator Stage

Generator Stage digunakan untuk acara besar seperti podcast, seminar, presentasi, atau diskusi dengan pembicara. Saat kamu masuk ke Generator Stage, Nexo membuat Stage khusus dengan nama yang mencantumkan namamu.

Pembuat stage mendapatkan izin untuk mengelola stage, memindahkan anggota, mengatur permission, dan mengatur mute member. Setelah semua orang keluar, stage sementara dihapus otomatis.

### Generator Voice

Generator Voice digunakan untuk diskusi kelompok, belajar bersama, rapat kecil, atau percakapan umum. Saat kamu masuk ke Generator Voice, Nexo membuat voice room khusus dan memindahkanmu ke sana.

Pembuat voice room mendapatkan izin untuk mengelola room, memindahkan anggota, dan mengatur permission. Room yang sudah kosong dihapus otomatis.

Kamu tidak perlu menghapus channel sementara secara manual. Jika channel masih kosong setelah bot restart, Nexo akan membersihkannya saat memulai kembali.

## Melihat daftar channel server

Nexo sudah memiliki kemampuan untuk menampilkan daftar channel di server melalui percakapan AI. Belum ada command `$channels` atau `/channels` khusus untuk fitur ini, jadi gunakan mention atau `/ask`:

```text
Nexo, tampilkan semua channel yang ada di server ini.
```

```text
/ask question: Tolong tampilkan daftar channel teks, voice, dan stage di server.
```

Nexo akan menampilkan nama, jenis, dan ID channel yang dapat diakses bot. Informasi ini berguna ketika kamu ingin memastikan nama channel sebelum menggunakan `$say` atau ingin menemukan channel voice tertentu.

## Jika command tidak berhasil

Periksa hal berikut:

- gunakan `$help` untuk memastikan nama command benar;
- pastikan Nexo memiliki izin membaca dan mengirim pesan di channel tersebut;
- pastikan kamu memiliki izin pengelola untuk command seperti `$say`, `$clear`, dan pengelolaan lomba;
- gunakan format waktu yang dicontohkan;
- jika Nexo sedang memproses pertanyaan lain, tunggu sampai antrean selesai.

Jika masalah tetap terjadi, kirimkan command yang digunakan dan pesan error kepada pengelola server.
