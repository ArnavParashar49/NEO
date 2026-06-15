from pathlib import Path
from hybrid.declarations import TOOL_DECLARATIONS

# This tool is fundamental so its declaration is manually loaded, but
# we still include it for consistency.
TOOL_DECLARATION = {
    "name": "create_action",
    "description": "Writes and registers a new ARIA tool dynamically.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "tool_name": {"type": "STRING"},
            "code":      {"type": "STRING"}
        },
        "required": ["tool_name", "code"]
    }
}

def create_action(parameters: dict) -> str:
    """Writes a new Python action and triggers a hot-reload of the tool registry."""
    tool_name = parameters.get("tool_name")
    code = parameters.get("code")
    
    if not tool_name or not code:
        return "Error: tool_name and code are required."
        
    try:
        # Write the file
        actions_dir = Path(__file__).parent
        file_path = actions_dir / f"{tool_name}.py"
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)
            
        print(f"[create_action] Wrote new tool to {file_path}")
        
        # Trigger hot-reload
        from hybrid.bootstrap import register_all_tools, get_orchestrator
        orch = get_orchestrator()
        # This will discover the new tool, add it to TOOL_DECLARATIONS if needed, and update registry
        register_all_tools(orch.registry)
        
        return f"Successfully created and registered tool '{tool_name}'! You can now use it immediately."
        
    except Exception as e:
        return f"Failed to create action '{tool_name}': {e}"
