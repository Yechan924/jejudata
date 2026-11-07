"""분석 도구 모음. 모든 도구는 (db, **params) → ToolResult 를 반환하며, 결과는 반드시 집계 형태다."""
from .base import ToolResult, TOOL_REGISTRY, tool_specs, call_tool  # noqa: F401
from . import congestion, bus, weather, sqltool, forecast, compare, explore, dong  # noqa: F401  (등록 부수효과)
