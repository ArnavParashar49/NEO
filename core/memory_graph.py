"""
Deep Knowledge Graph (Mind Palace)
Uses SQLite to store entities (Nodes) and relationships (Edges).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

from core.paths import base_dir

logger = logging.getLogger(__name__)

_DB_PATH = base_dir() / "memory" / "knowledge_graph.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

def _get_conn():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the knowledge graph tables."""
    try:
        with _get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    label TEXT,
                    name TEXT UNIQUE,
                    attributes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS edges (
                    id TEXT PRIMARY KEY,
                    source_id TEXT,
                    target_id TEXT,
                    relationship TEXT,
                    weight REAL DEFAULT 1.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(source_id) REFERENCES nodes(id),
                    FOREIGN KEY(target_id) REFERENCES nodes(id),
                    UNIQUE(source_id, target_id, relationship)
                )
            ''')
            conn.commit()
    except Exception as e:
        logger.error(f"[KnowledgeGraph] DB Init Error: {e}")

def add_node(name: str, label: str = "Entity", attributes: Dict[str, Any] = None) -> Optional[str]:
    """Adds or updates a node in the graph. Returns the node ID."""
    name = name.lower().strip()
    label = label.capitalize()
    attrs_str = json.dumps(attributes or {})
    
    try:
        with _get_conn() as conn:
            cursor = conn.cursor()
            
            # Check if exists
            cursor.execute("SELECT id, label, attributes FROM nodes WHERE name = ?", (name,))
            row = cursor.fetchone()
            
            if row:
                node_id = row["id"]
                existing_attrs = json.loads(row["attributes"])
                if attributes:
                    existing_attrs.update(attributes)
                
                # Only overwrite label if we provided a meaningful one
                new_label = label if label != "Entity" else row["label"]
                
                cursor.execute(
                    "UPDATE nodes SET label = ?, attributes = ? WHERE id = ?",
                    (new_label, json.dumps(existing_attrs), node_id)
                )
                conn.commit()
                return node_id
            
            # Create new
            node_id = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO nodes (id, label, name, attributes) VALUES (?, ?, ?, ?)",
                (node_id, label, name, attrs_str)
            )
            conn.commit()
            return node_id
    except Exception as e:
        logger.error(f"[KnowledgeGraph] Add Node Error: {e}")
        return None

def add_edge(source_name: str, target_name: str, relationship: str, weight: float = 1.0) -> bool:
    """Adds a directional edge between two nodes. Creates nodes if they don't exist."""
    source_name = source_name.lower().strip()
    target_name = target_name.lower().strip()
    relationship = relationship.upper().strip().replace(" ", "_")
    
    source_id = add_node(source_name)
    target_id = add_node(target_name)
    
    if not source_id or not target_id:
        return False
        
    try:
        with _get_conn() as conn:
            cursor = conn.cursor()
            edge_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO edges (id, source_id, target_id, relationship, weight)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, target_id, relationship) 
                DO UPDATE SET weight = weight + 0.1
                """,
                (edge_id, source_id, target_id, relationship, weight)
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"[KnowledgeGraph] Add Edge Error: {e}")
        return False

def get_neighborhood(name: str) -> Dict[str, Any]:
    """Gets a node and all its direct connections."""
    name = name.lower().strip()
    try:
        with _get_conn() as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM nodes WHERE name = ?", (name,))
            node = cursor.fetchone()
            if not node:
                return {}
                
            node_id = node["id"]
            result = {
                "name": node["name"],
                "label": node["label"],
                "attributes": json.loads(node["attributes"]),
                "connections_out": [],
                "connections_in": []
            }
            
            # Outgoing edges (This node -> Other node)
            cursor.execute("""
                SELECT e.relationship, n.name as target_name, n.label as target_label
                FROM edges e
                JOIN nodes n ON e.target_id = n.id
                WHERE e.source_id = ?
            """, (node_id,))
            for row in cursor.fetchall():
                result["connections_out"].append(f"-[{row['relationship']}]-> ({row['target_label']}: {row['target_name']})")
                
            # Incoming edges (Other node -> This node)
            cursor.execute("""
                SELECT e.relationship, n.name as source_name, n.label as source_label
                FROM edges e
                JOIN nodes n ON e.source_id = n.id
                WHERE e.target_id = ?
            """, (node_id,))
            for row in cursor.fetchall():
                result["connections_in"].append(f"({row['source_label']}: {row['source_name']}) -[{row['relationship']}]->")
                
            return result
    except Exception as e:
        logger.error(f"[KnowledgeGraph] Get Neighborhood Error: {e}")
        return {}

# Initialize DB on import
init_db()

def format_graph_for_prompt() -> str:
    """Fetches a summary of the knowledge graph for LLM prompt injection."""
    try:
        sys_ctx = ""
        with _get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT label, name, attributes FROM nodes ORDER BY created_at ASC LIMIT 50")
            nodes = c.fetchall()
            if nodes:
                sys_ctx += "\n\n[MIND PALACE: ENTITIES]\n"
                sys_ctx += "\n".join([f"- {r['label']} '{r['name'].title()}': {r['attributes']}" for r in nodes])
                
            c.execute("SELECT s.name as src, e.relationship, t.name as tgt FROM edges e JOIN nodes s ON e.source_id = s.id JOIN nodes t ON e.target_id = t.id LIMIT 50")
            edges = c.fetchall()
            if edges:
                sys_ctx += "\n\n[MIND PALACE: RELATIONSHIPS]\n"
                sys_ctx += "\n".join([f"- {e['src'].title()} [{e['relationship'].upper()}] {e['tgt'].title()}" for e in edges])
        return sys_ctx
    except Exception as e:
        logger.error(f"[KnowledgeGraph] Format Error: {e}")
        return ""
