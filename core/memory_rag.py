"""
Persistent Long-Term Memory (RAG) using ChromaDB.
"""

from __future__ import annotations

import logging
import uuid
from typing import List, Dict, Any

from core.paths import base_dir

try:
    import chromadb
    from chromadb.config import Settings
    _CHROMADB_AVAILABLE = True
except ImportError:
    _CHROMADB_AVAILABLE = False

# Setup basic logging
logger = logging.getLogger(__name__)

# Singleton client instance
_chroma_client = None
_collection = None
_COLLECTION_NAME = "aria_memory"

def _init_db():
    """Initializes the ChromaDB persistent client and collection."""
    global _chroma_client, _collection
    if not _CHROMADB_AVAILABLE:
        return

    if _chroma_client is None:
        try:
            db_path = base_dir() / "memory" / "chroma"
            db_path.mkdir(parents=True, exist_ok=True)
            
            _chroma_client = chromadb.PersistentClient(path=str(db_path))
            _collection = _chroma_client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"} # Use cosine similarity
            )
        except Exception as e:
            logger.error(f"[MemoryRAG] Failed to initialize ChromaDB: {e}")
            _chroma_client = None
            _collection = None

def store_memory(category: str, content: str, metadata: dict = None) -> bool:
    """
    Stores a new memory into the vector database.
    
    Args:
        category (str): A string representing the category (e.g. 'preference', 'lesson_learned', 'user_info').
        content (str): The actual memory text.
        metadata (dict): Optional extra metadata fields.
    """
    _init_db()
    if not _collection:
        return False
        
    try:
        mem_id = str(uuid.uuid4())
        meta = metadata or {}
        meta["category"] = category
        
        _collection.add(
            documents=[content],
            metadatas=[meta],
            ids=[mem_id]
        )
        return True
    except Exception as e:
        logger.error(f"[MemoryRAG] Failed to store memory: {e}")
        return False

def retrieve_relevant_memory(query: str, top_k: int = 5, category: str = None) -> List[Dict[str, Any]]:
    """
    Retrieves the most relevant memories for a given query.
    
    Args:
        query (str): The query text to embed and search against.
        top_k (int): Number of results to return.
        category (str): Optional category filter.
        
    Returns:
        List of dictionaries with 'content', 'metadata', and 'distance'.
    """
    _init_db()
    if not _collection:
        return []
        
    try:
        where_clause = {"category": category} if category else None
        
        results = _collection.query(
            query_texts=[query],
            n_results=top_k,
            where=where_clause
        )
        
        memories = []
        if results and results["documents"] and len(results["documents"][0]) > 0:
            docs = results["documents"][0]
            metas = results["metadatas"][0]
            distances = results["distances"][0] if "distances" in results and results["distances"] else [0.0] * len(docs)
            
            for doc, meta, dist in zip(docs, metas, distances):
                memories.append({
                    "content": doc,
                    "metadata": meta,
                    "distance": dist
                })
        return memories
    except Exception as e:
        logger.error(f"[MemoryRAG] Failed to retrieve memory: {e}")
        return []

def format_memory_for_prompt(query: str, top_k: int = 5) -> str:
    """
    Convenience method to retrieve and format memories into a text block for LLM prompts.
    """
    memories = retrieve_relevant_memory(query, top_k)
    if not memories:
        return ""
        
    formatted = "--- RELEVANT PAST MEMORIES ---\n"
    for mem in memories:
        if mem["distance"] > 1.8: # Increased threshold because L2 distance can be >1.0
            continue
        formatted += f"- [{mem['metadata'].get('category', 'general')}]: {mem['content']}\n"
    formatted += "------------------------------\n"
    return formatted
