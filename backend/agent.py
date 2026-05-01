"""
LangChain agent module.

Configures a tool-calling agent backed by Mistral Large and Tavily Search,
specialised as a cloud-infrastructure assistant that responds in Spanish.
"""

from __future__ import annotations

import os
import time
from typing import Any

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import LLMResult
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_mistralai import ChatMistralAI

from metrics import request_ctx

# ---------------------------------------------------------------------------
# Token usage callback
# ---------------------------------------------------------------------------

class _TokenCounter(BaseCallbackHandler):
    """Accumulates prompt/completion token counts across all LLM calls in a turn.

    Mistral returns usage in llm_output["usage"] with keys
    ``prompt_tokens`` and ``completion_tokens``.  A single agent turn
    may invoke the LLM twice (once to decide on tool use, once to
    synthesise the final answer), so we accumulate across calls.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tokens_in: int = 0
        self.tokens_out: int = 0

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        usage: dict[str, int] = (response.llm_output or {}).get("usage", {})
        self.tokens_in += usage.get("prompt_tokens", 0)
        self.tokens_out += usage.get("completion_tokens", 0)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "Eres un asistente experto en infraestructura cloud (AWS, GCP, Azure, "
    "Kubernetes, Terraform, CI/CD y DevOps en general). "
    "Respondes siempre en español de forma técnica pero clara y concisa. "
    "Cuando utilizas la herramienta de búsqueda web, citas las fuentes al "
    "final de tu respuesta con el formato: 'Fuentes: [título](url)'. "
    "Si la pregunta no requiere información actualizada, responde directamente "
    "con tu conocimiento sin invocar la búsqueda."
)

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

def _build_llm() -> ChatMistralAI:
    """Instantiate the Mistral LLM from environment variables.

    Returns:
        A :class:`ChatMistralAI` configured with ``mistral-large-latest``
        and a low temperature for deterministic technical answers.

    Raises:
        ValueError: If ``MISTRAL_API_KEY`` is not set in the environment.
    """
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        raise ValueError("MISTRAL_API_KEY environment variable is not set.")
    return ChatMistralAI(
        model="mistral-large-latest",
        temperature=0.3,
        api_key=api_key,
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _build_tools() -> list:
    """Build the list of tools available to the agent.

    Returns:
        A list containing the ``search_web`` Tavily tool.

    Raises:
        ValueError: If ``TAVILY_API_KEY`` is not set in the environment.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise ValueError("TAVILY_API_KEY environment variable is not set.")

    search = TavilySearchResults(
        max_results=3,
        api_key=api_key,
        name="search_web",
        description=(
            "Busca información actualizada en internet. "
            "Úsala cuando el usuario pregunte sobre eventos recientes, noticias "
            "o datos que podrían haber cambiado. "
            "No la uses para preguntas de conocimiento general."
        ),
    )
    return [search]


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

def _build_prompt() -> ChatPromptTemplate:
    """Build the chat prompt template with placeholders for history and input.

    Returns:
        A :class:`ChatPromptTemplate` with a system message, a
        ``chat_history`` placeholder for previous turns, the current
        ``input``, and an ``agent_scratchpad`` placeholder required by
        :func:`create_tool_calling_agent`.
    """
    return ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=_SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )


# ---------------------------------------------------------------------------
# AgentExecutor (lazy singleton)
# ---------------------------------------------------------------------------

_executor: AgentExecutor | None = None


def _get_executor() -> AgentExecutor:
    """Return a shared :class:`AgentExecutor`, building it on first call.

    Returns:
        A configured :class:`AgentExecutor` with verbose mode disabled.
    """
    global _executor
    if _executor is None:
        llm = _build_llm()
        tools = _build_tools()
        prompt = _build_prompt()
        agent = create_tool_calling_agent(llm=llm, tools=tools, prompt=prompt)
        _executor = AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=False,
            return_intermediate_steps=True,
            handle_parsing_errors=True,
        )
    return _executor


# ---------------------------------------------------------------------------
# History conversion
# ---------------------------------------------------------------------------

def _to_langchain_history(
    history: list[dict[str, str]],
) -> list[HumanMessage | AIMessage]:
    """Convert a plain-dict message history to LangChain message objects.

    Args:
        history: List of dicts with ``role`` (``'user'`` or ``'assistant'``)
            and ``content`` keys, as stored in Firestore.

    Returns:
        A list of :class:`HumanMessage` and :class:`AIMessage` objects
        suitable for the ``chat_history`` prompt placeholder.
    """
    lc_history: list[HumanMessage | AIMessage] = []
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            lc_history.append(HumanMessage(content=content))
        elif role == "assistant":
            lc_history.append(AIMessage(content=content))
    return lc_history


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_agent(message: str, history: list[dict[str, str]]) -> dict[str, Any]:
    """Execute the agent for a single user turn.

    Args:
        message: The current user message.
        history: Previous conversation turns as a list of dicts with
            ``role`` and ``content`` keys (Firestore format).

    Returns:
        A dict with:

        * ``response``      – The agent's text reply (``str``).
        * ``tokens_used``   – Total tokens (in + out) for backward compatibility.
        * ``tokens_in``     – Prompt/input tokens consumed.
        * ``tokens_out``    – Completion/output tokens generated.
        * ``used_search``   – ``True`` if the ``search_web`` tool was invoked.
        * ``inference_ms``  – Wall-clock time spent inside the LLM executor (ms).
    """
    try:
        executor = _get_executor()
        lc_history = _to_langchain_history(history)
        counter = _TokenCounter()

        t0 = time.perf_counter()
        result: dict[str, Any] = executor.invoke(
            {"input": message, "chat_history": lc_history},
            config={"callbacks": [counter]},
        )
        inference_ms = (time.perf_counter() - t0) * 1_000.0

        response_text: str = result.get("output", "")
        intermediate_steps: list = result.get("intermediate_steps", [])

        used_search = any(
            getattr(action, "tool", "") == "search_web"
            for action, _ in intermediate_steps
        )

        tokens_in: int = counter.tokens_in
        tokens_out: int = counter.tokens_out

        # Propagate sub-request metrics to the middleware via ContextVar.
        ctx = request_ctx.get()
        if ctx is not None:
            ctx["inference_ms"] = inference_ms
            ctx["tokens_in"] = tokens_in
            ctx["tokens_out"] = tokens_out
            ctx["used_search"] = used_search

        return {
            "response": response_text,
            "tokens_used": tokens_in + tokens_out,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "used_search": used_search,
            "inference_ms": inference_ms,
        }

    except ValueError as exc:
        # Configuration errors (missing API keys, etc.)
        error_msg = f"Error de configuración del agente: {exc}"
        print(f"[agent] ValueError: {exc}")
        return {
            "response": error_msg,
            "tokens_used": 0, "tokens_in": 0, "tokens_out": 0,
            "used_search": False, "inference_ms": 0.0,
        }

    except Exception as exc:
        # Runtime errors (network, model, parsing, etc.)
        error_msg = (
            "Lo siento, ocurrió un error al procesar tu consulta. "
            "Por favor, inténtalo de nuevo en unos momentos."
        )
        print(f"[agent] Unexpected error: {exc}")
        return {
            "response": error_msg,
            "tokens_used": 0, "tokens_in": 0, "tokens_out": 0,
            "used_search": False, "inference_ms": 0.0,
        }
