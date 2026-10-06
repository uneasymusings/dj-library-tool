"""Versioned MCP inventory checked against installed distributions and integration tests."""

# The original catalog tool group (plans, jobs, exports); not the default MCP profile below.
CORE_TOOLS = frozenset(
    {
        "djlib_capabilities",
        "djlib_plan_collection",
        "djlib_start",
        "djlib_scan",
        "djlib_download",
        "djlib_source_inspect",
        "djlib_job",
        "djlib_jobs",
        "djlib_items",
        "djlib_control",
        "djlib_reviews",
        "djlib_resolve",
        "djlib_library",
        "djlib_collection",
        "djlib_export",
        "djlib_usb_preflight",
    }
)
DELIVERY_TOOLS = frozenset(
    {
        "djlib_delivery_targets",
        "djlib_plan_delivery",
        "djlib_delivery",
        "djlib_prepare_delivery",
        "djlib_bind_delivery_device",
        "djlib_observe_delivery",
        "djlib_verify_delivery_device",
        "djlib_verify_delivery_app",
        "djlib_inspect_delivery_native_xml",
    }
)
LIBRARY_WORKFLOW_TOOLS = frozenset(
    {
        "djlib_create_request",
        "djlib_request",
        "djlib_refresh_request",
        "djlib_resolve_request",
        "djlib_request_report",
        "djlib_track_metadata",
        "djlib_annotations",
        "djlib_annotate",
        "djlib_organize",
    }
)
PREP_TOOLS = frozenset(
    {"djlib_collect_request", "djlib_import_rekordbox_analysis", "djlib_find_sources"}
)
DISCOVERY_TOOLS = frozenset(
    {
        "djlib_collections",
        "djlib_requests",
        "djlib_deliveries",
        "djlib_roots",
        "djlib_add_roots",
        "djlib_reconcile",
    }
)
TOOL_NAMES = CORE_TOOLS | DELIVERY_TOOLS | LIBRARY_WORKFLOW_TOOLS | DISCOVERY_TOOLS | PREP_TOOLS

# What `djlib mcp serve` exposes by default: tracklist → owned/missing → crate, set links,
# web sources and rekordbox analysis. DJLIB_MCP_TOOLS=full adds delivery, organization,
# plans, exports and reconciliation (TOOL_NAMES).
PROFILE_ENV = "DJLIB_MCP_TOOLS"
CORE_PROFILE = frozenset(
    {
        "djlib_capabilities",
        "djlib_library",
        "djlib_roots",
        "djlib_add_roots",
        "djlib_scan",
        "djlib_create_request",
        "djlib_request",
        "djlib_requests",
        "djlib_refresh_request",
        "djlib_resolve_request",
        "djlib_collect_request",
        "djlib_collection",
        "djlib_collections",
        "djlib_source_inspect",
        "djlib_find_sources",
        "djlib_download",
        "djlib_job",
        "djlib_items",
        "djlib_import_rekordbox_analysis",
    }
)
PROFILES = {"core": CORE_PROFILE, "full": TOOL_NAMES}


def profile_tools(value: str | None) -> frozenset[str]:
    """The tools for a DJLIB_MCP_TOOLS value: "full" for all; anything else is core."""
    return PROFILES.get((value or "").strip().lower(), CORE_PROFILE)
