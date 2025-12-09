import re
import uuid

JITSI_BASE_URL = "https://meet.jit.si"

def create_jitsi_link(topic):
    """
    Generates a unique Jitsi Meet URL.
    Format: https://meet.jit.si/Topic-RandomUUID
    
    Args:
        topic (str): The subject or course name (e.g. "CSC 648")
    
    Returns:
        str: A full, clickable URL.
    """
    # 1. Clean the topic (remove spaces/special chars) to make it URL safe
    # e.g., "CSC 648" -> "CSC648"
    safe_topic = re.sub(r'[^a-zA-Z0-9]', '', topic or "Session")
    
    # 2. Add a unique identifier to prevent room collisions
    # We use a short UUID (first 8 chars) to keep the URL readable but unique
    unique_id = str(uuid.uuid4())[:8]
    
    # 3. Construct the room name
    room_name = f"{safe_topic}-{unique_id}"
    
    # 4. Return the full URL
    return f"{JITSI_BASE_URL}/{room_name}"