"""Versioned MCP inventory checked against installed distributions and integration tests."""

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
TOOL_NAMES = CORE_TOOLS | DELIVERY_TOOLS | LIBRARY_WORKFLOW_TOOLS
