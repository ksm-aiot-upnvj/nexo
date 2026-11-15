import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import discord

from db.models import MessageRole
from db.repositories.conversation import get_or_create_conversation
from db.repositories.token_usage import log_token_usage
from services.agent.tool_schema import (
    _validate_tool_arguments,
    sanitize_tools_list,
)
from services.llm.client import LLM_MODEL_NAME, SYSTEM_PROMPT, ai_client
from services.llm.context import get_conversation_context, save_message
from services.llm.token_counter import (
    MAX_COMPLETION_TOKENS,
    count_token,
    get_context_budget,
    get_prompt_estimate_safety_margin,
    record_prompt_token_usage,
)
from services.mcp.client import execute_mcp_tool
from utils.metrics import LLM_REQUEST_DURATION, LLM_REQUESTS

logger = logging.getLogger("agent_orchestrator")
WIB = timezone(timedelta(hours=7))


async def _run_tool_handler(bot, tool_name: str, arguments: dict, ctx_obj) -> str:
    """Run one local or MCP handler and normalize its result for the LLM."""
    if hasattr(bot, "local_tool_handlers") and tool_name in bot.local_tool_handlers:
        import inspect

        handler = bot.local_tool_handlers[tool_name]
        sig = inspect.signature(handler)
        if "ctx_obj" in sig.parameters:
            result = await handler(arguments, ctx_obj=ctx_obj)
        else:
            result = await handler(arguments)
    else:
        result = await execute_mcp_tool(tool_name, arguments)

    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False)
    return result if isinstance(result, str) else str(result)


async def _run_tool_handler_safely(bot, tool_name: str, arguments: dict, ctx_obj):
    started_at = time.perf_counter()
    succeeded = False
    try:
        result = await _run_tool_handler(bot, tool_name, arguments, ctx_obj)
        succeeded = True
        return result
    except (TypeError, ValueError, RuntimeError, KeyError, TimeoutError) as error:
        logger.exception("Tool handler failed (tool=%s)", tool_name)
        return f"Tool '{tool_name}' failed: {error}"
    except Exception:
        logger.exception("Unexpected tool handler failure (tool=%s)", tool_name)
        return f"Tool '{tool_name}' failed due to an unexpected internal error."
    finally:
        logger.info(
            "Tool execution finished (tool=%s, succeeded=%s, latency_ms=%s)",
            tool_name,
            succeeded,
            int((time.perf_counter() - started_at) * 1000),
        )


def _log_prompt_token_comparison(
    iteration: int, local_estimate: int, provider_usage: int | None
) -> int | None:
    """Log the difference between local prompt estimates and provider usage."""
    if provider_usage is None:
        return None
    delta = record_prompt_token_usage(local_estimate, provider_usage)
    logger.info(
        "Prompt token estimate comparison (iteration=%s, local_estimate=%s, provider_usage=%s, undercount=%s, safety_margin=%s)",
        iteration,
        local_estimate,
        provider_usage,
        delta,
        get_prompt_estimate_safety_margin(),
    )
    return provider_usage - local_estimate


async def _trim_history_for_request(
    messages: list,
    current_user_message: dict,
    tools: list[dict],
    context_window: int,
    completion_reserve: int,
) -> int:
    """Trim oldest persisted context until the serialized request fits its budget."""
    request_budget = (
        context_window - completion_reserve - get_prompt_estimate_safety_margin()
    )
    while True:
        request_tokens = await _estimate_serialized_request_tokens(messages, tools)
        if request_tokens <= request_budget:
            return request_tokens

        user_index = next(
            index
            for index, message in enumerate(messages)
            if message is current_user_message
        )
        if user_index <= 1:
            if await _shrink_tool_results_to_budget(messages, tools, request_budget):
                continue
            logger.warning(
                "Request exceeds the model input budget after conversation history was removed "
                "(estimated_tokens=%s, request_budget=%s, context_window=%s)",
                request_tokens,
                request_budget,
                context_window,
            )
            raise ValueError(
                "The current prompt and tool schemas exceed the model input budget."
            )

        messages.pop(1)


def _message_payload(message):
    if isinstance(message, dict):
        return message
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    return {"content": getattr(message, "content", "")}


async def _estimate_serialized_request_tokens(
    messages: list,
    tools: list[dict],
    *,
    exclude_tool_results: bool = False,
) -> int:
    """Estimate token use for the serialized messages and tool schemas."""
    total = await count_token(json.dumps(tools, ensure_ascii=False)) if tools else 0
    for message in messages:
        payload = _message_payload(message)
        if exclude_tool_results and payload.get("role") == "tool":
            continue
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        total += await count_token(serialized)
    return total


async def _truncate_tool_message_to_budget(message: dict, token_budget: int) -> bool:
    """Truncate one tool result without emitting malformed JSON text."""
    content = str(message.get("content", ""))
    if not content:
        return False

    try:
        json.loads(content)
        is_json = True
    except json.JSONDecodeError:
        is_json = False

    def candidate_for(prefix_length: int) -> str:
        if prefix_length >= len(content):
            return content
        preview = content[:prefix_length]
        if is_json:
            return json.dumps(
                {
                    "truncated": True,
                    "preview": preview,
                    "message": "Tool result exceeded the available context budget.",
                },
                ensure_ascii=False,
            )
        return preview + "\n[Tool result truncated to fit the context budget.]"

    async def fits(prefix_length: int) -> bool:
        payload = dict(message)
        payload["content"] = candidate_for(prefix_length)
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        return await count_token(serialized) <= token_budget

    if await fits(len(content)):
        return False

    low, high = 0, len(content)
    best = ""
    while low <= high:
        midpoint = (low + high) // 2
        if await fits(midpoint):
            best = candidate_for(midpoint)
            low = midpoint + 1
        else:
            high = midpoint - 1

    if best == content:
        return False
    message["content"] = best
    return True


async def _shrink_tool_results_to_budget(
    messages: list,
    tools: list[dict],
    request_budget: int,
) -> bool:
    """Fit tool results into remaining request space after history is removed."""
    tool_messages = [
        message
        for message in messages
        if _message_payload(message).get("role") == "tool"
    ]
    if not tool_messages:
        return False

    base_tokens = await _estimate_serialized_request_tokens(
        messages, tools, exclude_tool_results=True
    )
    remaining_tokens = max(0, request_budget - base_tokens)
    changed = False

    for index, message in enumerate(tool_messages):
        results_remaining = len(tool_messages) - index
        allowance = remaining_tokens // results_remaining
        original_tokens = await count_token(
            json.dumps(message, ensure_ascii=False, default=str)
        )
        if await _truncate_tool_message_to_budget(message, allowance):
            changed = True
        serialized = json.dumps(message, ensure_ascii=False, default=str)
        used_tokens = await count_token(serialized)
        remaining_tokens = max(0, remaining_tokens - used_tokens)
        if used_tokens < original_tokens:
            logger.warning(
                "Tool result reduced to fit the model context (tool=%s, estimated_tokens=%s, retained_tokens=%s)",
                message.get("name", "unknown"),
                original_tokens,
                used_tokens,
            )

    return changed


async def _execute_tool_calls(
    bot, calls: list[dict], parallel_safe_tools: set, ctx_obj
):
    """Run adjacent read-only calls concurrently and preserve call order."""
    results = [None] * len(calls)
    index = 0
    while index < len(calls):
        call = calls[index]
        if call["error"] is not None:
            results[index] = f"Tool call was not executed: {call['error']}"
            index += 1
            continue

        if call["tool_name"] in parallel_safe_tools:
            batch_end = index + 1
            while (
                batch_end < len(calls)
                and calls[batch_end]["error"] is None
                and calls[batch_end]["tool_name"] in parallel_safe_tools
            ):
                batch_end += 1
            results[index:batch_end] = await asyncio.gather(
                *(
                    _run_tool_handler_safely(
                        bot,
                        item["tool_name"],
                        item["arguments"],
                        ctx_obj,
                    )
                    for item in calls[index:batch_end]
                )
            )
            index = batch_end
        else:
            results[index] = await _run_tool_handler_safely(
                bot,
                call["tool_name"],
                call["arguments"],
                ctx_obj,
            )
            index += 1
    return results


def _append_tool_results(messages: list, calls: list[dict], results: list[str]) -> None:
    """Append tool results in call order using each original call identifier."""
    for call, result in zip(calls, results, strict=True):
        tool_call = call["tool_call"]
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": call["tool_name"],
                "content": result,
            }
        )


async def process_with_mcp_tools(
    bot,
    user_id: int,
    user_name: str,
    user_question: str,
    max_iterations: int = 4,
    ctx_obj=None,
):
    """
    Process a user question with MCP tool support, latency tracking, token usage analytics, and context memory.
    Returns (reply_text, used_tools_boolean, usage_dict, elapsed_s)
    """
    start_time = time.perf_counter()

    channel_id = (
        getattr(ctx_obj.channel, "id", None)
        if ctx_obj and hasattr(ctx_obj, "channel")
        else None
    )
    conv = await get_or_create_conversation(user_id, channel_id)

    current_time_str = datetime.now(WIB).strftime("%A, %d %B %Y - %H:%M WIB")

    # Extract Channel & Voice Channel info from ctx_obj
    channel_name = "Unknown"
    is_voice_chat = False
    user_vc_name = "None"

    if ctx_obj and hasattr(ctx_obj, "channel") and ctx_obj.channel:
        channel_name = getattr(ctx_obj.channel, "name", "Unknown")
        is_voice_chat = isinstance(
            ctx_obj.channel, (discord.VoiceChannel, discord.StageChannel)
        )

    user_obj = getattr(ctx_obj, "author", getattr(ctx_obj, "user", None))
    if (
        user_obj
        and hasattr(user_obj, "voice")
        and user_obj.voice
        and user_obj.voice.channel
    ):
        user_vc_name = user_obj.voice.channel.name

    dynamic_user_prompt = (
        f"<system_context>\n"
        f"Current Time WIB: {current_time_str}\n"
        f"User: {user_name} (<@{user_id}>)\n"
        f"Current Channel: #{channel_name} (Is Voice Chat: {is_voice_chat})\n"
        f"User Connected Voice Channel: {user_vc_name}\n"
        f"</system_context>\n"
        f"{user_question}"
    )

    all_tools = []
    if getattr(bot, "ai_tools", None):
        all_tools.extend(bot.ai_tools)
    if getattr(bot, "cached_mcp_tools", None):
        all_tools.extend(bot.cached_mcp_tools)
    sanitized_tools = sanitize_tools_list(all_tools)
    prompt_overhead_tokens = sum(
        [
            await count_token(SYSTEM_PROMPT),
            await count_token(dynamic_user_prompt),
            await count_token(json.dumps(sanitized_tools, ensure_ascii=False)),
        ]
    )
    context_window, history_budget, _ = await get_context_budget(prompt_overhead_tokens)
    summary, recent_messages, history_tokens = await get_conversation_context(
        user_id,
        channel_id,
        prompt_overhead_tokens=prompt_overhead_tokens,
    )
    logger.info(
        "Prepared inference context (model=%s, context_window=%s, history_tokens=%s, history_budget=%s, tools=%s)",
        LLM_MODEL_NAME,
        context_window,
        history_tokens,
        history_budget,
        len(sanitized_tools),
    )

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if summary:
        messages.append(
            {
                "role": "system",
                "content": f"<past_conversation_summary>\n{summary}\n</past_conversation_summary>",
            }
        )
    messages.extend(recent_messages)
    current_user_message = {"role": "user", "content": dynamic_user_prompt}
    messages.append(current_user_message)

    used_tools = False
    total_prompt_tokens = 0
    total_completion_tokens = 0

    guild_id = None
    if ctx_obj and hasattr(ctx_obj, "guild") and ctx_obj.guild:
        guild_id = ctx_obj.guild.id

    local_tool_names = set(getattr(bot, "local_tool_handlers", {}))
    local_tools = getattr(bot, "ai_tools", [])
    parallel_safe_tools = {
        tool["function"]["name"]
        for tool in local_tools
        if tool.get("readOnlyHint") is True
    }
    parallel_safe_tools.update(
        tool["function"]["name"]
        for tool in getattr(bot, "cached_mcp_tools", [])
        if tool.get("readOnlyHint") is True
        and tool["function"]["name"] not in local_tool_names
    )
    tool_schemas = {
        tool["function"]["name"]: tool["function"].get("parameters", {})
        for tool in sanitized_tools
        if isinstance(tool, dict)
        and isinstance(tool.get("function"), dict)
        and isinstance(tool["function"].get("name"), str)
    }
    try:
        for iteration in range(max_iterations):
            kwargs = {
                "model": LLM_MODEL_NAME,
                "messages": messages,
                "temperature": 0.7,
                "top_p": 0.95,
                "max_completion_tokens": MAX_COMPLETION_TOKENS,
            }
            # On the last iteration, omit tools to force the model to summarize existing data into text
            if sanitized_tools and iteration < (max_iterations - 1):
                kwargs["tools"] = sanitized_tools
                # Chat Completions providers reject reasoning models with tools
                # unless reasoning is explicitly disabled.
                kwargs["reasoning_effort"] = "none"

            estimated_prompt_tokens = await _trim_history_for_request(
                messages,
                current_user_message,
                kwargs.get("tools", []),
                context_window,
                MAX_COMPLETION_TOKENS,
            )

            inference_started = time.perf_counter()
            try:
                response = await ai_client.chat.completions.create(**kwargs)
                LLM_REQUESTS.labels("success").inc()
            except Exception:
                LLM_REQUESTS.labels("failure").inc()
                raise
            finally:
                LLM_REQUEST_DURATION.observe(time.perf_counter() - inference_started)

            # Accumulate token usage from response if available
            if hasattr(response, "usage") and response.usage:
                total_prompt_tokens += response.usage.prompt_tokens or 0
                total_completion_tokens += response.usage.completion_tokens or 0

            choice = response.choices[0]
            message = choice.message
            logger.info(
                "Inference iteration completed (iteration=%s, finish_reason=%s, prompt_tokens=%s, completion_tokens=%s)",
                iteration + 1,
                choice.finish_reason,
                getattr(getattr(response, "usage", None), "prompt_tokens", None),
                getattr(getattr(response, "usage", None), "completion_tokens", None),
            )
            provider_prompt_tokens = getattr(
                getattr(response, "usage", None), "prompt_tokens", None
            )
            _log_prompt_token_comparison(
                iteration + 1, estimated_prompt_tokens, provider_prompt_tokens
            )
            if choice.finish_reason == "length":
                logger.warning(
                    "Inference output reached its completion token limit (iteration=%s)",
                    iteration + 1,
                )

            # Check if the model wants to call a tool
            if message.tool_calls and iteration < (max_iterations - 1):
                logger.info(
                    "Model requested tool calls (tools=%s)",
                    [call.function.name for call in message.tool_calls],
                )
                used_tools = True
                messages.append(message)

                calls = []
                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    args = tool_call.function.arguments
                    if isinstance(args, str):
                        try:
                            arguments = json.loads(args) if args.strip() else {}
                        except (json.JSONDecodeError, TypeError) as json_err:
                            logger.warning(
                                "Model returned malformed JSON tool arguments (tool=%s, error=%s)",
                                tool_name,
                                json_err,
                            )
                            calls.append(
                                {
                                    "tool_call": tool_call,
                                    "tool_name": tool_name,
                                    "arguments": None,
                                    "error": json_err,
                                }
                            )
                            continue
                    elif isinstance(args, dict):
                        arguments = args
                    else:
                        calls.append(
                            {
                                "tool_call": tool_call,
                                "tool_name": tool_name,
                                "arguments": {},
                                "error": ValueError(
                                    "tool arguments must be a JSON object"
                                ),
                            }
                        )
                        continue

                    validation_error = _validate_tool_arguments(
                        tool_name, arguments, tool_schemas
                    )
                    if validation_error:
                        calls.append(
                            {
                                "tool_call": tool_call,
                                "tool_name": tool_name,
                                "arguments": arguments,
                                "error": ValueError(validation_error),
                            }
                        )
                        continue

                    logger.info("Executing tool (tool=%s)", tool_name)
                    calls.append(
                        {
                            "tool_call": tool_call,
                            "tool_name": tool_name,
                            "arguments": arguments,
                            "error": None,
                        }
                    )

                # Run only explicitly read-only calls concurrently; preserve mutation order.
                results = await _execute_tool_calls(
                    bot, calls, parallel_safe_tools, ctx_obj
                )

                results = [str(result) for result in results]
                _append_tool_results(messages, calls, results)
                for call, result in zip(calls, results, strict=True):
                    tool_name = call["tool_name"]
                    try:
                        parsed = json.loads(result)
                        if isinstance(parsed, list):
                            log_msg = f"[Retrieved {len(parsed)} data items]"
                        elif isinstance(parsed, dict):
                            log_msg = f"[Retrieved JSON object with {len(parsed.keys())} keys]"
                        else:
                            log_msg = str(result)[:100] + (
                                "..." if len(str(result)) > 100 else ""
                            )
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.debug(
                            "Tool '%s' returned non-JSON result: %s", tool_name, e
                        )
                        log_msg = str(result)[:100] + (
                            "..." if len(str(result)) > 100 else ""
                        )

                    logger.info(
                        "Tool execution completed (tool=%s, result=%s)",
                        tool_name,
                        log_msg,
                    )

                continue
            else:
                reply = (
                    message.content
                    or "Nexo could not generate a response for this request."
                )

                # Fallback estimation if usage was not returned
                if total_prompt_tokens == 0:
                    total_prompt_tokens = sum(
                        [
                            await count_token(str(m.get("content", "")))
                            if isinstance(m, dict)
                            else await count_token(str(getattr(m, "content", "")))
                            for m in messages
                        ]
                    )
                if total_completion_tokens == 0:
                    total_completion_tokens = await count_token(reply)

                total_tokens = total_prompt_tokens + total_completion_tokens
                elapsed_s = time.perf_counter() - start_time
                latency_ms = int(elapsed_s * 1000)

                # Persist message history with exact token count
                user_msg_tokens = await count_token(user_question)
                reply_msg_tokens = await count_token(reply)
                await save_message(
                    conv.id,
                    MessageRole.USER,
                    user_question,
                    token_count=user_msg_tokens,
                )
                await save_message(
                    conv.id,
                    MessageRole.ASSISTANT,
                    reply,
                    token_count=reply_msg_tokens,
                )

                # Log token usage asynchronously in DB
                asyncio.create_task(
                    log_token_usage(
                        guild_id=guild_id,
                        user_id=user_id,
                        username=user_name,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        total_tokens=total_tokens,
                        latency_ms=latency_ms,
                    )
                )

                usage_data = {
                    "prompt_tokens": total_prompt_tokens,
                    "completion_tokens": total_completion_tokens,
                    "total_tokens": total_tokens,
                    "latency_ms": latency_ms,
                }
                return reply, used_tools, usage_data, elapsed_s

    except Exception:
        logger.exception("Unexpected error in process_with_mcp_tools")
        elapsed_s = time.perf_counter() - start_time
        return (
            "Nexo encountered a technical issue while processing this request. Please try again shortly. 🤖",
            False,
            {},
            elapsed_s,
        )

    elapsed_s = time.perf_counter() - start_time
    return (
        "Nexo could not complete the request using the available tools. Please try again or rephrase the request.",
        used_tools,
        {},
        elapsed_s,
    )
