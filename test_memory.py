import logging
from core.memory_rag import store_memory, retrieve_relevant_memory

logging.basicConfig(level=logging.INFO)

print("Storing memory...")
store_memory("preference", "The user's favorite color is neon green.")

print("\nRetrieving memory...")
memories = retrieve_relevant_memory("What is my favorite color?", top_k=2)

for i, m in enumerate(memories):
    print(f"Result {i}: [{m['metadata']['category']}] {m['content']} (distance: {m['distance']})")

