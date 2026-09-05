# CHANGELOG

<!-- version list -->

## v1.13.1 (2026-09-05)

### Bug Fixes

- **core**: Handle graceful shutdown in background task loops
  ([`2f839c2`](https://github.com/ksm-aiot-upnvj/nexo/commit/2f839c2045f75c80d566b64136e8778c90b4e08e))

- **gateway**: Drop duplicate published release webhook events
  ([`efc06f4`](https://github.com/ksm-aiot-upnvj/nexo/commit/efc06f46595fcfb6161eac84d44c5a210a910786))


## v1.13.0 (2026-09-05)

### Features

- Add competition radar cog, migrate repository to raw sql, and sanitize llama api key auth
  ([`4187a59`](https://github.com/ksm-aiot-upnvj/nexo/commit/4187a596f9d4901e1a7ba8904ebf0827c60a21a8))


## v1.12.0 (2026-09-03)

### Features

- **deploy**: Route releases to release-notes, mirror cd to dev-logs, add changelog button, and slim
  dockerfile
  ([`bb8b875`](https://github.com/ksm-aiot-upnvj/nexo/commit/bb8b87545bfbd129a0f08dd2d89a0b234d0aac9a))


## v1.11.1 (2026-09-03)

### Bug Fixes

- **gcal**: Strip quotes from gcal env vars and update docker credentials volume
  ([`afbe74d`](https://github.com/ksm-aiot-upnvj/nexo/commit/afbe74d6a74d9fcbed47ff901c9b687469eb43f6))


## v1.11.0 (2026-09-03)

### Documentation

- Update comprehensive architecture guide, folder structure, and project guidelines
  ([`26a4e2c`](https://github.com/ksm-aiot-upnvj/nexo/commit/26a4e2c6e0d94791984424fc2a9af85dc1687b3f))

### Features

- **events**: Add Google Calendar 2-way sync, dynamic broadcast messages, and interval reminders
  ([`39ed7ba`](https://github.com/ksm-aiot-upnvj/nexo/commit/39ed7ba48cd07ce6f7f85d548af5efc785bdb170))

- **events**: Implement hybrid event classifier, gcal 2-way sync, race-condition fix, and updated
  help command
  ([`b0cfdbe`](https://github.com/ksm-aiot-upnvj/nexo/commit/b0cfdbeb8c660b93494c0adc027f4763a656a90b))

- **orchestrator**: Add bearer auth, dynamic token budgeting, live footer compaction, and sync fixes
  ([`74ee39a`](https://github.com/ksm-aiot-upnvj/nexo/commit/74ee39a8ca69845c954e41003438731e4a34a512))

- **security**: Implement centralized RBAC authorization, anti-DoS rate limiting, and error
  sanitization
  ([`953e1c6`](https://github.com/ksm-aiot-upnvj/nexo/commit/953e1c6eea9118d178bb6ed334eaecc193975a78))

- **webhooks**: Modernize templates with compact markdown, discord link buttons, and view dispatch
  ([`5697d46`](https://github.com/ksm-aiot-upnvj/nexo/commit/5697d462f2800915b531c87839e13fe45f71c8bb))


## v1.10.0 (2026-09-01)

### Bug Fixes

- **gateway**: Filter duplicate cd triggers and unescape jinja templates
  ([`98777e3`](https://github.com/ksm-aiot-upnvj/nexo/commit/98777e325a71fe7e8fa944bf38de7eb1b9e533db))

- **orchestrator**: Prevent worker silent death and isolate tool execution errors
  ([`36ab76d`](https://github.com/ksm-aiot-upnvj/nexo/commit/36ab76df34cd507d3c9344730a5e9255f54c367e))

### Features

- **agent**: Add end_discord_poll tool, integrate db memory, and compact tool menu
  ([`277cae7`](https://github.com/ksm-aiot-upnvj/nexo/commit/277cae7ed2a59c3876dde1496499505ca3fca9b7))

- **conversation**: Implement 24-hour sliding TTL context expiration and graceful error handling
  ([`007d2ef`](https://github.com/ksm-aiot-upnvj/nexo/commit/007d2ef8633d62212d59fb8509ec6ee9c639c4ac))

- **db**: Implement async postgresql persistence, alembic migrations, and token compaction
  ([`4f2d449`](https://github.com/ksm-aiot-upnvj/nexo/commit/4f2d449436dbbe27a83c542b2c7eb90b0fde8cd5))

- **events**: Enforce WIB timezone, role mention footers, and broadcast AllowedMentions
  ([`4e1223d`](https://github.com/ksm-aiot-upnvj/nexo/commit/4e1223de32a255db4bfd99af9e74bc7eff17b198))

- **events**: Implement persistent discord event lifecycle, auto-reminder scheduler, and
  auto-management
  ([`7e00bd2`](https://github.com/ksm-aiot-upnvj/nexo/commit/7e00bd2f2740196619d018947a3af031ea7634f2))

- **tokens**: Implement true LRU token cache, usage analytics, UX feedback, and auto-migration
  entrypoint
  ([`1ff5d1a`](https://github.com/ksm-aiot-upnvj/nexo/commit/1ff5d1a3fa5484eadf3d82c34c412961508cc7b1))

### Performance Improvements

- **agent**: Sanitize tool schema and reduce context token footprint
  ([`1d6ef27`](https://github.com/ksm-aiot-upnvj/nexo/commit/1d6ef275dd33235604c61af6b2544234ed0caf56))


## v1.9.0 (2026-08-29)

### Features

- **config**: Add portainer webhook url for smart-hydroponic
  ([`baccf66`](https://github.com/ksm-aiot-upnvj/nexo/commit/baccf66a31681891e6c7b558632a646e25146359))


## v1.8.0 (2026-08-29)

### Features

- **deploy**: Integrate portainer webhook and add emergency fallback workflow
  ([`eae7b23`](https://github.com/ksm-aiot-upnvj/nexo/commit/eae7b23cf4662442a92ceb3a810e1015a980fb8f))


## v1.7.2 (2026-08-29)

### Bug Fixes

- **compose**: Migrate from env_file to explicit environment variables
  ([`7502ba5`](https://github.com/ksm-aiot-upnvj/nexo/commit/7502ba57843284573b6a7bcd5d50192cb5dc55b2))


## v1.7.1 (2026-08-14)

### Bug Fixes

- **events**: Update welcome channel env name and add new project configs
  ([`5036551`](https://github.com/ksm-aiot-upnvj/nexo/commit/503655115d534a4aac1b2966438d581cd8a07e33))


## v1.7.0 (2026-08-12)

### Features

- **gateway**: Defer CD announcements until VPS deploy succeeds and format release notes
  ([`c19348d`](https://github.com/ksm-aiot-upnvj/nexo/commit/c19348d5902ba64aed0358f525f7ccc5b217aa3b))

- **templates**: Integrate and format documentation update announcements
  ([`72395bf`](https://github.com/ksm-aiot-upnvj/nexo/commit/72395bf4448b636ea71ddd891ea906a1ece4939b))

- **templates**: Remove large image rendering across embeds for compact UI
  ([`5443a89`](https://github.com/ksm-aiot-upnvj/nexo/commit/5443a8975b44ca086d6d69226f303e9e9ac9354e))


## v1.6.0 (2026-08-12)

### Features

- **gateway**: Handle GitHub ping events gracefully and add ./data volume mount
  ([`6c8df64`](https://github.com/ksm-aiot-upnvj/nexo/commit/6c8df64bbe6087bf3989bb295a509a594c98d714))


## v1.5.0 (2026-08-12)

### Features

- **gateway**: Update webhook endpoints to /nexo/webhook and enhance interactive help UI
  ([`76ffae6`](https://github.com/ksm-aiot-upnvj/nexo/commit/76ffae6d43e6cb623694976dea1583ef0a509af7))


## v1.4.0 (2026-08-12)

### Features

- **deploy**: Configure VPS deployment paths and update gateway port to 8002
  ([`4b3c7ca`](https://github.com/ksm-aiot-upnvj/nexo/commit/4b3c7ca3e0e16ff6827f3e11345c2b0d55ede8e5))


## v1.3.0 (2026-08-12)

### Features

- **deploy**: Configure docker-compose to pull ksmaiotupnvj/nexo-bot:latest and expose port 8000
  ([`ddf61fb`](https://github.com/ksm-aiot-upnvj/nexo/commit/ddf61fb90476816c59790e62a7f52fc0251d21a6))


## v1.2.0 (2026-08-12)

### Features

- **gateway**: Implement team-centric notification routing and global release broadcast
  ([`18d139d`](https://github.com/ksm-aiot-upnvj/nexo/commit/18d139d5568998c5ea2e24dabb981216c8de8705))


## v1.1.4 (2026-08-12)

### Bug Fixes

- **templates**: Remove @everyone fallback when discord_role_id is null
  ([`3d65f51`](https://github.com/ksm-aiot-upnvj/nexo/commit/3d65f5191672284284a6ee4912f4f783ea0da79b))


## v1.1.3 (2026-08-11)

### Bug Fixes

- **ci**: Update tag regex in docker-prune workflow to support semver tags without v prefix
  ([`3300ddd`](https://github.com/ksm-aiot-upnvj/nexo/commit/3300ddd6bcc75cbf0f9cfcc3b310e1e883c73658))


## v1.1.2 (2026-08-11)

### Bug Fixes

- **ci**: Update docker hub secret name to DOCKER_RELEASE
  ([`558b3ce`](https://github.com/ksm-aiot-upnvj/nexo/commit/558b3ceebda75c1d610c55db13f90d8b53ccf838))


## v1.1.1 (2026-08-11)

### Bug Fixes

- **ci**: Allow docker build and push workflow for all release tag formats
  ([`6f47d7e`](https://github.com/ksm-aiot-upnvj/nexo/commit/6f47d7ec41957d20a315a10651174e741bfcd1bd))


## v1.0.0 (2026-08-11)

- Initial Release

## v1.1.0 (2026-08-11)

### Bug Fixes

- **ci**: Set semantic-release changelog template_dir to avoid conflict with bot templates
  ([`8fc98ae`](https://github.com/ksm-aiot-upnvj/nexo/commit/8fc98ae7289f87fad4a01a127172c27e6dd68608))

### Features

- Enhance voice channel commands, context reset, onboarding UI, and rate limit docs
  ([`b9d4097`](https://github.com/ksm-aiot-upnvj/nexo/commit/b9d4097f1f76cc623bbadf76554ce21bc118741a))

- Integrate FastAPI webhook gateway, dynamic CD notifications, and auto-deploy engine
  ([`c622c16`](https://github.com/ksm-aiot-upnvj/nexo/commit/c622c16cec7906c31a86cdaac5ae7cbbf74f5f2d))


## v1.0.1 (2026-08-04)

### Bug Fixes

- Migrate mcp client to SSE transport
  ([`12296e1`](https://github.com/ksm-aiot-upnvj/nexo/commit/12296e1fc93a0b9709bb38b5a6653e82b17e9934))


## v1.0.0 (2026-08-03)

- Initial Release
