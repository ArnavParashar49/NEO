"""
Mind Palace (Deep Knowledge Graph) Controls
Allows NEO to explicitly build and query its own relational memory.
"""

from __future__ import annotations

import json
from typing import Any

from core.memory_graph import add_node, add_edge, get_neighborhood

def mind_palace(parameters: dict, response: Any = None, player: Any = None, session_memory: Any = None) -> str:
    action = parameters.get("action", "").lower()
    
    if action == "memorize_entity":
        name = parameters.get("name")
        label = parameters.get("label", "Entity")
        
        # Attributes might be passed as a dict or a JSON string
        raw_attrs = parameters.get("attributes", {})
        if isinstance(raw_attrs, str):
            try:
                attributes = json.loads(raw_attrs)
            except Exception:
                attributes = {"raw": raw_attrs}
        else:
            attributes = raw_attrs
            
        if not name: return "Error: 'name' is required to memorize an entity."
        
        node_id = add_node(name, label, attributes)
        if node_id:
            return f"Successfully memorized entity '{name}' ({label})."
        return f"Failed to memorize entity '{name}'."
        
    if action == "connect_entities":
        source = parameters.get("source")
        target = parameters.get("target")
        relationship = parameters.get("relationship")
        
        if not all([source, target, relationship]):
            return "Error: 'source', 'target', and 'relationship' are required."
            
        success = add_edge(source, target, relationship)
        if success:
            return f"Successfully connected '{source}' -[{relationship.upper()}]-> '{target}'."
        return f"Failed to connect entities."
        
    if action == "recall_connections":
        name = parameters.get("name")
        if not name: return "Error: 'name' is required to recall connections."
        
        data = get_neighborhood(name)
        if not data:
            return f"Entity '{name}' not found in the Mind Palace."
            
        lines = [f"--- Entity: {data['name']} ({data['label']}) ---"]
        if data['attributes']:
            lines.append("Attributes: " + json.dumps(data['attributes']))
        
        if data['connections_in']:
            lines.append("\nIncoming Connections:")
            for conn in data['connections_in']: lines.append(f"  {conn}")
            
        if data['connections_out']:
            lines.append("\nOutgoing Connections:")
            for conn in data['connections_out']: lines.append(f"  {conn}")
            
        if not data['connections_in'] and not data['connections_out']:
            lines.append("\n(No known connections to other entities yet)")
            
        return "\n".join(lines)
        
    return f"Unknown action '{action}'. Use memorize_entity, connect_entities, or recall_connections."
