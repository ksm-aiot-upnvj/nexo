import asyncio
import html
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import uvicorn
from discord.ext import commands
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from jinja2 import Environment, FileSystemLoader
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from config.settings import settings
from db.repositories.webhook_outbox import (
    claim_next_webhook_job,
    complete_webhook_job,
    enqueue_webhook_jobs,
    fail_or_retry_webhook_job,
    get_webhook_outbox_counts,
)
from db.session import async_session
from utils.github_webhook_delivery import claim_delivery, finish_delivery
from utils.metrics import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    OUTBOX_FAILURES,
    OUTBOX_JOBS,
    OUTBOX_RETRIES,
    WEBHOOK_DELIVERIES,
)
from utils.verify import verify_signature
from utils.webhook_schemas import (
    PullRequestSchema,
    PushSchema,
    ReleaseSchema,
    WorkflowSchema,
)

logger = logging.getLogger("gateway_server")

UVICORN_LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "access": {
            "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "default": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "class": "logging.StreamHandler",
            "formatter": "access",
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {
            "handlers": ["default"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.access": {
            "handlers": ["access"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
app = FastAPI(title="Nexo Webhook Gateway", docs_url="/docs")

# Global references for the gateway and durable outbox worker.
_bot: commands.Bot | None = None
_outbox_worker_active = False


@app.middleware("http")
async def observe_http_requests(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    started_at = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        route_name = getattr(route, "path", request.url.path)
        HTTP_REQUESTS.labels(request.method, route_name, str(status_code)).inc()
        HTTP_REQUEST_DURATION.labels(request.method, route_name).observe(
            time.perf_counter() - started_at
        )


# Jinja2 Template Loader
TEMPLATES_DIR = Path("templates")
PROJECTS_CONFIG_PATH = Path("config/projects.json")


def compact_markdown(text: str) -> str:
    """Normalizes excessive newlines and removes blank lines between list items."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse 3 or more consecutive newlines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove blank lines between bullet points (- or *)
    text = re.sub(r"(\n-\s+[^\n]+)\n\n(-\s+)", r"\1\n\2", text)
    text = re.sub(r"(\n\*\s+[^\n]+)\n\n(\*\s+)", r"\1\n\2", text)
    return text.strip()


if not TEMPLATES_DIR.exists():
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
env_template = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=False,
)
env_template.filters["unescape"] = html.unescape
env_template.filters["compact_markdown"] = compact_markdown


def save_webhook_payload(event: str, payload: Any, delivery: str = "") -> Path:
    """Save every incoming GitHub webhook payload into data/payloads/{event}.json."""
    data_dir = Path("data/payloads")
    if not data_dir.exists():
        data_dir.mkdir(parents=True, exist_ok=True)

    safe_event_name = (
        "".join(c for c in event if c.isalnum() or c in ("_", "-")) or "unknown"
    )
    event_file = data_dir / f"{safe_event_name}.json"
    existing = []
    if event_file.exists():
        try:
            with open(event_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
                if not isinstance(existing, list):
                    existing = [existing]
        except Exception:  # noqa: BLE001
            existing = []

    existing.append(payload)
    with open(event_file, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=4, ensure_ascii=False)

    return event_file


def load_projects_config() -> dict[str, Any]:
    if PROJECTS_CONFIG_PATH.exists():
        try:
            with open(PROJECTS_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to load projects.json: {e}")
    return {}


async def read_limited_request_body(request: Request, max_bytes: int) -> bytes:
    """Read a webhook body while enforcing a limit before buffering it fully."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared_length < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared_length > max_bytes:
            raise HTTPException(status_code=413, detail="Webhook payload too large")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise HTTPException(status_code=413, detail="Webhook payload too large")
        body.extend(chunk)
    return bytes(body)


def is_configured_deployment_workflow(
    model: Any, projects_config: dict[str, Any]
) -> bool:
    repository = getattr(model, "repository", None)
    repo_full_name = getattr(repository, "full_name", "") or ""
    repo_name = getattr(repository, "name", "") or ""
    repo_config = projects_config.get(
        repo_full_name, projects_config.get(repo_name, {})
    )

    if repo_config.get("deployment_mode") != "github_actions_status":
        return False

    workflow = getattr(model, "workflow_run", None)
    workflow_name = (getattr(workflow, "name", "") or "").strip().lower()
    configured_workflows = repo_config.get("deployment_workflows", [])
    if not isinstance(configured_workflows, list):
        return False

    return workflow_name in {
        str(name).strip().lower() for name in configured_workflows if str(name).strip()
    }


def should_send_event(
    event_name: str, model: Any, projects_config: dict[str, Any] | None = None
) -> bool:
    if event_name == "pull_request":
        allowed_actions = {
            "opened",
            "closed",
            "reopened",
            "synchronize",
            "ready_for_review",
            "converted_to_draft",
        }
        return getattr(model, "action", None) in allowed_actions
    if event_name == "release":
        # Ignore 'published' because it is already handled by 'released' to prevent duplicate broadcasts
        return getattr(model, "action", None) in {
            "released",
            "prereleased",
        }
    if event_name == "workflow_run":
        wf_run = getattr(model, "workflow_run", None)
        if (
            getattr(wf_run, "conclusion", None) == "skipped"
            and projects_config is not None
            and is_configured_deployment_workflow(model, projects_config)
        ):
            return False
        return (
            getattr(model, "action", None) == "completed"
            and wf_run is not None
            and getattr(wf_run, "conclusion", None) is not None
        )
    if event_name == "push":
        return bool(
            getattr(model, "commits", None) or getattr(model, "head_commit", None)
        )
    return True


@app.get("/nexo/health")
async def health_check():
    return await readiness_check()


@app.get("/nexo/health/live")
async def liveness_check():
    """Report that the HTTP process is running, without checking dependencies."""
    return {"status": "alive"}


@app.get("/nexo/health/ready")
async def readiness_check():
    bot_online = _bot is not None and _bot.is_ready()
    database_online = False
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        database_online = True
    except Exception as error:  # noqa: BLE001
        logger.warning("Readiness database probe failed (%s)", type(error).__name__)

    ready = bot_online and database_online and _outbox_worker_active
    body = {
        "status": "healthy" if ready else "not_ready",
        "bot_online": bot_online,
        "database_online": database_online,
        "outbox_worker_active": _outbox_worker_active,
        "bot_name": str(_bot.user) if _bot and _bot.user else "Connecting...",
    }
    return JSONResponse(status_code=200 if ready else 503, content=body)


@app.get("/metrics")
async def prometheus_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def _dispatch_outbox_job(bot: commands.Bot, job: dict[str, Any]) -> None:
    cog = bot.get_cog("WebhookDeployCog")
    if cog is None:
        raise RuntimeError("Webhook deployment cog is not loaded")

    payload = job["payload"]
    if job["job_type"] == "discord_notification":
        await cog.send_discord_notification(
            payload["message"],
            target_channel_id=payload.get("target_channel_id"),
            fallback_to_devlogs=payload.get("fallback_to_devlogs", True),
        )
        return
    if job["job_type"] == "portainer_deployment":
        await cog.trigger_docker_compose(
            repo_key=payload["repo_key"],
            repo_name=payload.get("repo_name", ""),
            target_channel_id=payload.get("target_channel_id"),
            announcement_payload=payload.get("announcement_payload"),
            notify_start=job["attempt_count"] == 1,
            raise_on_failure=True,
        )
        return
    raise ValueError(f"Unsupported webhook outbox job type: {job['job_type']}")


async def _refresh_outbox_metrics() -> None:
    try:
        counts = await get_webhook_outbox_counts()
    except Exception as error:  # noqa: BLE001
        logger.warning("Could not refresh outbox metrics (%s)", type(error).__name__)
        return
    for status in ("pending", "processing", "sent", "failed"):
        OUTBOX_JOBS.labels(status).set(counts.get(status, 0))


async def run_webhook_outbox_worker(bot: commands.Bot) -> None:
    """Deliver durable webhook effects and recover expired job leases after restart."""
    global _outbox_worker_active
    await bot.wait_until_ready()
    _outbox_worker_active = True
    logger.info("Webhook outbox worker started")
    last_metrics_refresh = 0.0
    try:
        while True:
            try:
                job = await claim_next_webhook_job()
                if job is None:
                    now = time.monotonic()
                    if now - last_metrics_refresh >= 30:
                        await _refresh_outbox_metrics()
                        last_metrics_refresh = now
                    await asyncio.sleep(settings.GITHUB_WEBHOOK_OUTBOX_POLL_SECONDS)
                    continue

                try:
                    async with asyncio.timeout(120):
                        await _dispatch_outbox_job(bot, job)
                    await complete_webhook_job(job["id"])
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001
                    terminal = await fail_or_retry_webhook_job(job["id"], error)
                    if terminal:
                        OUTBOX_FAILURES.inc()
                        logger.error(
                            "Webhook outbox job exhausted retries (job_id=%s, error_type=%s)",
                            job["id"],
                            type(error).__name__,
                        )
                        if job["job_type"] == "portainer_deployment":
                            cog = bot.get_cog("WebhookDeployCog")
                            if cog:
                                payload = job["payload"]
                                repo_name = (
                                    payload.get("repo_name") or payload["repo_key"]
                                )
                                failure_notice = {
                                    "content": (
                                        "❌ **Portainer Deploy Failed**\n"
                                        f"Repo: **{repo_name}**\n"
                                        "The Portainer trigger failed after all retry attempts."
                                    )
                                }
                                target_channel_id = payload.get("target_channel_id")
                                try:
                                    if target_channel_id and bot.get_channel(
                                        target_channel_id
                                    ):
                                        await cog.send_discord_notification(
                                            failure_notice,
                                            target_channel_id=target_channel_id,
                                            fallback_to_devlogs=False,
                                        )
                                    devlogs_channel_id = (
                                        settings.WEBHOOK_DEVLOGS_CHANNEL_ID
                                    )
                                    if (
                                        devlogs_channel_id
                                        and devlogs_channel_id != target_channel_id
                                    ):
                                        await cog.send_discord_notification(
                                            failure_notice,
                                            target_channel_id=devlogs_channel_id,
                                            fallback_to_devlogs=False,
                                        )
                                except Exception as notification_error:  # noqa: BLE001
                                    logger.error(
                                        "Could not send terminal deployment failure notice (%s)",
                                        type(notification_error).__name__,
                                    )
                    else:
                        OUTBOX_RETRIES.inc()
                        logger.warning(
                            "Webhook outbox job will retry (job_id=%s, attempt=%s, error_type=%s)",
                            job["id"],
                            job["attempt_count"],
                            type(error).__name__,
                        )
                await _refresh_outbox_metrics()
                last_metrics_refresh = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception(
                    "Webhook outbox worker iteration failed (%s)",
                    type(error).__name__,
                )
                await asyncio.sleep(settings.GITHUB_WEBHOOK_OUTBOX_POLL_SECONDS)
    finally:
        _outbox_worker_active = False
        logger.info("Webhook outbox worker stopped")


@app.post("/nexo/webhook")
async def webhook_handler(
    request: Request,
    x_github_event: str | None = Header(None, alias="X-GitHub-Event"),
    x_github_delivery: str | None = Header(None, alias="X-GitHub-Delivery"),
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
):
    event = x_github_event or "unknown"
    delivery = (x_github_delivery or "").strip()
    secret = x_hub_signature_256 or ""

    if not delivery or len(delivery) > 128:
        raise HTTPException(status_code=400, detail="Invalid X-GitHub-Delivery header")

    # Reject missing authentication/configuration before reading the body.
    if not secret:
        raise HTTPException(
            status_code=401, detail="Missing X-Hub-Signature-256 header"
        )
    if not settings.GITHUB_SECRET.strip():
        raise HTTPException(
            status_code=500,
            detail="GITHUB_SECRET is not configured on the server",
        )

    raw_body = await read_limited_request_body(
        request, settings.GITHUB_WEBHOOK_MAX_BODY_BYTES
    )
    verify_signature(raw_body, secret)

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.error(
            f"Invalid JSON payload received | event={event} delivery={delivery}"
        )
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info(f"Received GitHub event: '{event}', delivery ID: {delivery}")

    try:
        claimed = await claim_delivery(delivery, event)
    except Exception as e:
        logger.exception("Could not claim GitHub webhook delivery %s", delivery)
        raise HTTPException(
            status_code=503, detail="Webhook deduplication is unavailable"
        ) from e

    if not claimed:
        logger.info("Ignoring duplicate GitHub webhook delivery: %s", delivery)
        WEBHOOK_DELIVERIES.labels("duplicate").inc()
        return {"status": "duplicate_ignored", "event": event, "delivery": delivery}

    outbox_jobs: list[dict[str, Any]] = []
    try:
        result = await _process_verified_webhook(event, delivery, payload, outbox_jobs)
        if outbox_jobs:
            await enqueue_webhook_jobs(delivery, outbox_jobs)
            WEBHOOK_DELIVERIES.labels("queued").inc()
            return {
                "status": "queued",
                "event": event,
                "delivery": delivery,
                "outbox_jobs": len(outbox_jobs),
            }
    except Exception:
        WEBHOOK_DELIVERIES.labels("failed").inc()
        try:
            await finish_delivery(delivery, error=True)
        except Exception:
            logger.exception("Could not record failed GitHub delivery %s", delivery)
        raise

    try:
        await finish_delivery(delivery)
        WEBHOOK_DELIVERIES.labels("accepted").inc()
    except Exception as e:
        logger.exception("Could not record accepted GitHub delivery %s", delivery)
        raise HTTPException(
            status_code=503, detail="Could not record webhook delivery"
        ) from e
    return result


async def _process_verified_webhook(
    event: str,
    delivery: str,
    payload: Any,
    outbox_jobs: list[dict[str, Any]] | None = None,
):
    """Process a signature-verified and uniquely claimed GitHub delivery."""

    # Always persist every incoming payload for analysis & debugging
    event_file = save_webhook_payload(event, payload, delivery)

    schema_map = {
        "push": PushSchema,
        "pull_request": PullRequestSchema,
        "release": ReleaseSchema,
        "workflow_run": WorkflowSchema,
    }

    cog = _bot.get_cog("WebhookDeployCog") if _bot else None
    projects_config = load_projects_config()
    scheduled_jobs = outbox_jobs if outbox_jobs is not None else []

    def schedule_notification(
        message_payload: dict[str, Any],
        target_channel_id: int | None = None,
        fallback_to_devlogs: bool = True,
    ) -> None:
        scheduled_jobs.append(
            {
                "job_type": "discord_notification",
                "payload": {
                    "message": message_payload,
                    "target_channel_id": target_channel_id,
                    "fallback_to_devlogs": fallback_to_devlogs,
                },
            }
        )

    def schedule_deployment(
        repo_key: str,
        repo_name: str,
        target_channel_id: int | None,
        announcement_payload: dict[str, Any],
    ) -> None:
        scheduled_jobs.append(
            {
                "job_type": "portainer_deployment",
                "payload": {
                    "repo_key": repo_key,
                    "repo_name": repo_name,
                    "target_channel_id": target_channel_id,
                    "announcement_payload": announcement_payload,
                },
            }
        )

    if event == "ping":
        zen = payload.get("zen", "GitHub Webhook Connected!")
        repo_data = payload.get("repository", {}) or {}
        repo_full_name = repo_data.get("full_name", "")
        repo_name = repo_data.get("name", "")
        repo_config = projects_config.get(
            repo_full_name, projects_config.get(repo_name, {})
        )

        logger.info(f"GitHub ping event received: '{zen}' | delivery={delivery}")

        if cog:
            ping_msg = {
                "content": (
                    f"🟢 **GitHub Webhook Connected!**\n"
                    f"Repo: **{repo_full_name or repo_name or 'GitHub Organization'}**\n"
                    f'Quote: *"{zen}"*'
                )
            }
            target_channel_id = repo_config.get("discord_channel_id")
            schedule_notification(ping_msg, target_channel_id=target_channel_id)

        return {"status": "ping_received", "zen": zen, "delivery": delivery}

    schema_cls = schema_map.get(event)
    if schema_cls is None:
        logger.warning(f"Unsupported event type: {event} | delivery={delivery}")

        if cog:
            devlog_msg = {
                "content": (
                    f"⚠️ Received unsupported GitHub event: **{event}**\n"
                    f"Delivery ID: `{delivery}`\n"
                    f"Stored payload to `{event_file}`."
                ),
            }
            schedule_notification(devlog_msg)

        return {
            "status": "unsupported_event_stored",
            "event": event,
            "file": str(event_file),
        }

    if not isinstance(payload, list):
        payload_items = [payload]
    else:
        payload_items = payload

    for item in payload_items:
        try:
            model = schema_cls(**item)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to validate payload for event '{event}': {e}")
            raise HTTPException(status_code=500, detail="Payload validation failed.")

        if not should_send_event(event, model, projects_config):
            logger.info(
                f"Filtered event '{event}' with action '{getattr(model, 'action', None)}'"
            )
            continue

        repo_name = getattr(getattr(model, "repository", None), "name", "")
        repo_full_name = getattr(getattr(model, "repository", None), "full_name", "")
        repo_config = projects_config.get(
            repo_full_name,
            projects_config.get(
                repo_name,
                {
                    "title": "Deployment / Pipeline Event",
                    "emoji": "🚀",
                    "discord_channel_id": None,
                    "discord_role_id": None,
                },
            ),
        )

        template_name = f"{event}_message.j2"
        is_cd_success = False
        is_docs_update = False
        is_github_actions_deployment = False

        if event == "workflow_run" and getattr(model, "action", None) == "completed":
            wf_run = getattr(model, "workflow_run", None)
            wf_name = (getattr(wf_run, "name", "") or "").lower()
            is_github_actions_deployment = is_configured_deployment_workflow(
                model, projects_config
            )

            if is_github_actions_deployment:
                # The repository workflow owns deployment and health checks.
                # Report its final status without triggering Portainer again.
                pass
            elif wf_run and getattr(wf_run, "conclusion", None) == "success":
                if re.search(r"\b(?:docs?|documentation|pages)\b", wf_name):
                    # GitHub Pages deploys the docs independently. Announce its
                    # success, but do not redeploy the Nexo container in Portainer.
                    is_docs_update = True
                elif any(
                    k in wf_name
                    for k in [
                        "build & push",
                        "build and push",
                        "docker",
                        "publish image",
                        "deploy",
                    ]
                ) and not any(
                    k in wf_name
                    for k in [
                        "ci (lint & format)",
                        "ci",
                        "continuous integration",
                        "continuous delivery",
                    ]
                ):
                    is_cd_success = True
        elif event == "release" and getattr(model, "action", None) in (
            "released",
            "prereleased",
        ):
            # For releases, announce the release notes without triggering premature redeploy
            template_name = "release_message.j2"

        if is_docs_update:
            template_name = "announce_docs.j2"
        elif is_github_actions_deployment:
            template_name = "deployment_status_message.j2"
        elif is_cd_success:
            template_name = "cd_success_message.j2"

        try:
            template = env_template.get_template(template_name)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Template '{template_name}' not found: {e}")
            raise HTTPException(
                status_code=500, detail=f"Template {template_name} not found."
            )

        try:
            rendered_content = template.render(data=model, config=repo_config)
            message_payload = json.loads(rendered_content)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Template rendering failed for '{template_name}': {e}")
            raise HTTPException(status_code=500, detail="Template rendering failed.")

        if cog:
            target_channel_id = repo_config.get("discord_channel_id")
            repo_key = repo_name or repo_full_name

            if event == "release":
                release_channel_id = int(settings.WEBHOOK_RELEASE_NOTES_CHANNEL_ID)
                action = getattr(model, "action", None)
                if action == "released":
                    dest_channel_id = release_channel_id or target_channel_id
                    schedule_notification(
                        message_payload, target_channel_id=dest_channel_id
                    )
                elif action == "prereleased":
                    devlogs_channel_id = int(settings.WEBHOOK_DEVLOGS_CHANNEL_ID)
                    dest_channel_id = target_channel_id or devlogs_channel_id
                    if dest_channel_id and dest_channel_id != release_channel_id:
                        schedule_notification(
                            message_payload, target_channel_id=dest_channel_id
                        )
                    else:
                        logger.info(
                            f"Skipping prerelease event ('{action}') on release-notes channel"
                        )
                else:
                    logger.info(
                        f"Ignoring unsupported release event action ('{action}')"
                    )
            elif is_docs_update:
                # Documentation has its own hosting/deployment lifecycle.
                schedule_notification(
                    message_payload, target_channel_id=target_channel_id
                )
            elif is_github_actions_deployment:
                # The repo channel receives every result. Successful deployments
                # are also mirrored to devlogs; failures stay out of devlogs.
                project_channel_id = target_channel_id
                if project_channel_id:
                    schedule_notification(
                        message_payload,
                        target_channel_id=project_channel_id,
                        fallback_to_devlogs=False,
                    )
                else:
                    logger.error(
                        "No discord_channel_id configured for deployment notification: %s",
                        repo_full_name,
                    )

                wf_run = getattr(model, "workflow_run", None)
                if wf_run and getattr(wf_run, "conclusion", None) == "success":
                    devlogs_channel_id = int(settings.WEBHOOK_DEVLOGS_CHANNEL_ID)
                    if devlogs_channel_id and devlogs_channel_id != project_channel_id:
                        schedule_notification(
                            message_payload,
                            target_channel_id=devlogs_channel_id,
                        )
                    elif (
                        not devlogs_channel_id
                        and settings.WEBHOOK_DEVLOGS_CHANNEL
                        and project_channel_id
                    ):
                        schedule_notification(message_payload)
            elif is_cd_success:
                # Defer sending rich CD announcement until AFTER VPS deployment succeeds
                schedule_deployment(
                    repo_key=repo_key,
                    repo_name=repo_full_name,
                    target_channel_id=target_channel_id,
                    announcement_payload=message_payload,
                )
            else:
                # Standard non-CD event notification (Push, PR, In-progress workflow)
                schedule_notification(
                    message_payload, target_channel_id=target_channel_id
                )

    return {"status": "success", "event": event, "delivery": delivery}


async def start_gateway_server(bot: commands.Bot):
    global _bot
    _bot = bot

    host = settings.HOST  # nosec B104
    port = settings.PORT
    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
        log_config=UVICORN_LOGGING_CONFIG,
        use_colors=False,
    )
    server = uvicorn.Server(config)
    worker_task = asyncio.create_task(run_webhook_outbox_worker(bot))
    logger.info(f"Starting FastAPI Webhook Gateway Server on port {port}...")
    try:
        await server.serve()
    finally:
        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)
