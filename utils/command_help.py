"""Build command help from the commands registered by the running bot."""

import discord
from discord import app_commands
from discord.ext import commands

PREFIX_GUIDES = {
    "ping": {
        "description": "Memeriksa latensi koneksi Nexo ke Discord Gateway.",
        "usage": ["$ping"],
    },
    "help": {
        "description": "Menampilkan panduan, kategori fitur, atau cara pakai command tertentu.",
        "usage": ["$help", "$help <command>", "$help <command> <topik>"],
        "examples": ["$help say", "$help say to", "$help competition add"],
    },
    "tools": {
        "description": "Menampilkan local Discord tools dan tools MCP yang aktif.",
        "usage": ["$tools"],
    },
    "status": {
        "description": "Memeriksa ketersediaan provider LLM dan MCP Server.",
        "usage": ["$status"],
    },
    "sync": {
        "description": "Menghapus command slash duplikat tingkat server lalu menyinkronkan command global. Khusus owner bot.",
        "usage": ["$sync"],
    },
    "clear": {
        "description": "Menghapus pesan terbaru di channel ini. Memerlukan izin Manage Messages.",
        "usage": ["$clear", "$clear <jumlah>"],
        "examples": ["$clear", "$clear 50"],
        "notes": ["Jumlah default adalah 100 pesan."],
    },
    "reset": {
        "description": "Menghapus riwayat percakapan AI milikmu di channel ini. Tambahkan `all` untuk menghapus seluruh riwayat persisten semua pengguna dan server; opsi ini memerlukan Manage Messages.",
        "usage": ["$reset", "$reset all"],
        "examples": ["$reset", "$reset all"],
        "notes": [
            "Alias: `$resetcontext`, `$clearcontext`.",
            "Slash command `/reset` hanya menghapus riwayatmu di channel tempat command dijalankan.",
        ],
    },
    "voice": {
        "description": "Melihat anggota di voice channel. Tanpa nama channel, Nexo memakai channel tempat command dijalankan atau voice channel yang sedang kamu ikuti.",
        "usage": ["$voice", "$voice <nama channel>", "/voice [channel_name]"],
        "examples": ["$voice", "$voice Lab IoT", "/voice channel_name: Lab IoT"],
        "notes": ["Alias prefix: `$vc`, `$voicechannel`."],
    },
    "say": {
        "description": "Mengirim pesan atas nama Nexo ke channel tujuan, membuat embed, atau menjadwalkan pengumuman. Memerlukan Manage Messages atau role pengelola yang dikonfigurasi.",
        "usage": [
            "$say to <channel> <pesan>",
            '$say to <channel> --embed [--title "Judul"] <pesan>',
            '$say to <channel> --time "YYYY-MM-DD HH:MM" <pesan>',
            "$say to <channel> --in <30m|2h|1d> <pesan>",
            "$say list",
            "$say cancel <UUID>",
        ],
        "examples": [
            "$say to #pengumuman Rapat dimulai pukul 19.00 WIB.",
            '$say to pengumuman --embed --title "Pengingat rapat" Jangan lupa hadir.',
            '$say to #pengumuman --time "2026-11-01 19:00" Rapat evaluasi.',
            "$say to #pengumuman --in 30m Pengingat: kumpul di ruang meeting.",
            "$say list",
            "$say cancel 550e8400-e29b-41d4-a716-446655440000",
        ],
        "notes": [
            "Channel dapat ditulis sebagai mention, ID, atau nama persis tanpa spasi. Kata `to` boleh dihilangkan.",
            "Waktu absolut memakai WIB. `--in` menerima menit (`m`), jam (`h`), atau hari (`d`).",
            "Topik: `$help say to`, `$help say embed`, `$help say time`, `$help say in`, `$help say list`, dan `$help say cancel`.",
        ],
    },
    "my-token-usage": {
        "description": "Menampilkan statistik token AI milikmu.",
        "usage": ["$my-token-usage", "/my-token-usage"],
        "notes": [
            "Prefix aliases: `$token-usage`, `$tokenstats`, `$tokens`, and `$mytoken`."
        ],
    },
    "server-token-leaderboard": {
        "description": "Menampilkan sepuluh pengguna teratas berdasarkan konsumsi token AI di server ini.",
        "usage": [
            "$tokenleaderboard",
            "$toptoken",
            "$topusers",
            "/server-token-leaderboard",
        ],
        "notes": [
            "Prefix aliases: `$token-leaderboard`, `$tokenleaderboard`, `$toptoken`, and `$topusers`."
        ],
    },
}

SAY_TOPIC_GUIDES = {
    "to": {
        "description": "Mengirim pesan ke channel yang ditentukan. `to` bersifat opsional; setelahnya tulis channel lalu isi pesan.",
        "usage": ["$say to <channel> <pesan>", "$say <channel> <pesan>"],
        "examples": [
            "$say to #pengumuman Rapat dimulai pukul 19.00 WIB.",
            "$say 1520017240097951774 Jangan lupa mengisi presensi.",
            "$say to pengumuman Jangan lupa hadir.",
        ],
        "notes": ["Channel dapat berupa mention, ID, atau nama persis tanpa spasi."],
    },
    "embed": {
        "description": "Mengirim isi pesan sebagai Discord embed. `--title` bersifat opsional.",
        "usage": ['$say to <channel> --embed [--title "Judul"] <pesan>'],
        "examples": [
            '$say to #pengumuman --embed --title "Pengingat rapat" Jangan lupa hadir.',
            "$say to #pengumuman --embed Pengumuman ini dikirim sebagai embed.",
        ],
        "notes": [
            "Gunakan `-e` sebagai alias `--embed`. Mention di isi embed dapat memicu notifikasi."
        ],
    },
    "time": {
        "description": "Menjadwalkan pengumuman pada tanggal dan jam tertentu dalam WIB.",
        "usage": [
            '$say to <channel> --time "YYYY-MM-DD HH:MM" <pesan>',
            '$say to <channel> --time "HH:MM" <pesan>',
        ],
        "examples": [
            '$say to #pengumuman --time "2026-11-01 19:00" Rapat evaluasi.',
            '$say to #pengumuman --time "19:00" Rapat evaluasi.',
        ],
        "notes": [
            "Jika hanya jam yang diberikan, jadwal dibuat untuk waktu tersebut berikutnya. Waktu harus berada di masa depan. Pemeriksaan jadwal berjalan setiap 20 detik."
        ],
    },
    "in": {
        "description": "Menjadwalkan pengumuman berdasarkan jeda dari waktu command diproses.",
        "usage": ["$say to <channel> --in <jumlah><m|h|d> <pesan>"],
        "examples": [
            "$say to #pengumuman --in 30m Pengingat rapat.",
            "$say to #pengumuman --in 2h --embed --title Pengingat Agenda dimulai.",
            "$say to #pengumuman --in 1d Pengingat agenda berlangsung besok.",
        ],
        "notes": [
            "Satuan yang didukung: `m` (menit), `h` (jam), `d` (hari). Detik dan nama satuan lengkap belum didukung."
        ],
    },
    "list": {
        "description": "Menampilkan pengumuman terjadwal yang masih menunggu di server ini.",
        "usage": ["$say list"],
    },
    "cancel": {
        "description": "Membatalkan pengumuman terjadwal yang belum terkirim menggunakan UUID jadwal.",
        "usage": ["$say cancel <UUID>"],
        "examples": ["$say cancel 550e8400-e29b-41d4-a716-446655440000"],
        "notes": [
            "Gunakan UUID dari konfirmasi jadwal atau `$say list`. Jadwal yang sudah dikirim atau gagal tidak dapat dibatalkan."
        ],
    },
}

SAY_TOPIC_ALIASES = {
    "--embed": "embed",
    "-e": "embed",
    "--title": "embed",
    "--time": "time",
    "--in": "in",
    "schedule": "time",
}


def _add_guide_fields(embed: discord.Embed, guide: dict) -> None:
    for field_name, label in (
        ("usage", "Cara pakai"),
        ("examples", "Contoh"),
        ("notes", "Catatan"),
    ):
        values = guide.get(field_name)
        if values:
            embed.add_field(
                name=label,
                value="\n".join(
                    f"• `{value}`" if field_name != "notes" else f"• {value}"
                    for value in values
                )[:1024],
                inline=False,
            )


def _prefix_help(
    command: commands.Command, bot, query_tail: list[str]
) -> discord.Embed:
    name = command.name
    if name == "say" and query_tail:
        topic = query_tail[0].casefold()
        topic = SAY_TOPIC_ALIASES.get(topic, topic)
        guide = SAY_TOPIC_GUIDES.get(topic)
        if guide:
            guide = dict(guide)
            guide["notes"] = [
                *guide.get("notes", []),
                "Semua bentuk `$say` memerlukan izin Manage Messages atau role pengelola yang dikonfigurasi.",
            ]
            embed = discord.Embed(
                title=f"Bantuan command: `$say {topic}`",
                description=guide["description"],
                color=discord.Color.blue(),
            )
            _add_guide_fields(embed, guide)
            return embed

    guide = PREFIX_GUIDES.get(name)
    if guide:
        embed = discord.Embed(
            title=f"Bantuan command: `${name}`",
            description=guide["description"],
            color=discord.Color.blue(),
        )
        _add_guide_fields(embed, guide)
    else:
        signature = f" {command.signature}" if command.signature else ""
        description = command.help or command.brief or "Tidak ada deskripsi tambahan."
        embed = discord.Embed(
            title=f"Bantuan command: `${name}`",
            description=description[:4096],
            color=discord.Color.blue(),
        )
        embed.add_field(name="Cara pakai", value=f"`${name}{signature}`", inline=False)

    if command.aliases:
        embed.add_field(
            name="Alias",
            value=", ".join(f"`${alias}`" for alias in command.aliases),
            inline=False,
        )

    slash_command = bot.tree.get_command(name)
    if isinstance(slash_command, app_commands.Command):
        _add_slash_command(embed, slash_command)
    return embed


def _slash_signature(command: app_commands.Command, prefix: str = "") -> str:
    parts = [f"/{prefix}{command.name}"]
    for parameter in command.parameters:
        name = parameter.name
        parts.append(f"<{name}>" if parameter.required else f"[{name}]")
    return " ".join(parts)


def _add_slash_command(embed: discord.Embed, command: app_commands.Command) -> None:
    description = command.description or "Tidak ada deskripsi tambahan."
    value = f"`{_slash_signature(command)}` — {description}"
    if command.parameters:
        options = []
        for parameter in command.parameters:
            required = "wajib" if parameter.required else "opsional"
            options.append(
                f"• `{parameter.name}` ({required}): {parameter.description or 'tanpa deskripsi'}"
            )
        value += "\n\n" + "\n".join(options)
    embed.add_field(name="Slash command", value=value[:1024], inline=False)


def _slash_help(command, query_tail: list[str]) -> discord.Embed:
    if isinstance(command, app_commands.Group):
        subcommand = None
        if query_tail:
            subcommand = next(
                (
                    item
                    for item in command.commands
                    if item.name.casefold() == query_tail[0].casefold()
                ),
                None,
            )
        if subcommand is not None:
            embed = discord.Embed(
                title=f"Bantuan command: `/{command.name} {subcommand.name}`",
                description=subcommand.description or command.description,
                color=discord.Color.blue(),
            )
            value = f"`{_slash_signature(subcommand, f'{command.name} ')}`"
            if subcommand.parameters:
                value += "\n" + "\n".join(
                    f"• `{parameter.name}` ({'wajib' if parameter.required else 'opsional'}): {parameter.description or 'tanpa deskripsi'}"
                    for parameter in subcommand.parameters
                )
            embed.add_field(name="Cara pakai", value=value[:1024], inline=False)
            return embed

        embed = discord.Embed(
            title=f"Bantuan command: `/{command.name}`",
            description=command.description,
            color=discord.Color.blue(),
        )
        value = "\n".join(
            f"• `/{command.name} {item.name}` — {item.description}"
            for item in command.commands
        )
        embed.add_field(
            name="Subcommand",
            value=value[:1024] or "Tidak ada subcommand.",
            inline=False,
        )
        return embed

    embed = discord.Embed(
        title=f"Bantuan command: `/{command.name}`",
        description=command.description or "Tidak ada deskripsi tambahan.",
        color=discord.Color.blue(),
    )
    value = f"`{_slash_signature(command)}`"
    if command.parameters:
        value += "\n" + "\n".join(
            f"• `{parameter.name}` ({'wajib' if parameter.required else 'opsional'}): {parameter.description or 'tanpa deskripsi'}"
            for parameter in command.parameters
        )
    embed.add_field(name="Cara pakai", value=value[:1024], inline=False)
    return embed


def build_command_help(bot, query: str) -> discord.Embed:
    """Resolve prefix or slash command help, including slash subcommands."""
    normalized = query.strip()
    slash_only = normalized.startswith("/")
    if normalized[:1] in {"$", "/"}:
        normalized = normalized[1:]
    parts = normalized.split()
    if not parts:
        return discord.Embed(
            title="Cara mencari bantuan",
            description="Gunakan `$help <command>` atau `$help <command> <topik>`. Contoh: `$help say to`.",
            color=discord.Color.blue(),
        )

    name, *query_tail = parts
    prefix_command = None if slash_only else bot.get_command(name)
    slash_command = bot.tree.get_command(name)

    if name.casefold() == "say" and not slash_only:
        return _prefix_help(bot.get_command("say"), bot, query_tail)
    if prefix_command is not None and not slash_only:
        return _prefix_help(prefix_command, bot, query_tail)
    if slash_command is not None:
        return _slash_help(slash_command, query_tail)

    available = sorted(
        {command.name for command in bot.commands if not command.hidden}
        | {command.name for command in bot.tree.get_commands()}
    )
    return discord.Embed(
        title=f"Command `{name}` tidak ditemukan",
        description=(
            "Cek nama command lalu coba `$help <command>`.\n"
            "Command yang tersedia: " + ", ".join(f"`{item}`" for item in available)
        )[:4096],
        color=discord.Color.orange(),
    )
